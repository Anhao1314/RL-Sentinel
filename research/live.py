"""Live training page for the research dashboard.

A live snapshot is read-only and is never merged into the versioned research
dataset.  The page refreshes itself, while the static report remains the
evidence artifact.
"""

from __future__ import annotations

import html
import json
import pathlib
from datetime import datetime, timezone
from typing import Any


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return html.escape(str(value))
    if number != number:
        return "N/A"
    return f"{number:,.{digits}f}"


def _pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.0f}%"
    except (TypeError, ValueError):
        return "N/A"


def _status_class(status: str) -> str:
    text = status.lower()
    if text in ("finished", "completed"):
        return "good"
    if text in ("failed", "stopped"):
        return "bad"
    if text in ("evaluating", "finalizing"):
        return "warn"
    return "live"


def _badge(value: str, css: str | None = None) -> str:
    cls = css or (
        "pass" if value.lower() == "pass" else
        "fail" if value.lower() == "fail" else
        "blocked" if value.lower() in ("evaluating", "finalizing") else
        "live" if value.lower() in ("running", "launched") else "unknown"
    )
    return f'<span class="badge {cls}">{html.escape(value.upper())}</span>'


def render_live(
    snapshot_path: pathlib.Path,
    out_dir: pathlib.Path,
    *,
    report_url: str | None = None,
    refresh: int = 15,
) -> dict[str, Any]:
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    status = str(snapshot.get("status", "unknown"))
    training_complete = bool(snapshot.get("training_complete"))
    progress = float(snapshot.get("progress") or 0.0)
    progress = max(0.0, min(1.0, progress))
    event_progress = snapshot.get("event_progress")
    progress_title = "Curriculum progress" if training_complete else "Budget progress"
    label = snapshot.get("label") or {}
    latest = snapshot.get("latest_eval") or {}
    stage_count = int(snapshot.get("stage_count") or 0)
    stages_completed = int(snapshot.get("stages_completed") or 0)
    current_stage = str(snapshot.get("current_stage") or "—")
    source = snapshot.get("source") or {}
    recent = snapshot.get("recent_evals") or []

    stage_pills = []
    for index in range(1, stage_count + 1):
        name = f"stage{index}"
        if index <= stages_completed:
            cls, text = "done", "done"
        elif name == current_stage and status not in ("finished", "failed", "stopped"):
            cls, text = "current", "running"
        else:
            cls, text = "pending", "pending"
        stage_pills.append(
            f'<span class="stage {cls}"><b>{name}</b><small>{text}</small></span>'
        )

    eval_rows = "".join(
        "<tr>"
        f"<td class='mono'>{html.escape(str(row.get('stage','')))}</td>"
        f"<td class='mono'>{_fmt(row.get('timesteps'), 0)}</td>"
        f"<td>{_pct(row.get('success_rate'))}</td>"
        f"<td>{_fmt(row.get('mean_x'))}</td>"
        f"<td>{_fmt(row.get('mean_reward'))}</td>"
        f"<td>{_fmt(row.get('mean_ep_len'), 0)}</td>"
        "</tr>"
        for row in recent
    ) or "<tr><td colspan='6' class='empty'>No eval rows yet.</td></tr>"

    report_link = (
        f'<a class="button" href="{html.escape(report_url)}">Open static research report →</a>'
        if report_url else
        '<span class="muted">Static report will appear after the run is collected.</span>'
    )
    label_block = ""
    if label:
        verdict = str(label.get("verdict", "unknown"))
        label_block = (
            f'<div class="verdict"><div class="verdict-label">FINAL LABEL</div>'
            f'<div class="verdict-value">{_badge(verdict, "pass" if verdict == "pass" else "fail" if verdict == "fail" else "unknown")}</div>'
            f'<div class="muted mono small">source: {html.escape(str(label.get("source")))}</div></div>'
        )
    elif status in ("evaluating", "finalizing"):
        label_block = (
            '<div class="verdict"><div class="verdict-label">FINAL LABEL</div>'
            '<div class="verdict-value">pending final acceptance</div></div>'
        )
    else:
        label_block = (
            '<div class="verdict"><div class="verdict-label">FINAL LABEL</div>'
            '<div class="verdict-value muted">pending</div></div>'
        )

    css = """
:root{--bg:#f7f5ef;--surface:#fffdf8;--surface-2:#f1eee6;--ink:#1f1e1b;
--muted:#6d685e;--line:#e4ded2;--clay:#d97757;--clay-soft:#f4e4db;
--green:#2f7d5b;--green-soft:#e3f0e8;--red:#b24a3f;--red-soft:#f6e3df;
--amber:#b07b2c;--amber-soft:#f6ecd9;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
--serif:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;
--sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,system-ui,sans-serif}
@media(prefers-color-scheme:dark){:root{--bg:#191816;--surface:#211f1c;--surface-2:#2a2723;
--ink:#f2ede4;--muted:#aaa397;--line:#39352f;--clay:#e08a6d;--clay-soft:#3a2a24;
--green:#7fc3a1;--green-soft:#23352c;--red:#e08b7f;--red-soft:#3a2521;
--amber:#e0b36a;--amber-soft:#3a3022}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.6 var(--sans)}.wrap{max-width:1100px;margin:0 auto;padding:0 28px 64px}
.top{display:flex;align-items:center;gap:14px;padding:20px 0;border-bottom:1px solid var(--line)}
.mark{width:30px;height:30px;border-radius:9px;background:var(--clay);color:#fff;
display:grid;place-items:center;font-size:17px}.brand{font:600 15px/1.2 var(--sans)}
.brand small{display:block;color:var(--muted);font:500 10px/1.4 var(--mono);
letter-spacing:.14em;text-transform:uppercase;margin-top:2px}
.nav{margin-left:auto;display:flex;gap:18px}.nav a{color:var(--muted);text-decoration:none;
font:500 12px var(--mono)}.nav a:hover{color:var(--ink)}
.hero{padding:42px 0 22px}.kicker{font:600 11px/1.4 var(--mono);letter-spacing:.18em;
text-transform:uppercase;color:var(--clay);margin-bottom:12px}
h1{font:500 46px/1.08 var(--serif);letter-spacing:-.02em;margin:0 0 10px}
.hero p{color:var(--muted);margin:0;max-width:720px}
.layout{display:grid;grid-template-columns:1.3fr .7fr;gap:18px;margin-top:20px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:22px}
.progress-label{display:flex;justify-content:space-between;align-items:baseline;
font:12px var(--mono);color:var(--muted);margin-bottom:10px}
.progress-label b{color:var(--ink);font-size:18px;font-family:var(--serif);font-weight:500}
.progress{height:14px;border-radius:8px;background:var(--surface-2);overflow:hidden}
.progress>span{display:block;height:100%;background:var(--clay);border-radius:8px;
transition:width .4s ease}.meta{display:flex;gap:18px;flex-wrap:wrap;margin-top:14px;
font:11px var(--mono);color:var(--muted)}.meta b{color:var(--ink);font-weight:500}
.kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:18px}
.kpi{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:14px}
.kpi-label{font:600 9.5px var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
.kpi-value{font:500 24px/1.2 var(--serif);margin-top:6px;font-variant-numeric:tabular-nums}
.kpi.good .kpi-value{color:var(--green)}.kpi.bad .kpi-value{color:var(--red)}
.kpi.warn .kpi-value{color:var(--amber)}
.stages{display:flex;gap:8px;flex-wrap:wrap;margin-top:18px}
.stage{display:flex;flex-direction:column;gap:2px;padding:8px 12px;border-radius:10px;
border:1px solid var(--line);font:11px var(--mono);background:var(--surface-2)}
.stage b{font-weight:600}.stage small{color:var(--muted);font-size:9px;text-transform:uppercase;letter-spacing:.08em}
.stage.done{border-color:color-mix(in srgb,var(--green) 35%,var(--line));background:var(--green-soft)}
.stage.current{border-color:var(--clay);background:var(--clay-soft)}
.badge{display:inline-flex;border-radius:999px;padding:2px 9px;font:600 10px var(--mono);
letter-spacing:.08em;border:1px solid transparent}
.badge.pass{color:var(--green);background:var(--green-soft);border-color:color-mix(in srgb,var(--green) 30%,transparent)}
.badge.fail{color:var(--red);background:var(--red-soft);border-color:color-mix(in srgb,var(--red) 30%,transparent)}
.badge.good{color:var(--green);background:var(--green-soft);border-color:color-mix(in srgb,var(--green) 30%,transparent)}
.badge.bad{color:var(--red);background:var(--red-soft);border-color:color-mix(in srgb,var(--red) 30%,transparent)}
.badge.live{color:var(--clay);background:var(--clay-soft);border-color:color-mix(in srgb,var(--clay) 30%,transparent)}
.badge.blocked{color:var(--amber);background:var(--amber-soft);border-color:color-mix(in srgb,var(--amber) 30%,transparent)}
.badge.unknown{color:var(--muted);background:var(--surface-2);border-color:var(--line)}
.verdict{display:flex;flex-direction:column;gap:6px;margin-top:16px;padding-top:16px;border-top:1px solid var(--line)}
.verdict-label{font:600 9.5px var(--mono);letter-spacing:.12em;color:var(--muted)}
.verdict-value{font:500 20px/1.3 var(--serif)}
table{width:100%;border-collapse:collapse;font-size:12px;margin-top:12px}
th{text-align:left;color:var(--muted);font:600 9.5px var(--mono);letter-spacing:.08em;
text-transform:uppercase;padding:8px 10px;border-bottom:1px solid var(--line);background:var(--surface-2)}
td{padding:8px 10px;border-bottom:1px solid var(--line)}
tr:last-child td{border-bottom:none}.mono{font-family:var(--mono)}
.small{font-size:10px}.muted{color:var(--muted)}.empty{text-align:center;color:var(--muted);padding:20px}
.button{display:inline-block;margin-top:12px;padding:8px 12px;border-radius:9px;background:var(--clay);
color:#fff;text-decoration:none;font:600 11px var(--mono)}
.footer{margin-top:28px;padding-top:16px;border-top:1px solid var(--line);
color:var(--muted);font:10.5px/1.7 var(--mono)}
@media(max-width:800px){.layout{grid-template-columns:1fr}.kpis{grid-template-columns:repeat(2,1fr)}
h1{font-size:34px}.hero{padding-top:28px}}
"""
    refresh_tag = f'<meta http-equiv="refresh" content="{int(refresh)}">' if refresh else ""
    html_doc = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">{refresh_tag}
