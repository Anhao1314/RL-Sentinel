"""Repository self-audit CLI: claims, rules, secrets, environment."""
from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

from .claims import load_claims, verify_claims
from .secrets import run as run_secret_scan

ROOT = Path(__file__).resolve().parents[1]


def _emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def _revision() -> str:
    proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else "unavailable"


def rules_check() -> dict[str, Any]:
    engine = (ROOT / "rl_risk_replay" / "engine.py").read_text(encoding="utf-8")
    backtest = (ROOT / "research" / "backtest.py").read_text(encoding="utf-8")
    claims = load_claims()
    registered = [
        item["check"]["keys_equals"]
        for item in claims["claims"]
        if item.get("source", {}).get("callable") == "rules_manifest"
    ]
    from rl_risk_replay.policy import rules_manifest
    actual_ids = sorted(rules_manifest()["profiles"])
    checks = {
        "engine_imports_single_source": "from .policy import" in engine,
        "backtest_imports_single_source": "rl_risk_replay.policy" in backtest,
        "no_duplicate_strict_config": "class RuleConfig" not in engine,
        "no_duplicate_cohort_config": "class RuleConfig" not in backtest,
        "registered_ids_match_manifest": (
            not registered or sorted(registered[0]) == actual_ids
        ),
    }
    return {"status": "passed" if all(checks.values()) else "failed",
            "checks": checks, "rule_ids": actual_ids}


def environment() -> dict[str, Any]:
    modules = ("pytest", "pandas", "numpy", "scipy", "duckdb", "pyarrow",
               "sklearn", "matplotlib", "requests", "tensorboard", "pyright")
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "code_revision": _revision(),
        "dependencies": {name: bool(importlib.util.find_spec(name))
                         for name in modules},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RL-Sentinel repository self-audit")
    sub = parser.add_subparsers(dest="command", required=True)
    claims = sub.add_parser("claims", help="list or verify registered claims")
    claims.add_argument("--verify", action="store_true")
    claims.add_argument("--only")
    secrets = sub.add_parser("secrets", help="full-history credential scan")
    secrets.add_argument("--report", action="store_true",
                         help="print all fingerprints without failing")
    sub.add_parser("rules", help="check rule sources against the registry")
    sub.add_parser("env", help="print reproduction environment")
    args = parser.parse_args(argv)
    if args.command == "claims":
        result = (verify_claims(only=args.only) if args.verify
                  else load_claims())
        _emit(result)
        return 0 if result.get("status", "listed") != "failed" else 1
    if args.command == "secrets":
        code, result = run_secret_scan(report_only=args.report)
        _emit(result)
        return code
    if args.command == "rules":
        result = rules_check()
        _emit(result)
        return 0 if result["status"] == "passed" else 1
    _emit(environment())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
