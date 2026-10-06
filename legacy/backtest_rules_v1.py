"""规则止损回测：历史重放 R2/R3 风控规则，输出赔率表与期望净节省（B 方案 P0）。

方法（参考梁文锋/幻方量化思路：先防亏再求胜、用赔率而非点估计）：
- 时间轴 = snapshots.time（Unix 秒），默认 10 分钟扫描粒度；
- 每个决策时刻 T 的"在线视野"：
    snapshots(time<=T)、eval_points(timesteps<=progress(T))、
    tb_points(step<=progress(T))、runs 副本（completed 强制 False，
    模拟训练进行中，stall/idle 规则才可触发）、total_steps 保留；
    reports 完全排除（验收报告是训练结束后的产物，在线不可观测）。
- 复用 factors.run_factors / run_risk_items / decide 纯函数；
  每 run 每规则类型只记首次触发（冷却，不重复计数）。
- 标签三层：pass / fail / unknown；unknown 不进命中率分母。
- 节省分钟 = max(0, t_end - t_trigger)：
    completed run 用实际结束时间（t0 + duration_seconds）；
    incomplete run 用 step_rate 外推（total_steps / (进度/已耗时)），
    缺失回退同 task 已完成 run 平均时长（task_mean）。
- 算力成本 saved_yuan_compute = 节省小时 x gpu_count x gpu_hourly_price
  （估算）；token 成本不做逐 run 归因，仅报告项目级日趋势。
- 期望净节省 = hit_rate x avg_saved(fail) - false_kill_rate x avg_wasted(pass)。

免责声明：本报告为框架验证报告，不构成训练早停决策依据。
"""

from __future__ import annotations

import argparse
import pathlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import date as _Date
from typing import Any

import pandas as pd

from collector import PROJECT_ROOT, load_config
from factors import (
    LEVEL_INDEX,
    decide,
    early_low_reward_factor,
    max_level,
    run_factors,
    run_risk_items,
)
from quant import load_tables, resolve_report_dir

# 报告常驻免责声明（直到标签数量达标）
WARNING_LINE = (
    "⚠️ 框架验证报告。当前 n_pass={n_pass}, n_fail={n_fail}, n_unknown={n_unknown}。"
    "赔率表仅验证管线正确性，不构成训练早停决策依据。"
    "待 n_pass≥5 且 n_fail≥10 后产出结论性报告。"
)

STALL_PROGRESS = 0.6   # 停滞判定：进度阈值（与 config 的 stagnation.progress 一致）
STALL_RATIO = 0.6      # 停滞判定：奖励峰值比阈值
STD_COLLAPSE = 0.01    # 策略输出标准差塌缩阈值（fail_score 用）


@dataclass
class TriggerEvent:
    """一次规则触发：级别 + 决策 + 时刻 + 触发因子集。"""

    task: str
    seed: str
    level: str
    decision: str
    trigger_time: float
    factors: tuple[str, ...]
    messages: tuple[str, ...]
    progress_ratio: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "seed": self.seed,
            "level": self.level,
            "decision": self.decision,
            "trigger_time": self.trigger_time,
            "factors": list(self.factors),
            "progress_ratio": self.progress_ratio,
        }


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------

