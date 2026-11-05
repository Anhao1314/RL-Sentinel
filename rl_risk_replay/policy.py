"""Single source of truth for rule definitions and rule identities.

Historically two different algorithms were both labelled ``rules-v2``:

* the strict replay policy (``rl_risk_replay/engine.py``) with actions
  continue / watch / stop / tune / resize / abstain, and
* the cohort backtest policy (``research/backtest.py``) with actions
  continue / stop / abstain and different thresholds.

Their behaviour is intentionally preserved, but they now have distinct
``rule_id`` values and are defined in this one stdlib-only module so that a
reported number can always be traced to exactly one rule definition.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .events import ContractError, integer, number

RULE_SCHEMA_VERSION = "2.1"

STRICT_RULE_ID = "rules-v2/strict-replay@1"
COHORT_RULE_ID = "rules-v2/cohort-backtest@1"

# Legacy display names are kept so existing bundles, reports and tests remain
# reproducible.  Use rule_id (not name) when citing a result.
STRICT_POLICY_NAME = "rules-v2"
COHORT_POLICY_NAME = "rules-v2"


@dataclass(frozen=True)
class StrictRuleConfig:
    """Thresholds for the as-of event replay policy (stdlib strict core)."""

    min_reward_points: int = 3
    drawdown_stop: float = 0.3
    kl_watch: float = 0.03
    kl_tune: float = 0.1
    memory_resize: float = 95.0

    def __post_init__(self) -> None:
        integer(self.min_reward_points, "min_reward_points", 2)
        for key, val in self.to_dict().items():
            number(val, key, 0)
        if not 0 < self.drawdown_stop <= 1 or not 0 < self.kl_watch <= self.kl_tune:
            raise ContractError("invalid rule threshold order/range")
        if not 0 < self.memory_resize <= 100:
            raise ContractError("memory_resize must be in (0,100]")

    def to_dict(self) -> dict[str, object]:
        return {"min_reward_points": self.min_reward_points,
                "drawdown_stop": self.drawdown_stop, "kl_watch": self.kl_watch,
                "kl_tune": self.kl_tune, "memory_resize": self.memory_resize}


@dataclass(frozen=True)
class CohortRuleConfig:
    """Thresholds for the offline cohort backtest (research layer)."""

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


def rules_manifest() -> dict[str, Any]:
    """Machine-readable rule registry; the only place thresholds are declared."""
    return {
        "schema_version": RULE_SCHEMA_VERSION,
        "note": (
            "Two distinct policies share the legacy display name 'rules-v2'. "
            "Cite rule_id, never the display name, when reporting a result."
        ),
        "profiles": {
            STRICT_RULE_ID: {
                "display_name": STRICT_POLICY_NAME,
                "layer": "rl_risk_replay",
                "actions": ["continue", "watch", "stop", "tune", "resize", "abstain"],
                "config": StrictRuleConfig().to_dict(),
            },
            COHORT_RULE_ID: {
                "display_name": COHORT_POLICY_NAME,
                "layer": "research",
                "actions": ["continue", "stop", "abstain"],
                "config": CohortRuleConfig().to_dict(),
            },
        },
    }
