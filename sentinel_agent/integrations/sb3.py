"""Stable-Baselines3 adapter: a callback that streams contract-valid events.

The public entrypoint :func:`sentinel_callback` imports stable-baselines3
lazily and fails with an actionable message when it is missing.  The class
itself is built by :func:`build_sentinel_callback`, which takes the
``BaseCallback`` class as a parameter — that keeps the callback logic
testable without the framework installed.

The callback is strictly observation-only: it returns ``True`` from
``_on_step`` on every call and never influences the training loop.  If the
training run crashes, SB3 does not call ``_on_training_end``; call
:meth:`SentinelCallbackAPI.mark_failed` from your own exception handler (the
producer silence alert covers the unhandled case anyway).
"""
from __future__ import annotations

import importlib.util
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from rl_risk_replay.events import ContractError

from ..producer import SentinelProducer
from .forwarding import MetricForwarder

DEFAULT_SB3_METRIC_MAP: Mapping[str, str] = {
    "rollout/ep_rew_mean": "reward",
    "train/approx_kl": "approx_kl",
    "train/value_loss": "value_loss",
}


def require_module(module_name: str, hint: str) -> Any:
    """Import a framework module or fail with an actionable install hint."""
    if importlib.util.find_spec(module_name.split(".")[0]) is None:
        raise ContractError(
            f"{module_name} is not installed; the watchdog adapter is optional. "
            f"Install it with: pip install '{hint}'")
    return importlib.import_module(module_name)


def _logger_values(callback: Any) -> dict[str, float]:
    """Snapshot the framework logger; values reduce to finite floats or drop."""
    logger = getattr(callback, "logger", None)
    values = getattr(logger, "name_to_value", None)
    if not isinstance(values, Mapping):
        return {}
    pairs: Mapping[object, object] = cast("Mapping[object, object]", values)
    result: dict[str, float] = {}
    for key, item in pairs.items():
        try:
            # Duck-typed on purpose: 0-dim tensors and numpy scalars reduce here.
            number = float(item)  # pyright: ignore[reportArgumentType]
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            result[str(key)] = number
    return result


def build_sentinel_callback(base: type[Any], *, producer: SentinelProducer,
                            metric_map: Mapping[str, str] | None = DEFAULT_SB3_METRIC_MAP,
                            emit_every_steps: int = 2048,
                            defer_seconds: float = 0.0) -> Any:
    """Assemble the SB3 callback around an injected ``BaseCallback`` class."""
    forwarder = MetricForwarder(producer, metric_map, defer_seconds=defer_seconds)
    every = int(emit_every_steps)
    if every <= 0:
        raise ValueError("emit_every_steps must be positive")

    class _SentinelCallback(base):  # type: ignore[misc, valid-type]
        producer: SentinelProducer
        num_timesteps: int
        logger: Any

        # SB3 calls _on_training_start(locals_); the argument is ignored here.
        def _on_training_start(self, locals_: dict[str, object] | None = None) -> None:
            producer.start()

        def _on_step(self) -> bool:
            last = forwarder.last_emitted_step or 0
            if self.num_timesteps - last >= every:
                forwarder.forward(self.num_timesteps, _logger_values(self))
            return True  # observation-only: never aborts training

        def _on_training_end(self) -> None:
            producer.finish("completed")

        def mark_failed(self) -> None:
            """Emit a failed finish; call from your own exception handler."""
            producer.finish("failed")

    callback = _SentinelCallback()
    callback.producer = producer  # exposed for preemption_guard and cleanup
    return callback


def sentinel_callback(*, out_path: Path | str, task: str, seed: str,
                      planned_steps: int, metric_map: Mapping[str, str] | None = DEFAULT_SB3_METRIC_MAP,
                      emit_every_steps: int = 2048, defer_seconds: float = 0.0,
                      producer_kwargs: Mapping[str, Any] | None = None) -> Any:
    """Create a ready-to-use SB3 callback (requires stable-baselines3 >= 2.0)."""
    callbacks = require_module("stable_baselines3.common.callbacks", "stable-baselines3>=2.0")
    producer = SentinelProducer.from_env(out_path=Path(out_path), task=task, seed=seed,
                                         planned_steps=planned_steps,
                                         **(producer_kwargs or {}))
    return build_sentinel_callback(callbacks.BaseCallback, producer=producer,
                                   metric_map=metric_map, emit_every_steps=emit_every_steps,
                                   defer_seconds=defer_seconds)
