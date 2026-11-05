"""Append-only SQLite ingestion and independently verifiable output bundles."""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import tempfile
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Callable, Mapping, cast

from .events import (ContractError, Event, Kind, Origin, Scalar, canonical, integer,
                     make_event, mapping, number, parse_json, validate_events)

# A crashed publisher used to leave an empty O_EXCL lock file forever, making
# the output directory permanently unusable.  Locks now carry a creation time
# and can be reclaimed only after this TTL.
LOCK_TTL_NS = 300 * 1_000_000_000
PROVENANCE_FIELDS = frozenset({
    "provenance_schema_version", "code_revision", "environment",
    "input_manifest", "rule_registry",
})


def encode_events(events: tuple[Event, ...], origin: Origin) -> str:
    if origin not in ("observed", "synthetic", "controlled"):
        raise ContractError("unsupported origin")
    ordered = validate_events(events)
    return canonical({"schema_version": 2, "origin": origin}) + "\n" + "".join(
        canonical(e.to_dict()) + "\n" for e in ordered)


def decode_events(content: str) -> tuple[tuple[Event, ...], Origin]:
    lines = content.splitlines()
    if not lines:
        raise ContractError("empty event stream")
    header = mapping(parse_json(lines[0]))
    if set(header) != {"schema_version", "origin"} or type(header["schema_version"]) is not int or header["schema_version"] != 2:
        raise ContractError("missing schema-v2 stream header")
    if header["origin"] not in ("observed", "synthetic", "controlled"):
        raise ContractError("stream origin must be observed, synthetic or controlled")
    events: list[Event] = []
    for n, line in enumerate(lines[1:], 2):
        if not line.strip():
            raise ContractError(f"blank event line {n}")
        try:
            events.append(Event.from_dict(parse_json(line)))
        except (ValueError, TypeError) as exc:
            raise ContractError(f"event line {n}: {exc}") from exc
    return validate_events(tuple(events)), header["origin"]


class EventStore:
    """Local producer log. Imported timestamps remain claims of their producer."""
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        if not self.path.is_file():
            raise FileNotFoundError(self.path)

    @classmethod
    def create(cls, path: Path, origin: Origin = "observed") -> EventStore:
        if origin not in ("observed", "synthetic", "controlled"):
            raise ContractError("invalid origin")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb"):
            pass
        try:
            with closing(sqlite3.connect(str(path), isolation_level=None)) as con:
                con.execute("BEGIN IMMEDIATE")
                con.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                con.execute("CREATE TABLE events (event_id TEXT PRIMARY KEY, body TEXT NOT NULL, sha256 TEXT NOT NULL)")
                con.execute("INSERT INTO metadata VALUES ('schema_version','2')")
                con.execute("INSERT INTO metadata VALUES ('origin',?)", (origin,))
                con.execute("CREATE TRIGGER immutable_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'events are append-only'); END")
                con.execute("CREATE TRIGGER immutable_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'events are append-only'); END")
                con.execute("COMMIT")
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return cls(path)

    @staticmethod
    def _read(con: sqlite3.Connection) -> tuple[tuple[Event, ...], Origin]:
        metadata = dict(cast(list[tuple[str, str]], con.execute("SELECT key,value FROM metadata").fetchall()))
        if metadata.get("schema_version") != "2" or metadata.get("origin") not in ("observed", "synthetic", "controlled"):
            raise ContractError("unsupported store metadata")
        events: list[Event] = []
        for event_id, body, sha in cast(list[tuple[str, str, str]], con.execute("SELECT event_id,body,sha256 FROM events").fetchall()):
            if hashlib.sha256(body.encode("utf-8")).hexdigest() != sha:
                raise ContractError("event content hash mismatch")
            event = Event.from_dict(parse_json(body))
            if event.event_id != event_id:
                raise ContractError("event key/body mismatch")
            events.append(event)
        return validate_events(tuple(events)), cast(Origin, metadata["origin"])

    def read(self) -> tuple[tuple[Event, ...], Origin]:
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, isolation_level=None)) as con:
            con.execute("BEGIN")
            result = self._read(con)
            con.execute("COMMIT")
            return result

    def append(self, events: tuple[Event, ...], *, expected_origin: Origin | None = None) -> int:
        """All-or-nothing batch; exact duplicate IDs are idempotent, conflicts fail."""
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, isolation_level=None, timeout=10)) as con:
            con.execute("PRAGMA synchronous=FULL")
            con.execute("BEGIN IMMEDIATE")
            try:
                old, origin = self._read(con)
                if expected_origin is not None and origin != expected_origin:
                    raise ContractError("store and producer origins do not match")
                combined = {e.event_id: e for e in old}
                pending: dict[str, Event] = {}
                for e in events:
                    if e.event_id in combined and combined[e.event_id] != e:
                        raise ContractError(f"conflicting immutable event_id: {e.event_id}")
                    if e.event_id not in combined:
                        pending[e.event_id] = e
                    combined[e.event_id] = e
                validate_events(tuple(combined.values()))
                for e in pending.values():
                    body = canonical(e.to_dict())
                    con.execute("INSERT INTO events VALUES (?,?,?)",
                                (e.event_id, body, hashlib.sha256(body.encode("utf-8")).hexdigest()))
                con.execute("COMMIT")
                return len(pending)
            except BaseException:
                con.execute("ROLLBACK")
                raise

    def record(self, run_id: str, kind: Kind, payload: Mapping[str, Scalar], *,
               event_time: float | None = None, clock: Callable[[], float] = time.time) -> Event:
        """Stamp local availability now, rather than copying a historical step timestamp."""
        now = number(clock(), "producer clock", 0)
        event = make_event(uuid.uuid4().hex, run_id, kind,
                           now if event_time is None else event_time, now, payload)
        old, origin = self.read()
        if origin == "synthetic":
            raise ContractError("live recording cannot use a synthetic store")
        if old and now < max(e.available_at for e in old):
            raise ContractError("producer clock moved backwards")
        self.append((event,), expected_origin=origin)
        return event


