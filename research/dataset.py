"""Versioned research dataset built from Go2W events and legacy CSV tables."""

from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import duckdb
import pandas as pd

from research.factors import (
    CHECKPOINTS,
    FACTOR_SPECS,
    FACTOR_VERSION,
    build_factor_panel,
    summarize_factors,
)
from rl_risk_replay.events import ContractError
from rl_risk_replay.storage import decode_events

DATASET_SCHEMA = "research-dataset-v1"
DEFAULT_CHECKPOINTS = CHECKPOINTS


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def file_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def code_revision() -> dict[str, Any]:
    commit: str | None = None
    dirty: bool | None = None
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
            check=True, timeout=5,
        ).stdout.strip()
        dirty = bool(subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True,
            check=True, timeout=5,
        ).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    research_root = pathlib.Path(__file__).resolve().parent
    source_hash = hashlib.sha256()
    for path in sorted(research_root.glob("*.py")):
        source_hash.update(path.name.encode("utf-8"))
        source_hash.update(path.read_bytes())
    return {
        "commit": commit,
        "dirty": dirty,
        "research_source_sha256": source_hash.hexdigest(),
    }


def find_run_dirs(root: pathlib.Path) -> list[pathlib.Path]:
    if (root / "run_config.json").is_file():
        return [root]
    return sorted({path.parent for path in root.rglob("run_config.json")})


