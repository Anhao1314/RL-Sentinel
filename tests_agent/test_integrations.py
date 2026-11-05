"""Framework adapters: metric forwarding and lifecycle shells without frameworks."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import types
import unittest
from pathlib import Path

from rl_risk_replay.events import ContractError, Event, validate_events
from sentinel_agent.integrations.forwarding import MetricForwarder
from sentinel_agent.integrations.lightning import (
    build_lightning_callback,
    sentinel_lightning_callback,
)
from sentinel_agent.integrations.sb3 import build_sentinel_callback, sentinel_callback
from sentinel_agent.producer import SentinelProducer


def _producer(tmp: str, name: str = "events.jsonl") -> SentinelProducer:
    return SentinelProducer(out_path=Path(tmp) / name, run_id="run-1", task="go2w_flat",
                            seed="42", planned_steps=1000)


def _read_events(path: Path) -> tuple[Event, ...]:
    return tuple(Event.from_dict(json.loads(line))
                 for line in path.read_text(encoding="utf-8").splitlines() if line)


class MetricForwarderTests(unittest.TestCase):
    def test_maps_present_metrics_and_tracks_last_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            producer = _producer(tmp)
            producer.start()
            forwarder = MetricForwarder(producer, {"loss": "value_loss", "rew": "reward"})
            event = forwarder.forward(10, {"loss": 0.4, "rew": 1.2, "unmapped": 99})
            self.assertIsNotNone(event)
            self.assertEqual(event["payload"], {"step": 10, "value_loss": 0.4, "reward": 1.2})
            self.assertEqual(forwarder.last_emitted_step, 10)
            producer.close()

    def test_repeats_same_step_are_skipped_not_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            producer = _producer(tmp)
            producer.start()
            forwarder = MetricForwarder(producer, {"rew": "reward"})
            forwarder.forward(10, {"rew": 1.0})
            self.assertIsNone(forwarder.forward(10, {"rew": 1.0}))
            self.assertIsNone(forwarder.forward(9, {"rew": 1.0}))
            self.assertEqual(forwarder.skipped_repeats, 2)
            producer.close()

    def test_absent_and_nonfinite_metrics_are_never_written_as_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            producer = _producer(tmp)
            producer.start()
            forwarder = MetricForwarder(producer, {"kl": "approx_kl", "rew": "reward"})
            self.assertIsNone(forwarder.forward(1, {"rew": float("nan")}))
            self.assertIsNone(forwarder.forward(2, {"rew": float("inf")}))
            self.assertIsNone(forwarder.forward(3, {"kl": None}))
            self.assertIsNone(forwarder.forward(4, {}))
            self.assertEqual(forwarder.skipped_empty, 4)
            # tensors that cannot reduce to one float are skipped, not fatal
            class _StubTensor:
                def __float__(self) -> float:
                    raise TypeError("multi-element tensor")
            self.assertIsNone(forwarder.forward(5, {"rew": _StubTensor()}))
            self.assertEqual(forwarder.last_emitted_step, None)
            producer.close()

    def test_unknown_target_metric_is_rejected_at_construction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            producer = _producer(tmp)
            with self.assertRaises(ValueError):
                MetricForwarder(producer, {"x": "not_a_metric"})
            producer.close()


class _FakeSB3Base:
    def __init__(self) -> None:
        self.n_calls = 0
        self.num_timesteps = 0
        self.logger = None
        self.model = None


class SB3AdapterTests(unittest.TestCase):
    def test_callback_streams_valid_events_and_never_aborts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            producer = _producer(tmp)
            callback = build_sentinel_callback(_FakeSB3Base, producer=producer,
                                               metric_map={"rollout/ep_rew_mean": "reward",
                                                           "train/approx_kl": "approx_kl"},
                                               emit_every_steps=10)
            callback._on_training_start()
            callback.logger = types.SimpleNamespace(
                name_to_value={"rollout/ep_rew_mean": 1.5, "train/approx_kl": 0.02})
            callback.num_timesteps = 10
            self.assertTrue(callback._on_step())  # observation-only
            callback.num_timesteps = 15
            self.assertTrue(callback._on_step())  # below emit interval: no sample
            callback.num_timesteps = 21
            self.assertTrue(callback._on_step())
            callback.mark_failed()  # crash path: user handler reports the failure
            producer.label("fail")
            producer.close()
            events = _read_events(Path(tmp) / "events.jsonl")
            validate_events(events)
            samples = [event for event in events if event.kind == "sample"]
            self.assertEqual([event.payload()["step"] for event in samples], [10, 21])
            self.assertEqual(samples[0].payload()["approx_kl"], 0.02)
            finishes = [event.payload()["status"] for event in events if event.kind == "finish"]
            self.assertEqual(finishes, ["failed"])

    def test_factory_requires_stable_baselines3_with_actionable_hint(self) -> None:
        if importlib.util.find_spec("stable_baselines3") is not None:
            self.skipTest("stable-baselines3 is installed")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ContractError) as caught:
                sentinel_callback(out_path=Path(tmp) / "events.jsonl", task="t", seed="s",
                                  planned_steps=100)
            self.assertIn("pip install", str(caught.exception))


class _FakeLightningBase:
    pass


class LightningAdapterTests(unittest.TestCase):
    def test_callback_streams_valid_events_and_reports_exceptions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            producer = _producer(tmp)
            callback = build_lightning_callback(
                _FakeLightningBase, producer=producer, metric_map={"train_loss": "value_loss"})
            trainer = types.SimpleNamespace(global_step=0, callback_metrics={})
            callback.on_train_start(trainer, None)
            trainer.callback_metrics = {"train_loss": 0.7, "val_loss": 0.6}  # val unmapped
            trainer.global_step = 100
            callback.on_train_batch_end(trainer, None, None, None, 0)
            callback.on_train_batch_end(trainer, None, None, None, 1)  # repeat step: skipped
            trainer.global_step = 250
            trainer.callback_metrics = {"train_loss": 0.3}
            callback.on_train_batch_end(trainer, None, None, None, 2)
            callback.on_exception(trainer, None, RuntimeError("boom"))
            producer.close()
            events = _read_events(Path(tmp) / "events.jsonl")
            validate_events(events)
            samples = [event for event in events if event.kind == "sample"]
            self.assertEqual([event.payload()["step"] for event in samples], [100, 250])
            finishes = [event.payload()["status"] for event in events if event.kind == "finish"]
            self.assertEqual(finishes, ["failed"])

    def test_factory_requires_planned_steps(self) -> None:
        # Fires before any framework import: the argument error is unconditional.
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ContractError) as caught:
                sentinel_lightning_callback(out_path=Path(tmp) / "events.jsonl",
                                            task="t", seed="s", planned_steps=None)
            self.assertIn("planned_steps", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
