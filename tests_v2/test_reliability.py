"""Normal, boundary, adversarial temporal, transaction and publication tests."""
from __future__ import annotations

import copy
import json
import random
import sqlite3
import tempfile
import threading
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest.mock import patch

from rl_risk_replay.__main__ import main
from rl_risk_replay.controlled import evaluate, train_trial, transition
from rl_risk_replay.engine import (ConstantPolicy, Decision, Row, RuleConfig, RulePolicy,
                                  Timeline, View)
from rl_risk_replay.events import (ContractError, Event, canonical, make_event, parse_json,
                                  validate_events)
from rl_risk_replay.legacy_audit import audit_legacy
from rl_risk_replay.metrics import summarize
from rl_risk_replay.reporting import render_html
from rl_risk_replay.storage import (EventStore, decode_events, encode_events,
                                   publish_bundle, verify_bundle)


def run_events(run_id="target", start=100.0, verdict="fail", label_delay=0.0):
    return (
        make_event(run_id + "-start", run_id, "start", start, start,
                   {"task": "grid", "seed": "0", "planned_steps": 100}),
        make_event(run_id + "-s1", run_id, "sample", start+10, start+10, {"step": 10, "reward": 50.0}),
        make_event(run_id + "-s2", run_id, "sample", start+30, start+30, {"step": 30, "reward": 100.0}),
        make_event(run_id + "-s3", run_id, "sample", start+50, start+50, {"step": 50, "reward": 10.0}),
        make_event(run_id + "-s4", run_id, "sample", start+80, start+80, {"step": 80, "reward": 90.0}),
        make_event(run_id + "-finish", run_id, "finish", start+90, start+90, {"status": "completed"}),
        make_event(run_id + "-label", run_id, "label", start+90, start+91+label_delay,
                   {"verdict": verdict, "source": "evaluation"}),
    )


class EventContractTest(unittest.TestCase):
    def test_roundtrip_and_order_independence(self):
        source = run_events()
        encoded = encode_events(source, "synthetic")
        decoded, origin = decode_events(encoded)
        self.assertEqual(origin, "synthetic")
        self.assertEqual(encode_events(tuple(reversed(decoded)), origin), encoded)

    def test_missing_available_at_is_rejected(self):
        raw = run_events()[0].to_dict()
        del raw["available_at"]
        with self.assertRaises(ContractError):
            Event.from_dict(raw)

    def test_future_metadata_payload_is_rejected(self):
        for index in (0, 1):
            for forbidden in ("verdict", "duration_seconds", "final_reward", "unrelated_run"):
                with self.subTest(index=index, forbidden=forbidden):
                    raw = run_events()[index].to_dict()
                    raw["payload"][forbidden] = "fail"
                    with self.assertRaises(ContractError):
                        Event.from_dict(raw)

    def test_time_and_numeric_validation(self):
        for value in (float("nan"), float("inf"), -1, True, "100", 10**1000):
            raw = run_events()[0].to_dict()
            raw["event_time"] = value
            with self.subTest(value=str(value)[:30]), self.assertRaises(ContractError):
                Event.from_dict(raw)
        raw = run_events()[0].to_dict()
        raw["available_at"] = 99
        with self.assertRaises(ContractError):
            Event.from_dict(raw)

    def test_duplicate_json_keys_and_nan_rejected(self):
        for data in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}'):
            with self.assertRaises(ContractError):
                parse_json(data)

    def test_unknown_fields_and_blank_lines_rejected(self):
        source = encode_events(run_events(), "synthetic")
        with self.assertRaises(ContractError):
            decode_events(source + "\n")
        with self.assertRaises(ContractError):
            decode_events('{"schema_version":2,"origin":"synthetic","other":1}\n')

    def test_ids_must_be_unique(self):
        events = run_events()
        with self.assertRaises(ContractError):
            validate_events(events + (events[0],))

    def test_orphans_and_multiple_starts_rejected(self):
        events = run_events()
        with self.assertRaises(ContractError):
            validate_events(events[1:])
        with self.assertRaises(ContractError):
            validate_events(events + (replace(events[0], event_id="another-start"),))

    def test_steps_cannot_restart_in_same_run(self):
        events = list(run_events())
        events[3] = replace(events[3], fields=(("reward", 10.0), ("step", 10)))
        with self.assertRaises(ContractError):
            validate_events(tuple(events))

    def test_finish_label_order(self):
        events = list(run_events())
        events[-1] = replace(events[-1], available_at=180.0, event_time=180.0)
        with self.assertRaises(ContractError):
            validate_events(tuple(events))

    def test_ambiguous_label_revision_rejected(self):
        source = run_events()
        another = make_event("conflict", "target", "label", 190, 191,
                             {"verdict": "pass", "source": "evaluation"})
        with self.assertRaises(ContractError):
            validate_events(source + (another,))

    def test_late_arriving_pre_finish_sample_is_valid(self):
        source = list(run_events())
        source[4] = replace(source[4], available_at=300.0)
        validate_events(tuple(source))
        source[4] = replace(source[4], event_time=200.0)
        with self.assertRaises(ContractError):
            validate_events(tuple(source))

    def test_event_payload_is_immutable(self):
        e = run_events()[0]
        e.payload()["task"] = "mutated"
        self.assertEqual(e.payload()["task"], "grid")
        with self.assertRaises(FrozenInstanceError):
            e.run_id = "other"

    def test_memory_range(self):
        with self.assertRaises(ContractError):
            make_event("x", "r", "sample", 0, 0, {"step": 0, "memory_percent": 101})


