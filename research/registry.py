"""Append-only research registry for datasets, experiments, and reports."""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = "research-registry-v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_experiment(
    path: pathlib.Path,
    *,
    name: str,
    hypothesis: str,
    dataset_manifest: dict[str, Any],
    report_manifest: dict[str, Any],
    report_dir: pathlib.Path,
) -> dict[str, Any]:
    record = {
        "schema_version": SCHEMA_VERSION,
        "created_at": utc_now(),
        "name": name,
        "hypothesis": hypothesis,
        "dataset_version": dataset_manifest.get("dataset_version"),
        "factor_version": dataset_manifest.get("factor_version"),
        "code": dataset_manifest.get("code"),
        "readiness": report_manifest.get("readiness"),
        "report_dir": str(report_dir.resolve()),
        "report_sha256": report_manifest.get("report_sha256"),
        "results_sha256": report_manifest.get("results_sha256"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True,
                                allow_nan=False) + "\n")
    return record


def read_registry(path: pathlib.Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records