def _reclaim_stale_lock(lock: Path) -> bool:
    """Remove only locks older than LOCK_TTL_NS; never touch a live publisher."""
    try:
        raw = lock.read_text(encoding="utf-8")
        created_ns: int | None = None
        if raw.strip():
            try:
                created_ns = integer(mapping(parse_json(raw))["created_ns"], "created_ns")
            except (ContractError, KeyError, TypeError, ValueError):
                return False
        if created_ns is None:
            # Pre-fix empty lock files left behind by a crashed publisher.
            created_ns = lock.stat().st_mtime_ns
        if time.time_ns() - created_ns < LOCK_TTL_NS:
            return False
        lock.unlink()
        return True
    except OSError:
        return False


def _acquire_lock(lock: Path) -> int:
    for attempt in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if attempt or not _reclaim_stale_lock(lock):
                raise
            continue
        # No fsync here: the O_EXCL creation is the atomic guarantee.  Keeping
        # fsync out of lock acquisition preserves the existing fault-injection
        # contract, where an fsync failure must happen inside the try/finally
        # so that staging and lock are always cleaned up.
        os.write(fd, canonical({"pid": os.getpid(), "created_ns": time.time_ns()}).encode("utf-8"))
        return fd
    raise FileExistsError(lock)


def publish_bundle(destination: Path, files: Mapping[str, str], *,
                   provenance: Mapping[str, object] | None = None) -> Path:
    """Stage and publish one complete directory under a reclaimable no-overwrite lock."""
    for name in files:
        if name in ("manifest.json", ".", "..") or Path(name).name != name or "\\" in name:
            raise ContractError("bundle members must be plain filenames, excluding manifest.json")
    destination = destination.absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock = destination.parent / f".{destination.name}.lock"
    fd = _acquire_lock(lock)
    stage: Path | None = None
    try:
        if os.path.lexists(destination):
            raise FileExistsError(destination)
        stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent))
        members: dict[str, object] = {}
        for name, content in files.items():
            data = content.encode("utf-8")
            with (stage / name).open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            members[name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        manifest: dict[str, object] = {"schema_version": 2, "status": "complete", "members": members}
        if provenance is not None:
            manifest["provenance"] = dict(provenance)
        with (stage / "manifest.json").open("xb") as handle:
            handle.write((canonical(manifest) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        if os.path.lexists(destination):
            raise FileExistsError(destination)
        os.rename(stage, destination)
        stage = None
        return destination
    finally:
        if stage is not None:
            shutil.rmtree(stage)
        os.close(fd)
        lock.unlink(missing_ok=True)


def verify_bundle(path: Path) -> dict[str, object]:
    if (path / "manifest.json").is_symlink():
        raise ContractError("symlink manifest is not a bundle member")
    manifest = mapping(parse_json((path / "manifest.json").read_text(encoding="utf-8")))
    allowed = {"schema_version", "status", "members", "provenance"}
    if not {"schema_version", "status", "members"} <= set(manifest) or set(manifest) - allowed:
        raise ContractError("unsupported or incomplete bundle manifest")
    if manifest["schema_version"] != 2 or manifest["status"] != "complete":
        raise ContractError("unsupported or incomplete bundle manifest")
    if "provenance" in manifest:
        prov = mapping(manifest["provenance"])
        if not PROVENANCE_FIELDS <= set(prov):
            raise ContractError("incomplete bundle provenance")
    members = mapping(manifest["members"])
    if {p.name for p in path.iterdir()} != set(members) | {"manifest.json"}:
        raise ContractError("bundle membership mismatch")
    for name, raw in members.items():
        if Path(name).name != name or name in (".", "..", "manifest.json") or "\\" in name:
            raise ContractError("unsafe bundle member")
        member = path / name
        if member.is_symlink() or not member.is_file():
            raise ContractError("bundle member must be a regular file")
        spec = mapping(raw)
        data = member.read_bytes()
        if set(spec) != {"sha256", "bytes"} or spec["bytes"] != len(data) or spec["sha256"] != hashlib.sha256(data).hexdigest():
            raise ContractError(f"bundle member checksum mismatch: {name}")
    return manifest
