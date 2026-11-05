"""Producer SDK: emit frozen-core schema-v2 events from any training loop.

Every event is validated through ``rl_risk_replay.events.Event.from_dict``
before it is written, so anything this SDK emits satisfies the replay
contract by construction.  Stdlib only; framework adapters are thin wrappers
around :class:`SentinelProducer`.

Availability semantics (the reason this layer exists):

* training telemetry is observed at write time, so ``available_at == event_time``;
* lagging signals (evaluation jobs, offline labels) must declare their real
  observability delay via ``defer_seconds`` instead of faking immediacy.
  Under-reported delay is exactly the look-ahead bias the strict core exists
  to catch, so the SDK makes the honest choice the easy one.

One producer instance writes exactly one run: retries and resumed attempts
need a new ``run_id`` (the contract forbids a second ``start`` per run).
Events are appended with periodic ``fsync``; a crashed writer can leave a
partial final line, which the agent tolerates when tailing.
"""
from __future__ import annotations

import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

from rl_risk_replay.events import ContractError, Event, canonical, integer, number, text

SAMPLE_METRICS = frozenset({"reward", "approx_kl", "value_loss", "memory_percent",
                            "progress", "success_rate", "segment"})
FINISH_STATUSES = ("completed", "failed", "cancelled")
LABEL_VERDICTS = ("pass", "fail", "unknown")
LABEL_SOURCES = ("manual", "evaluation")


def torchrun_run_id(base: str) -> str:
    """Suffix the torchrun rank when running multi-process.

    The frozen schema-v2 sample payload has a fixed field whitelist, so rank
    identity is encoded in ``run_id`` instead of adding a payload field.  This
    keeps every rank's telemetry a separate, contract-valid run.
    """
    world = os.environ.get("WORLD_SIZE", "1")
    rank = os.environ.get("RANK", "0")
    try:
        if int(world) > 1:
            return f"{base}-r{integer(int(rank), 'RANK')}"
    except ValueError as exc:
        raise ContractError("WORLD_SIZE/RANK must be integers") from exc
    return base


@dataclass
class SentinelProducer:
    """Single-run event writer; output must not already exist (no overwrite)."""

    out_path: Path
    run_id: str
    task: str
    seed: str
    planned_steps: int
    fsync_every: int = 1
    clock: Callable[[], float] = time.time
    _handle: Any = field(default=None, init=False, repr=False)
    _phase: str = field(default="unstarted", init=False, repr=False)
    _last_step: int = field(default=-1, init=False, repr=False)
    _written: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        self.run_id = text(self.run_id, "run_id")
        self.task = text(self.task, "task")
        self.seed = text(self.seed, "seed")
        self.planned_steps = integer(self.planned_steps, "planned_steps", 1)
        self.fsync_every = integer(self.fsync_every, "fsync_every", 1)
        self.out_path = Path(self.out_path)
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.out_path.open("xb")

    @classmethod
    def from_env(cls, *, out_path: Path | str, task: str, seed: str, planned_steps: int,
                 run_id: str | None = None, **kwargs: Any) -> SentinelProducer:
        """Build a producer whose run_id is rank-aware under torchrun."""
        base = run_id if run_id is not None else f"{task}-{seed}"
        return cls(out_path=Path(out_path), run_id=torchrun_run_id(base), task=task,
                   seed=seed, planned_steps=integer(planned_steps, "planned_steps", 1),
                   **kwargs)

    # -- lifecycle ---------------------------------------------------------
    def start(self, *, event_time: float | None = None) -> dict[str, object]:
        if self._phase != "unstarted":
            raise ContractError("start must be the first event of a run")
        self._phase = "running"
        return self._emit("start", {"task": self.task, "seed": self.seed,
                                    "planned_steps": self.planned_steps},
                          event_time=event_time)

    def sample(self, step: int, *, defer_seconds: float = 0.0,
               event_time: float | None = None, **metrics: object) -> dict[str, object]:
        if self._phase != "running":
            raise ContractError("samples require a started, unfinished run")
        step = integer(step, "step")
        if step <= self._last_step:
            raise ContractError(
                f"sample steps must strictly increase per run (retry needs a new run_id): "
                f"{step} <= {self._last_step}")
        unknown = set(metrics) - SAMPLE_METRICS
        if unknown:
            raise ContractError(f"unsupported sample metrics: {sorted(unknown)}")
        self._last_step = step
        payload: dict[str, object] = {"step": step, **metrics}
        return self._emit("sample", payload, event_time=event_time, defer_seconds=defer_seconds)

    def finish(self, status: str, *, event_time: float | None = None) -> dict[str, object]:
        if self._phase != "running":
            raise ContractError("finish requires a started, unfinished run")
        if status not in FINISH_STATUSES:
            raise ContractError(f"finish status must be one of {FINISH_STATUSES}")
        self._phase = "finished"
        return self._emit("finish", {"status": status}, event_time=event_time)

    def label(self, verdict: str, *, source: str = "manual",
              event_time: float | None = None) -> dict[str, object]:
        if self._phase != "finished":
            raise ContractError("labels are only observable after a finish event")
        if verdict not in LABEL_VERDICTS or source not in LABEL_SOURCES:
            raise ContractError("invalid label verdict or source")
        return self._emit("label", {"verdict": verdict, "source": source},
                          event_time=event_time)

    @property
    def written(self) -> int:
        return self._written

    # -- output ------------------------------------------------------------
    def _emit(self, kind: str, payload: dict[str, object], *, event_time: float | None,
              defer_seconds: float = 0.0) -> dict[str, object]:
        now = number(self.clock(), "producer clock", 0)
        when = now if event_time is None else number(event_time, "event_time", 0)
        defer = number(defer_seconds, "defer_seconds", 0)
        event = Event.from_dict({
            "schema_version": 2, "event_id": uuid.uuid4().hex, "run_id": self.run_id,
            "kind": kind, "event_time": when, "available_at": when + defer,
            "payload": payload,
        })
        line = canonical(event.to_dict()) + "\n"
        assert self._handle is not None
        self._handle.write(line.encode("utf-8"))
        self._handle.flush()
        self._written += 1
        if self._written % self.fsync_every == 0:
            os.fsync(self._handle.fileno())
        return event.to_dict()

    def close(self) -> None:
        if self._handle is not None:
            self._handle.flush()
            os.fsync(self._handle.fileno())
            self._handle.close()
            self._handle = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
