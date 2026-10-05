"""滚动回测框架（B 方案 P1）：leave-one-future-out，插件式 predictor。

设计：
- run 开始时间 = 首快照；结束时间 = 实际结束（completed）或外推
  （step_rate / task_mean，与 backtest_rules 一致）；
- 按开始时间排序；对每个目标 run，训练集 = 结束时间早于其开始时间
  且有 pass/fail 标签的 run（时间不泄漏）；
- 决策时刻 = 训练进度达到 decision_progress x total_steps 的时刻；
- predictor 插件协议：
    predictor_fn(train_runs_info, target_online, cfg)
        -> {"stop": bool, "confidence": float, "reason": str}
  内置 rule_predictor（包装规则重放）、always_continue / always_stop 基线；
  未来 P3 贝叶斯模型按同一协议接入；
- 指标：minutes_saved / saved_yuan_compute / stop_accuracy /
  false_kill_rate（零 pass 时 N/A）/ potential_false_kill_rate /
  early_stop_precision / recall；
- 确定性：同输入数据两次运行输出一致（无随机）。

免责声明：本报告为框架验证报告，不构成训练早停决策依据。
"""

from __future__ import annotations

import argparse
import json
import pathlib
from collections import Counter
from datetime import date as _Date
from typing import Any, Callable

import pandas as pd

from collector import PROJECT_ROOT, load_config
from factors import LEVEL_INDEX, decide, max_level, run_factors, run_risk_items
from quant import load_tables
from backtest_rules import (
    _filter,
    _row,
    _as_bool,
    _to_float,
    compute_price,
    estimate_t_end,
    fail_score,
    ground_truth,
    online_view,
    progress_at,
    run_pairs,
    same_task_rewards,
    _task_mean_duration,
)

# 插件类型：训练信息 + 目标在线快照 + 配置 -> 决策
PredictorFn = Callable[
    [list[dict[str, Any]], dict[str, Any], dict[str, Any]], dict[str, Any]
]


# --------------------------------------------------------------------------
# run 元信息：开始/结束时间、标签、软标签
# --------------------------------------------------------------------------

def estimate_end_time(
    tables: dict[str, pd.DataFrame], task: str, seed: str
) -> tuple[float | None, str]:
    """run 结束时间（不依赖触发时刻）：completed 用实际；incomplete 用末快照
    step_rate 外推；缺失回退同 task 已完成 run 平均时长。"""
    snaps = _filter(tables.get("snapshots"), task, seed)
    if snaps.empty:
        return None, "none"
    t0 = float(snaps["time"].min())
    t_last = float(snaps["time"].max())
    row = _row(tables.get("runs"), task, seed)
    completed = _as_bool(row.get("completed")) if row else False
    duration = _to_float(row.get("duration_seconds")) if row else None
    if completed and duration is not None and duration > 0:
        return t0 + duration, "actual"

    total = _to_float(row.get("total_steps")) if row else None
    prog = progress_at(snaps, t_last)
    elapsed_h = (t_last - t0) / 3600.0
    if total and prog and elapsed_h > 0 and prog > 0:
        return t0 + total / (prog / elapsed_h) * 3600.0, "step_rate"

    mean_dur = _task_mean_duration(tables, task)
    if mean_dur is not None:
        return t0 + mean_dur, "task_mean"
    return t_last, "actual"


def build_run_meta(
    tables: dict[str, pd.DataFrame], cfg: dict[str, Any]
) -> pd.DataFrame:
    """每 run 一行：start_time / end_time / end_method / label / fail_score / proxy_pass。"""
    rows: list[dict[str, Any]] = []
    runs_df = tables.get("runs")
    reports_df = tables.get("reports")
    for task, seed in run_pairs(tables):
        snaps = _filter(tables.get("snapshots"), task, seed)
        if snaps.empty:
            continue  # 无快照无法回测（无时间轴）
        start = float(snaps["time"].min())
        end, method = estimate_end_time(tables, task, seed)
        label = ground_truth(runs_df, reports_df, task, seed)
        f = run_factors(task, seed, tables, cfg)
        row = _row(runs_df, task, seed)
        rewards = same_task_rewards(tables, task)
        rows.append({
            "task": task,
            "seed": seed,
            "start_time": start,
            "end_time": end if end is not None else float("nan"),
            "end_method": method,
            "label": label,
            "fail_score": fail_score(f, row, rewards),
            "total_steps": _to_float(row.get("total_steps")) if row else None,
        })
    meta = pd.DataFrame(rows)
    if len(meta):
        meta = meta.sort_values("start_time").reset_index(drop=True)
    return meta


def decision_time(
    tables: dict[str, pd.DataFrame], task: str, seed: str, decision_progress: float
) -> float | None:
    """训练进度达到 decision_progress x total_steps 的时刻；未达到返回 None。"""
    row = _row(tables.get("runs"), task, seed)
    total = _to_float(row.get("total_steps")) if row else None
    if total is None or total <= 0:
        return None
    snaps = _filter(tables.get("snapshots"), task, seed)
    if snaps.empty:
        return None
    snaps = snaps.sort_values("time")
    target = decision_progress * total
    ts = pd.to_numeric(snaps["timesteps"], errors="coerce")
    hit = snaps[ts >= target]
    return float(hit["time"].iloc[0]) if len(hit) else None


