"""Deprecated v1 compatibility entry. Canonical replay: python -m rl_risk_replay."""
import sys
import warnings

if __name__ == "__main__" and "--legacy-retrospective" not in sys.argv:
    print("This is the retrospective v1 protocol, NOT time-correct replay. "
          "Use python -m rl_risk_replay, or explicitly add --legacy-retrospective "
          "to reproduce historical analyses.", file=sys.stderr)
    raise SystemExit(2)

from legacy import backtest_rules_v1 as _legacy

if __name__ == "__main__":
    sys.argv.remove("--legacy-retrospective")
    _legacy.main()
else:
    warnings.warn("backtest_rules imports retrospective v1 behavior; use rl_risk_replay for strict as-of inputs",
                  DeprecationWarning, stacklevel=2)
    # Preserve existing Python callers, patch targets and historical test semantics.
    sys.modules[__name__] = _legacy
