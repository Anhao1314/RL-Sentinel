"""Lightning adapter: a callback that streams contract-valid events.

Works with both ``lightning`` and ``pytorch_lightning`` package names.
Like the SB3 adapter, the class is assembled around an injected ``Callback``
base so the logic is testable without the framework installed, and it is
strictly observation-only.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from rl_risk_replay.events import ContractError

from ..producer import SentinelProducer
from .forwarding import MetricForwarder
from .sb3 import _require

DEFAULT_LIGHTNING_METRIC_MAP: Mapping[str, str] = {
    "train_loss": "value_loss",
}


def _planned_steps(trainer: Any, fallback: int) -> int:
    max_steps = getattr(trainer, "max_steps", -1)
    try:
        return int(max_steps) if max_steps not in (None, -1) else int(fallback)
    except (TypeError, ValueError):
        return int(fallback)


def build_lightning_callback(base: type, *, producer: SentinelProducer,
                             metric_map: Mapping[str, str] | None = DEFAULT_LIGHTNING_METRIC_MAP,
                             defer_seconds: float = 0.0) -> Any:
    """Assemble the Lightning callback around an injected ``Callback`` class."""
    forwarder = MetricForwarder(producer, metric_map, defer_seconds=defer_seconds)

    class _SentinelLightningCallback(base):  # type: ignore[misc, valid-type]
        def on_train_start(self, trainer: Any, pl_module: Any) -> None:
            producer.start()

        def on_train_batch_end(self, trainer: Any, pl_module: Any, outputs: Any,
                               batch: Any, batch_idx: int) -> None:
            forwarder.forward(int(trainer.global_step),
                              dict(getattr(trainer, "callback_metrics", {})))

        def on_train_end(self, trainer: Any, pl_module: Any) -> None:
            producer.finish("completed")

        def on_exception(self, trainer: Any, pl_module: Any, exception: BaseException) -> None:
            producer.finish("failed")

    return _SentinelLightningCallback()


def sentinel_lightning_callback(*, out_path: Path | str, task: str, seed: str,
                                planned_steps: int | None = None,
                                metric_map: Mapping[str, str] | None = DEFAULT_LIGHTNING_METRIC_MAP,
                                defer_seconds: float = 0.0,
                                producer_kwargs: Mapping[str, Any] | None = None) -> Any:
    """Create a ready-to-use Lightning callback (requires lightning/pytorch_lightning)."""
    if planned_steps is None:
        raise ContractError(
            "planned_steps is required for the start event; pass trainer.max_steps "
            "or an explicit budget")
    try:
        base = _require("lightning.pytorch", "lightning>=2.0").Callback
    except ContractError:
        base = _require("pytorch_lightning", "lightning>=2.0").Callback
    producer = SentinelProducer.from_env(out_path=Path(out_path), task=task, seed=seed,
                                         planned_steps=planned_steps,
                                         **(producer_kwargs or {}))
    return build_lightning_callback(base, producer=producer, metric_map=metric_map,
                                    defer_seconds=defer_seconds)