# --------------------------------------------------------------------------
# 插件
# --------------------------------------------------------------------------

def always_continue(
    train_runs_info: list[dict[str, Any]],
    target_online: dict[str, Any],
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """基线：从不止损。"""
    return {"stop": False, "confidence": 1.0, "reason": "baseline: always continue"}


def always_stop(
    train_runs_info: list[dict[str, Any]],
    target_online: dict[str, Any],
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """基线：总是止损。"""
    return {"stop": True, "confidence": 1.0, "reason": "baseline: always stop"}


def rule_predictor(
    train_runs_info: list[dict[str, Any]],
    target_online: dict[str, Any],
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """规则插件：在目标 run 的在线视野上跑 v2 风控规则，stop 类决策即止损。"""
    view = target_online.get("view") or {}
    f = run_factors(target_online["task"], target_online["seed"], view, cfg)
    if not f:
        return {"stop": False, "confidence": 0.0, "reason": "no factors at decision point"}
    risks = run_risk_items(f, cfg)
    if not risks:
        return {"stop": False, "confidence": 0.0, "reason": "no risk triggered"}
    decision, msgs = decide(f, risks, cfg)
    if decision == "stop":
        level = max_level(risks)
        confidence = round(LEVEL_INDEX[level] / 3.0, 4)
        return {"stop": True, "confidence": confidence, "reason": " | ".join(msgs)}
    return {"stop": False, "confidence": 0.0, "reason": " | ".join(msgs)}


PLUGINS: dict[str, PredictorFn] = {
    "rule": rule_predictor,
    "always_continue": always_continue,
    "always_stop": always_stop,
}


# --------------------------------------------------------------------------
# 滚动回测
# --------------------------------------------------------------------------

def _train_info(
    tables: dict[str, pd.DataFrame],
    cfg: dict[str, Any],
    meta: pd.DataFrame,
    mask: pd.Series,
) -> list[dict[str, Any]]:
    infos: list[dict[str, Any]] = []
    for _, r in meta[mask].iterrows():
        f = run_factors(r["task"], r["seed"], tables, cfg)
        infos.append({
            "task": r["task"],
            "seed": r["seed"],
            "label": r["label"],
            "fail_score": float(r["fail_score"]),
            "factors": f,
            "start_time": float(r["start_time"]),
            "end_time": float(r["end_time"]),
        })
    return infos


def run_backtest(
    tables: dict[str, pd.DataFrame],
    cfg: dict[str, Any],
    predictor_fn: PredictorFn,
    decision_progress: float = 0.5,
) -> list[dict[str, Any]]:
    """滚动回测一轮（单个 decision_progress）；返回逐 run 决策行。"""
    meta = build_run_meta(tables, cfg)
    if meta.empty:
        return []
    gpu_count, gpu_price = compute_price(cfg)
    rows: list[dict[str, Any]] = []
    for _, target in meta.iterrows():
        train_mask = (
            (meta["end_time"] < target["start_time"])
            & meta["label"].isin(["pass", "fail"])
        )
        train_info = _train_info(tables, cfg, meta, train_mask)
        T_d = decision_time(tables, target["task"], target["seed"], decision_progress)
        label = str(target["label"])
        base = {
            "task": target["task"],
            "seed": target["seed"],
            "decision_progress": decision_progress,
            "label": label,
            "fail_score": round(float(target["fail_score"]), 4),
            "train_size": len(train_info),
        }
        if T_d is None:
            base.update({
                "stop": False,
                "confidence": 0.0,
                "reason": "progress never reached",
                "skipped": True,
                "minutes_saved": 0.0,
                "saved_yuan_compute": 0.0,
                "t_end_method": target["end_method"],
                "hit": False,
                "false_kill": False,
            })
            rows.append(base)
            continue
        target_online = {
            "task": target["task"],
            "seed": target["seed"],
            "view": online_view(tables, target["task"], target["seed"], T_d),
        }
        pred = predictor_fn(train_info, target_online, cfg)
        stop = bool(pred.get("stop"))
        t_end, method = estimate_t_end(tables, target["task"], target["seed"], T_d)
        saved = max(0.0, (t_end - T_d) / 60.0) if stop else 0.0
        yuan = round(saved / 60.0 * gpu_count * gpu_price, 2) if stop else 0.0
        hit = bool(stop and label == "fail")
        false_kill = bool(stop and label == "pass")
        base.update({
            "stop": stop,
            "confidence": round(float(pred.get("confidence") or 0.0), 4),
            "reason": str(pred.get("reason") or ""),
            "skipped": False,
            "t_decision": T_d,
            "t_end": round(t_end, 1),
            "t_end_method": method,
            "minutes_saved": round(saved, 1),
            "saved_yuan_compute": yuan,
            "hit": hit,
            "false_kill": false_kill,
        })
        rows.append(base)
    return rows


def aggregate_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """聚合指标（只统计有标签且未跳过的决策行）。"""
    labeled = [r for r in rows if r["label"] in ("pass", "fail") and not r["skipped"]]
    n_labeled = len(labeled)
    if n_labeled == 0:
        return {
            "n_labeled": 0, "n_stop": 0, "n_pass": 0, "n_fail": 0,
            "stop_accuracy": float("nan"), "false_kill_rate": "N/A (no stop)",
            "potential_false_kill_rate": float("nan"),
            "early_stop_precision": float("nan"), "recall": float("nan"),
            "minutes_saved": 0.0, "saved_yuan_compute": 0.0,
        }
    n_stop = sum(1 for r in labeled if r["stop"])
    n_pass = sum(1 for r in labeled if r["label"] == "pass")
    n_fail = sum(1 for r in labeled if r["label"] == "fail")
    hits = [r for r in labeled if r["hit"]]
    false_kills = [r for r in labeled if r["false_kill"]]
    continue_ok = [r for r in labeled if not r["stop"] and r["label"] == "pass"]
    accuracy = (len(hits) + len(continue_ok)) / n_labeled
    if n_pass == 0:
        fk_rate: Any = "N/A (no pass samples)"
    else:
        fk_rate = round(len(false_kills) / n_stop, 4) if n_stop else float("nan")
    pot = [
        r for r in labeled
        if r["stop"] and (r["fail_score"] is not None) and r["fail_score"] < 0.3
    ]
    return {
        "n_labeled": n_labeled,
        "n_stop": n_stop,
        "n_pass": n_pass,
        "n_fail": n_fail,
        "stop_accuracy": round(accuracy, 4),
        "false_kill_rate": fk_rate,
        "potential_false_kill_rate": round(len(pot) / n_stop, 4) if n_stop else float("nan"),
        "early_stop_precision": round(len(hits) / n_stop, 4) if n_stop else float("nan"),
        "recall": round(len(hits) / n_fail, 4) if n_fail else float("nan"),
        "minutes_saved": round(sum(r["minutes_saved"] for r in hits), 1),
        "saved_yuan_compute": round(sum(r["saved_yuan_compute"] for r in hits), 2),
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="go2w-quant 滚动回测框架（B 方案 P1）")
    parser.add_argument("--config", default=None)
    parser.add_argument("--today", default=None, help="报告日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--out", default=None, help="覆盖输出目录（默认 config.modeling_dir）")
    parser.add_argument("--plugin", default="rule", choices=sorted(PLUGINS),
                        help="predictor 插件（默认 rule）")
    parser.add_argument("--decision-progress", default="0.5",
                        help="决策进度点，逗号分隔支持列表，如 0.3,0.5,0.7")
    args = parser.parse_args()

    cfg = load_config(args.config)
    tables = load_tables(cfg)
    today = args.today or _Date.today().isoformat()
    predictor = PLUGINS[args.plugin]
    try:
        progress_list = [float(x) for x in args.decision_progress.split(",") if x.strip()]
    except ValueError:
        raise SystemExit(f"无效 --decision-progress: {args.decision_progress}")
    if not progress_list:
        raise SystemExit("--decision-progress 不能为空")

    all_rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for dp in progress_list:
        rows = run_backtest(tables, cfg, predictor, decision_progress=dp)
        all_rows.extend(rows)
        summaries.append({"decision_progress": dp, **aggregate_rows(rows)})

    out_dir = pathlib.Path(args.out) if args.out else pathlib.Path(cfg["modeling_dir"])
    if not out_dir.is_absolute():
        out_dir = PROJECT_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"backtest_{today}.csv"
    json_path = out_dir / f"backtest_{today}.json"
    if all_rows:
        df = pd.DataFrame(all_rows)
        csv_cols = [
            "task", "seed", "decision_progress", "label", "fail_score",
            "train_size", "stop", "confidence", "reason", "skipped",
            "t_decision", "t_end", "t_end_method", "minutes_saved",
            "saved_yuan_compute", "hit", "false_kill",
        ]
        df[csv_cols].to_csv(csv_path, index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame().to_csv(csv_path, index=False, encoding="utf-8-sig")
    json_path.write_text(
        json.dumps({"today": today, "plugin": args.plugin, "summaries": summaries},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"[backtest_engine] 滚动回测 {today}　插件={args.plugin}")
    for s in summaries:
        print("  decision_progress={:.1f}: labeled={} stop={} acc={} "
              "precision={} recall={} fk_rate={} minutes_saved={} yuan={}".format(
                  s["decision_progress"], s["n_labeled"], s["n_stop"],
                  s["stop_accuracy"], s["early_stop_precision"], s["recall"],
                  s["false_kill_rate"], s["minutes_saved"], s["saved_yuan_compute"]))
    print(f"  CSV: {csv_path}")
    print(f"  JSON: {json_path}")


if __name__ == "__main__":
    main()