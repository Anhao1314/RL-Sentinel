"""Demo trainer: a run that looks healthy, then deadlocks without a finish.

The point of the demo is the staleness trap: the last emitted samples show a
climbing reward and a calm KL, then the writer goes silent while the process
stays alive.  A naive dashboard keeps displaying the healthy plateau; the
agent raises a producer-silence alert instead.  Nothing here stops or
signals any real training process.
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable
from pathlib import Path

from .producer import SentinelProducer


def run_demo(out_path: Path, *, task: str = "hang-demo", seed: str = "demo",
             planned_steps: int = 1000, samples: int = 40, interval: float = 1.0,
             hang: bool = True, clock: Callable[[], float] = time.time) -> dict[str, object]:
    """Emit ``samples`` healthy checkpoints, then either finish or hang."""
    producer = SentinelProducer.from_env(out_path=out_path, task=task, seed=seed,
                                         planned_steps=planned_steps, clock=clock)
    summary: dict[str, object] = {}
    try:
        producer.start()
        for step in range(1, samples + 1):
            progress = step / samples
            producer.sample(
                step,
                reward=round(1.5 * (1.0 - math.exp(-3.0 * progress)), 6),
                approx_kl=round(0.01 + 0.004 * math.sin(step / 3.0), 6),
                value_loss=round(0.8 * (1.0 - progress) + 0.05, 6),
                memory_percent=round(38.0 + 2.0 * math.sin(step / 5.0), 6),
                progress=round(progress, 6),
            )
            if interval > 0:
                time.sleep(interval)
        if hang:
            summary["outcome"] = "hung: writer silent, process alive, no finish event"
            while True:  # simulated deadlock
                time.sleep(3600)
        producer.finish("completed")
        producer.label("pass")
        summary["outcome"] = "completed normally"
    finally:
        producer.close()
    summary["events"] = producer.written
    return summary
