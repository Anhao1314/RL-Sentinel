"""Static research report in a Claude Code-inspired visual language.

The report is deliberately self-contained: no external scripts, fonts, or
images.  It consumes a versioned research dataset and writes a new report
directory; it never mutates the dataset or historical reports.
"""

from __future__ import annotations

import hashlib
import html
import json
import math
import pathlib
from datetime import datetime, timezone
from typing import Any, Iterable

import pandas as pd

from research.backtest import run_backtest
from research.dataset import read_dataset
from research.factors import summarize_factors

PALETTE = ("#d97757", "#2f7d5b", "#5b6fb0", "#b9842f", "#8a5a8f", "#4f8a8b")


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "N/A"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int,)):
        return str(value)
    if isinstance(value, float):
        if abs(value) >= 1000:
            return f"{value:,.0f}"
        return f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return html.escape(str(value))


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    clean = frame.astype(object).where(pd.notna(frame), None)
    return clean.to_dict(orient="records")


def _pct(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if not math.isfinite(number):
        return "N/A"
    return f"{number * 100:.0f}%"


def _badge(value: Any, kind: str | None = None) -> str:
    text = str(value if value is not None else "unknown").lower()
    css = kind or (
        "pass" if text == "pass" else
        "fail" if text == "fail" else
        "candidate" if "candidate" in text else
        "blocked" if text == "blocked" else
        "unknown"
    )
    label = {
        "pass": "PASS", "fail": "FAIL", "unknown": "UNKNOWN",
        "blocked": "BLOCKED", "candidate": "CANDIDATE",
    }.get(css, text.upper())
    return f'<span class="badge {css}">{html.escape(label)}</span>'


def _table(headers: Iterable[str], rows: Iterable[Iterable[str]],
           caption: str | None = None, class_name: str = "") -> str:
    head = "".join(f"<th>{html.escape(str(item))}</th>" for item in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    cap = f"<caption>{html.escape(caption)}</caption>" if caption else ""
    return (
        f'<div class="table-wrap"><table class="{class_name}">{cap}'
        f"<thead><tr>{head}</tr></thead><tbody>{body or '<tr><td colspan=\"99\" class=\"empty\">No rows</td></tr>'}</tbody></table></div>"
    )


def _kpi(label: str, value: str, hint: str = "", tone: str = "") -> str:
    return (
        f'<div class="kpi {tone}"><div class="kpi-label">{html.escape(label)}</div>'
        f'<div class="kpi-value">{value}</div>'
        f'<div class="kpi-hint">{html.escape(hint)}</div></div>'
    )


def _coverage_bar(value: Any) -> str:
    try:
        number = max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return '<span class="muted">N/A</span>'
    return (
        f'<span class="coverage"><span style="width:{number*100:.0f}%"></span></span>'
        f'<span class="mono small">{number*100:.0f}%</span>'
    )


def _eval_series(
    eval_points: pd.DataFrame, runs: pd.DataFrame
) -> tuple[list[dict[str, Any]], str, str, bool]:
    """Observed curves are preferred; legacy curves are a labelled fallback."""
    if eval_points.empty:
        return [], "success_rate", "none", False
    observed_ids = set(runs.loc[runs["cohort"] == "observed", "run_id"])
    observed = eval_points[eval_points["run_id"].isin(observed_ids)].copy()
    if not observed.empty:
        frame = observed
        cohort_label = "observed"
        limit = 8
        metric = (
            "success_rate"
            if observed.get("success_rate", pd.Series(dtype=float)).notna().any()
            else "mean_reward"
        )
        normalized = False
    else:
        legacy_ids = set(runs.loc[runs["cohort"] == "legacy", "run_id"])
        frame = eval_points[eval_points["run_id"].isin(legacy_ids)].copy()
        cohort_label = "legacy"
        limit = 4
        metric = "mean_reward"
        normalized = True
    if frame.empty:
        return [], metric, "none", normalized
    series: list[dict[str, Any]] = []
    run_ids = sorted(frame["run_id"].unique())[:limit]
    for index, run_id in enumerate(run_ids):
        group = frame[frame["run_id"] == run_id].sort_values("timesteps")
        points = [
            (float(row["timesteps"]), float(row[metric]))
            for _, row in group.iterrows()
            if pd.notna(row.get("timesteps")) and pd.notna(row.get(metric))
        ]
        if points:
            series.append({"run_id": run_id, "points": points,
                           "color": PALETTE[index % len(PALETTE)]})
    return series, metric, cohort_label, normalized


def _sparkline(series: list[dict[str, Any]], metric: str,
               normalized: bool = False) -> str:
    if not series:
        return '<div class="empty-chart">No eval points yet.</div>'
    width, height = 920, 260
    left, right, top, bottom = 54, 18, 18, 38
    max_x = max(point[0] for item in series for point in item["points"]) or 1.0
    display: list[dict[str, Any]] = []
    for item in series:
        points = list(item["points"])
        if normalized:
            values = [point[1] for point in points]
            low, high = min(values), max(values)
            if high == low:
                high = low + 1.0
            points = [(x, (y - low) / (high - low)) for x, y in points]
        display.append({**item, "points": points})
    values = [point[1] for item in display for point in item["points"]]
    if metric == "success_rate" and not normalized:
        min_y, max_y = 0.0, 1.0
    elif normalized:
        min_y, max_y = 0.0, 1.0
    else:
        min_y, max_y = min(values), max(values)
        if min_y == max_y:
            min_y, max_y = min_y - 1.0, max_y + 1.0
    def sx(x: float) -> float:
        return left + (x / max_x) * (width - left - right)
    def sy(y: float) -> float:
        return top + (1.0 - (y - min_y) / (max_y - min_y)) * (height - top - bottom)
    grid = []
    for fraction in (0.0, 0.5, 1.0):
        y = top + fraction * (height - top - bottom)
        grid.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}" class="grid"/>')
    for item in display:
        points = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in item["points"])
        grid.append(
            f'<polyline points="{points}" fill="none" stroke="{item["color"]}" '
            f'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>'
        )
    legend = "".join(
        f'<span class="legend"><i style="background:{item["color"]}"></i>'
        f'{html.escape(item["run_id"])}</span>'
        for item in display
    )
    y_label = (
        "normalized reward" if normalized
        else "success rate" if metric == "success_rate"
        else "mean reward"
    )
    return (
        f'<div class="chart"><svg viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Eval {y_label} by training step">'
        f'{"".join(grid)}'
        f'<text x="{left}" y="{height-12}" class="axis">0</text>'
        f'<text x="{width-right}" y="{height-12}" text-anchor="end" class="axis">{int(max_x):,}</text>'
        f'<text x="{left}" y="{top+10}" class="axis">{_fmt(max_y, 2)}</text>'
        f'<text x="{left}" y="{height-bottom}" class="axis">{_fmt(min_y, 2)}</text>'
        f'</svg><div class="legend-row">{legend}</div></div>'
    )


