"""Producer SDK: contract round-trip, availability semantics, lifecycle faults."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rl_risk_replay.events import ContractError, Event, validate_events
from sentinel_agent.producer import SentinelProducer, torchrun_run_id


def _make_producer(path: Path) -> SentinelProducer:
    return SentinelProducer(out_path=path, run_id="run-1", task="go2w_flat", seed="42",
                            planned_steps=100)


class ProducerContractTests(unittest.TestCase):
    def test_output_is_a_valid_strict_core_revision(self) -> None:
        """Everything the SDK writes must survive the frozen contract validator."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            producer = _make_producer(path)
            producer.start()
            for step in range(1, 4):
                producer.sample(step, reward=0.5 + 0.1 * step, approx_kl=0.012)
            producer.finish("completed")
            producer.label("pass")
            producer.close()
            events = tuple(Event.from_dict(json.loads(line))
                           for line in path.read_text(encoding="utf-8").splitlines() if line)
            self.assertEqual(len(events), 6)
            validate_events(events)  # must not raise

    def test_immediate_availability_equals_write_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            producer = _make_producer(Path(tmp) / "events.jsonl")
            produced = producer.start()
            self.assertEqual(produced["available_at"], produced["event_time"])
            producer.close()

    def test_deferred_availability_declares_real_lag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            producer = _make_producer(Path(tmp) / "events.jsonl")
            producer.start()
            produced = producer.sample(1, reward=0.5, defer_seconds=30.0)
            self.assertEqual(produced["available_at"], produced["event_time"] + 30.0)
            producer.close()

    def test_negative_defer_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            producer = _make_producer(Path(tmp) / "events.jsonl")
            producer.start()
            with self.assertRaises(ContractError):
                producer.sample(1, reward=0.5, defer_seconds=-1.0)
            producer.close()

    def test_sample_steps_must_strictly_increase(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            producer = _make_producer(Path(tmp) / "events.jsonl")
            producer.start()
            producer.sample(5, reward=0.5)
            with self.assertRaises(ContractError):
                producer.sample(3, reward=0.5)
            with self.assertRaises(ContractError):
                producer.sample(5, reward=0.5)
            producer.close()

    def test_lifecycle_ordering_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            producer = _make_producer(Path(tmp) / "events.jsonl")
            with self.assertRaises(ContractError):
                producer.sample(1, reward=0.5)
            producer.start()
            with self.assertRaises(ContractError):
                producer.label("pass")
            producer.finish("completed")
            with self.assertRaises(ContractError):
                producer.finish("failed")
            producer.label("pass")
            producer.close()

    def test_unsupported_metric_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            producer = _make_producer(Path(tmp) / "events.jsonl")
            producer.start()
            with self.assertRaises(ContractError):
                producer.sample(1, reward=0.5, not_a_metric=1.0)
            producer.close()

    def test_output_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            producer = _make_producer(path)
            producer.start()
            producer.close()
            with self.assertRaises(FileExistsError):
                _make_producer(path)

    def test_written_counts_emitted_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            producer = _make_producer(Path(tmp) / "events.jsonl")
            producer.start()
            producer.sample(1, reward=0.5)
            producer.finish("completed")
            producer.close()
            self.assertEqual(producer.written, 3)


class TorchRunTests(unittest.TestCase):
    def test_rank_suffix_only_for_multi_process(self) -> None:
        self.assertEqual(torchrun_run_id("task-seed"), "task-seed")
        import os

        old = {key: os.environ.get(key) for key in ("WORLD_SIZE", "RANK")}
        try:
            os.environ["WORLD_SIZE"] = "1"
            os.environ["RANK"] = "3"
            self.assertEqual(torchrun_run_id("task-seed"), "task-seed")
            os.environ["WORLD_SIZE"] = "8"
            self.assertEqual(torchrun_run_id("task-seed"), "task-seed-r3")
        finally:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_from_env_builds_rank_aware_producer(self) -> None:
        import os

        old = {key: os.environ.get(key) for key in ("WORLD_SIZE", "RANK")}
        try:
            os.environ["WORLD_SIZE"] = "4"
            os.environ["RANK"] = "2"
            with tempfile.TemporaryDirectory() as tmp:
                producer = SentinelProducer.from_env(out_path=Path(tmp) / "events.jsonl",
                                                     task="go2w_flat", seed="42",
                                                     planned_steps=10)
                self.assertEqual(producer.run_id, "go2w_flat-42-r2")
                producer.close()
        finally:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