<title>Live · {html.escape(str(snapshot.get('job_id','training')))}</title>
<style>{css}</style></head><body><div class="wrap">
<div class="top"><div class="mark">✳</div><div class="brand">RL-Sentinel Research
<small>Live training · read-only snapshot</small></div>
<nav class="nav"><a href="../">Research</a><a href="#evals">Evals</a><a href="#evidence">Evidence</a></nav></div>
<header class="hero"><div class="kicker">Live · {html.escape(status)}</div>
<h1>{html.escape(str(snapshot.get('job_id','training')))}</h1>
<p>实时训练进度只读快照。它不是版本化证据；run 完成后以 fetch + collect 的 manifest 为准。</p></header>
<div class="layout">
  <section class="card">
    <div class="progress-label"><span>{html.escape(progress_title)}</span><b>{_pct(progress)}</b></div>
    <div class="progress"><span style="width:{progress*100:.1f}%"></span></div>
    <div class="meta"><span>status <b>{_badge(status, _status_class(status))}</b></span>
      <span>stage <b>{html.escape(current_stage)}</b></span>
      <span>stages <b>{stages_completed}/{stage_count}</b></span>
      <span>event step <b>{_fmt(snapshot.get('current_step'), 0)}</b> / {_fmt(snapshot.get('planned_steps'), 0)}</span>
      <span>event budget <b>{_pct(event_progress)}</b></span>
    </div>
    {('<p class="small muted" style="margin:10px 0 0">Curriculum completed; event budget coverage reflects recorded samples, not unused compute.</p>' if training_complete else '')}
    <div class="stages">{''.join(stage_pills) or '<span class="muted mono small">no stages yet</span>'}</div>
    {label_block}
    {report_link}
  </section>
  <section class="card">
    <div class="kpis">
      <div class="kpi"><div class="kpi-label">Latest eval</div>
        <div class="kpi-value">{_pct(latest.get('success_rate'))}</div></div>
      <div class="kpi"><div class="kpi-label">Mean x</div>
        <div class="kpi-value">{_fmt(latest.get('mean_x'))}</div></div>
      <div class="kpi"><div class="kpi-label">Throughput</div>
        <div class="kpi-value">{_fmt(snapshot.get('throughput_steps_s'), 0)}<small class="muted small"> steps/s</small></div></div>
      <div class="kpi"><div class="kpi-label">ETA</div>
        <div class="kpi-value" style="font-size:18px">{html.escape(str(snapshot.get('eta_human') or 'N/A'))}</div></div>
      <div class="kpi"><div class="kpi-label">Samples</div>
        <div class="kpi-value">{_fmt(snapshot.get('sample_count'), 0)}</div></div>
      <div class="kpi"><div class="kpi-label">Source</div>
        <div class="kpi-value mono" style="font-size:12px">{html.escape(str((source.get('commit') or 'unknown'))[:12])}</div></div>
    </div>
  </section>
</div>
<section class="card" id="evals" style="margin-top:18px">
  <div class="progress-label"><span>Recent evals</span><span class="mono small">latest checkpoint first</span></div>
  <table><thead><tr><th>stage</th><th>timesteps</th><th>success</th><th>mean x</th>
  <th>mean reward</th><th>ep len</th></tr></thead><tbody>{eval_rows}</tbody></table>
</section>
<section class="card" id="evidence" style="margin-top:18px">
  <div class="progress-label"><span>Snapshot</span><span class="mono small">not versioned evidence</span></div>
  <div class="meta"><span>updated <b>{html.escape(str(snapshot.get('updated_at')))}</b></span>
  <span>events <b>{_fmt(snapshot.get('event_count'), 0)}</b></span>
  <span>finish <b>{html.escape(str(snapshot.get('finish_status') or 'pending'))}</b></span></div>
</section>
<div class="footer">RL-Sentinel Research · live snapshot · page refreshes every {int(refresh)}s<br>
Read-only: no stop/tune/resize action is executed. Static evidence lives in the research report.</div>
</div></body></html>"""
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "index.html"
    path.write_text(html_doc, encoding="utf-8")
    return {
        "path": str(path),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "progress": progress,
        "label": label.get("verdict") if label else None,
    }
