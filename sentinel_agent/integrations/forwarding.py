"""Framework-free metric forwarding: framework dicts -> schema-v2 samples.

Training frameworks expose metrics as their own keyed dicts (SB3's
``logger.name_to_value``, Lightning's ``trainer.callback_metrics``).  This
module maps those onto producer samples under the frozen contract.  Deliberate
behaviors:

* repeats of the same step are skipped (frameworks re-log), not errors;
* only mapped, present, finite metrics are emitted — an absent metric is
  never written as 0 (the repository's ``N/A != 0`` discipline);
* mapping to unknown sample metrics raises at construction, not mid-training.
"""
from __future__ import annotations

import math
from collections.abc import Mapping

from ..producer import SAMPLE_METRICS, SentinelProducer


class MetricForwarder:
    """Forward framework metrics into contract-valid producer samples."""

    def __init__(self, producer: SentinelProducer,
                 metric_map: Mapping[str, str] | None = None, *,
                 defer_seconds: float = 0.0) -> None:
        metric_map = metric_map or {}
        unknown = {target for target in metric_map.values() if target not in SAMPLE_METRICS}
        if unknown:
            raise ValueError(f"unknown sample metrics in metric_map: {sorted(unknown)}")
        self.producer = producer
        self.metric_map: dict[str, str] = dict(metric_map)
        self.defer_seconds = float(defer_seconds)
        self._last_emitted_step: int | None = None
        self.skipped_repeats = 0
        self.skipped_empty = 0

    @property
    def last_emitted_step(self) -> int | None:
        return self._last_emitted_step

    def forward(self, step: int, source_metrics: Mapping[str, object]) -> dict[str, object] | None:
        """Emit one sample if new step and at least one mapped metric exist."""
        if self._last_emitted_step is not None and step <= self._last_emitted_step:
            self.skipped_repeats += 1
            return None
        payload: dict[str, object] = {}
        for source_key, target_key in self.metric_map.items():
            value = source_metrics.get(source_key)
            if value is None:
                continue
            try:
                number = float(value)  # tensors/numpy scalars reduce here
            except (TypeError, ValueError):
                continue  # multi-element tensors and friends are not telemetry
            if not math.isfinite(number):
                continue
            payload[target_key] = number
        if not payload:
            self.skipped_empty += 1
            return None
        event = self.producer.sample(step, defer_seconds=self.defer_seconds, **payload)
        self._last_emitted_step = step
        return event