def _map_verdict(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in ("pass", "fail", "unknown"):
        return text
    if text in ("smoke_only", "cancelled", ""):
        return "unknown"
    return "unknown"


def _first_summary(run_dir: pathlib.Path) -> dict[str, Any] | None:
    for name in ("evaluation", "smoke_evaluation"):
        path = run_dir / name / "summary.json"
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    return None


def _job_spec(run_dir: pathlib.Path) -> dict[str, Any] | None:
    for candidate in (run_dir / "_job" / "spec.json", run_dir / "spec.json"):
        if candidate.is_file():
            return json.loads(candidate.read_text(encoding="utf-8"))
    return None


def _read_eval_points(run_dir: pathlib.Path, run_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for stage_dir in sorted(run_dir.glob("stage*")):
        path = stage_dir / "eval_log.csv"
        if not path.is_file():
            continue
        frame = pd.read_csv(path)
        for _, row in frame.iterrows():
            record: dict[str, Any] = {"run_id": run_id, "stage": stage_dir.name}
            for key in ("timesteps", "mean_reward", "mean_ep_len", "mean_x",
                        "success_rate", "passed_rate"):
                if key in frame.columns:
                    value = pd.to_numeric(row[key], errors="coerce")
                    record[key] = None if pd.isna(value) else float(value)
            rows.append(record)
    return rows


@dataclass(frozen=True)
class LoadedRun:
    run: dict[str, Any]
    events: list[dict[str, Any]]
    eval_points: list[dict[str, Any]]
    label: dict[str, Any]


def load_go2w_run(run_dir: pathlib.Path) -> LoadedRun:
    run_dir = run_dir.resolve()
    config_path = run_dir / "run_config.json"
    events_path = run_dir / "events.jsonl"
    if not config_path.is_file() or not events_path.is_file():
        raise FileNotFoundError(f"not a Go2W run: {run_dir}")
    config_raw = json.loads(config_path.read_text(encoding="utf-8"))
    config = config_raw.get("config", config_raw)
    try:
        strict_events, origin = decode_events(events_path.read_text(encoding="utf-8"))
    except ContractError as exc:
        raise ValueError(f"{run_dir}: invalid event stream: {exc}") from exc

    run_id = run_dir.name
    task = str(config.get("task", "unknown"))
    seed = str(config.get("seed", ""))
    summary = _first_summary(run_dir)
    spec = _job_spec(run_dir)
    source = (spec or {}).get("source") or {}
    runtime = config_raw.get("runtime") or {}

    start = next((event for event in strict_events if event.kind == "start"), None)
    finish = next((event for event in strict_events if event.kind == "finish"), None)
    label_event = next((event for event in strict_events if event.kind == "label"), None)
    planned_steps = int(start.payload()["planned_steps"]) if start else 0
    if planned_steps <= 0 and isinstance(config.get("stages"), list):
        planned_steps = sum(int(stage.get("steps", 0)) for stage in config["stages"])
    samples = [event for event in strict_events if event.kind == "sample"]
    if planned_steps <= 0 and samples:
        planned_steps = max(int(event.payload()["step"]) for event in samples)

    summary_verdict = _map_verdict((summary or {}).get("verdict"))
    label = label_event.payload() if label_event else None
    verdict = _map_verdict(label.get("verdict")) if label else summary_verdict
    label_source = (label or {}).get("source") if label else ("summary" if summary else None)
    label_available_at = label_event.available_at if label_event else None
    final_success_rate = (
        float(summary["success_rate"])
        if summary and summary.get("success_rate") is not None else None
    )

    event_rows: list[dict[str, Any]] = []
    for event in strict_events:
        payload = event.payload()
        event_rows.append({
            "run_id": run_id,
            "event_id": event.event_id,
            "kind": event.kind,
            "event_time": event.event_time,
            "available_at": event.available_at,
            "step": payload.get("step"),
            "reward": payload.get("reward"),
            "approx_kl": payload.get("approx_kl"),
            "value_loss": payload.get("value_loss"),
            "memory_percent": payload.get("memory_percent"),
            "progress": payload.get("progress"),
            "success_rate": payload.get("success_rate"),
            "segment": payload.get("segment"),
            "payload_json": canonical(payload),
        })

    eval_points = _read_eval_points(run_dir, run_id)
    run = {
        "run_id": run_id,
        "cohort": "observed",
        "source": "go2w",
        "task": task,
        "seed": seed,
        "planned_steps": planned_steps,
        "origin": origin,
        "label": verdict,
        "label_source": label_source,
        "label_available_at": label_available_at,
        "final_success_rate": final_success_rate,
        "finish_status": finish.payload().get("status") if finish else None,
        "strict_asof": bool(label_event is not None and finish is not None),
        "source_commit": source.get("commit") or runtime.get("source_commit"),
        "source_dirty": source.get("dirty", runtime.get("source_dirty")),
        "config_hash": file_sha256(config_path),
        "events_sha256": file_sha256(events_path),
        "event_count": len(strict_events),
        "sample_count": len(samples),
        "eval_count": len(eval_points),
        "run_dir": str(run_dir),
    }
    label_record = {
        "run_id": run_id,
        "verdict": verdict,
        "source": label_source,
        "available_at": label_available_at,
        "success_rate": final_success_rate,
        "strict_asof": run["strict_asof"],
    }
    return LoadedRun(run=run, events=event_rows, eval_points=eval_points,
                    label=label_record)


def load_legacy(data_dir: pathlib.Path) -> tuple[list[dict[str, Any]],
                                                  list[dict[str, Any]],
                                                  list[dict[str, Any]]]:
    runs_path = data_dir / "runs.csv"
    labels_path = data_dir / "labels.csv"
    eval_path = data_dir / "eval_points.csv"
    if not runs_path.is_file():
        return [], [], []
    runs_frame = pd.read_csv(runs_path, encoding="utf-8-sig")
    labels_frame = (
        pd.read_csv(labels_path, encoding="utf-8-sig")
        if labels_path.is_file() else pd.DataFrame()
    )
    eval_frame = (
        pd.read_csv(eval_path, encoding="utf-8-sig")
        if eval_path.is_file() else pd.DataFrame()
    )
    label_map: dict[tuple[str, str], dict[str, Any]] = {}
    for _, row in labels_frame.iterrows():
        key = (str(row.get("task")), str(row.get("seed")))
        label_map[key] = {
            "verdict": _map_verdict(row.get("verdict")),
            "verdict_raw": str(row.get("verdict") or "").strip(),
            "success_rate": pd.to_numeric(row.get("success_rate"), errors="coerce"),
            "source": str(row.get("label_source") or "legacy"),
        }
    runs: list[dict[str, Any]] = []
    labels: list[dict[str, Any]] = []
    for _, row in runs_frame.iterrows():
        task = str(row.get("task"))
        seed = str(row.get("seed"))
        run_id = f"legacy:{task}:{seed}"
        label_info = label_map.get((task, seed), {})
        verdict = (
            _map_verdict(label_info.get("verdict_raw"))
            if label_info.get("verdict_raw")
            else _map_verdict(row.get("verdict"))
        )
        success = label_info.get("success_rate")
        if pd.isna(success):
            success = pd.to_numeric(row.get("success_rate"), errors="coerce")
        planned = pd.to_numeric(row.get("total_steps"), errors="coerce")
        runs.append({
            "run_id": run_id,
            "cohort": "legacy",
            "source": "legacy",
            "task": task,
            "seed": seed,
            "planned_steps": int(planned) if pd.notna(planned) else 0,
            "origin": "legacy",
            "label": verdict,
            "label_source": label_info.get("source") or "legacy",
            "label_available_at": None,
            "final_success_rate": None if pd.isna(success) else float(success),
            "finish_status": "completed" if bool(row.get("completed")) else None,
            "strict_asof": False,
            "source_commit": None,
            "source_dirty": None,
            "config_hash": None,
            "events_sha256": None,
            "event_count": 0,
            "sample_count": 0,
            "eval_count": 0,
            "run_dir": str(data_dir),
        })
        labels.append({
            "run_id": run_id,
            "verdict": verdict,
            "source": label_info.get("source") or "legacy",
            "available_at": None,
            "success_rate": None if pd.isna(success) else float(success),
            "strict_asof": False,
        })
    eval_points: list[dict[str, Any]] = []
    if not eval_frame.empty:
        for _, row in eval_frame.iterrows():
            run_id = f"legacy:{row.get('task')}:{row.get('seed')}"
            record: dict[str, Any] = {"run_id": run_id, "stage": "legacy"}
            for key in ("timesteps", "mean_reward", "mean_ep_len", "mean_x",
                        "success_rate", "passed_rate"):
                if key in eval_frame.columns:
                    value = pd.to_numeric(row.get(key), errors="coerce")
                    record[key] = None if pd.isna(value) else float(value)
            eval_points.append(record)
    return runs, labels, eval_points


def _write_parquet(frame: pd.DataFrame, path: pathlib.Path) -> None:
    frame.to_parquet(path, index=False)


def _write_duckdb(db_path: pathlib.Path, tables: dict[str, pd.DataFrame]) -> None:
    con = duckdb.connect(str(db_path))
    try:
        for name, frame in tables.items():
            con.register(f"_{name}", frame)
            con.execute(f'CREATE OR REPLACE TABLE "{name}" AS SELECT * FROM _{name}')
            con.unregister(f"_{name}")
    finally:
        con.close()


def build_dataset(
    runs_root: pathlib.Path,
    out_root: pathlib.Path,
    *,
    legacy_data: pathlib.Path | None = None,
    checkpoints: tuple[float, ...] = DEFAULT_CHECKPOINTS,
) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    eval_points: list[dict[str, Any]] = []
    labels: list[dict[str, Any]] = []
    for run_dir in find_run_dirs(runs_root):
        loaded = load_go2w_run(run_dir)
        runs.append(loaded.run)
        events.extend(loaded.events)
        eval_points.extend(loaded.eval_points)
        labels.append(loaded.label)
    if legacy_data is not None and legacy_data.is_dir():
        legacy_runs, legacy_labels, legacy_evals = load_legacy(legacy_data)
        runs.extend(legacy_runs)
        labels.extend(legacy_labels)
        eval_points.extend(legacy_evals)

    if not runs:
        raise ValueError(f"no runs found under {runs_root}")
    runs_frame = pd.DataFrame(runs)
    if runs_frame["run_id"].duplicated().any():
        duplicates = runs_frame.loc[runs_frame["run_id"].duplicated(), "run_id"].tolist()
        raise ValueError(f"duplicate run_id: {duplicates}")
    events_frame = pd.DataFrame(events)
    if events_frame.empty:
        events_frame = pd.DataFrame(columns=[
            "run_id", "event_id", "kind", "event_time", "available_at", "step",
            "reward", "approx_kl", "value_loss", "memory_percent", "progress",
            "success_rate", "segment", "payload_json",
        ])
    eval_frame = pd.DataFrame(eval_points)
    if eval_frame.empty:
        eval_frame = pd.DataFrame(columns=[
            "run_id", "stage", "timesteps", "mean_reward", "mean_ep_len",
            "mean_x", "success_rate", "passed_rate",
        ])
    labels_frame = pd.DataFrame(labels)
    factor_panel = build_factor_panel(
        runs_frame, events_frame, eval_frame, checkpoints=checkpoints
    )
    factor_summary = summarize_factors(factor_panel, runs_frame)

    revision = code_revision()
    input_hashes = {
        row.run_id: {
            "events_sha256": (
                None if pd.isna(row.events_sha256) else row.events_sha256
            ),
            "config_hash": (
                None if pd.isna(row.config_hash) else row.config_hash
            ),
            "eval_count": int(row.eval_count),
            "label": row.label,
            "success_rate": (
                None if pd.isna(row.final_success_rate)
                else float(row.final_success_rate)
            ),
        }
        for row in runs_frame.itertuples(index=False)
    }
    version_payload = {
        "schema": DATASET_SCHEMA,
        "run_ids": sorted(runs_frame["run_id"].tolist()),
        "input_hashes": input_hashes,
        "factor_version": FACTOR_VERSION,
        "checkpoints": list(checkpoints),
        "code": revision,
    }
    version = hashlib.sha256(canonical(version_payload).encode("utf-8")).hexdigest()[:16]
    dataset_dir = out_root / version
    manifest_path = dataset_dir / "manifest.json"
    if dataset_dir.exists():
        if manifest_path.is_file():
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        raise FileExistsError(dataset_dir)
    dataset_dir.mkdir(parents=True)

    tables = {
        "runs": runs_frame,
        "events": events_frame,
        "eval_points": eval_frame,
        "labels": labels_frame,
        "factor_panel": factor_panel,
        "factor_summary": factor_summary,
        "factor_registry": pd.DataFrame([
            {
                "name": spec.name,
                "category": spec.category,
                "description": spec.description,
                "direction": spec.direction,
                "source": spec.source,
                "version": FACTOR_VERSION,
            }
            for spec in FACTOR_SPECS
        ]),
    }
    table_paths: dict[str, str] = {}
    for name, frame in tables.items():
        path = dataset_dir / f"{name}.parquet"
        _write_parquet(frame, path)
        table_paths[name] = path.name
    _write_duckdb(dataset_dir / "research.duckdb", tables)

    manifest = {
        "schema_version": DATASET_SCHEMA,
        "dataset_version": version,
        "created_at": utc_now(),
        "factor_version": FACTOR_VERSION,
        "checkpoints": list(checkpoints),
        "code": revision,
        "n_runs": int(len(runs_frame)),
        "n_observed": int((runs_frame["cohort"] == "observed").sum()),
        "n_legacy": int((runs_frame["cohort"] == "legacy").sum()),
        "n_labeled": int(runs_frame["label"].isin(["pass", "fail"]).sum()),
        "labels": runs_frame["label"].value_counts().to_dict(),
        "tables": table_paths,
        "source_roots": {
            "runs": str(runs_root.resolve()),
            "legacy": str(legacy_data.resolve()) if legacy_data else None,
        },
        "limitations": [
            "Legacy CSV runs have no event-level available_at timestamps; they are "
            "reported as an offline cohort, not strict as-of evidence.",
            "Labels are targets for scoring and are never factor inputs.",
            "This dataset version hashes run inputs and research source; it is not "
            "a cryptographic signature.",
        ],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2,
                                        allow_nan=False) + "\n", encoding="utf-8")
    return manifest


def read_dataset(dataset_dir: pathlib.Path) -> tuple[dict[str, Any], dict[str, pd.DataFrame]]:
    manifest = json.loads((dataset_dir / "manifest.json").read_text(encoding="utf-8"))
    tables = {
        name: pd.read_parquet(dataset_dir / filename)
        for name, filename in manifest["tables"].items()
    }
    return manifest, tables