class TemporalTest(unittest.TestCase):
    def test_only_target_observed_prefix_exposed(self):
        source = run_events("past", 0) + run_events() + run_events("future", 1000)
        view = Timeline(source).as_of("target", 150)
        self.assertEqual(len(view.samples), 3)
        self.assertEqual([r.start.run_id for r in view.history], ["past"])
        self.assertNotIn("future", canonical(view.to_dict()))
        self.assertNotIn('"verdict"', canonical(View(150, view.start, view.samples, ()).to_dict()))

    def test_label_arrival_not_finish_controls_membership(self):
        source = run_events("past", 0, label_delay=200) + run_events()
        self.assertFalse(Timeline(source).as_of("target", 150).history)

    def test_historical_membership_frozen_at_target_start(self):
        source = run_events("past", 0, label_delay=20) + run_events()
        self.assertFalse(Timeline(source).as_of("target", 180).history)

    def test_label_exactly_at_start_is_excluded(self):
        source = run_events("past", 0, label_delay=9) + run_events()
        self.assertFalse(Timeline(source).as_of("target", 150).history)

    def test_delayed_low_step_telemetry_is_not_visible(self):
        source = list(run_events())
        source[1] = replace(source[1], available_at=170.0)
        self.assertEqual(len(Timeline(tuple(source)).as_of("target", 150).samples), 2)
        row = Timeline(tuple(source)).replay(RulePolicy())[0]
        self.assertEqual(row.status, "abstained")

    def test_delayed_historical_telemetry_excluded(self):
        prior = list(run_events("past", 0))
        prior[2] = replace(prior[2], available_at=300)
        history = Timeline(tuple(prior) + run_events()).as_of("target", 150).history
        self.assertEqual(len(history[0].samples), 3)

    def test_manual_label_override_is_as_of(self):
        source = run_events("past", 0, verdict="fail") + (
            make_event("manual", "past", "label", 90, 200, {"source": "manual", "verdict": "pass"}),)
        tl = Timeline(source + run_events())
        self.assertEqual(tl.label("past", 100).payload()["verdict"], "fail")
        self.assertEqual(tl.label("past", 210).payload()["verdict"], "pass")
        self.assertEqual(tl.as_of("target", 250).history[0].label.payload()["verdict"], "fail")

    def test_target_start_must_be_observable(self):
        with self.assertRaises(ContractError):
            Timeline(run_events()).as_of("target", 99)

    def test_finish_already_observable_skips_decision(self):
        source = list(run_events())
        source[3] = replace(source[3], available_at=300)
        source[4] = replace(source[4], available_at=310)
        row = Timeline(tuple(source)).replay(RulePolicy())[0]
        self.assertEqual(row.status, "skipped")
        self.assertIn("finish already", row.reason)

    def test_decision_uses_observation_time_not_step_time(self):
        source = list(run_events())
        source[3] = replace(source[3], available_at=160)
        row = Timeline(tuple(source)).replay(RulePolicy())[0]
        self.assertEqual(row.view.cutoff, 160)

    def test_invalid_progress(self):
        for p in (0, -0.1, 1.1, float("nan"), float("inf"), True):
            with self.subTest(progress=p), self.assertRaises(ContractError):
                Timeline(run_events()).replay(RulePolicy(), p)

    def test_unavailable_outcome_is_unknown(self):
        row = Timeline(run_events()).replay(RulePolicy(), evaluation_at=180)[0]
        self.assertEqual(row.verdict, "unknown")
        self.assertIsNone(row.finish_time)

    def test_evaluation_horizon_hides_future_runs(self):
        rows = Timeline(run_events() + run_events("future", 1000)).replay(RulePolicy(), evaluation_at=200)
        self.assertEqual(len(rows), 1)

    def test_future_mutation_200_cases(self):
        source = run_events("past", 0) + run_events()
        initial = Timeline(source).as_of("target", 150)
        policy = RulePolicy()
        rng = random.Random(74821)
        for trial in range(200):
            changed = list(source)
            # All changed fields belong to events not available at cutoff 150.
            changed[-3] = replace(changed[-3], fields=(("reward", rng.uniform(-500, 500)), ("step", 80)))
            changed[-1] = make_event("target-label", "target", "label", 190, 191,
                                    {"verdict": rng.choice(["pass", "fail", "unknown"]), "source": "evaluation"})
            changed.extend(run_events(f"future-{trial}", 1000+trial))
            changed.append(make_event(f"revision-{trial}", "past", "label", 90, 500,
                                      {"verdict": rng.choice(["pass", "fail"]), "source": "manual"}))
            rng.shuffle(changed)
            view = Timeline(tuple(changed)).as_of("target", 150)
            self.assertEqual(view.sha256, initial.sha256)
            self.assertEqual(policy(view), policy(initial))

    def test_visible_mutation_negative_control_changes_hash_and_decision(self):
        source = list(run_events())
        before = Timeline(tuple(source)).as_of("target", 150)
        source[3] = replace(source[3], fields=(("reward", 99.0), ("step", 50)))
        after = Timeline(tuple(source)).as_of("target", 150)
        self.assertNotEqual(before.sha256, after.sha256)
        self.assertNotEqual(RulePolicy()(before), RulePolicy()(after))

    def test_outcome_mutation_changes_metrics_not_decision(self):
        before = Timeline(run_events()).replay(RulePolicy())
        after = Timeline(run_events(verdict="pass")).replay(RulePolicy())
        self.assertEqual(before[0].view.sha256, after[0].view.sha256)
        self.assertEqual(before[0].decision, after[0].decision)
        self.assertNotEqual(summarize(before, "synthetic")["false_stops"], summarize(after, "synthetic")["false_stops"])


