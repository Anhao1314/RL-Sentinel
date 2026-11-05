"""Operator console: a single-file, zero-dependency live UI served by the agent.

One static HTML page (no build step, no external assets, no framework) fed by
the SSE stream at ``/stream``.  It answers the operator question Grafana is
bad at — *what is happening right now* — while Grafana keeps the long-range
analytics.  Nothing user-controlled is embedded in the HTML; all dynamic
values reach the DOM through ``textContent``, so run data cannot inject
markup.
"""
from __future__ import annotations

_TEMPLATE = """<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RL Sentinel — operator console</title>
<style>
:root{--bg:#111217;--panel:#1a1c23;--fg:#d8dae0;--dim:#8b8f9a;--accent:#4fd06a;--warn:#e5b567;--bad:#e5484d;--line:#2a2d35}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,sans-serif}
header{display:flex;justify-content:space-between;align-items:baseline;padding:14px 20px;border-bottom:1px solid var(--line);gap:12px;flex-wrap:wrap}
h1{font-size:16px;margin:0}h1 small{color:var(--dim);font-weight:400;margin-left:8px}
.meta{color:var(--dim);font-size:12px}.meta a{color:#6ea8fe;text-decoration:none;margin-left:10px}
.cards{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;padding:16px 20px 0}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px}
.card b{display:block;font-size:26px;font-weight:600}.card span{color:var(--dim);font-size:12px}
main{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:16px 20px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px;min-width:0}
.panel h2{font-size:13px;margin:0 0 8px;color:var(--dim);font-weight:500}
canvas{width:100%;height:180px;display:block}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:200px}
th{color:var(--dim);font-weight:500}.stale{color:var(--bad);font-weight:600}.ok{color:var(--dim)}
.alert{border-left:3px solid var(--bad);padding:8px 10px;margin:6px 0;background:rgba(229,72,77,.08);border-radius:4px;font-size:12.5px;overflow-wrap:anywhere}
footer{padding:10px 20px;color:var(--dim);font-size:12px;border-top:1px solid var(--line)}
@media(max-width:900px){main{grid-template-columns:1fr}.cards{grid-template-columns:repeat(2,1fr)}}
</style>
<header>
  <h1>RL Sentinel — operator console<small id="ver"></small></h1>
  <div class="meta" id="meta">connecting… <a href="/metrics">/metrics</a><a href="/state">/state</a></div>
</header>
<div class="cards">
  <div class="card"><b id="c-active">—</b><span>活跃 runs</span></div>
  <div class="card"><b id="c-alerts">—</b><span>静默告警</span></div>
  <div class="card"><b id="c-preempt">—</b><span>抢占</span></div>
  <div class="card"><b id="c-lost">—</b><span>损失步数(浪费算力下限)</span></div>
  <div class="card"><b id="c-invalid">—</b><span>契约拒绝事件</span></div>
</div>
<main>
  <div class="panel"><h2>Producer silence(秒,自最后事件)— 爬升 = 挂死</h2><canvas id="ch-stale"></canvas></div>
  <div class="panel"><h2>Last reward(最后指标,平坦 = staleness 陷阱)</h2><canvas id="ch-rew"></canvas></div>
  <div class="panel"><h2>Runs</h2><div style="overflow-x:auto"><table><thead><tr>
    <th>run_id</th><th>task</th><th>attempt</th><th>状态</th><th>step</th><th>silence</th><th>reward</th><th>kl</th>
  </tr></thead><tbody id="runs"></tbody></table></div></div>
  <div class="panel"><h2>Alerts(recommendation-only,仅通知人工)</h2><div id="alerts"><div class="alert ok">暂无告警</div></div></div>
</main>
<footer>数据经 schema-v2 冻结契约逐条校验;缺失指标显示为 — 而非 0。本控制台仅观察,不触碰训练进程。长程分析见 Grafana。</footer>
<script>
"use strict";
const $ = id => document.getElementById(id);
$("ver").textContent = "v" + "%%VERSION%%";
const hist = new Map();
const PALETTE = ["#4fd06a","#6ea8fe","#e5b567","#c678dd","#56b6c2","#e5484d"];
let renderedAlerts = -1;
function push(id, stale, rew) {
  if (!hist.has(id)) hist.set(id, {stale: [], rew: []});
  const h = hist.get(id), cut = Date.now() - 300000;
  h.stale.push([Date.now(), stale]); h.rew.push([Date.now(), rew]);
  while (h.stale.length && h.stale[0][0] < cut) h.stale.shift();
  while (h.rew.length && h.rew[0][0] < cut) h.rew.shift();
}
function draw(canvasId, series, fmt) {
  const cv = $(canvasId), dpr = window.devicePixelRatio || 1;
  const W = cv.clientWidth, H = cv.clientHeight;
  if (cv.width !== W * dpr) { cv.width = W * dpr; cv.height = H * dpr; }
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  ctx.strokeStyle = "#2a2d35"; ctx.lineWidth = 1;
  for (let i = 1; i <= 3; i++) { const y = H * i / 4; ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke(); }
  let max = 0, min = Infinity;
  for (const pts of series.values()) for (const p of pts) { if (p[1] > max) max = p[1]; if (p[1] < min) min = p[1]; }
  if (!isFinite(min)) { ctx.fillStyle = "#8b8f9a"; ctx.fillText("waiting for stream…", 8, 20); return; }
  if (max === min) { max = min + 1; }
  const t0 = Date.now() - 300000, t1 = Date.now() + 1;
  let i = 0;
  for (const [id, pts] of series) {
    if (pts.length < 2) continue;
    ctx.strokeStyle = PALETTE[i++ % PALETTE.length]; ctx.lineWidth = 1.6; ctx.beginPath();
    pts.forEach(([t, v], k) => {
      const x = (t - t0) / (t1 - t0) * W, y = H - (v - min) / (max - min) * (H - 8) - 4;
      k ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.stroke();
    ctx.fillStyle = ctx.strokeStyle; ctx.fillText(id, 8, 14 * i);
  }
  if (i) { ctx.fillStyle = "#8b8f9a"; ctx.fillText(fmt(max), W - 64, 12); }
}
function cell(tr, text, cls) { const td = document.createElement("td"); td.textContent = text; if (cls) td.className = cls; tr.appendChild(td); }
function render(snap) {
  $("meta").firstChild.textContent = "live · stale_after " + snap.stale_after + "s · ";
  $("c-active").textContent = activeOf(snap);
  $("c-alerts").textContent = snap.counters.silence_alerts;
  $("c-preempt").textContent = snap.counters.preemptions;
  $("c-lost").textContent = snap.counters.steps_lost;
  $("c-invalid").textContent = snap.counters.invalid;
  const tbody = $("runs"); tbody.textContent = "";
  for (const [id, r] of Object.entries(snap.runs)) {
    const tr = document.createElement("tr");
    const status = r.finished ? "finished" : (r.started ? "running" : "—");
    const staleTxt = r.staleness_seconds == null ? "—" : r.staleness_seconds.toFixed(0) + "s";
    cell(tr, id); cell(tr, r.task || "—"); cell(tr, r.attempt || "—"); cell(tr, status);
    cell(tr, r.last_step == null ? "—" : r.last_step);
    cell(tr, staleTxt, (!r.finished && r.staleness_seconds > snap.stale_after) ? "stale" : "ok");
    cell(tr, r.last_metrics.reward == null ? "—" : r.last_metrics.reward.toFixed(4));
    cell(tr, r.last_metrics.approx_kl == null ? "—" : r.last_metrics.approx_kl.toFixed(4));
    tbody.appendChild(tr);
    if (r.started && !r.finished) {
      push(id, Math.max(0, r.staleness_seconds || 0),
           r.last_metrics.reward == null ? 0 : r.last_metrics.reward);
    }
  }
  if (snap.alerts.length !== renderedAlerts) {
    renderedAlerts = snap.alerts.length;
    const box = $("alerts"); box.textContent = "";
    if (!snap.alerts.length) { const d = document.createElement("div"); d.className = "alert ok"; d.textContent = "暂无告警"; box.appendChild(d); }
    for (const a of [...snap.alerts].reverse()) {
      const d = document.createElement("div"); d.className = "alert";
      const t = document.createElement("time");
      t.textContent = new Date().toLocaleTimeString() + " · " + a.type;
      d.appendChild(t); d.appendChild(document.createTextNode(a.message));
      box.appendChild(d);
    }
  }
}
function activeOf(snap) { return Object.values(snap.runs).filter(r => r.started && !r.finished).length; }
const es = new EventSource("/stream");
es.onmessage = e => render(JSON.parse(e.data));
es.onerror = () => { $("meta").firstChild.textContent = "stream disconnected — retrying… "; };
setInterval(() => {
  const staleSeries = new Map(), rewSeries = new Map();
  for (const [id, h] of hist) { staleSeries.set(id, h.stale); rewSeries.set(id, h.rew); }
  draw("ch-stale", staleSeries, v => v.toFixed(0) + "s");
  draw("ch-rew", rewSeries, v => v.toFixed(2));
}, 1000);
</script>
</html>
"""


def console_html(version: str) -> str:
    """Render the static console page; only the version string is interpolated."""
    safe = (version.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))
    return _TEMPLATE.replace("%%VERSION%%", safe)
