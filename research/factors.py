"""Factor registry and checkpoint factor computation.

Factors are pure functions of information available up to a training budget
checkpoint.  The target label (final verdict) is never an input.  Legacy CSV
runs have no event-level availability timestamps and are marked as offline.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

FACTOR_VERSION = "research-factors-v1"
CHECKPOINTS = (0.3, 0.5, 0.7)
RECENT = 5


@dataclass(frozen=True)
class FactorSpec:
    name: str
    category: str
    description: str
    direction: int  # +1: higher is better, -1: higher is risk, 0: neutral
    source: str


FACTOR_SPECS: tuple[FactorSpec, ...] = (
    FactorSpec("reward_last", "convergence", "Last visible reward", 0, "sample"),
    FactorSpec("reward_peak", "convergence", "Maximum visible reward", 1, "sample"),
    FactorSpec("reward_drawdown", "convergence", "Peak-to-last reward drawdown", -1, "sample"),
    FactorSpec("reward_slope", "convergence", "Reward slope per 1k steps", 1, "sample"),
    FactorSpec("reward_neg_ratio", "convergence", "Share of negative rewards", -1, "sample"),
    FactorSpec("reward_std_recent", "stability", "Reward std over the last 5 samples", 0, "sample"),
    FactorSpec("kl_last", "health", "Last approx_kl", -1, "sample"),
    FactorSpec("kl_mean_recent", "health", "Mean approx_kl over the last 5 samples", -1, "sample"),
    FactorSpec("kl_max_recent", "health", "Max approx_kl over the last 5 samples", -1, "sample"),
    FactorSpec("kl_divergent_streak", "health", "Consecutive tail samples with KL > 0.1", -1, "sample"),
    FactorSpec("value_loss_last", "health", "Last value loss", -1, "sample"),
    FactorSpec("value_loss_slope", "health", "Value-loss slope per 1k steps", -1, "sample"),
    FactorSpec("progress_last", "progress", "Last task progress scalar", 1, "sample"),
    FactorSpec("progress_slope", "progress", "Progress slope per 1k steps", 1, "sample"),
    FactorSpec("step_coverage", "progress", "Visible step / planned steps", 0, "sample"),
    FactorSpec("success_rate_last", "execution", "Last training success indicator", 1, "sample"),
    FactorSpec("success_rate_slope", "execution", "Success-indicator slope per 1k steps", 1, "sample"),
    FactorSpec("segment_last", "execution", "Last curriculum segment index", 0, "sample"),
    FactorSpec("segment_changes", "execution", "Number of curriculum segment changes", 0, "sample"),
    FactorSpec("sample_count", "coverage", "Visible samples", 0, "sample"),
    FactorSpec("eval_success_rate_last", "evaluation", "Last eval success rate before checkpoint", 1, "eval"),
    FactorSpec("eval_success_rate_slope", "evaluation", "Eval success-rate slope per 1k steps", 1, "eval"),
    FactorSpec("eval_mean_reward_last", "evaluation", "Last eval mean reward", 1, "eval"),
    FactorSpec("eval_mean_x_last", "evaluation", "Last eval progress metric", 1, "eval"),
)

FACTOR_BY_NAME = {spec.name: spec for spec in FACTOR_SPECS}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _slope(x: pd.Series, y: pd.Series) -> float | None:
    clean = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(clean) < 3 or clean["x"].nunique() < 2:
        return None
    result = stats.linregress(clean["x"].to_numpy(), clean["y"].to_numpy())
    if not math.isfinite(float(result.slope)):
        return None
    return float(result.slope) * 1000.0


def _recent(series: pd.Series, n: int = RECENT) -> pd.Series:
    return series.dropna().tail(n)


def _divergent_streak(series: pd.Series, threshold: float = 0.1) -> int:
    values = series.dropna().tolist()
    streak = 0
    for value in reversed(values):
        if float(value) > threshold:
            streak += 1
        else:
            break
    return streak


def compute_checkpoint_factors(
    samples: pd.DataFrame,
    eval_points: pd.DataFrame,
    *,
    checkpoint: float,
    planned_steps: int,
) -> dict[str, float | int | None]:
    """Compute factors from rows visible at ``checkpoint * planned_steps``."""
    if planned_steps <= 0:
        raise ValueError("planned_steps must be positive")
    cutoff = float(checkpoint) * planned_steps
    visible = samples[samples["step"] <= cutoff].sort_values("step")
    evals = eval_points[eval_points["timesteps"] <= cutoff].sort_values("timesteps")

    rewards = visible.get("reward", pd.Series(dtype=float))
    reward_last = _finite(rewards.iloc[-1]) if len(rewards) else None
    reward_peak = _finite(rewards.max()) if len(rewards) else None
    drawdown = None
    if reward_peak is not None and reward_last is not None and reward_peak > 0:
        drawdown = max(0.0, (reward_peak - reward_last) / reward_peak)
    kl = visible.get("approx_kl", pd.Series(dtype=float))
    value_loss = visible.get("value_loss", pd.Series(dtype=float))
    progress = visible.get("progress", pd.Series(dtype=float))
    success = visible.get("success_rate", pd.Series(dtype=float))
    segment = visible.get("segment", pd.Series(dtype=float))

    eval_success = evals.get("success_rate", pd.Series(dtype=float))
    eval_reward = evals.get("mean_reward", pd.Series(dtype=float))
    eval_x = evals.get("mean_x", pd.Series(dtype=float))

    return {
        "reward_last": reward_last,
        "reward_peak": reward_peak,
        "reward_drawdown": drawdown,
        "reward_slope": _slope(visible["step"], rewards) if len(rewards) else None,
        "reward_neg_ratio": float((rewards.dropna() < 0).mean()) if len(rewards.dropna()) else None,
        "reward_std_recent": float(_recent(rewards).std(ddof=0)) if len(_recent(rewards)) > 1 else None,
        "kl_last": _finite(kl.iloc[-1]) if len(kl) else None,
        "kl_mean_recent": float(_recent(kl).mean()) if len(_recent(kl)) else None,
        "kl_max_recent": float(_recent(kl).max()) if len(_recent(kl)) else None,
        "kl_divergent_streak": _divergent_streak(kl) if len(kl) else 0,
        "value_loss_last": _finite(value_loss.iloc[-1]) if len(value_loss) else None,
        "value_loss_slope": _slope(visible["step"], value_loss) if len(value_loss) else None,
        "progress_last": _finite(progress.iloc[-1]) if len(progress) else None,
        "progress_slope": _slope(visible["step"], progress) if len(progress) else None,
        "step_coverage": float(visible["step"].iloc[-1]) / planned_steps if len(visible) else 0.0,
        "success_rate_last": _finite(success.iloc[-1]) if len(success) else None,
        "success_rate_slope": _slope(visible["step"], success) if len(success) else None,
        "segment_last": _finite(segment.iloc[-1]) if len(segment) else None,
        "segment_changes": (
            int((segment.dropna().diff().iloc[1:] != 0).sum())
            if len(segment.dropna()) > 1 else 0
        ),
        "sample_count": int(len(visible)),
        "eval_success_rate_last": _finite(eval_success.iloc[-1]) if len(eval_success) else None,
        "eval_success_rate_slope": _slope(evals["timesteps"], eval_success) if len(eval_success) else None,
        "eval_mean_reward_last": _finite(eval_reward.iloc[-1]) if len(eval_reward) else None,
        "eval_mean_x_last": _finite(eval_x.iloc[-1]) if len(eval_x) else None,
    }


def build_factor_panel(
    runs: pd.DataFrame,
    events: pd.DataFrame,
    eval_points: pd.DataFrame,
    *,
    checkpoints: tuple[float, ...] = CHECKPOINTS,
) -> pd.DataFrame:
    """One row per (run, checkpoint) with factor values and as-of metadata."""
    rows: list[dict[str, Any]] = []
    for run in runs.itertuples(index=False):
        planned = int(run.planned_steps) if pd.notna(run.planned_steps) else 0
        if planned <= 0:
            continue
        run_events = events[events["run_id"] == run.run_id]
        samples = run_events[run_events["kind"] == "sample"].copy()
        if samples.empty:
            # Legacy cohort: eval points are the only observation time series.
            legacy = eval_points[eval_points["run_id"] == run.run_id]
            if legacy.empty:
                continue
            samples = pd.DataFrame({
                "step": legacy["timesteps"].astype(float),
                "reward": legacy["mean_reward"].astype(float),
                "available_at": np.nan,
            })
        run_eval = eval_points[eval_points["run_id"] == run.run_id]
        for checkpoint in checkpoints:
            cutoff = float(checkpoint) * planned
            visible = samples[samples["step"] <= cutoff]
            as_of = (
                float(visible["available_at"].max())
                if "available_at" in visible and visible["available_at"].notna().any()
                else None
            )
            factors = compute_checkpoint_factors(
                samples, run_eval, checkpoint=checkpoint, planned_steps=planned
            )
            rows.append({
                "run_id": run.run_id,
                "cohort": run.cohort,
                "strict_asof": bool(run.strict_asof),
                "checkpoint": float(checkpoint),
                "checkpoint_step": int(cutoff),
                "as_of_available_at": as_of,
                **factors,
            })
    columns = [
        "run_id", "cohort", "strict_asof", "checkpoint", "checkpoint_step",
        "as_of_available_at", *[spec.name for spec in FACTOR_SPECS],
    ]
    return pd.DataFrame(rows, columns=columns)


def summarize_factors(panel: pd.DataFrame, runs: pd.DataFrame) -> pd.DataFrame:
    """Coverage, distribution, and label association per factor/checkpoint."""
    summary_columns = [
        "factor", "category", "direction", "source", "checkpoint", "coverage",
        "n", "n_labeled", "mean", "std", "ic_pass", "ic_success",
    ]
    if panel.empty or "run_id" not in panel.columns:
        return pd.DataFrame(columns=summary_columns)
    merged = panel.merge(
        runs[["run_id", "label", "final_success_rate"]], on="run_id", how="left"
    )
    rows: list[dict[str, Any]] = []
    for checkpoint, group in merged.groupby("checkpoint", sort=True):
        labeled = group[group["label"].isin(["pass", "fail"])].copy()
        labeled["_pass"] = (labeled["label"] == "pass").astype(float)
        for spec in FACTOR_SPECS:
            values = group[spec.name].dropna()
            row: dict[str, Any] = {
                "factor": spec.name,
                "category": spec.category,
                "direction": spec.direction,
                "source": spec.source,
                "checkpoint": float(checkpoint),
                "coverage": float(len(values) / len(group)) if len(group) else 0.0,
                "n": int(len(values)),
                "n_labeled": int(len(labeled)),
                "mean": float(values.mean()) if len(values) else None,
                "std": float(values.std(ddof=0)) if len(values) > 1 else None,
                "ic_pass": None,
                "ic_success": None,
            }
            factor_labeled = labeled[["_pass", "final_success_rate", spec.name]].dropna()
            if len(factor_labeled) >= 5 and factor_labeled[spec.name].nunique() > 1:
                try:
                    row["ic_pass"] = float(stats.spearmanr(
                        factor_labeled[spec.name], factor_labeled["_pass"]
                    ).statistic)
                    if factor_labeled["final_success_rate"].nunique() > 1:
                        row["ic_success"] = float(stats.spearmanr(
                            factor_labeled[spec.name],
                            factor_labeled["final_success_rate"],
                        ).statistic)
                except ValueError:
                    pass
            rows.append(row)
    return pd.DataFrame(rows)
