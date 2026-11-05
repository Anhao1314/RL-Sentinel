"""As-of availability must gate research factors, with explicit degradation."""
from __future__ import annotations

import pandas as pd

from research.factors import build_factor_panel, compute_checkpoint_factors

EMPTY_EVALS = pd.DataFrame(columns=["run_id", "timesteps", "success_rate",
                                    "mean_reward", "mean_x"])


def _samples() -> pd.DataFrame:
    return pd.DataFrame({
        "step": [100, 200, 300, 150],
        "reward": [1.0, 1.2, 1.4, 99.0],
        "available_at": [10.0, 20.0, 30.0, 999.0],
    })


def test_as_of_excludes_late_arriving_records() -> None:
    strict = compute_checkpoint_factors(
        _samples(), EMPTY_EVALS, checkpoint=0.3, planned_steps=1000, as_of=25.0
    )
    degraded = compute_checkpoint_factors(
        _samples(), EMPTY_EVALS, checkpoint=0.3, planned_steps=1000
    )
    assert strict["sample_count"] == 2
    assert degraded["sample_count"] == 4
    assert strict["reward_peak"] == 1.2
    assert degraded["reward_peak"] == 99.0


def test_as_of_requires_an_availability_column() -> None:
    try:
        compute_checkpoint_factors(
            pd.DataFrame({"step": [100], "reward": [1.0]}), EMPTY_EVALS,
            checkpoint=0.3, planned_steps=1000, as_of=25.0,
        )
    except ValueError as exc:
        assert "available_at" in str(exc)
    else:
        raise AssertionError("as_of filtering must refuse a clock-less frame")


def test_panel_marks_missing_clock_as_degraded() -> None:
    runs = pd.DataFrame([{
        "run_id": "legacy-1", "cohort": "legacy",
        "strict_asof": False, "planned_steps": 1000,
    }])
    events = pd.DataFrame([{
        "run_id": "legacy-1", "kind": "sample", "step": 100,
        "available_at": float("nan"), "reward": 1.0,
    }])
    panel = build_factor_panel(runs, events, EMPTY_EVALS, checkpoints=(0.3,))
    assert panel.iloc[0]["asof_mode"] == "degraded"
    assert panel.iloc[0]["strict_asof"] is False or not panel.iloc[0]["strict_asof"]