def _factor_rows(summary: pd.DataFrame, checkpoint: float) -> list[list[str]]:
    if summary.empty or "checkpoint" not in summary.columns:
        return []
    frame = summary[summary["checkpoint"] == checkpoint].copy()
    if frame.empty:
        return []
    frame["abs_ic"] = pd.to_numeric(frame["ic_pass"], errors="coerce").abs()
    frame = frame.sort_values(["abs_ic", "coverage"], ascending=[False, False])
    rows: list[list[str]] = []
    for _, row in frame.iterrows():
        direction = {1: "higher better", -1: "higher risk", 0: "neutral"}.get(
            int(row["direction"]), "neutral"
        )
        rows.append([
            f'<span class="mono">{html.escape(str(row["factor"]))}</span>',
            html.escape(str(row["category"])),
            _coverage_bar(row["coverage"]),
            _fmt(row["ic_pass"], 3),
            _fmt(row["ic_success"], 3),
            _fmt(row["n_labeled"]),
            html.escape(direction),
        ])
    return rows


def _backtest_rows(summary: pd.DataFrame) -> list[list[str]]:
    if summary.empty:
        return []
    ordered = summary.sort_values(["policy", "checkpoint"])
    rows: list[list[str]] = []
    for _, row in ordered.iterrows():
        rows.append([
            f'<span class="mono">{html.escape(str(row["policy"]))}</span>',
            _pct(row["checkpoint"]),
            _fmt(row["n_pass"]),
            _fmt(row["n_fail"]),
            _fmt(row["true_stops"]),
            _fmt(row["false_stops"]),
            _fmt(row["pass_kill_rate"], 3),
            _fmt(row["fail_recall"], 3),
            _fmt(row["decision_coverage"], 3),
            _badge(row["readiness"]),
        ])
    return rows


