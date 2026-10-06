"""Explicit denominators; abstentions, missing labels and savings stay visible."""
from __future__ import annotations

import math

from .engine import Row
from .events import ContractError, Origin


def ratio(n: int, d: int) -> float | None:
    return n / d if d else None


def wilson(successes: int, total: int) -> list[float] | None:
    """Descriptive 95% Wilson interval; not an independence assertion."""
    if not total:
        return None
    p = successes / total
    z2 = 1.96 ** 2
    center = p + z2 / (2 * total)
    width = 1.96 * math.sqrt(p * (1 - p) / total + z2 / (4 * total * total))
    denom = 1 + z2 / total
    return [max(0.0, (center - width) / denom), min(1.0, (center + width) / denom)]


def summarize(rows: tuple[Row, ...], origin: Origin) -> dict[str, object]:
    if origin not in ("observed", "synthetic", "controlled"):
        raise ContractError("invalid dataset origin")
    if len({(r.policy, r.progress) for r in rows}) > 1:
        raise ContractError("do not pool policies or progress checkpoints")
    if len({r.run_id for r in rows}) != len(rows):
        raise ContractError("a run must occur at most once per summary")
    eligible = [r for r in rows if r.status != "skipped"]
    labeled = [r for r in eligible if r.verdict in ("pass", "fail")]
    decided = [r for r in labeled if r.status == "decided"]
    stops = [r for r in labeled if r.decision and r.decision.action == "stop"]
    passes = [r for r in labeled if r.verdict == "pass"]
    fails = [r for r in labeled if r.verdict == "fail"]
    hits = [r for r in stops if r.verdict == "fail"]
    false_stops = [r for r in stops if r.verdict == "pass"]
    correct = len(hits) + sum(r.verdict == "pass" and r not in stops for r in decided)
    saving = sum(max(0.0, r.finish_time - r.view.cutoff) for r in hits
                 if r.finish_time is not None and r.view is not None)
    lost_remaining = sum(max(0.0, r.finish_time - r.view.cutoff) for r in false_stops
                         if r.finish_time is not None and r.view is not None)
    gate_reasons: list[str] = []
    if origin != "observed":
        gate_reasons.append("synthetic/controlled data cannot establish operational effectiveness")
    if len(passes) < 5 or len(fails) < 10:
        gate_reasons.append("insufficient eligible labeled runs (operational floor: pass>=5, fail>=10)")
    if any(r.status != "decided" for r in rows):
        gate_reasons.append("incomplete decision coverage requires explicit cohort review")
    if any(r.verdict == "unknown" for r in eligible):
        gate_reasons.append("eligible runs include outcomes not yet available")
    return {
        "metric_version": "2.0", "n_total": len(rows), "n_eligible": len(eligible),
        "n_skipped": len(rows) - len(eligible),
        "n_abstained": sum(r.status == "abstained" for r in eligible),
        "n_labeled": len(labeled), "n_unknown": len(eligible) - len(labeled),
        "n_pass": len(passes), "n_fail": len(fails), "n_stop_labeled": len(stops),
        "true_stops": len(hits), "false_stops": len(false_stops),
        "decision_coverage": ratio(sum(r.status == "decided" for r in rows), len(rows)),
        "eligible_coverage": ratio(len(eligible), len(rows)),
        "decision_accuracy": ratio(correct, len(decided)),
        "stop_precision": ratio(len(hits), len(stops)),
        "false_stop_share": ratio(len(false_stops), len(stops)),
        "pass_kill_rate": ratio(len(false_stops), len(passes)),
        "fail_recall": ratio(len(hits), len(fails)),
        "pass_kill_rate_wilson95": wilson(len(false_stops), len(passes)),
        "counterfactual_failed_run_remaining_wall_seconds": saving,
        "false_stop_remaining_wall_seconds": lost_remaining,
        "realized_compute_savings": None,
        "denominators": {
            "stop_precision": "true stops / labeled stops",
            "false_stop_share": "false stops / labeled stops",
            "pass_kill_rate": "false stops / eligible passes (including abstentions)",
            "fail_recall": "true stops / eligible failures (including abstentions)",
            "decision_accuracy": "correct binary stop/continue decisions / decided labeled runs",
            "decision_coverage": "decided runs / all target runs",
        },
        "sample_floor_met": len(passes) >= 5 and len(fails) >= 10,
        "readiness": "blocked" if gate_reasons else "candidate_for_external_validation",
        "readiness_reasons": gate_reasons,
        "limitations": ["No automatic interventions or realized cost savings.",
                        "Observed origin is a producer declaration, not an external provenance attestation.",
                        "The sample floor is not statistical sufficiency; review independence, tasks and label quality.",
                        "Wall time is not GPU utilization, effective compute, failure onset or causal regret."],
    }
