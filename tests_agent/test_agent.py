"""Agent: silence detection, dedup, crash recovery, metrics, webhook delivery."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from rl_risk_replay.events import make_event
from sentinel_agent.agent import (
    Agent,
    AgentConfig,
    _split_bytes,
    iter_complete_lines,
    replay_file,
    webhook_sink,
)
from sentinel_agent.producer import SentinelProducer


class FakeClock:
    def __init__(self, start: float) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


def _hung_run_events(path: Path) -> int:
    producer = SentinelProducer(out_path=path, run_id="run-1", task="go2w_flat", seed="42",
                                planned_steps=100)
    producer.start()
    for step in range(1, 4):
        producer.sample(step, reward=0.5 + 0.1 * step, approx_kl=0.012)
    producer.close()  # no finish: the producer "hangs"
    return producer.written


class SilenceDetectionTests(unittest.TestCase):
    def _agent(self, clock: FakeClock) -> Agent:
        return Agent(AgentConfig(stale_after=60.0), clock=clock)

    def _feed(self, agent: Agent, path: Path) -> None:
        replay_file(agent, path)

    def test_silence_alerts_only_after_threshold(self) -> None:
        clock = FakeClock(1000.0)
        agent = self._agent(clock)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            _hung_run_events(path)
            self._feed(agent, path)
        self.assertEqual(agent.evaluate(), [])
        clock.advance(60.0)
        self.assertEqual(agent.evaluate(), [])  # exactly at threshold: not yet
        clock.advance(0.5)
        alerts = agent.evaluate()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["type"], "producer_silence")
        self.assertEqual(alerts[0]["run_id"], "run-1")
        self.assertIn("staleness trap", alerts[0]["message"])
        self.assertAlmostEqual(alerts[0]["fired_at"], clock.now, places=3)
        # Re-evaluating without new data must not duplicate the alert.
        self.assertEqual(agent.evaluate(), [])
        self.assertEqual(agent.counters()["silence_alerts"], 1)

    def test_alert_resets_on_new_data_and_can_realert(self) -> None:
        clock = FakeClock(1000.0)
        agent = self._agent(clock)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            _hung_run_events(path)
            self._feed(agent, path)
            clock.advance(61.0)
            self.assertEqual(len(agent.evaluate()), 1)
            # The producer resumes: the incident clears.
            resumed = make_event("resume-1", "run-1", "sample", clock(), clock(),
                                 {"step": 4, "reward": 0.9})
            self.assertEqual(agent.ingest(resumed.to_dict()), "ok")
            self.assertEqual(agent.evaluate(), [])
            clock.advance(61.0)
            self.assertEqual(len(agent.evaluate()), 1)
            self.assertEqual(agent.counters()["silence_alerts"], 2)

    def test_finished_run_never_alerts(self) -> None:
        clock = FakeClock(1000.0)
        agent = self._agent(clock)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            producer = SentinelProducer(out_path=path, run_id="run-1", task="t", seed="s",
                                        planned_steps=10)
            producer.start()
            producer.sample(1, reward=0.5)
            producer.finish("completed")
            producer.close()
            self._feed(agent, path)
        clock.advance(10_000.0)
        self.assertEqual(agent.evaluate(), [])

    def test_unstarted_run_never_alerts(self) -> None:
        clock = FakeClock(1000.0)
        agent = self._agent(clock)
        orphan = make_event("orphan-1", "run-x", "sample", 1.0, 1.0, {"step": 1, "reward": 0.5})
        self.assertEqual(agent.ingest(orphan.to_dict()), "ok")
        clock.advance(10_000.0)
        self.assertEqual(agent.evaluate(), [])

    def test_snapshot_reports_active_run_staleness(self) -> None:
        clock = FakeClock(1000.0)
        agent = self._agent(clock)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            _hung_run_events(path)
            self._feed(agent, path)
        state = agent.snapshot()["runs"]["run-1"]
        self.assertTrue(state["started"])
        self.assertFalse(state["finished"])
        self.assertEqual(state["last_step"], 3)
        self.assertAlmostEqual(state["staleness_seconds"], 0.0, places=6)


class IngestRobustnessTests(unittest.TestCase):
    def test_duplicate_event_id_is_idempotent(self) -> None:
        clock = FakeClock(1000.0)
        agent = Agent(AgentConfig(stale_after=60.0), clock=clock)
        event = make_event("e1", "run-1", "sample", 1.0, 1.0, {"step": 1, "reward": 0.5})
        self.assertEqual(agent.ingest(event.to_dict()), "ok")
        self.assertEqual(agent.ingest(event.to_dict()), "duplicate")
        self.assertEqual(agent.runs()["run-1"].events, 1)
        self.assertEqual(agent.counters()["duplicates"], 1)

    def test_invalid_events_are_counted_and_dropped(self) -> None:
        clock = FakeClock(1000.0)
        agent = Agent(AgentConfig(stale_after=60.0), clock=clock)
        self.assertEqual(agent.ingest_line("not json"), "invalid")
        self.assertEqual(agent.ingest(42), "invalid")
        self.assertEqual(agent.ingest({"schema_version": 1}), "invalid")
        self.assertEqual(agent.ingest({"schema_version": 2, "event_id": "e", "run_id": "r",
                                       "kind": "sample", "event_time": 1.0,
                                       "available_at": 0.5, "payload": {"step": 1}}), "invalid")
        self.assertEqual(agent.counters()["invalid"], 4)
        self.assertEqual(agent.runs(), {})

    def test_restart_replay_rebuilds_identical_state(self) -> None:
        clock = FakeClock(1000.0)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            _hung_run_events(path)
            first = Agent(AgentConfig(stale_after=60.0), clock=clock)
            replay_file(first, path)
            second = Agent(AgentConfig(stale_after=60.0), clock=clock)
            replay_file(second, path)  # crashed agent restarts by full replay
            self.assertEqual(first.snapshot(), second.snapshot())

    def test_partial_trailing_line_is_never_ingested_or_dropped(self) -> None:
        lines, remainder = iter_complete_lines('{"a": 1}\n{"b"')
        self.assertEqual(lines, ['{"a": 1}'])
        self.assertEqual(remainder, '{"b"')
        self.assertEqual(_split_bytes(b'{"a": 1}\n{"b"'), ([b'{"a": 1}'], b'{"b"'))

    def test_label_escapes_in_metrics_text(self) -> None:
        clock = FakeClock(1000.0)
        agent = Agent(AgentConfig(stale_after=60.0), clock=clock)
        event = make_event('e"1', 'run"\\x', "sample", 1.0, 1.0, {"step": 1, "reward": 0.5})
        agent.ingest(event.to_dict())
        text = agent.metrics_text()
        self.assertIn('run_id="run\\"\\\\x"', text)


class MetricsFormatTests(unittest.TestCase):
    def test_families_declared_once_and_absent_metrics_omitted(self) -> None:
        clock = FakeClock(1000.0)
        agent = Agent(AgentConfig(stale_after=60.0), clock=clock)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            _hung_run_events(path)
            replay_file(agent, path)
        text = agent.metrics_text()
        self.assertEqual(text.count("# TYPE sentinel_run_reward gauge"), 1)
        self.assertIn("sentinel_runs_active 1", text)
        self.assertIn('sentinel_run_reward{run_id="run-1",task="go2w_flat"} 0.8', text)
        self.assertNotIn("sentinel_run_value_loss", text)  # N/A stays N/A, never 0
        self.assertIn("sentinel_events_invalid_total 0", text)

    def test_finished_run_drops_out_of_active(self) -> None:
        clock = FakeClock(1000.0)
        agent = Agent(AgentConfig(stale_after=60.0), clock=clock)
        agent.ingest(make_event("s1", "r1", "start", 1.0, 1.0,
                                {"task": "t", "seed": "s", "planned_steps": 10}).to_dict())
        agent.ingest(make_event("f1", "r1", "finish", 2.0, 2.0,
                                {"status": "completed"}).to_dict())
        self.assertIn("sentinel_runs_active 0", agent.metrics_text())


class _CaptureHandler(BaseHTTPRequestHandler):
    body: bytes = b""

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        _CaptureHandler.body = self.rfile.read(length)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args: object) -> None:
        pass


class WebhookTests(unittest.TestCase):
    def test_alert_delivered_in_feishu_format(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _CaptureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/hook"
            clock = FakeClock(1000.0)
            agent = Agent(AgentConfig(stale_after=60.0), clock=clock,
                          alert_sink=webhook_sink(url))
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "events.jsonl"
                _hung_run_events(path)
                replay_file(agent, path)
            clock.advance(61.0)
            alerts = agent.evaluate()
            self.assertEqual(len(alerts), 1)
            payload = json.loads(_CaptureHandler.body.decode("utf-8"))
            self.assertEqual(payload["msg_type"], "text")
            self.assertIn("producer silence", payload["content"]["text"])
        finally:
            server.shutdown()
            server.server_close()

    def test_delivery_failure_is_counted_never_raised(self) -> None:
        clock = FakeClock(1000.0)
        agent = Agent(AgentConfig(stale_after=60.0), clock=clock,
                      alert_sink=webhook_sink("http://127.0.0.1:1/hook", timeout=2))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            _hung_run_events(path)
            replay_file(agent, path)
        clock.advance(61.0)
        self.assertEqual(len(agent.evaluate()), 1)  # alert still recorded locally
        self.assertEqual(agent.counters()["alert_failures"], 1)
        self.assertEqual(agent.counters()["silence_alerts"], 1)


class HttpEndpointTests(unittest.TestCase):
    def test_ingest_metrics_state_endpoints(self) -> None:
        clock = FakeClock(1000.0)
        agent = Agent(AgentConfig(stale_after=60.0), clock=clock)
        server = _start(agent)
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "events.jsonl"
                _hung_run_events(path)
                body = path.read_bytes()
            with urllib.request.urlopen(urllib.request.Request(
                    f"{base}/events", data=body, method="POST",
                    headers={"Content-Type": "application/x-ndjson"})) as response:
                first = json.loads(response.read().decode("utf-8"))
            self.assertEqual(first, {"status": "accepted", "ok": 4, "duplicate": 0,
                                     "invalid": 0})
            # Replay the same bytes: at-least-once delivery must be idempotent.
            with urllib.request.urlopen(urllib.request.Request(
                    f"{base}/events", data=body, method="POST",
                    headers={"Content-Type": "application/x-ndjson"})) as response:
                second = json.loads(response.read().decode("utf-8"))
            self.assertEqual(second["duplicate"], 4)
            self.assertEqual(second["ok"], 0)
            with urllib.request.urlopen(f"{base}/metrics") as response:
                self.assertEqual(response.status, 200)
            with urllib.request.urlopen(f"{base}/state") as response:
                state = json.loads(response.read().decode("utf-8"))
            self.assertEqual(state["runs"]["run-1"]["last_step"], 3)
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(f"{base}/nope")
            self.assertEqual(caught.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()


def _start(agent: Agent) -> ThreadingHTTPServer:
    from sentinel_agent.agent import AgentHTTPServer

    server = AgentHTTPServer(("127.0.0.1", 0), agent)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":
    unittest.main()