def _decision_rows(decisions: pd.DataFrame) -> list[list[str]]:
    if decisions.empty:
        return []
    frame = decisions.sort_values(["checkpoint", "policy", "run_id"])
    rows: list[list[str]] = []
    for _, row in frame.iterrows():
        rows.append([
            f'<span class="mono">{html.escape(str(row["run_id"]))}</span>',
            _pct(row["checkpoint"]),
            html.escape(str(row["policy"])),
            f'<span class="decision {html.escape(str(row["decision"]))}">{html.escape(str(row["decision"]))}</span>',
            _badge(row["label"]),
            html.escape(str(row["outcome"])),
            html.escape(str(row["reason"]))[:160],
            f'<span class="hash">{html.escape(str(row["input_sha256"])[:16])}</span>',
        ])
    return rows


def render_report(dataset_dir: pathlib.Path, out_dir: pathlib.Path) -> dict[str, Any]:
    manifest, tables = read_dataset(dataset_dir)
    runs = tables["runs"]
    panel = tables["factor_panel"]
    eval_points = tables["eval_points"]
    observed = runs[runs["cohort"] == "observed"].copy()
    legacy = runs[runs["cohort"] == "legacy"].copy()
    observed_panel = panel[panel["cohort"] == "observed"]
    legacy_panel = panel[panel["cohort"] == "legacy"]

    decisions_obs, summary_obs = run_backtest(panel, runs, cohort="observed")
    decisions_legacy, summary_legacy = run_backtest(panel, runs, cohort="legacy")
    factor_obs = summarize_factors(observed_panel, observed)

    label_counts = observed["label"].value_counts().to_dict()
    n_pass = int(label_counts.get("pass", 0))
    n_fail = int(label_counts.get("fail", 0))
    n_unknown = int(label_counts.get("unknown", 0))
    readiness = "blocked"
    if n_pass >= 5 and n_fail >= 10:
        readiness = "candidate_for_external_validation"
    series, metric, chart_scope, normalized = _eval_series(eval_points, runs)
    chart = _sparkline(series, metric, normalized)
    if chart_scope == "legacy":
        chart_note = ("Observed cohort has no eval points in this snapshot; "
                      "showing up to 4 legacy offline curves, normalized per run "
                      "(absolute scales are not comparable).")
    elif chart_scope == "observed":
        chart_note = "Observed cohort eval curves; offline aggregates, not event-level availability."
    else:
        chart_note = "No eval points in this snapshot."

    run_rows: list[list[str]] = []
    for _, row in observed.sort_values("run_id").iterrows():
        run_rows.append([
            f'<span class="mono">{html.escape(str(row["run_id"]))}</span>',
            html.escape(str(row["task"])),
            html.escape(str(row["seed"])),
            _badge(row["label"]),
            _pct(row["final_success_rate"]),
            _fmt(row["event_count"]),
            _fmt(row["eval_count"]),
            "yes" if bool(row["strict_asof"]) else "no",
            f'<span class="hash">{html.escape(str(row["source_commit"] or "unknown")[:12])}</span>',
        ])
    legacy_rows = [
        [
            f'<span class="mono">{html.escape(str(row["run_id"]))}</span>',
            html.escape(str(row["task"])),
            html.escape(str(row["seed"])),
            _badge(row["label"]),
            _pct(row["final_success_rate"]),
        ]
        for _, row in legacy.sort_values("run_id").iterrows()
    ]

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    version = html.escape(str(manifest["dataset_version"]))
    code = manifest.get("code") or {}
    commit = html.escape(str(code.get("commit") or "unknown")[:12])
    dirty = "dirty" if code.get("dirty") else "clean"

    css = """
:root{
  --bg:#f7f5ef;--surface:#fffdf8;--surface-2:#f1eee6;--ink:#1f1e1b;
  --muted:#6d685e;--line:#e4ded2;--clay:#d97757;--clay-soft:#f4e4db;
  --green:#2f7d5b;--green-soft:#e3f0e8;--red:#b24a3f;--red-soft:#f6e3df;
  --amber:#b07b2c;--amber-soft:#f6ecd9;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  --serif:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,system-ui,sans-serif;
}
@media (prefers-color-scheme:dark){
  :root{--bg:#191816;--surface:#211f1c;--surface-2:#2a2723;--ink:#f2ede4;
    --muted:#aaa397;--line:#39352f;--clay:#e08a6d;--clay-soft:#3a2a24;
    --green:#7fc3a1;--green-soft:#23352c;--red:#e08b7f;--red-soft:#3a2521;
    --amber:#e0b36a;--amber-soft:#3a3022;}
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 var(--sans);
  -webkit-font-smoothing:antialiased}
::selection{background:var(--clay-soft);color:var(--ink)}
a{color:var(--clay);text-decoration:none}
a:hover{text-decoration:underline}
.wrap{max-width:1180px;margin:0 auto;padding:0 28px 72px}
.topbar{position:sticky;top:0;z-index:5;background:color-mix(in srgb,var(--bg) 88%,transparent);
  backdrop-filter:blur(12px);border-bottom:1px solid var(--line)}
.topbar-inner{max-width:1180px;margin:0 auto;padding:14px 28px;display:flex;
  align-items:center;gap:14px}
.mark{width:30px;height:30px;border-radius:9px;background:var(--clay);color:#fff;
  display:grid;place-items:center;font-size:17px;line-height:1;flex:0 0 auto}
.brand{font:600 15px/1.2 var(--sans);letter-spacing:.01em}
.brand small{display:block;color:var(--muted);font:500 10px/1.4 var(--mono);
  letter-spacing:.14em;text-transform:uppercase;margin-top:2px}
.nav{margin-left:auto;display:flex;gap:18px;flex-wrap:wrap}
.nav a{color:var(--muted);font:500 12px/1 var(--mono);letter-spacing:.02em}
.nav a:hover{color:var(--ink)}
.hero{padding:54px 0 28px;display:grid;grid-template-columns:1.4fr .6fr;gap:28px;align-items:end}
.kicker{font:600 11px/1.4 var(--mono);letter-spacing:.18em;text-transform:uppercase;
  color:var(--clay);margin-bottom:14px}
h1{font:500 46px/1.08 var(--serif);letter-spacing:-.02em;margin:0 0 14px}
.hero p{color:var(--muted);max-width:640px;margin:0}
.hero-meta{justify-self:end;text-align:right;color:var(--muted);
  font:12px/1.8 var(--mono)}
.hero-meta b{color:var(--ink);font-weight:500}
.grid{display:grid;gap:14px}
.kpis{grid-template-columns:repeat(6,1fr);margin:22px 0 8px}
.kpi{background:var(--surface);border:1px solid var(--line);border-radius:14px;
  padding:16px 16px 14px;min-width:0}
.kpi-label{font:600 10px/1.4 var(--mono);letter-spacing:.12em;text-transform:uppercase;
  color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kpi-value{font:500 28px/1.2 var(--serif);margin:8px 0 2px;
  font-variant-numeric:tabular-nums}
.kpi-hint{font:11px/1.4 var(--mono);color:var(--muted)}
.kpi.good .kpi-value{color:var(--green)} .kpi.bad .kpi-value{color:var(--red)}
.kpi.warn .kpi-value{color:var(--amber)}
.card{background:var(--surface);border:1px solid var(--line);border-radius:16px;
  padding:24px;margin-top:18px}
.card-head{display:flex;align-items:flex-start;gap:16px;margin-bottom:18px}
.card-head h2{font:500 24px/1.2 var(--serif);margin:0}
.card-head p{color:var(--muted);margin:4px 0 0;font-size:13px}
.card-head .spacer{margin-left:auto}
.note{border-left:3px solid var(--clay);background:var(--clay-soft);
  border-radius:0 10px 10px 0;padding:14px 16px;color:var(--ink);font-size:13px}
.note strong{font-weight:600}
.muted{color:var(--muted)}
.small{font-size:11px}
.mono{font-family:var(--mono)}
.hash{font-family:var(--mono);font-size:11px;color:var(--muted)}
.table-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:12px}
table{width:100%;border-collapse:collapse;font-size:12.5px}
caption{text-align:left;color:var(--muted);font:11px/1.4 var(--mono);
  padding:10px 12px;background:var(--surface-2)}
th{position:sticky;top:0;background:var(--surface-2);color:var(--muted);
  font:600 10px/1.4 var(--mono);letter-spacing:.08em;text-transform:uppercase;
  text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);
  white-space:nowrap}
td{padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:top}
tbody tr:last-child td{border-bottom:none}
tbody tr:hover td{background:color-mix(in srgb,var(--clay-soft) 45%,transparent)}
td.empty{text-align:center;color:var(--muted);padding:24px}
.badge{display:inline-flex;align-items:center;gap:5px;border-radius:999px;
  padding:2px 8px;font:600 9.5px/1.5 var(--mono);letter-spacing:.08em;
  border:1px solid transparent;white-space:nowrap}
.badge.pass{color:var(--green);background:var(--green-soft);border-color:color-mix(in srgb,var(--green) 30%,transparent)}
.badge.fail{color:var(--red);background:var(--red-soft);border-color:color-mix(in srgb,var(--red) 30%,transparent)}
.badge.unknown{color:var(--muted);background:var(--surface-2);border-color:var(--line)}
.badge.blocked{color:var(--amber);background:var(--amber-soft);border-color:color-mix(in srgb,var(--amber) 30%,transparent)}
.badge.candidate{color:var(--green);background:var(--green-soft);border-color:color-mix(in srgb,var(--green) 30%,transparent)}
.coverage{display:inline-block;width:68px;height:6px;border-radius:3px;
  background:var(--surface-2);overflow:hidden;vertical-align:middle;margin-right:7px}
.coverage span{display:block;height:100%;background:var(--clay);border-radius:3px}
.decision{font-family:var(--mono);font-size:11px;padding:2px 6px;border-radius:6px;
  border:1px solid var(--line)}
.decision.stop{color:var(--red);background:var(--red-soft)}
.decision.continue{color:var(--green);background:var(--green-soft)}
.decision.abstain{color:var(--muted);background:var(--surface-2)}
.chart{margin-top:8px}
.chart svg{width:100%;height:auto;display:block}
.grid{stroke:var(--line);stroke-width:1;stroke-dasharray:3 5}
.axis{fill:var(--muted);font:10px var(--mono)}
.legend-row{display:flex;gap:16px;flex-wrap:wrap;margin-top:10px}
.legend{font:11px var(--mono);color:var(--muted)}
.legend i{display:inline-block;width:9px;height:9px;border-radius:3px;
  margin-right:6px;vertical-align:middle}
.empty-chart{padding:30px;text-align:center;color:var(--muted);
  background:var(--surface-2);border-radius:12px;font:12px var(--mono)}
.two{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.list{margin:0;padding-left:18px;color:var(--muted);font-size:13px}
.list li{margin:6px 0}
.footer{margin-top:36px;border-top:1px solid var(--line);padding-top:18px;
  color:var(--muted);font:11px/1.7 var(--mono)}
details{margin-top:14px}
summary{cursor:pointer;font:600 12px var(--mono);color:var(--muted);
  padding:10px 0}
code{font-family:var(--mono);font-size:.92em;background:var(--surface-2);
  padding:1px 5px;border-radius:5px}
@media(max-width:900px){.kpis{grid-template-columns:repeat(3,1fr)}
  .hero{grid-template-columns:1fr}.hero-meta{justify-self:start;text-align:left}
  .two{grid-template-columns:1fr}}
@media(max-width:560px){.kpis{grid-template-columns:repeat(2,1fr)}
  h1{font-size:34px}.wrap{padding:0 18px 48px}.topbar-inner{padding:12px 18px}
  .nav{display:none}}
@media print{body{background:#fff}.topbar{position:static}.card{break-inside:avoid}}
"""

    html_doc = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>RL-Sentinel Research · {version}</title>
