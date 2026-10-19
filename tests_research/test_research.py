"""Tests for dataset, factor, backtest, and report layers."""

from __future__ import annotations

import json
import pathlib
from typing import Any

import duckdb
import pandas as pd
import pytest

from research.backtest import RuleConfig, run_backtest
from research.dataset import build_dataset, read_dataset
from research.factors import compute_checkpoint_factors
from research.report import render_report


def _write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _event(event_id: str, run_id: str, kind: str, time: float,
           payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "event_id": event_id,
        "run_id": run_id,
        "kind": kind,
        "event_time": time,
        "available_at": time,
        "payload": payload,
    }


def _make_observed_run(root: pathlib.Path) -> pathlib.Path:
    run = root / "observed-pass"
    run.mkdir(parents=True)
    _write(run / "run_config.json", json.dumps({
        "schema_version": "navigation-integrity-v1",
        "config": {
            "task": "fixture", "seed": 0, "stages": [{"steps": 1000}],
        },
        "runtime": {"python": "3.12", "source_commit": None},
        "inputs": {},
    }))
    events = [_event("start", run.name, "start", 100, {
        "task": "fixture", "seed": "0", "planned_steps": 1000,
    })]
    for index, step in enumerate((100, 200, 300, 600, 900), start=1):
        events.append(_event(f"s{index}", run.name, "sample", 100 + index * 10, {
            "step": step,
            "reward": 1.0 + index * 0.2,
            "approx_kl": 0.02,
            "value_loss": 10.0 - index,
            "progress": step / 10.0,
            "success_rate": 1.0 if step >= 600 else 0.0,
            "segment": 0.0,
        }))
    events.append(_event("finish", run.name, "finish", 200, {
        "status": "completed",
    }))
    events.append(_event("label", run.name, "label", 210, {
        "verdict": "pass", "source": "evaluation",
    }))
    _write(run / "events.jsonl",
           "\n".join([json.dumps({"schema_version": 2, "origin": "observed"})]
                     + [json.dumps(event) for event in events]) + "\n")
    _write(run / "stage1" / "eval_log.csv",
           "timesteps,mean_reward,mean_ep_len,mean_x,success_rate,passed_rate\n"
           "300,1.2,600,3.0,0.5,0.5\n"
           "600,1.6,620,6.0,1.0,1.0\n"
           "900,1.8,630,9.0,1.0,1.0\n")
    _write(run / "evaluation" / "summary.json", json.dumps({
        "verdict": "pass", "success_rate": 1.0,
    }))
    _write(run / "_job" / "spec.json", json.dumps({
        "source": {"commit": "fixture-commit", "dirty": False},
    }))
    return run


def _make_legacy(root: pathlib.Path) -> pathlib.Path:
    data = root / "legacy"
    data.mkdir()
    _write(data / "runs.csv",
           "task,seed,completed,total_steps,verdict,success_rate\n"
           "balance,seed00,True,1000,pass,1.0\n"
           "balance,seed01,True,1000,fail,0.0\n")
    _write(data / "labels.csv",
           "task,seed,completed,verdict,success_rate,label_source\n"
           "balance,seed00,True,pass,1.0,manual\n"
           "balance,seed01,True,fail,0.0,manual\n")
    _write(data / "eval_points.csv",
           "task,seed,timesteps,mean_reward,std_reward,mean_ep_len\n"
           "balance,seed00,300,1.0,0.1,600\n"
           "balance,seed00,600,1.5,0.1,620\n"
           "balance,seed00,900,1.7,0.1,630\n"
           "balance,seed01,300,1.0,0.0,600\n"
           "balance,seed01,600,0.4,0.2,600\n"
           "balance,seed01,900,0.2,0.3,600\n")
    return data


def _dataset(tmp_path: pathlib.Path) -> tuple[pathlib.Path, dict[str, Any]]:
    runs_root = tmp_path / "runs"
    _make_observed_run(runs_root)
    legacy = _make_legacy(tmp_path)
    manifest = build_dataset(runs_root, tmp_path / "data", legacy_data=legacy)
    return tmp_path / "data" / manifest["dataset_version"], manifest


def test_build_dataset_and_duckdb(tmp_path: pathlib.Path) -> None:
    dataset_dir, manifest = _dataset(tmp_path)
    assert manifest["n_observed"] == 1
    assert manifest["n_legacy"] == 2
    assert manifest["n_labeled"] == 3
    assert manifest["dataset_version"] in str(dataset_dir)
    for name in ("runs", "events", "eval_points", "labels", "factor_panel",
                 "factor_summary", "factor_registry"):
        assert (dataset_dir / f"{name}.parquet").is_file()
    con = duckdb.connect(str(dataset_dir / "research.duckdb"))
    try:
        count = con.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        assert count == 3
    finally:
        con.close()
    loaded_manifest, tables = read_dataset(dataset_dir)
    assert loaded_manifest["dataset_version"] == manifest["dataset_version"]
    observed = tables["runs"][tables["runs"]["cohort"] == "observed"]
    assert len(observed) == 1
    assert observed.iloc[0]["strict_asof"]
    assert len(tables["factor_panel"]) == 9


def test_factor_values_and_no_label_input(tmp_path: pathlib.Path) -> None:
    samples = pd.DataFrame({
        "step": [100, 200, 300, 400, 500],
        "reward": [1.0, 2.0, 3.0, 2.0, 1.0],
        "approx_kl": [0.01, 0.02, 0.12, 0.13, 0.14],
        "value_loss": [10.0, 9.0, 8.0, 9.0, 10.0],
        "progress": [1.0, 2.0, 3.0, 3.0, 3.0],
        "success_rate": [0.0, 0.0, 1.0, 1.0, 1.0],
        "segment": [0.0, 0.0, 1.0, 1.0, 1.0],
    })
    factors = compute_checkpoint_factors(
        samples, pd.DataFrame(columns=["timesteps", "mean_reward"]),
        checkpoint=1.0, planned_steps=500,
    )
    assert factors["reward_drawdown"] == pytest.approx(2.0 / 3.0)
    assert factors["kl_divergent_streak"] == 3
    assert factors["segment_changes"] == 1
    assert factors["sample_count"] == 5
    assert "label" not in factors


def test_backtest_metrics_and_readiness(tmp_path: pathlib.Path) -> None:
    dataset_dir, _ = _dataset(tmp_path)
    _, tables = read_dataset(dataset_dir)
    decisions, summary = run_backtest(
        tables["factor_panel"], tables["runs"], cohort="observed",
        config=RuleConfig(),
    )
    assert len(decisions) == 9
    assert set(decisions["policy"]) == {"rules-v2", "always-continue", "always-stop"}
    assert (summary["readiness"] == "blocked").all()
    rule_rows = summary[summary["policy"] == "rules-v2"]
    assert len(rule_rows) == 3
    always_stop = summary[summary["policy"] == "always-stop"].iloc[0]
    assert always_stop["true_stops"] == 0
    assert always_stop["false_stops"] == 1


def test_report_is_self_contained(tmp_path: pathlib.Path) -> None:
    dataset_dir, _ = _dataset(tmp_path)
    out = tmp_path / "report"
    record = render_report(dataset_dir, out)
    html = (out / "report.html").read_text(encoding="utf-8")
    assert "RL-Sentinel Research" in html
    assert "Factor Lab" in html
    assert "Backtest" in html
    assert "Readiness" in html
    assert "<script src=" not in html
    assert record["readiness"] == "blocked"
    assert (out / "results.json").is_file()
    assert (out / "manifest.json").is_file()
