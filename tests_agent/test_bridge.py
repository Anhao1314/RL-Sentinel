"""Bridge: raw producer JSONL -> versioned stream -> frozen-core analysis."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rl_risk_replay.events import ContractError, canonical
from rl_risk_replay.storage import decode_events
from sentinel_agent.bridge import analyze, export_stream
from sentinel_agent.producer import SentinelProducer


class _Clock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


def _write_run(path: Path, *, planned: int = 100, samples: int = 50,
               run_id: str = "run-1") -> int:
    clock = _Clock()
    producer = SentinelProducer(out_path=path, run_id=run_id, task="t", seed="42",
                                planned_steps=planned, clock=clock, fsync_every=64)
    try:
        producer.start()
        for step in range(1, samples + 1):
            producer.sample(step, reward=0.5 + 0.01 * step, approx_kl=0.012)
            clock.advance(1)
        producer.finish("completed")
    finally:
        producer.close()
    return producer.written


class ExportTests(unittest.TestCase):
    def test_stream_has_origin_header_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "events.jsonl"
            _write_run(raw)
            out = Path(tmp) / "stream.jsonl"
            stats = export_stream([raw], "observed", out)
            self.assertEqual(stats["events"], 52)  # start + 50 samples + finish
            self.assertEqual(stats["duplicates"], 0)
            lines = out.read_text(encoding="utf-8").splitlines()
            header = json.loads(lines[0])
            self.assertEqual(header, {"schema_version": 2, "origin": "observed"})
            events, origin = decode_events(out.read_text(encoding="utf-8"))
            self.assertEqual(origin, "observed")
            self.assertEqual(len(events), 52)

    def test_exact_duplicates_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "events.jsonl"
            _write_run(raw)
            copy = Path(tmp) / "copy.jsonl"
            copy.write_text(raw.read_text(encoding="utf-8"), encoding="utf-8")
            out = Path(tmp) / "stream.jsonl"
            stats = export_stream([raw, copy], "observed", out)
            self.assertEqual(stats["duplicates"], 52)
            self.assertEqual(stats["events"], 52)

    def test_conflicting_duplicate_fails_loudly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "events.jsonl"
            _write_run(raw)
            tampered = Path(tmp) / "tampered.jsonl"
            lines = raw.read_text(encoding="utf-8").splitlines()
            event = json.loads(lines[1])
            event["payload"]["reward"] = 999.0
            tampered.write_text("\n".join(lines[:1] + [canonical(event)]) + "\n",
                                encoding="utf-8")
            with self.assertRaises(ContractError) as caught:
                export_stream([raw, tampered], "observed", Path(tmp) / "stream.jsonl")
            self.assertIn("conflicting immutable event_id", str(caught.exception))

    def test_trailing_partial_line_from_crashed_writer_is_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "events.jsonl"
            _write_run(raw)
            with raw.open("a", encoding="utf-8") as handle:
                handle.write('{"schema_version": 2, "event_id": "cut')  # crashed mid-write
            out = Path(tmp) / "stream.jsonl"
            stats = export_stream([raw], "observed", out)
            self.assertEqual(stats["dropped_trailing_partial"], 1)
            decode_events(out.read_text(encoding="utf-8"))

    def test_invalid_complete_line_fails_with_location(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "events.jsonl"
            _write_run(raw)
            lines = raw.read_text(encoding="utf-8").splitlines()
            lines.insert(1, '{"not": "an event"}')
            broken = Path(tmp) / "broken.jsonl"
            broken.write_text("\n".join(lines) + "\n", encoding="utf-8")
            with self.assertRaises(ContractError) as caught:
                export_stream([broken], "observed", Path(tmp) / "stream.jsonl")
            self.assertIn("broken.jsonl:2", str(caught.exception))

    def test_origin_is_mandatory_and_validated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ContractError):
            export_stream([Path(tmp) / "x.jsonl"], "whatever", Path(tmp) / "s.jsonl")


class AnalyzeTests(unittest.TestCase):
    def test_one_command_from_events_to_replay_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "events.jsonl"
            _write_run(raw, planned=100, samples=50)
            out = Path(tmp) / "replay-bundle"
            result = analyze([raw], "synthetic", out)
            self.assertEqual(result["origin"], "synthetic")
            self.assertEqual(result["decisions"], 9)  # 3 policies x 3 checkpoints
            self.assertEqual(result["summaries"][0]["readiness"], "blocked")
            self.assertTrue((out / "results.json").exists())
            self.assertTrue((Path(str(out) + ".stream.jsonl")).exists())
            document = json.loads((out / "results.json").read_text(encoding="utf-8"))
            rules = [d for d in document["decisions"] if d["policy"] == "rules-v2"
                     and d["decision"] is not None]
            self.assertTrue(all(d["decision"]["action"] == "continue" for d in rules))

    def test_replay_refuses_existing_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / "events.jsonl"
            _write_run(raw)
            out = Path(tmp) / "replay-bundle"
            analyze([raw], "synthetic", out)
            with self.assertRaises(ContractError):
                analyze([raw], "synthetic", out)


if __name__ == "__main__":
    unittest.main()