class MetricsTest(unittest.TestCase):
    def make_rows(self):
        rows = [Row(f"pass-{i}", "p", 0.5, "decided", "fixture", None,
                    Decision("stop" if i == 0 else "continue", 0, ("fixture",)), "pass", None)
                for i in range(100)]
        rows.append(Row("fail", "p", 0.5, "decided", "fixture", None,
                        Decision("stop", 0, ("fixture",)), "fail", None))
        return tuple(rows)

    def test_explicit_denominators(self):
        s = summarize(self.make_rows(), "synthetic")
        self.assertEqual(s["pass_kill_rate"], 0.01)
        self.assertEqual(s["false_stop_share"], 0.5)
        self.assertEqual(s["stop_precision"], 0.5)
        self.assertEqual(s["fail_recall"], 1.0)

    def test_no_pass_is_null_not_zero(self):
        rows = Timeline(run_events()).replay(ConstantPolicy(True))
        self.assertIsNone(summarize(rows, "synthetic")["pass_kill_rate"])

    def test_no_stop_precision_null(self):
        rows = Timeline(run_events()).replay(ConstantPolicy(False))
        self.assertIsNone(summarize(rows, "synthetic")["stop_precision"])

    def test_empty_metrics_are_valid_json(self):
        text = canonical(summarize((), "synthetic"))
        self.assertNotIn("NaN", text)
        self.assertEqual(json.loads(text)["n_total"], 0)

    def test_abstentions_do_not_inflate_failure_recall(self):
        rows = self.make_rows()
        rows = rows[:-1] + (replace(rows[-1], status="abstained", decision=Decision("abstain", 0, ("missing",))),)
        s = summarize(rows, "synthetic")
        self.assertEqual(s["fail_recall"], 0)
        self.assertLess(s["decision_coverage"], 1)

    def test_synthetic_and_controlled_cannot_certify_operational_readiness(self):
        source = tuple(e for i in range(20) for e in run_events(f"r{i}", i*100, "pass" if i<6 else "fail"))
        rows = Timeline(source).replay(ConstantPolicy())
        for origin in ("synthetic", "controlled"):
            s = summarize(rows, origin)
            self.assertTrue(s["sample_floor_met"])
            self.assertEqual(s["readiness"], "blocked")

    def test_cannot_pool_points_or_duplicate_runs(self):
        rows = self.make_rows()
        with self.assertRaises(ContractError):
            summarize(rows + (replace(rows[-1], run_id="another", progress=0.7),), "synthetic")
        with self.assertRaises(ContractError):
            summarize(rows + (rows[0],), "synthetic")

    def test_remaining_wall_time_not_realized_savings(self):
        s = summarize(Timeline(run_events()).replay(RulePolicy()), "synthetic")
        self.assertEqual(s["counterfactual_failed_run_remaining_wall_seconds"], 40)
        self.assertIsNone(s["realized_compute_savings"])


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = EventStore.create(self.root / "events.sqlite", "synthetic")

    def test_batch_and_idempotence(self):
        self.assertEqual(self.store.append(run_events()), 7)
        self.assertEqual(self.store.append(run_events()), 0)
        self.assertEqual(len(self.store.read()[0]), 7)

    def test_conflicting_event_rolls_back_entire_batch(self):
        source = run_events()
        self.store.append(source)
        with self.assertRaises(ContractError):
            self.store.append(run_events("new", 1000) + (replace(source[-1], available_at=220),))
        self.assertEqual(len(self.store.read()[0]), 7)

    def test_sql_fault_mid_batch_rolls_back(self):
        with sqlite3.connect(str(self.store.path)) as con:
            con.execute("CREATE TRIGGER fault BEFORE INSERT ON events WHEN NEW.event_id='target-s2' BEGIN SELECT RAISE(ABORT,'injected write fault'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.append(run_events())
        self.assertEqual(self.store.read()[0], ())

    def test_invalid_batch_cannot_partially_insert(self):
        with self.assertRaises(ContractError):
            self.store.append(run_events() + run_events("broken", 1000)[1:])
        self.assertEqual(self.store.read()[0], ())

    def test_read_does_not_create_missing_db(self):
        with self.assertRaises(FileNotFoundError):
            EventStore(self.root / "missing")
        self.assertFalse((self.root / "missing").exists())

    def test_create_does_not_overwrite(self):
        before = self.store.path.read_bytes()
        with self.assertRaises(FileExistsError):
            EventStore.create(self.store.path)
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_sql_updates_and_deletes_rejected(self):
        self.store.append(run_events())
        with sqlite3.connect(str(self.store.path)) as con:
            for sql in ("DELETE FROM events", "UPDATE events SET body='{}'"):
                with self.assertRaises(sqlite3.IntegrityError):
                    con.execute(sql)

    def test_origin_mismatch_rejected(self):
        with self.assertRaises(ContractError):
            self.store.append(run_events(), expected_origin="observed")

    def test_concurrent_batches_stay_consistent(self):
        errors = []
        def append(run):
            try:
                self.store.append(run_events(run, 1000))
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=append, args=(f"r{i}",)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertFalse(errors)
        self.assertEqual(len(self.store.read()[0]), 28)

    def test_live_recorder_stamps_and_rejects_backwards_clock(self):
        store = EventStore.create(self.root / "live.sqlite")
        e = store.record("r", "start", {"task":"t", "seed":"0", "planned_steps":100}, clock=lambda:100)
        self.assertEqual(e.available_at, 100)
        with self.assertRaises(ContractError):
            store.record("r", "sample", {"step":1, "reward":0}, clock=lambda:99)


class BundleAndCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_bundle_checksums_and_tamper_detection(self):
        out = publish_bundle(self.root / "bundle", {"results.json":"{}\n"})
        self.assertEqual(verify_bundle(out)["status"], "complete")
        (out / "results.json").write_text("tampered")
        with self.assertRaises(ContractError):
            verify_bundle(out)

    def test_extra_member_and_symlink_rejected(self):
        out = publish_bundle(self.root / "bundle", {"data":"x"})
        (out / "extra").write_text("x")
        with self.assertRaises(ContractError):
            verify_bundle(out)
        (out / "extra").unlink()
        (out / "data").unlink()
        (out / "data").symlink_to(self.root / "elsewhere")
        with self.assertRaises(ContractError):
            verify_bundle(out)

    def test_no_overwrite_and_lock_conflict(self):
        out = publish_bundle(self.root / "bundle", {"data":"x"})
        with self.assertRaises(FileExistsError):
            publish_bundle(out, {"data":"y"})
        self.assertEqual((out / "data").read_text(), "x")
        (self.root / ".busy.lock").write_text("lock")
        with self.assertRaises(FileExistsError):
            publish_bundle(self.root / "busy", {"data":"x"})

    def test_fsync_fault_leaves_no_complete_output(self):
        with patch("rl_risk_replay.storage.os.fsync", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                publish_bundle(self.root / "failed", {"data":"x"})
        self.assertFalse((self.root / "failed").exists())
        self.assertFalse(list(self.root.glob(".failed*")))

    def test_path_traversal_rejected(self):
        for name in ("../outside", "/tmp/outside", "a/b", "a\\b", "manifest.json"):
            with self.subTest(name=name), self.assertRaises(ContractError):
                publish_bundle(self.root / "bad", {name:"x"})

    def test_html_escapes_experiment_strings(self):
        rendered = render_html({"title":"<script>alert(1)</script>", "message":"</pre><script>x</script>"})
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)

    def test_cli_replay_and_verify(self):
        events = self.root / "input.jsonl"
        events.write_text(encode_events(run_events(), "synthetic"))
        out = self.root / "out"
        self.assertEqual(main(["replay", "--events", str(events), "--out", str(out)]), 0)
        self.assertEqual(main(["verify", "--bundle", str(out)]), 0)
        result = json.loads((out/"results.json").read_text())
        self.assertEqual(len(result["summaries"]), 9)
        self.assertEqual(main(["replay", "--events", str(events), "--out", str(out)]), 2)

    def test_cli_invalid_and_missing_inputs_fail_without_output(self):
        for progress in ("nan", "0", "1.1", "0.5,0.5", ""):
            self.assertEqual(main(["replay", "--events", str(self.root/"missing"),
                                   "--out", str(self.root/"out"), "--progress", progress]), 2)
        self.assertFalse((self.root/"out").exists())

    def test_legacy_audit_never_fabricates_timestamps(self):
        source = self.root / "data"
        source.mkdir()
        (source/"runs.csv").write_text("task,seed,verdict\na,s,pass\n")
        before = (source/"runs.csv").read_bytes()
        report = audit_legacy(source)
        self.assertEqual(report["strict_replay_eligible_runs"], 0)
        self.assertEqual(report["declared_outcomes"], {"pass":1})
        self.assertEqual((source/"runs.csv").read_bytes(), before)


class ControlledRlTest(unittest.TestCase):
    def test_grid_obstacles_and_terminal(self):
        self.assertEqual(transition(1,1)[0], 1)
        self.assertEqual(transition(0,2)[0], 0)
        self.assertEqual(transition(14,0), (15,1.0,True))
        self.assertEqual(transition(15,0), (15,0.0,True))

    def test_real_training_and_disabled_learning_are_distinct(self):
        samples = []
        q, good = train_trial(0, "none", lambda s,r: samples.append((s,r)))
        zero, bad = train_trial(0, "learning_disabled", lambda s,r: None)
        self.assertEqual(len(samples), 16)
        self.assertNotEqual(q, zero)
        self.assertGreaterEqual(sum(r.success for r in good)/len(good), 0.9)
        self.assertLess(sum(r.success for r in bad)/len(bad), 0.9)

    def test_repeatable_training_not_repeatable_clock(self):
        a = train_trial(7, "late_reset", lambda s,r: None)
        b = train_trial(7, "late_reset", lambda s,r: None)
        self.assertEqual(a, b)

    def test_rule_config_and_negative_reward_abstention(self):
        with self.assertRaises(ContractError):
            RuleConfig(kl_watch=1, kl_tune=0.1)
        with self.assertRaises(ContractError):
            RuleConfig(min_reward_points=True)
        source = list(run_events())
        for index in (1,2,3):
            source[index] = replace(source[index], fields=(("reward", -100.0), ("step", source[index].payload()["step"])))
        decision = RulePolicy()(Timeline(tuple(source)).as_of("target",150))
        self.assertEqual(decision.action,"abstain")


if __name__ == "__main__":
    unittest.main()
