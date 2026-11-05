"""Machine-verifiable register of outward-facing claims.

Every number that appears in a README table or research note should be
registered here with a denominator, an applicability boundary, and a
reproduce command.  ``audit claims --verify`` re-runs those commands in a
temporary directory and fails on any drift.  Claims that cannot be reproduced
from this repository are marked ``unverified`` or ``retired`` and are never
silently promoted to facts.
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CLAIMS_PATH = ROOT / "CLAIMS.json"


def load_claims() -> dict[str, Any]:
    return json.loads(CLAIMS_PATH.read_text(encoding="utf-8"))


def _resolve(node: Any, path: str) -> Any:
    for part in path.split("."):
        if isinstance(node, list):
            node = node[int(part)]
        elif isinstance(node, dict):
            node = node[part]
        else:
            raise KeyError(path)
    return node


def _compare(node: Any, check: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if "match" in check:
        if not isinstance(node, list):
            return ["match requested but value is not a list"]
        matched = [item for item in node
                   if isinstance(item, dict)
                   and all(item.get(k) == v for k, v in check["match"].items())]
        if len(matched) != 1:
            return [f"expected exactly one matching row, found {len(matched)}"]
        node = matched[0]
    if ("keys_equals" in check
            and (not isinstance(node, dict)
                 or sorted(node) != sorted(check["keys_equals"]))):
        failures.append(f"keys differ: {sorted(node) if isinstance(node, dict) else node}")
    if "value_equals" in check and node != check["value_equals"]:
        failures.append(f"value differs: {node!r} != {check['value_equals']!r}")
    if "length_equals" in check and (not isinstance(node, (list, dict))
                                     or len(node) != check["length_equals"]):
        actual = len(node) if isinstance(node, (list, dict)) else repr(node)
        failures.append(f"length differs: {actual} != {check['length_equals']}")
    for key, expected in check.get("equals", {}).items():
        actual = node.get(key) if isinstance(node, dict) else None
        if actual != expected:
            failures.append(f"{key}: {actual!r} != {expected!r}")
    return failures


def _document(claim: dict[str, Any], bundle: Path) -> Any:
    if "reproduce" in claim:
        argv = [arg.replace("{bundle}", str(bundle)) for arg in claim["reproduce"][1:]]
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONUTF8="1")
        proc = subprocess.run([sys.executable, *argv], cwd=ROOT, env=env,
                              capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"reproduce failed: {proc.stderr.strip()[:400]}")
        return json.loads((bundle / claim["artifact"]).read_text(encoding="utf-8"))
    source = claim["source"]
    module = importlib.import_module(source["module"])
    return getattr(module, source["callable"])()


def verify_claims(*, only: str | None = None) -> dict[str, Any]:
    payload = load_claims()
    verified, skipped, failures = 0, [], []
    with tempfile.TemporaryDirectory(prefix="sentinel-claims-") as tmp:
        for index, claim in enumerate(payload["claims"]):
            if only and claim["id"] != only:
                continue
            if claim.get("status") != "verified":
                skipped.append({"id": claim["id"], "status": claim.get("status")})
                continue
            try:
                bundle = Path(tmp) / f"claim-{index}"
                document = _document(claim, bundle)
                node = _resolve(document, claim["check"]["path"])
                problems = _compare(node, claim["check"])
                if problems:
                    failures.append({"id": claim["id"], "problems": problems})
                else:
                    verified += 1
            except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
                failures.append({"id": claim["id"], "problems": [str(exc)]})
    return {
        "status": "passed" if not failures else "failed",
        "verified": verified, "skipped": skipped, "failures": failures,
        "total": len(payload["claims"]),
    }