<style>{css}</style></head><body>
<div class="topbar"><div class="topbar-inner">
  <div class="mark">✳</div>
  <div class="brand">RL-Sentinel Research<small>Training reliability · quant research</small></div>
  <nav class="nav"><a href="#overview">Overview</a><a href="#runs">Runs</a>
    <a href="#factors">Factors</a><a href="#backtest">Backtest</a>
    <a href="#evidence">Evidence</a></nav>
</div></div>
<div class="wrap">
  <header class="hero" id="overview">
    <div><div class="kicker">Research note · {html.escape(metric.replace('_',' '))}</div>
      <h1>训练可靠性，<br>像量化研究一样被测量。</h1>
      <p>用版本化数据集、因子面板和 as-of 回测回答：训练早期能否识别失败，
      以及早停策略的收益与误杀代价。</p></div>
    <div class="hero-meta">
      <div>dataset <b>{version}</b></div>
      <div>factor set <b>{html.escape(manifest.get('factor_version',''))}</b></div>
      <div>code <b>{commit}</b> · {dirty}</div>
      <div>generated <b>{generated}</b></div>
    </div>
  </header>
  <p class="note"><strong>边界：</strong>面板只做离线研究与建议，不自动停止训练。
  观测 cohort 与 legacy/offline cohort 分开；label 只用于评分，不进入因子输入。
  样本门槛通过也不等于生产有效。</p>
  <section class="grid kpis">
    {_kpi("Observed runs", _fmt(len(observed)), "as-of cohort")}
    {_kpi("Labeled", _fmt(n_pass + n_fail), "pass + fail")}
    {_kpi("Pass", _fmt(n_pass), "successful runs", "good")}
    {_kpi("Fail", _fmt(n_fail), "failed runs", "bad")}
    {_kpi("Unknown", _fmt(n_unknown), "not scored", "warn")}
    {_kpi("Legacy", _fmt(len(legacy)), "offline cohort")}
  </section>
  <section class="card">
    <div class="card-head"><div><h2>Readiness</h2>
      <p>操作门槛：pass ≥ 5 且 fail ≥ 10。当前 observed cohort 是否足以进入策略讨论？</p></div>
      <div class="spacer">{_badge(readiness)}</div></div>
    <div class="note">
      <strong>{html.escape(readiness)}</strong> — 当前 observed cohort 有
      {n_pass} pass / {n_fail} fail / {n_unknown} unknown。
      这不是统计充分性声明；legacy cohort 不计入该门槛。
    </div>
  </section>

  <section class="card" id="runs">
    <div class="card-head"><div><h2>Run Explorer</h2>
      <p>观测 cohort：事件数、评估点、严格 as-of 状态与最终标签。</p></div></div>
    {_table(["run_id","task","seed","label","success","events","eval pts","strict as-of","commit"],
            run_rows, "Observed runs")}
  </section>

  <section class="card">
    <div class="card-head"><div><h2>Learning signal</h2>
      <p>{html.escape(chart_note)}</p></div></div>
    {chart}
  </section>

  <section class="card" id="factors">
    <div class="card-head"><div><h2>Factor Lab · 70% checkpoint</h2>
      <p>覆盖率、与 pass 的 Spearman IC、与最终 success_rate 的 IC。IC 为 N/A 表示样本不足或因子无变化。</p></div></div>
    {_table(["factor","category","coverage","IC pass","IC success","n labeled","direction"],
            _factor_rows(factor_obs, 0.7), "Observed cohort · checkpoint 0.7")}
    <details><summary>Why some factors are N/A</summary>
      <ul class="list">
        <li>IC 需要至少 5 个有标签样本，且因子取值不能为常数。</li>
        <li>legacy 数据没有 KL/progress 事件，因此这些因子在 legacy cohort 中覆盖率为 0。</li>
        <li>legacy 没有 event-level available_at，不参与严格 as-of 结论。</li>
      </ul>
    </details>
  </section>

  <section class="card" id="backtest">
    <div class="card-head"><div><h2>Backtest · observed cohort</h2>
      <p>每个 checkpoint 只用当时可见的因子；label 只用于评分。</p></div></div>
    {_table(["policy","checkpoint","pass","fail","true stops","false stops",
             "pass kill","fail recall","coverage","readiness"],
            _backtest_rows(summary_obs), "Rules-v2 vs constant baselines")}
    <h3 class="mono" style="font-size:12px;color:var(--muted);margin:22px 0 8px">RULE DECISIONS</h3>
    {_table(["run","checkpoint","policy","decision","label","outcome","reason","input"],
            _decision_rows(decisions_obs), "Decision audit trail")}
  </section>

  <section class="card">
    <details><summary>Legacy / offline cohort ({len(legacy)} runs) — not strict as-of</summary>
      <p class="note">这些 run 来自旧 CSV，没有事件级 available_at。它们只用于历史因子研究，
      不进入 observed cohort 的门槛或严格回放结论。</p>
      {_table(["run","task","seed","label","success"], legacy_rows, "Legacy runs")}
      {_table(["policy","checkpoint","pass","fail","true stops","false stops",
               "pass kill","fail recall","coverage","readiness"],
              _backtest_rows(summary_legacy), "Legacy offline backtest")}
    </details>
  </section>

  <section class="card" id="evidence">
    <div class="card-head"><div><h2>Evidence &amp; limits</h2>
      <p>研究结果必须和数据集版本、因子版本、代码提交一起阅读。</p></div></div>
    <div class="two">
      <div><h3 class="mono" style="font-size:12px;color:var(--muted)">DATASET</h3>
        <ul class="list">
          <li>version <code>{version}</code></li>
          <li>factor set <code>{html.escape(manifest.get('factor_version',''))}</code></li>
          <li>observed {len(observed)} · legacy {len(legacy)}</li>
          <li>tables: {html.escape(', '.join(manifest.get('tables', {})))}</li>
        </ul></div>
      <div><h3 class="mono" style="font-size:12px;color:var(--muted)">CLAIMS</h3>
        <ul class="list">
          <li>不声称生产级早停或真实算力节省。</li>
          <li>不把 legacy 数据伪装成严格 as-of 证据。</li>
          <li>不把 IC 或通过门槛等同于因果效应。</li>
          <li>不自动执行 stop/tune/resize。</li>
        </ul></div>
    </div>
    <details><summary>Manifest and limitations</summary>
      <pre class="mono small" style="white-space:pre-wrap">{html.escape(json.dumps(manifest, ensure_ascii=False, indent=2))}</pre>
    </details>
  </section>
  <div class="footer">RL-Sentinel Research · dataset {version} · generated by research/report.py<br>
  no external scripts · labels are targets, not inputs · recommendations only</div>
</div></body></html>"""

    out_dir = out_dir.resolve()
    if out_dir.exists():
        raise FileExistsError(out_dir)
    out_dir.mkdir(parents=True)
    report_path = out_dir / "report.html"
    report_path.write_text(html_doc, encoding="utf-8")
    results = {
        "dataset_version": manifest["dataset_version"],
        "readiness": readiness,
        "observed": {
            "runs": len(observed), "pass": n_pass, "fail": n_fail,
            "unknown": n_unknown,
        },
        "backtest": _records(summary_obs),
        "legacy_backtest": _records(summary_legacy),
        "factors": _records(factor_obs),
        "generated_at": generated,
    }
    results_path = out_dir / "results.json"
    results_path.write_text(json.dumps(results, ensure_ascii=False, indent=2,
                                       allow_nan=False) + "\n", encoding="utf-8")
    manifest_record = {
        "schema_version": "research-report-v1",
        "created_at": generated,
        "dataset_version": manifest["dataset_version"],
        "factor_version": manifest.get("factor_version"),
        "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "results_sha256": hashlib.sha256(results_path.read_bytes()).hexdigest(),
        "readiness": readiness,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest_record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_record
