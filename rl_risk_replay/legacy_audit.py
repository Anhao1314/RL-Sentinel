"""Read-only inventory of historical CSVs. Never invent availability timestamps."""
from __future__ import annotations

import csv
import hashlib
import math
from collections import Counter
from pathlib import Path

from .events import ContractError

KEYS: dict[str, tuple[str, ...]] = {
    "runs": ("task", "seed"), "eval_points": ("task", "seed", "timesteps"),
    "tb_points": ("task", "seed", "step"), "snapshots": ("time", "task", "seed"),
    "reports": ("task", "seed", "label"), "labels": ("task", "seed"),
    "manual_labels": ("task", "seed"), "costs": ("date", "thread_id"),
}


def audit_legacy(path: Path) -> dict[str, object]:
    if not path.is_dir():
        raise FileNotFoundError(path)
    tables: dict[str, object] = {}
    rows_by_table: dict[str, list[dict[str, str]]] = {}
    for name, key in KEYS.items():
        source = path / f"{name}.csv"
        if not source.is_file():
            tables[name] = {"present": False}
            continue
        data = source.read_bytes()
        with source.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = list(reader.fieldnames or [])
            if len(columns) != len(set(columns)):
                raise ContractError(f"{name}: duplicate CSV column names")
            records: list[dict[str, str]] = []
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise ContractError(f"{name}: malformed CSV row")
                records.append(dict(row))
        rows_by_table[name] = records
        keys = [tuple(row.get(col, "") for col in key) for row in records]
        counts = Counter(keys)
        invalid_time = 0
        if "time" in columns:
            for row in records:
                try:
                    timestamp = float(row["time"])
                    invalid_time += int(not math.isfinite(timestamp) or timestamp < 0)
                except ValueError:
                    invalid_time += 1
        tables[name] = {"present": True, "rows": len(records), "columns": columns,
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "duplicate_key_rows": sum(n - 1 for n in counts.values()),
                        "empty_key_rows": sum(any(not v.strip() for v in k) for k in keys),
                        "invalid_time_rows": invalid_time,
                        "has_available_at": "available_at" in columns}
    runs = rows_by_table.get("runs", [])
    if not runs:
        raise ContractError("historical audit requires a nonempty runs.csv")
    overrides: dict[tuple[str, str], str] = {}
    for row in rows_by_table.get("manual_labels", []):
        key = (row.get("task", ""), row.get("seed", ""))
        label = row.get("verdict", "").strip().lower()
        if key in overrides or label not in ("", "pass", "fail"):
            raise ContractError("manual labels contain duplicate keys or invalid verdicts")
        overrides[key] = label
    labels: Counter[str] = Counter()
    snapshot_keys = {(r.get("task"), r.get("seed")) for r in rows_by_table.get("snapshots", [])}
    covered: Counter[str] = Counter()
    for row in runs:
        key = (row.get("task", ""), row.get("seed", ""))
        label = overrides.get(key) or row.get("verdict", "").strip().lower()
        label = label if label in ("pass", "fail") else "unknown"
        labels[label] += 1
        if key in snapshot_keys:
            covered[label] += 1
    return {"mode": "legacy-read-only-inventory", "tables": tables,
            "run_rows": len(runs), "declared_outcomes": dict(labels),
            "snapshot_covered_outcomes": dict(covered),
            "strict_replay_eligible_runs": 0,
            "temporal_readiness": "blocked_legacy_schema",
            "reasons": ["Legacy CSV schema does not record per-event availability or immutable run-start revisions.",
                        "Step position, file mtime and final snapshot duration cannot reconstruct those facts.",
                        "No timestamps were inferred; no rows were deleted, relabeled or cleaned.",
                        "This inventory does not replace the six-category legacy quality checker."]}
