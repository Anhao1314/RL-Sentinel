"""Preemption handling: turn platform kill signals into honest cancelled finishes.

Spot reclaims and node drains arrive as SIGTERM/SIGINT.  The guard converts
them into a contract-valid ``finish("cancelled")`` with a flushed, fsynced
producer file, then restores the platform's default kill semantics — the
process still dies exactly as the scheduler expects, but the evidence stream
records why.  A retried attempt must use a new ``run_id`` (the frozen
contract already forbids a second start per run); the agent derives the
resume lineage from ``(task, seed)`` without any schema change.

SIGKILL cannot be handled — by design, by the kernel.  What survives is the
fsynced event stream, and the agent's producer-silence alert is the net that
catches that case.
"""
from __future__ import annotations

import os
import signal
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from rl_risk_replay.events import ContractError

from .producer import SentinelProducer


def make_preemption_handler(producer: SentinelProducer, signum: int, *,
                            rekill: bool = True):
    """Build a signal handler that cancels the run, then re-applies the kill.

    ``rekill=False`` is for tests and embedders that want to keep control of
    process exit.
    """
    def handler(received: int, frame: object) -> None:
        try:
            producer.finish("cancelled")
        except ContractError:
            pass  # not started, or already finished: nothing to cancel honestly
        producer.close()
        if rekill:
            signal.signal(received, signal.SIG_DFL)
            os.kill(os.getpid(), received)

    return handler


@contextmanager
def preemption_guard(producer: SentinelProducer, *,
                     signals: Sequence[int] | None = None) -> Iterator[SentinelProducer]:
    """Install cancellation handlers for the guarded block; restore on exit.

    Restricted environments (non-main threads, exotic platforms) are handled
    by skipping installation rather than failing the training run.
    """
    chosen = list(signals) if signals is not None else [
        sig for sig in (signal.SIGTERM, signal.SIGINT) if sig is not None]
    installed: dict[int, object] = {}
    for sig in chosen:
        try:
            installed[sig] = signal.signal(sig, make_preemption_handler(producer, sig))
        except (OSError, ValueError, RuntimeError):
            pass
    try:
        yield producer
    finally:
        for sig, previous in installed.items():
            try:
                signal.signal(sig, previous)
            except (OSError, ValueError, RuntimeError):
                pass
