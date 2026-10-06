"""Reproducible release experiment: mutations, live RL trials and legacy inventory."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl_risk_replay.controlled import experiment
from rl_risk_replay.engine import RulePolicy, Timeline
from rl_risk_replay.events import canonical, make_event
from rl_risk_replay.legacy_audit import audit_legacy
from rl_risk_replay.reporting import runtime_info
from rl_risk_replay.storage import verify_bundle
from tests_v2.test_reliability import run_events


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--include-legacy", action="store_true")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    source = run_events("past", 0) + run_events()
    base = Timeline(source).as_of("target", 150)
    policy = RulePolicy()
    rng = random.Random(74821)
    for trial in range(200):
        changed = list(source)
        changed[-3] = replace(changed[-3], fields=(("reward", rng.uniform(-500, 500)), ("step", 80)))
        changed[-1] = make_event("target-label", "target", "label", 190, 191,
                                {"verdict": rng.choice(["pass", "fail", "unknown"]), "source": "evaluation"})
        changed.extend(run_events(f"future-{trial}", 1000 + trial))
        changed.append(make_event(f"revision-{trial}", "past", "label", 90, 500,
                                  {"verdict": rng.choice(["pass", "fail"]), "source": "manual"}))
        rng.shuffle(changed)
        view = Timeline(tuple(changed)).as_of("target", 150)
        assert view.sha256 == base.sha256 and policy(view) == policy(base)
    changed = list(source)
    target_sample = next(i for i, e in enumerate(changed) if e.event_id == "target-s3")
    changed[target_sample] = replace(changed[target_sample], fields=(("reward", 99.0), ("step", 50)))
    negative = Timeline(tuple(changed)).as_of("target", 150)
    assert negative.sha256 != base.sha256 and policy(negative) != policy(base)
    results = {"runtime": runtime_info(), "future_mutation_cases": 200,
               "future_mutation_failures": 0, "visible_mutation_negative_control": "changed_as_expected"}
    if args.include_legacy:
        from tests.test_backtest_engine import make_tables
        import backtest_rules
        legacy_view = backtest_rules.online_view(make_tables(), "a", "s0", 50)
        visible_seeds = set(legacy_view["runs"]["seed"])
        target_verdict = legacy_view["runs"].loc[legacy_view["runs"]["seed"] == "s0", "verdict"].iloc[0]
        assert "s2" in visible_seeds and target_verdict == "fail"
        results["legacy_negative_control"] = {"future_run_visible": True, "target_final_verdict_visible": True,
                                               "scope": "synthetic v1 counterexample, not a training outcome"}
        dataset = ROOT / "data" / "datasets"
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in dataset.glob("*.csv")}
        results["historical_inventory"] = audit_legacy(dataset)
        after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in dataset.glob("*.csv")}
        assert before == after
        results["historical_csv_hashes_unchanged"] = True
    results["controlled_experiment"] = experiment(out / "controlled", seeds=6)
    verify_bundle(out / "controlled")
    (out / "validation.json").write_text(json.dumps(results, indent=2, ensure_ascii=False, allow_nan=False)+"\n", encoding="utf-8")
    installed = subprocess.run([sys.executable, "-m", "pip", "freeze"], capture_output=True, text=True, check=True)
    (out / "environment.lock.txt").write_text(installed.stdout, encoding="utf-8")
    print(canonical(results))


if __name__ == "__main__":
    main()