def _filter(df: pd.DataFrame | None, task: str, seed: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    return df[(df["task"] == task) & (df["seed"] == seed)]


def _row(runs_df: pd.DataFrame | None, task: str, seed: str) -> dict[str, Any] | None:
    if runs_df is None or runs_df.empty:
        return None
    rows = runs_df[(runs_df["task"] == task) & (runs_df["seed"] == seed)]
    if rows.empty:
        return None
    return rows.iloc[0].to_dict()


def _as_bool(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v) if not pd.isna(v) else False
    s = str(v).strip().lower()
    return s in ("true", "1", "yes", "pass")


def _to_float(v: Any) -> float | None:
    try:
        if v is None or pd.isna(v):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def run_pairs(tables: dict[str, pd.DataFrame]) -> list[tuple[str, str]]:
    """全部 (task, seed)：runs 表优先，快照表补充。"""
    seen: set[tuple[str, str]] = set()
    runs = tables.get("runs")
    if runs is not None and len(runs):
        seen.update(zip(runs["task"], runs["seed"]))
    snaps = tables.get("snapshots")
    if snaps is not None and len(snaps):
        seen.update(zip(snaps["task"], snaps["seed"]))
    return sorted(seen)


def same_task_rewards(tables: dict[str, pd.DataFrame], task: str) -> list[float]:
    evals = tables.get("eval_points")
    if evals is None or evals.empty:
        return []
    sub = evals[evals["task"] == task]
    vals = pd.to_numeric(sub["mean_reward"], errors="coerce").dropna()
    return [float(v) for v in vals]


# --------------------------------------------------------------------------
# 重放协议
# --------------------------------------------------------------------------

def progress_at(snaps: pd.DataFrame, T: float) -> float | None:
    """时刻 T 的训练进度：time<=T 的最新快照 timesteps。"""
    view = snaps[snaps["time"] <= T]
    if view.empty:
        return None
    ts = pd.to_numeric(view["timesteps"], errors="coerce").dropna()
    return float(ts.iloc[-1]) if len(ts) else None


def online_view(
    tables: dict[str, pd.DataFrame], task: str, seed: str, T: float
) -> dict[str, pd.DataFrame]:
    """构造时刻 T 的在线视野：按时间截断、completed 强制 False、不含 reports。"""
    view: dict[str, pd.DataFrame] = {}
    snaps = _filter(tables.get("snapshots"), task, seed)
    if snaps.empty:
        return view
    snaps_t = snaps[snaps["time"] <= T]
    view["snapshots"] = snaps_t
    prog = progress_at(snaps, T)
    if prog is not None:
        evals = _filter(tables.get("eval_points"), task, seed)
        if len(evals):
            view["eval_points"] = evals[
                pd.to_numeric(evals["timesteps"], errors="coerce") <= prog
            ]
        tb = _filter(tables.get("tb_points"), task, seed)
        if len(tb):
            view["tb_points"] = tb[
                pd.to_numeric(tb["step"], errors="coerce") <= prog
            ]
    runs = tables.get("runs")
    if runs is not None and len(runs):
        r = runs.copy()
        r["completed"] = False  # 在线模拟：训练进行中，stall/idle 规则才可触发
        view["runs"] = r
    return view


def _run_total_steps(
    tables: dict[str, pd.DataFrame], task: str, seed: str
) -> float | None:
    """run 总步数：runs 行 total_steps 优先，缺失回退最后一个 eval timesteps。"""
    row = _row(tables.get("runs"), task, seed)
    if row is not None:
        total = _to_float(row.get("total_steps"))
        if total is not None and total > 0:
            return total
    evals = _filter(tables.get("eval_points"), task, seed)
    ts = pd.to_numeric(evals["timesteps"], errors="coerce").dropna()
    return float(ts.max()) if len(ts) else None


def _early_first_ts(
    tables: dict[str, pd.DataFrame], task: str, seed: str, cfg: dict[str, Any]
) -> float | None:
    """early_low_reward 触发截面：全量窗口因子成立时，返回快照轴上最早
    >= 25% 窗口闭合步长的时刻；否则返回 None。"""
    evals = _filter(tables.get("eval_points"), task, seed)
    snaps = _filter(tables.get("snapshots"), task, seed)
    if evals.empty or snaps.empty:
        return None
    total = _run_total_steps(tables, task, seed)
    if total is None or total <= 0:
        return None
    d = evals.sort_values("timesteps").reset_index(drop=True)
    if early_low_reward_factor(d, task, total, cfg) is not True:
        return None
    trig_ts = total * 0.25
    sn_ts = pd.to_numeric(snaps["timesteps"], errors="coerce")
    sn_t = pd.to_numeric(snaps["time"], errors="coerce")
    cand = sn_t[sn_t.notna() & sn_ts.notna() & (sn_ts >= trig_ts)]
    return float(cand.min()) if len(cand) else None

def _early_axis_event(
    tables: dict[str, pd.DataFrame], task: str, seed: str, cfg: dict[str, Any]
) -> TriggerEvent | None:
    """无快照 run 的 eval 轴单因子回放：仅按全量 eval 点判定 early_low_reward。

    全量窗口因子成立时，在 25% 窗口闭合步长（total*0.25）产生 R2 stop 事件；
    不做全规则回放（无快照 pass run 的全量 drawdown 会误杀）。
    注：max(mean_reward) 随前缀单调不减，首真前缀可能在窗口内被后续点推翻
    （如 flat_slope_v1 early_max=30.05），故以全量因子为准。
    """
    evals = _filter(tables.get("eval_points"), task, seed)
    if evals.empty:
        return None
    total = _run_total_steps(tables, task, seed)
    if total is None or total <= 0:
        return None
    d = evals.sort_values("timesteps").reset_index(drop=True)
    if early_low_reward_factor(d, task, total, cfg) is not True:
        return None
    trig_ts = total * 0.25
    return TriggerEvent(
        task, seed, "R2", "stop", trig_ts,
        ("early_low_reward",),
        ("前25%训练步数内评估奖励始终低于阈值（从未学会型失败）",),
        0.25,
    )

def replay_run(
    tables: dict[str, pd.DataFrame],
    task: str,
    seed: str,
    cfg: dict[str, Any],
    step_minutes: int = 10,
) -> list[TriggerEvent]:
    """沿时间轴重放单个 run；返回首次 R3、首次 R2/R3、首次 stop 事件（可去重）。

    视野记忆化：指纹键 (n_snap, n_eval, n_tb) 沿时间轴单调不减，唯一确定
    在线视野内容（run_factors 为确定性纯函数，快照行序不影响因子值），
    重复键跳过视图构建与因子重算；同键结果恒同，first_* 首触发语义不变。
    """
    snaps = _filter(tables.get("snapshots"), task, seed)
    if snaps.empty:
        # 无快照 run：仅按 eval 轴回放 early_low_reward（单因子），
        # 不做全规则回放（全量 drawdown 会误杀无快照 pass run）
        ev = _early_axis_event(tables, task, seed, cfg)
        return [ev] if ev is not None else []
    snaps = snaps.sort_values("time")
    t0 = float(snaps["time"].iloc[0])
    t1 = float(snaps["time"].iloc[-1])
    step = max(1, step_minutes) * 60.0

    # 预过滤子表一次（保留原行序），循环内用布尔掩码切片构造视野
    evals_raw = _filter(tables.get("eval_points"), task, seed)
    tb_raw = _filter(tables.get("tb_points"), task, seed)
    runs_raw = tables.get("runs")
    runs_view: pd.DataFrame | None = None
    if runs_raw is not None and len(runs_raw):
        runs_view = runs_raw.copy()
        runs_view["completed"] = False  # 在线模拟：训练进行中，stall/idle 规则才可触发
    snap_times = snaps["time"].to_numpy()
    ev_ts = (
        pd.to_numeric(evals_raw["timesteps"], errors="coerce").to_numpy()
        if len(evals_raw)
        else None
    )
    tb_st = (
        pd.to_numeric(tb_raw["step"], errors="coerce").to_numpy()
        if len(tb_raw)
        else None
    )

    first_r3: TriggerEvent | None = None
    first_r2: TriggerEvent | None = None
    first_stop: TriggerEvent | None = None

    # 扫描点：从 t0 起每 step 一个；末尾补扫 t1，避免末快照时刻漏扫
    points: list[float] = []
    T = t0
    while T <= t1 + 1e-6:
        points.append(T)
        T += step
    if points[-1] < t1 - 1e-6:
        points.append(t1)

    seen: dict[tuple[int, int, int], float] = {}
    for T in points:
        mask_s = snap_times <= T
        n_snap = int(mask_s.sum())
        if n_snap == 0:
            continue
        # prog：time<=T 的最新快照 timesteps（progress_at 同语义，dropna 取最后）
        ts_sel = pd.to_numeric(
            snaps.loc[mask_s, "timesteps"], errors="coerce"
        ).dropna()
        prog = float(ts_sel.iloc[-1]) if len(ts_sel) else None
        n_eval = (
            int((ev_ts <= prog).sum())
            if (prog is not None and ev_ts is not None)
            else 0
        )
        n_tb = (
            int((tb_st <= prog).sum())
            if (prog is not None and tb_st is not None)
            else 0
        )
        key = (n_snap, n_eval, n_tb)
        if key in seen:
            continue
        seen[key] = T

        view: dict[str, pd.DataFrame] = {"snapshots": snaps[mask_s]}
        if prog is not None:
            if ev_ts is not None:
                view["eval_points"] = evals_raw[ev_ts <= prog]
            if tb_st is not None:
                view["tb_points"] = tb_raw[tb_st <= prog]
        if runs_view is not None:
            view["runs"] = runs_view
        f = run_factors(task, seed, view, cfg)
        if not f:
            continue
        risks = run_risk_items(f, cfg)
        if not risks:
            continue
        level = max_level(risks)
        decision, msgs = decide(f, risks, cfg)
        factors = tuple(sorted({i.factor for i in risks}))
        ev = TriggerEvent(
            task, seed, level, decision, T, factors, tuple(msgs),
            f.get("progress_ratio"),
        )
        if first_r3 is None and level == "R3":
            first_r3 = ev
        if first_r2 is None and LEVEL_INDEX[level] >= 2:
            first_r2 = ev
        if first_stop is None and decision == "stop":
            first_stop = ev

    return [e for e in (first_r3, first_r2, first_stop) if e is not None]
# --------------------------------------------------------------------------
# 标签与软标签
# --------------------------------------------------------------------------

def ground_truth(
    runs_df: pd.DataFrame | None, reports_df: pd.DataFrame | None, task: str, seed: str
) -> str:
    """三层标签：pass / fail / unknown（unknown 不进命中率分母）。"""
    label = "unknown"
    row = _row(runs_df, task, seed)
    if row is not None:
        verdict = str(row.get("verdict") or "").strip().lower()
        completed = _as_bool(row.get("completed"))
        if completed and verdict == "pass":
            label = "pass"
        elif verdict == "fail":
            label = "fail"
    reps = _filter(reports_df, task, seed)
    if len(reps):
        succ = pd.to_numeric(reps["success"], errors="coerce")
        if (succ == 0).any():
            label = "fail"  # 验收含失败场景
    return label


def stagnation_score(pr: float | None, rpr: float | None) -> float:
    """停滞分：进度>60% 且奖励<峰值 60% 记 1，否则按差距线性。"""
    if pr is None or rpr is None:
        return 0.0
    if pr >= STALL_PROGRESS and rpr < STALL_RATIO:
        return 1.0
    lin = min(1.0, max(0.0, (STALL_RATIO - rpr) / STALL_RATIO))
    lin *= min(1.0, pr / STALL_PROGRESS)
    return round(lin, 4)


def divergence_score(f: dict[str, Any], kl_warn: float = 0.1) -> float:
    """发散分：归一化 approx_kl、value_loss 发散、std 塌缩取最大。"""
    parts: list[float] = []
    kl = f.get("approx_kl_last")
    if kl is not None:
        parts.append(min(1.0, max(0.0, float(kl) / kl_warn)))
    parts.append(1.0 if f.get("value_loss_divergent") else 0.0)
    std = f.get("std_last")
    if std is not None and float(std) < STD_COLLAPSE:
        parts.append(1.0)
    return max(parts) if parts else 0.0


def reward_rank(value: float | None, same_task_rewards: list[float]) -> float:
    """同 task 内 eval_last_reward 的百分位（0~1，高=好）。"""
    vals = [v for v in same_task_rewards if v is not None]
    if value is None or not vals:
        return 0.5
    return sum(1 for v in vals if v <= value) / len(vals)


def fail_score(
    f: dict[str, Any],
    runs_row: dict[str, Any] | None,
    same_task_rewards: list[float],
) -> float:
    """软标签：0~1 连续失败嫌疑分（仅辅助指标，不参与命中率分母）。"""
    completed = _as_bool(runs_row.get("completed")) if runs_row else False
    verdict = str(runs_row.get("verdict") or "").strip().lower() if runs_row else ""
    s = 0.2 * (0.0 if completed else 1.0)
    s += 0.3 * (1.0 if verdict == "fail" else 0.0)
    s += 0.2 * stagnation_score(f.get("progress_ratio"), f.get("reward_peak_ratio"))
    s += 0.2 * divergence_score(f)
    s += 0.1 * (1.0 - reward_rank(f.get("eval_last_reward"), same_task_rewards))
    return round(min(1.0, max(0.0, s)), 4)


def proxy_pass(
    f: dict[str, Any], runs_row: dict[str, Any] | None, same_task_rewards: list[float]
) -> bool:
    """代理 pass（辅助列，不替换硬 pass）：完成、无发散证据、reward 居同 task 前 50%。"""
    completed = _as_bool(runs_row.get("completed")) if runs_row else False
    return (
        completed
        and divergence_score(f) == 0.0
        and reward_rank(f.get("eval_last_reward"), same_task_rewards) >= 0.5
    )


# --------------------------------------------------------------------------
# 结束时间估算与节省
# --------------------------------------------------------------------------

def _task_mean_duration(tables: dict[str, pd.DataFrame], task: str) -> float | None:
    """同 task 已完成 run 的平均 duration_seconds。"""
    runs = tables.get("runs")
    if runs is None or runs.empty:
        return None
    rows = runs[runs["task"] == task]
    durs = pd.to_numeric(rows["duration_seconds"], errors="coerce")
    comp = rows["completed"].apply(_as_bool)
    d = durs[comp & durs.notna()]
    return float(d.mean()) if len(d) else None


def estimate_t_end(
    tables: dict[str, pd.DataFrame], task: str, seed: str, trigger_time: float
) -> tuple[float, str]:
    """估算 run 实际结束时间；返回 (t_end, method)，method ∈ actual/step_rate/task_mean。"""
    snaps = _filter(tables.get("snapshots"), task, seed)
    if snaps.empty:
        return trigger_time, "no_snapshot"
    t0 = float(snaps["time"].min())
    row = _row(tables.get("runs"), task, seed)
    completed = _as_bool(row.get("completed")) if row else False
    duration = _to_float(row.get("duration_seconds")) if row else None
    if completed and duration is not None and duration > 0:
        return t0 + duration, "actual"

    total = _to_float(row.get("total_steps")) if row else None
    prog = progress_at(snaps, trigger_time)
    elapsed_h = (trigger_time - t0) / 3600.0
    if total and prog and elapsed_h > 0 and prog > 0:
        expected_total_h = total / (prog / elapsed_h)
        return t0 + expected_total_h * 3600.0, "step_rate"

    mean_dur = _task_mean_duration(tables, task)
    if mean_dur is not None:
        return t0 + mean_dur, "task_mean"
    return float(snaps["time"].max()), "actual"  # 最后快照兜底


def compute_price(cfg: dict[str, Any]) -> tuple[int, float]:
    """算力定价 (gpu_count, gpu_hourly_price)；缺失时按 0 处理。"""
    c = cfg.get("compute") or {}
    return int(c.get("gpu_count") or 0), float(c.get("gpu_hourly_price") or 0.0)
# --------------------------------------------------------------------------
# 事件加工：标签、节省、成本
# --------------------------------------------------------------------------

def enrich_stop_events(
    events: list[TriggerEvent], tables: dict[str, pd.DataFrame], cfg: dict[str, Any]
) -> list[dict[str, Any]]:
    """仅保留 stop 事件并附加标签/节省/成本；事件按 run 首次 stop 去重（冷却）。"""
    runs_df = tables.get("runs")
    reports_df = tables.get("reports")
    gpu_count, gpu_price = compute_price(cfg)
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for ev in events:
        if ev.decision != "stop":
            continue
        key = (ev.task, ev.seed)
        if key in seen:
            continue
        seen.add(key)
        label = ground_truth(runs_df, reports_df, ev.task, ev.seed)
        t_end, method = estimate_t_end(tables, ev.task, ev.seed, ev.trigger_time)
        saved = max(0.0, (t_end - ev.trigger_time) / 60.0)
        f = run_factors(ev.task, ev.seed, tables, cfg)  # 全量因子（事后诊断用）
        # early_low_reward 事后归因：仅计全量窗口因子成立的 run；
        # 已含 early 但全量不成立时移除（前缀首真会被窗口内后续点推翻，如 flat_slope_v1）；
        # 全量成立但首次 stop 未含时，若窗口闭合时刻不早于 stop 则并入
        if "early_low_reward" in ev.factors and f.get("early_low_reward") is not True:
            ev.factors = tuple(x for x in ev.factors if x != "early_low_reward")
        if (
            "early_low_reward" not in ev.factors
            and f.get("early_low_reward") is True
        ):
            early_ts = _early_first_ts(tables, ev.task, ev.seed, cfg)
            if early_ts is not None and early_ts >= ev.trigger_time:
                ev.factors = tuple(sorted(ev.factors + ("early_low_reward",)))
        row = _row(runs_df, ev.task, ev.seed)
        rewards = same_task_rewards(tables, ev.task)
        fs = fail_score(f, row, rewards)
        pp = proxy_pass(f, row, rewards)
        rows.append({
            "task": ev.task,
            "seed": ev.seed,
            "level": ev.level,
            "trigger_time": ev.trigger_time,
            "factors": ";".join(ev.factors),
            "progress_ratio": ev.progress_ratio,
            "label": label,
            "fail_score": fs,
            "proxy_pass": pp,
            "t_end": round(t_end, 1),
            "t_end_method": method,
            "saved_minutes": round(saved, 1),
            "saved_yuan_compute": round(saved / 60.0 * gpu_count * gpu_price, 2),
            "messages": " | ".join(ev.messages),
        })
    rows.sort(key=lambda r: (r["task"], r["seed"]))
    return rows


def build_odds_table(
    stop_rows: list[dict[str, Any]], cfg: dict[str, Any]
) -> pd.DataFrame:
    """按 (level, factor) 展开 stop 事件聚合为赔率表。

    一个事件可命中多个因子行（触发因子集展开），trigger_count 为展开后计数。
    """
    records: list[dict[str, Any]] = []
    for r in stop_rows:
        for factor in r["factors"].split(";"):
            if not factor:
                continue
            rec = dict(r)
            rec["factor"] = factor
            records.append(rec)
    if not records:
        return pd.DataFrame(columns=[
            "level", "factor", "trigger_count", "n_pass", "n_fail", "n_unknown",
            "hit_rate", "false_kill_count", "false_kill_rate",
            "potential_false_kill_rate", "avg_saved_minutes_fail",
            "avg_saved_minutes_all", "expected_net_saving_minutes",
            "saved_yuan_compute_estimate", "t_end_method_dist",
        ])
    df = pd.DataFrame(records)
    gpu_count, gpu_price = compute_price(cfg)
    out: list[dict[str, Any]] = []
    for (level, factor), sub in df.groupby(["level", "factor"], sort=True):
        n = len(sub)
        n_pass = int((sub["label"] == "pass").sum())
        n_fail = int((sub["label"] == "fail").sum())
        n_unknown = int((sub["label"] == "unknown").sum())
        denom = n_fail + n_pass
        hit_rate = n_fail / denom if denom > 0 else float("nan")
        saved_fail = sub.loc[sub["label"] == "fail", "saved_minutes"]
        saved_all = sub["saved_minutes"]
        avg_fail = float(saved_fail.mean()) if len(saved_fail) else float("nan")
        avg_all = float(saved_all.mean()) if len(saved_all) else float("nan")
        # 误杀惩罚：avg_wasted = 被误杀（pass）run 的平均浪费分钟
        saved_pass = sub.loc[sub["label"] == "pass", "saved_minutes"]
        avg_wasted = float(saved_pass.mean()) if len(saved_pass) else 0.0
        fk_rate = n_pass / n  # 数值版本（展示用 N/A 见下）
        expected = hit_rate * avg_fail - fk_rate * avg_wasted
        if pd.isna(expected):
            expected = float("nan")
        # 零 pass：false_kill_rate 展示为 N/A
        fk_show = "N/A (no pass samples)" if n_pass == 0 else round(fk_rate, 4)
        # 代理误杀：被 stop 且 fail_score<0.3 的比例（unknown 也参与）
        pot = sub["fail_score"].apply(lambda x: _to_float(x) is not None and x < 0.3)
        pot_rate = round(float(pot.mean()), 4) if n else float("nan")
        method_dist = ";".join(
            f"{k}:{v}" for k, v in sorted(
                Counter(sub["t_end_method"]).items(), key=lambda kv: (-kv[1], kv[0])
            )
        )
        out.append({
            "level": level,
            "factor": factor,
            "trigger_count": n,
            "n_pass": n_pass,
            "n_fail": n_fail,
            "n_unknown": n_unknown,
            "hit_rate": round(hit_rate, 4) if not pd.isna(hit_rate) else float("nan"),
            "false_kill_count": n_pass,
            "false_kill_rate": fk_show,
            "potential_false_kill_rate": pot_rate,
            "avg_saved_minutes_fail": round(avg_fail, 1) if not pd.isna(avg_fail) else float("nan"),
            "avg_saved_minutes_all": round(avg_all, 1) if not pd.isna(avg_all) else float("nan"),
            "expected_net_saving_minutes": round(expected, 1) if not pd.isna(expected) else float("nan"),
            "saved_yuan_compute_estimate": round(
                avg_all / 60.0 * gpu_count * gpu_price, 2
            ) if not pd.isna(avg_all) else float("nan"),
            "t_end_method_dist": method_dist,
        })
    cols = [
        "level", "factor", "trigger_count", "n_pass", "n_fail", "n_unknown",
        "hit_rate", "false_kill_count", "false_kill_rate",
        "potential_false_kill_rate", "avg_saved_minutes_fail",
        "avg_saved_minutes_all", "expected_net_saving_minutes",
        "saved_yuan_compute_estimate", "t_end_method_dist",
    ]
    return pd.DataFrame(out, columns=cols)


def label_counts(tables: dict[str, pd.DataFrame]) -> dict[str, int]:
    """全部 runs 的三层标签计数（免责声明与数据覆盖用）。"""
    runs_df = tables.get("runs")
    reports_df = tables.get("reports")
    pairs = run_pairs(tables)
    c: Counter[str] = Counter(ground_truth(runs_df, reports_df, t, s) for t, s in pairs)
    return {"pass": c["pass"], "fail": c["fail"], "unknown": c["unknown"]}


def cost_trend(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """项目级 token 成本日趋势（不做逐 run 归因）。"""
    costs = tables.get("costs")
    if costs is None or costs.empty:
        return pd.DataFrame(columns=["date", "total_tokens", "cost_yuan"])
    g = costs.groupby("date", sort=True).agg(
        total_tokens=("total_tokens", "sum"), cost_yuan=("cost_yuan", "sum")
    ).reset_index()
    return g


def warning_line(tables: dict[str, pd.DataFrame]) -> str:
    c = label_counts(tables)
    return WARNING_LINE.format(
        n_pass=c["pass"], n_fail=c["fail"], n_unknown=c["unknown"]
    )


# --------------------------------------------------------------------------
# Markdown 报告
# --------------------------------------------------------------------------

def _fmt(x: Any) -> str:
    if x is None or (isinstance(x, float) and (pd.isna(x) or pd.isnull(x))):
        return "—"
    return str(x)


def render_markdown(
    today: str,
    tables: dict[str, pd.DataFrame],
    events: list[TriggerEvent],
    stop_rows: list[dict[str, Any]],
    odds: pd.DataFrame,
    step_minutes: int,
    cfg: dict[str, Any],
) -> str:
    c = label_counts(tables)
    lines: list[str] = []
    lines.append(f"# go2w-quant 规则止损回测（B 方案 P0）：{today}")
    lines.append("")
    lines.append(warning_line(tables))
    lines.append("")
    lines.append("## 一、数据覆盖")
    lines.append("")
    lines.append(f"- 扫描粒度：{step_minutes} 分钟；回测 run 数：{len(run_pairs(tables))}（有快照者参与重放）")
    lines.append(f"- 标签：pass {c['pass']} / fail {c['fail']} / unknown {c['unknown']}（unknown 不进命中率分母）")
    lines.append(f"- 首次 stop 事件：{len(stop_rows)}；首次 R2/R3 预警事件："
                 f"{sum(1 for e in events if e.decision != 'stop')}")
    lines.append("")
    lines.append("## 二、止损赔率表（按 级别×因子 展开）")
    lines.append("")
    lines.append("| 级别 | 触发因子 | 触发数 | pass | fail | unknown | 命中率 | 误杀数 | 误杀率 | 代理误杀率 | 平均节省min(fail) | 平均节省min(全部) | 期望净节省min | 单次节省估算(元) | t_end 方法分布 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for _, r in odds.iterrows():
        lines.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            r["level"], r["factor"], r["trigger_count"], r["n_pass"], r["n_fail"],
            r["n_unknown"], _fmt(r["hit_rate"]), r["false_kill_count"],
            _fmt(r["false_kill_rate"]), _fmt(r["potential_false_kill_rate"]),
            _fmt(r["avg_saved_minutes_fail"]), _fmt(r["avg_saved_minutes_all"]),
            _fmt(r["expected_net_saving_minutes"]), _fmt(r["saved_yuan_compute_estimate"]),
            r["t_end_method_dist"],
        ))
    if odds.empty:
        lines.append("| （无 stop 事件） | | | | | | | | | | | | | | |")
    lines.append("")
    lines.append("## 三、逐 run 首次止损明细")
    lines.append("")
    lines.append("| task | seed | 级别 | 触发因子 | 标签 | fail_score | 代理pass | 节省min | 节省估算(元) | t_end 方法 | 触发证据 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in stop_rows:
        lines.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            r["task"], r["seed"], r["level"], r["factors"], r["label"],
            r["fail_score"], r["proxy_pass"], r["saved_minutes"],
            r["saved_yuan_compute"], r["t_end_method"],
            (r["messages"] or "")[:160],
        ))
    if not stop_rows:
        lines.append("| （无 stop 事件） | | | | | | | | | | |")
    lines.append("")
    lines.append("## 四、首次 R2/R3 预警统计（不产生止损）")
    lines.append("")
    warn_counter: Counter[tuple[str, str]] = Counter()
    for e in events:
        if e.decision != "stop":
            for factor in e.factors:
                warn_counter[(e.level, factor)] += 1
    lines.append("| 级别 | 触发因子 | 预警 run 数 |")
    lines.append("|---|---|---|")
    for (level, factor), n in sorted(warn_counter.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| {level} | {factor} | {n} |")
    if not warn_counter:
        lines.append("| （无预警） | | |")
    lines.append("")
    lines.append("## 五、项目级 token 成本趋势（不逐 run 归因）")
    lines.append("")
    trend = cost_trend(tables)
    if len(trend):
        lines.append("| 日期 | 总 token | 成本(元) |")
        lines.append("|---|---|---|")
        for _, r in trend.iterrows():
            lines.append(f"| {r['date']} | {int(r['total_tokens'])} | {r['cost_yuan']} |")
    else:
        lines.append("（无成本数据）")
    lines.append("")
    lines.append("## 六、方法与假设")
    lines.append("")
    lines.append("- **标签**：pass=completed 且 verdict=pass；fail=verdict=fail 或验收含失败场景；其余 unknown。")
    lines.append("- **t_end**：completed run 用实际结束时间；incomplete 用 step_rate 外推（total_steps/(触发时进度/触发时已耗时)），缺失回退同 task 平均时长。")
    gpu_count, gpu_price = compute_price(cfg)
    if gpu_price > 0:
        lines.append("- **saved_yuan_compute** = 节省小时 × gpu_count × gpu_hourly_price（估算，config.compute 可调）；token 成本不做逐 run 归因。")
    else:
        lines.append("- **saved_yuan_compute** = 0（config.compute.gpu_hourly_price=0，仅输出节省分钟）；token 成本不做逐 run 归因。")
    lines.append("- **期望净节省** = hit_rate × 平均节省(fail) − false_kill_rate × 平均浪费(pass)。")
    lines.append("- **fail_score** 为软标签代理（0~1），仅辅助判断 potential_false_kill_rate，不参与命中率分母。")
    lines.append("- 重放为在线模拟：reports 排除、runs.completed 强制 False；每 run 每规则只记首次触发。")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def run(
    cfg: dict[str, Any],
    tables: dict[str, pd.DataFrame] | None = None,
    today: str | None = None,
    out: str | None = None,
    step_minutes: int = 10,
) -> tuple[pd.DataFrame, list[TriggerEvent], str]:
    """规则止损回测全流程：重放 -> 富化 -> 赔率表 -> 写 csv/md -> 打印摘要。

    返回 (odds_df, stop_rows, summary_md)；供 CLI 与 run_pipeline 复用。
    注：重放按"在线视野"逐截面重算因子，不做缓存（口径要求）。
    """
    if tables is None:
        tables = load_tables(cfg)
    today = today or _Date.today().isoformat()
    step_minutes = max(1, int(step_minutes))

    events: list[TriggerEvent] = []
    for task, seed in run_pairs(tables):
        events.extend(replay_run(tables, task, seed, cfg, step_minutes=step_minutes))
    stop_rows = enrich_stop_events(events, tables, cfg)
    odds = build_odds_table(stop_rows, cfg)

    out_dir = pathlib.Path(out) if out else pathlib.Path(cfg["modeling_dir"])
    if not out_dir.is_absolute():
        out_dir = PROJECT_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"rule_backtest_{today}.csv"
    md_path = out_dir / f"rule_backtest_{today}.md"
    odds.to_csv(csv_path, index=False, encoding="utf-8-sig")
    summary_md = render_markdown(
        today, tables, events, stop_rows, odds, step_minutes, cfg
    )
    md_path.write_text(summary_md, encoding="utf-8")

    c = label_counts(tables)
    print(f"[backtest_rules] 规则止损回测 {today}")
    print(f"  runs={len(run_pairs(tables))}  pass={c['pass']}  fail={c['fail']}  unknown={c['unknown']}")
    print(f"  stop 事件={len(stop_rows)}  R2/R3 预警={sum(1 for e in events if e.decision != 'stop')}")
    if len(odds):
        top = odds.sort_values("trigger_count", ascending=False).iloc[0]
        print(f"  最高频触发: {top['level']}/{top['factor']} × {top['trigger_count']}")
        top_save = odds.dropna(subset=["expected_net_saving_minutes"]).sort_values(
            "expected_net_saving_minutes", ascending=False
        )
        if len(top_save):
            r = top_save.iloc[0]
            print(f"  期望净节省最高: {r['level']}/{r['factor']} = {r['expected_net_saving_minutes']} min")
    print(f"  报告: {md_path}")
    print(f"  CSV: {csv_path}")
    return odds, stop_rows, summary_md


def main() -> None:
    parser = argparse.ArgumentParser(description="go2w-quant 规则止损回测（B 方案 P0）")
    parser.add_argument("--config", default=None)
    parser.add_argument("--today", default=None, help="报告日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--out", default=None, help="覆盖输出目录（默认 config.modeling_dir）")
    parser.add_argument("--step-minutes", type=int, default=10, help="扫描粒度分钟（默认 10）")
    args = parser.parse_args()

    cfg = load_config(args.config)
    run(cfg, today=args.today, out=args.out, step_minutes=args.step_minutes)


if __name__ == "__main__":
    main()
