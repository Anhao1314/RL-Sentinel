"""Offline cohort backtest for early-stop research policies.

This is the research layer, not the strict event replay.  Factors are built
only from observations up to each checkpoint; labels are used only for scoring.
Legacy runs are marked offline and must not be presented as strict as-of data.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import pandas as pd

POLICIES = ("rules-v2", "always-continue", "always-stop")
READINESS_PASS_FLOOR = 5
READINESS_FAIL_FLOOR = 10


@dataclass(frozen=True)
class RuleConfig:
    drawdown_stop: float = 0.30
    kl_stop: float = 0.10
    kl_streak_stop: int = 3
    progress_stall_slope: float = 0.0
    min_samples: int = 3

    def to_dict(self) -> dict[str, float | int]:
        return {
            "drawdown_stop": self.drawdown_stop,
            "kl_stop": self.kl_stop,
            "kl_streak_stop": self.kl_streak_stop,
            "progress_stall_slope": self.progress_stall_slope,
            "min_samples": self.min_samples,
        }


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if pd.notna(number) else None


def _rules_decision(row: pd.Series, config: RuleConfig) -> tuple[str, str]:
    samples = _finite(row.get("sample_count"))
    if samples is None or samples < config.min_samples:
        return "abstain", "insufficient samples"
    drawdown = _finite(row.get("reward_drawdown"))
    kl_last = _finite(row.get("kl_last"))
    kl_streak = _finite(row.get("kl_divergent_streak"))
    progress_slope = _finite(row.get("progress_slope"))
    coverage = _finite(row.get("step_coverage"))

    failed = False
    reasons: list[str] = []
    if drawdown is not None and drawdown >= config.drawdown_stop:
        failed = True
        reasons.append(f"drawdown {drawdown:.3f} >= {config.drawdown_stop:.2f}")
    if kl_last is not None and kl_last >= config.kl_stop:
        failed = True
        reasons.append(f"kl {kl_last:.3f} >= {config.kl_stop:.2f}")
    if kl_streak is not None and kl_streak >= config.kl_streak_stop:
        failed = True
        reasons.append(f"kl streak {int(kl_streak)} >= {config.kl_streak_stop}")
    if (
        progress_slope is not None
        and coverage is not None
        and coverage >= 0.3
        and progress_slope <= config.progress_stall_slope
    ):
        failed = True
        reasons.append(f"progress slope {progress_slope:.3f} <= 0")
    if failed:
        return "stop", "; ".join(reasons)
    if drawdown is None and kl_last is None and progress_slope is None:
        return "abstain", "no risk factor available"
    return "continue", "no stop condition"


def _decision(row: pd.Series, policy: str, config: RuleConfig) -> tuple[str, str]:
    if policy == "always-continue":
        return "continue", "constant control baseline"
    if policy == "always-stop":
        return "stop", "constant control baseline"
    if policy == "rules-v2":
        return _rules_decision(row, config)
    raise ValueError(f"unknown policy: {policy}")


def _input_hash(row: pd.Series, policy: str, factor_names: list[str]) -> str:
    payload = {
        "run_id": str(row["run_id"]),
        "checkpoint": float(row["checkpoint"]),
        "policy": policy,
        "factors": {
            name: (None if pd.isna(row.get(name)) else float(row.get(name)))
            for name in factor_names
        },
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _score(decision: str, label: str) -> str:
    if decision == "abstain":
        return "abstained"
    if label == "fail":
        return "true_stop" if decision == "stop" else "missed_failure"
    if label == "pass":
        return "false_stop" if decision == "stop" else "correct_continue"
    return "unknown_label"


def run_backtest(
    panel: pd.DataFrame,
    runs: pd.DataFrame,
    *,
    cohort: str = "observed",
    policies: tuple[str, ...] = POLICIES,
    config: RuleConfig | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (decision rows, policy/checkpoint summary)."""
    config = config or RuleConfig()
    merged = panel.merge(
        runs[["run_id", "label", "final_success_rate", "cohort", "strict_asof"]],
        on="run_id", how="inner", suffixes=("", "_run"),
    )
    merged = merged[merged["cohort"] == cohort].copy()
    if merged.empty:
        return pd.DataFrame(), pd.DataFrame()

    factor_names = [
        "reward_drawdown", "kl_last", "kl_divergent_streak",
        "progress_slope", "step_coverage", "sample_count",
    ]
    rows: list[dict[str, Any]] = []
    for _, row in merged.iterrows():
        for policy in policies:
            decision, reason = _decision(row, policy, config)
            outcome = _score(decision, str(row["label"]))
            rows.append({
                "run_id": row["run_id"],
                "cohort": cohort,
                "strict_asof": bool(row["strict_asof"]),
                "checkpoint": float(row["checkpoint"]),
                "as_of_available_at": row.get("as_of_available_at"),
                "policy": policy,
                "decision": decision,
                "reason": reason,
                "label": row["label"],
                "final_success_rate": row["final_success_rate"],
                "outcome": outcome,
                "input_sha256": _input_hash(row, policy, factor_names),
            })
    decisions = pd.DataFrame(rows)

    summaries: list[dict[str, Any]] = []
    for (policy, checkpoint), group in decisions.groupby(["policy", "checkpoint"],
                                                         sort=True):
        labeled = group[group["label"].isin(["pass", "fail"])]
        pass_rows = labeled[labeled["label"] == "pass"]
        fail_rows = labeled[labeled["label"] == "fail"]
        true_stops = int((labeled["outcome"] == "true_stop").sum())
        false_stops = int((labeled["outcome"] == "false_stop").sum())
        missed = int((labeled["outcome"] == "missed_failure").sum())
        correct = int((labeled["outcome"] == "correct_continue").sum())
        decided = int(labeled["decision"].isin(["stop", "continue"]).sum())
        abstained = int((labeled["decision"] == "abstain").sum())
        stop_total = true_stops + false_stops
        readiness = (
            "candidate_for_external_validation"
            if len(pass_rows) >= READINESS_PASS_FLOOR
            and len(fail_rows) >= READINESS_FAIL_FLOOR
            else "blocked"
        )
        summaries.append({
            "policy": policy,
            "checkpoint": float(checkpoint),
            "cohort": cohort,
            "n_total": int(len(group)),
            "n_labeled": int(len(labeled)),
            "n_pass": int(len(pass_rows)),
            "n_fail": int(len(fail_rows)),
            "n_unknown": int((group["label"] == "unknown").sum()),
            "n_decided": decided,
            "n_abstained": abstained,
            "true_stops": true_stops,
            "false_stops": false_stops,
            "missed_failures": missed,
            "correct_continues": correct,
            "stop_precision": true_stops / stop_total if stop_total else None,
            "fail_recall": true_stops / len(fail_rows) if len(fail_rows) else None,
            "pass_kill_rate": false_stops / len(pass_rows) if len(pass_rows) else None,
            "decision_coverage": decided / len(labeled) if len(labeled) else None,
            "readiness": readiness,
            "readiness_reason": (
                "insufficient labeled runs (need pass>=5 and fail>=10)"
                if readiness == "blocked" else "sample floor met; external validation still required"
            ),
            "rule_config": json.dumps(config.to_dict(), sort_keys=True),
        })
    return decisions, pd.DataFrame(summaries)
