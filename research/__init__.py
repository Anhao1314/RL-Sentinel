"""RL-Sentinel research layer.

The strict event/replay core lives in :mod:`rl_risk_replay`.  This package is
the quantitative-research layer: versioned datasets, factor panels, cohort
backtests, registries, and the static research report.

Research code may use pandas/DuckDB/Parquet.  The strict core remains
stdlib-only and is never weakened to make a research result look better.
"""

from research.factors import FACTOR_SPECS, FACTOR_VERSION, FactorSpec

__all__ = ["FACTOR_SPECS", "FACTOR_VERSION", "FactorSpec"]
