"""Preemption guard and derived resume lineage (no schema change)."""
from __future__ import annotations

import json
import signal
import tempfile
import unittest
from pathlib import Path

from rl_risk_replay.events import make_event
from sentinel_agent.agent import Agent, AgentConfig, replay_file
from sentinel_agent.producer import SentinelProducer
from sentinel_agent.resilience import make_preemption_handler, preemption_guard


class _FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


def _started_producer(path: Path) -> SentinelProducer:
    producer = SentinelProducer(out_path=path, run_id="run-1", task="go2w_flat", seed="42",
                                planned_steps=1000)
    producer.start()
    producer.sample(7, reward=0.9, approx_kl=0.01)
    return producer


def _run_events(path: Path) -> bytes:
    return path.read_bytes()


class PreemptionHandlerTests(unittest.TestCase):
    def test_handler_emits_cancelled_finish_and_closes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            producer = _started_producer(Path(tmp) / "events.jsonl")
            handler = make_preemption_handler(producer, signal.SIGTERM, rekill=False)
            handler(signal.SIGTERM, None)
            events = [json.loads(line)
                      for line in _run_events(Path(tmp) / "events.jsonl").splitlines() if line]
            self.assertEqual(events[-1]["kind"], "finish")
            self.assertEqual(events[-1]["payload"]["status"], "cancelled")
            self.assertEqual(producer.written, 3)  # start + sample + cancelled finish

    def test_handler_tolerates_unstarted_and_finished_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # Unstarted: the handler cancels nothing and closes the file.
            unstarted = SentinelProducer(out_path=Path(tmp) / "a.jsonl", run_id="r1",
                                         task="t", seed="s", planned_steps=10)
            make_preemption_handler(unstarted, signal.SIGTERM, rekill=False)(signal.SIGTERM, None)
            self.assertEqual(unstarted.written, 0)
            # Already finished: no duplicate cancelled finish is emitted.
            finished = SentinelProducer(out_path=Path(tmp) / "b.jsonl", run_id="r2",
                                        task="t", seed="s", planned_steps=10)
            finished.start()
            finished.finish("completed")
            make_preemption_handler(finished, signal.SIGTERM, rekill=False)(signal.SIGTERM, None)
            finished.close()
            statuses = [json.loads(line)["payload"].get("status")
                        for line in _run_events(Path(tmp) / "b.jsonl").splitlines() if line
                        if json.loads(line)["kind"] == "finish"]
            self.assertEqual(statuses, ["completed"])

    def test_guard_installs_and_restores_handlers(self) -> None:
        original = signal.getsignal(signal.SIGTERM)
        with tempfile.TemporaryDirectory() as tmp:
            producer = SentinelProducer(out_path=Path(tmp) / "events.jsonl", run_id="r",
                                        task="t", seed="s", planned_steps=10)
            with preemption_guard(producer) as guarded:
                self.assertIs(guarded, producer)
                self.assertIsNot(signal.getsignal(signal.SIGTERM), original)
            self.assertIs(signal.getsignal(signal.SIGTERM), original)
            producer.close()


