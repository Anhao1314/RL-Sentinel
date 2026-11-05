"""Portable time-isolation contract, exercised against a correct and a leaky implementation.

The leaky implementation is a deliberate negative control: if the contract
cannot detect it, the contract itself is worthless.
"""
from __future__ import annotations

import unittest

from contracts.time_isolation import check_label_isolation, check_no_future_leak
from rl_risk_replay.engine import Timeline
from rl_risk_replay.events import make_event


def _events():
    return (
        make_event("start-1", "r1", "start", 10.0, 10.0,
                   {"task": "fixture", "seed": "0", "planned_steps": 1000}),
        make_event("sample-1", "r1", "sample", 20.0, 20.0,
                   {"step": 100, "reward": 1.0}),
        make_event("sample-2", "r1", "sample", 24.0, 24.0,
                   {"step": 200, "reward": 1.2}),
        make_event("finish-1", "r1", "finish", 25.0, 25.0,
                   {"status": "completed"}),
    )


def _future_sample():
    # event_time is inside the cutoff, but availability is in the future.
    return make_event("sample-late", "r1", "sample", 22.0, 999.0,
                      {"step": 150, "reward": 99.0})


def _label(verdict: str):
    return make_event(f"label-{verdict}", "r1", "label", 26.0, 999.0,
                      {"verdict": verdict, "source": "manual"})


def _correct_visible(events, cutoff):
    timeline = Timeline(tuple(events))
    return tuple(e.event_id for e in timeline.visible("r1", cutoff))


def _leaky_visible(events, cutoff):
    # Ignores available_at entirely: the classic look-ahead bug.
    return tuple(e.event_id for e in events if e.event_time <= cutoff)


def _leaky_label_visible(events, cutoff):
    labels = [e for e in events if e.kind == "label"]
    return _leaky_visible(events, cutoff) + (
        (labels[-1].payload()["verdict"],) if labels else ()
    )


class TimeIsolationContractTest(unittest.TestCase):
    def test_strict_replay_ignores_late_arriving_sample(self):
        report = check_no_future_leak(_correct_visible, _events(), 25.0,
                                      [_future_sample()])
        self.assertTrue(report["ok"], report)

    def test_negative_control_contract_detects_leak(self):
        report = check_no_future_leak(_leaky_visible, _events(), 25.0,
                                      [_future_sample()])
        self.assertFalse(report["ok"])

    def test_strict_replay_ignores_contradictory_future_labels(self):
        report = check_label_isolation(_correct_visible, _events(), 25.0,
                                       _label("pass"), _label("fail"))
        self.assertTrue(report["ok"], report)

    def test_negative_control_contract_detects_label_leak(self):
        report = check_label_isolation(_leaky_label_visible, _events(), 25.0,
                                       _label("pass"), _label("fail"))
        self.assertFalse(report["ok"])


if __name__ == "__main__":
    unittest.main()
