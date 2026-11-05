"""Reproducible release experiment: future mutations, live RL trials and bundle verification."""
from __future__ import annotations

import argparse
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
        # The v1 negative control needs backtest_rules, tests/ and data/datasets.
        # All three moved to the archive/v1 branch in v0.3.0; a silent skip would
        # make this flag look like a passing check when it verified nothing.
        raise SystemExit(
            "--include-legacy requires the archived v1 modules and data/datasets "
            "(archive/v1 branch, v0.3.0). Check out that branch to run the "
            "historical negative control; the default v0.2 validation does not need it."
        )
    results["controlled_experiment"] = experiment(out / "controlled", seeds=6)
    verify_bundle(out / "controlled")
    (out / "validation.json").write_text(json.dumps(results, indent=2, ensure_ascii=False, allow_nan=False)+"\n", encoding="utf-8")
    installed = subprocess.run([sys.executable, "-m", "pip", "freeze"], capture_output=True, text=True, check=True)
    (out / "environment.lock.txt").write_text(installed.stdout, encoding="utf-8")
    print(canonical(results))


if __name__ == "__main__":
    main()