class ResumeLineageTests(unittest.TestCase):
    def _agent(self) -> Agent:
        return Agent(AgentConfig(stale_after=60.0), clock=_FakeClock())

    def _ingest(self, agent: Agent, run_id: str, seed: str, status: str | None,
               last_step: int | None = None) -> None:
        agent.ingest(make_event(f"{run_id}-s", run_id, "start", 1.0, 1.0,
                                {"task": "go2w_flat", "seed": seed,
                                 "planned_steps": 100}).to_dict())
        if last_step is not None:
            agent.ingest(make_event(f"{run_id}-m", run_id, "sample", 2.0, 2.0,
                                    {"step": last_step, "reward": 0.5}).to_dict())
        if status is not None:
            agent.ingest(make_event(f"{run_id}-f", run_id, "finish", 3.0, 3.0,
                                    {"status": status}).to_dict())

    def test_retry_after_cancel_is_attempt_two_and_flagged(self) -> None:
        agent = self._agent()
        self._ingest(agent, "attempt-1", "42", "cancelled", last_step=300)
        self._ingest(agent, "attempt-2", "42", None)
        state = agent.snapshot()["runs"]
        self.assertEqual(state["attempt-1"]["attempt"], 1)
        self.assertEqual(state["attempt-2"]["attempt"], 2)
        self.assertTrue(state["attempt-2"]["resumed_after_cancelled"])
        self.assertEqual(agent.counters()["preemptions"], 1)
        self.assertEqual(agent.counters()["steps_lost"], 300)

    def test_independent_seeds_have_independent_lineage(self) -> None:
        agent = self._agent()
        self._ingest(agent, "run-a", "42", "cancelled", last_step=100)
        self._ingest(agent, "run-b", "43", None)
        state = agent.snapshot()["runs"]
        self.assertEqual(state["run-b"]["attempt"], 1)
        self.assertFalse(state["run-b"]["resumed_after_cancelled"])

    def test_normal_completion_does_not_flag_next_start(self) -> None:
        agent = self._agent()
        self._ingest(agent, "run-a", "42", "completed")
        self._ingest(agent, "run-b", "42", None)
        self.assertFalse(agent.snapshot()["runs"]["run-b"]["resumed_after_cancelled"])
        self.assertEqual(agent.counters()["preemptions"], 0)

    def test_steps_lost_sums_across_multiple_preemptions(self) -> None:
        agent = self._agent()
        self._ingest(agent, "run-a", "42", "cancelled", last_step=100)
        self._ingest(agent, "run-b", "42", "cancelled", last_step=250)
        self._ingest(agent, "run-c", "42", None)
        self.assertEqual(agent.counters()["preemptions"], 2)
        self.assertEqual(agent.counters()["steps_lost"], 350)
        self.assertEqual(agent.snapshot()["runs"]["run-c"]["attempt"], 3)

    def test_metrics_declare_resilience_families_once(self) -> None:
        agent = self._agent()
        self._ingest(agent, "run-a", "42", "cancelled", last_step=100)
        self._ingest(agent, "run-b", "42", None)
        text = agent.metrics_text()
        self.assertEqual(text.count("# TYPE sentinel_preemptions_total counter"), 1)
        self.assertEqual(text.count("# TYPE sentinel_steps_lost_total counter"), 1)
        self.assertEqual(text.count("# TYPE sentinel_run_attempt gauge"), 1)
        self.assertIn("sentinel_preemptions_total 1", text)
        self.assertIn("sentinel_steps_lost_total 100", text)
        self.assertIn('sentinel_run_attempt{run_id="run-b",task="go2w_flat"} 2', text)

    def test_replay_rebuilds_identical_lineage(self) -> None:
        clock = _FakeClock()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            producer = SentinelProducer(out_path=path, run_id="attempt-1", task="t",
                                        seed="42", planned_steps=100)
            producer.start()
            producer.sample(50, reward=0.5)
            producer.finish("cancelled")
            producer.close()
            retry = SentinelProducer(out_path=Path(tmp) / "retry.jsonl", run_id="attempt-2",
                                     task="t", seed="42", planned_steps=100)
            retry.start()
            retry.close()
            first = Agent(AgentConfig(stale_after=60.0), clock=clock)
            replay_file(first, path)
            replay_file(first, Path(tmp) / "retry.jsonl")
            second = Agent(AgentConfig(stale_after=60.0), clock=clock)
            replay_file(second, path)
            replay_file(second, Path(tmp) / "retry.jsonl")
            self.assertEqual(first.snapshot(), second.snapshot())
            self.assertEqual(second.snapshot()["runs"]["attempt-2"]["attempt"], 2)


if __name__ == "__main__":
    unittest.main()
