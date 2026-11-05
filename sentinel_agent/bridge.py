"""Bridge: carry agent-collected events into the frozen-core analysis layer.

The watchdog ingests raw producer JSONL (per-event contract validation, no
provenance header — the agent trusts the contract, not the file).  The
offline replay consumes a *versioned stream* whose first line explicitly
declares ``origin`` — a claim made by a human, not a file default.  This
module is the seam between the two:

* :func:`export_stream` validates, deduplicates and re-emits raw producer
  files as a replay-ready versioned stream; ``origin`` is a required
  argument so the provenance claim stays explicit.
* :func:`analyze` runs the frozen-core as-of replay on that stream in one
  command and returns its summaries.

A trailing partial line from a crashed writer is tolerated (dropped, and
counted); anything else that fails the contract fails loudly with its file
and line number.  Output files are never overwritten.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
from collections.abc import Sequence
from pathlib import Path

from rl_risk_replay.events import ContractError, Event, canonical
from rl_risk_replay.storage import decode_events

_ORIGINS = ("observed", "synthetic", "controlled")


def export_stream(paths: Sequence[Path], origin: str, out_path: Path) -> dict[str, object]:
    """Validate + deduplicate raw producer JSONL into a versioned stream.

    Returns a small stats document; the output file starts with the origin
    header and contains one canonical event per line, exactly what
    ``rl_risk_replay replay --events`` and ``decode_events`` accept.
    """
    if origin not in _ORIGINS:
        raise ContractError(f"origin must be one of {_ORIGINS}")
    seen: dict[str, str] = {}
    events: list[Event] = []
    duplicates = 0
    dropped_partial = 0
    for path_index, path in enumerate(paths):
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        for line_no, line in enumerate(lines, 1):
            if not line.strip():
                continue
            is_last_of_last = (path_index == len(paths) - 1 and line_no == len(lines))
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                if is_last_of_last:
                    dropped_partial += 1  # crashed writer: tolerate the tail
                    break
                raise ContractError(f"{path}:{line_no}: invalid JSON") from None
            try:
                event = Event.from_dict(raw)
            except (ValueError, TypeError) as exc:
                raise ContractError(f"{path}:{line_no}: {exc}") from None
            body = canonical(event.to_dict())
            if event.event_id in seen:
                if seen[event.event_id] != body:
                    raise ContractError(
                        f"{path}:{line_no}: conflicting immutable event_id "
                        f"{event.event_id}")
                duplicates += 1
                continue
            seen[event.event_id] = body
            events.append(event)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = out_path.open("xb")
    except FileExistsError:
        raise ContractError(
            f"stream already exists: {out_path} (the bridge never overwrites)") from None
    with handle:
        handle.write((canonical({"schema_version": 2, "origin": origin}) + "\n").encode("utf-8"))
        for event in events:
            handle.write((canonical(event.to_dict()) + "\n").encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())
    # Round-trip guarantee: what we wrote is exactly what the core consumes.
    decode_events(out_path.read_text(encoding="utf-8"))
    return {"stream": str(out_path), "events": len(events),
            "duplicates": duplicates, "dropped_trailing_partial": dropped_partial,
            "origin": origin}


def analyze(paths: Sequence[Path], origin: str, out_dir: Path, *,
            policy: str = "all", progress: str = "0.3,0.5,0.7") -> dict[str, object]:
    """One command: versioned stream + frozen-core as-of replay.

    ``out_dir`` must not exist (the core's no-overwrite bundle rule); the
    versioned stream is written next to it as ``<out_dir>.stream.jsonl``.
    """
    from rl_risk_replay.__main__ import main as core_main

    out_dir = Path(out_dir)
    stream_path = Path(str(out_dir) + ".stream.jsonl")
    stats = export_stream(paths, origin, stream_path)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = core_main(["replay", "--events", str(stream_path), "--out", str(out_dir),
                          "--policy", policy, "--progress", progress])
    if code != 0:
        raise ContractError(f"core replay failed: {buffer.getvalue().strip()[:300]}")
    document = json.loads((out_dir / "results.json").read_text(encoding="utf-8"))
    return {**stats, "out_dir": str(out_dir),
            "decisions": len(document["decisions"]),
            "summaries": document["summaries"]}
