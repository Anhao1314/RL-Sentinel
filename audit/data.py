"""Verify the archived artifact manifest against a checkout of archive/v1.

The manifest on main is a content-addressed anchor, not the data itself.  It
lets anyone prove that the bodies moved to the archive branch are unchanged
without keeping 9.5 MB of unversioned CSV/PNG artifacts on main.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "manifest.json"


def load_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _resolve(root: Path) -> Path:
    """Accept either an archive checkout root or its ``data/`` directory."""
    if (root / "data").is_dir():
        return root
    if (root / "manifest.json").is_file():
        return root.parent
    return root


def verify(root: Path) -> dict[str, Any]:
    manifest = load_manifest()
    if manifest.get("schema_version") != 1:
        return {"status": "failed", "reason": "unsupported manifest schema_version"}
    entries = manifest["files"]
    base = _resolve(root)
    missing: list[str] = []
    mismatched: list[dict[str, str]] = []
    checked = 0
    for entry in entries:
        target = base / entry["path"]
        if not target.is_file():
            missing.append(entry["path"])
            continue
        raw = target.read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        if len(raw) != entry["bytes"] or actual != entry["sha256"]:
            mismatched.append({"path": entry["path"], "expected": entry["sha256"],
                               "actual": actual, "bytes": len(raw)})
        checked += 1
    structural = (
        manifest["file_count"] == len(entries)
        and manifest["total_bytes"] == sum(e["bytes"] for e in entries)
        and all(len(e["sha256"]) == 64 and e["path"].startswith("data/") for e in entries)
    )
    ok = structural and not missing and not mismatched and checked == len(entries)
    return {
        "status": "passed" if ok else "failed",
        "manifest_schema_version": manifest["schema_version"],
        "source_revision": manifest["source_revision"],
        "archive_ref": manifest["archive_ref"],
        "root": str(base),
        "expected_files": len(entries),
        "checked_files": checked,
        "total_bytes": manifest["total_bytes"],
        "structural_checks": bool(structural),
        "missing": missing[:20],
        "mismatched": mismatched[:20],
    }
