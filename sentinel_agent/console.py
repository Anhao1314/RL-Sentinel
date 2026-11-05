"""Operator console: a single-file, zero-dependency live UI served by the agent.

One static HTML page (no build step, no external assets, no framework) fed by
the SSE stream at ``/stream``.  It answers the operator question Grafana is
bad at — *what is happening right now* — while Grafana keeps the long-range
analytics.  Nothing user-controlled is embedded in the HTML; all dynamic
values reach the DOM through ``textContent`` (or inert canvas pixels), so run
data cannot inject markup.  Missing metrics are drawn as gaps, never as 0 —
the repository's ``N/A != 0`` discipline holds in the UI layer too.
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
.meta{color:var(--dim);font-size:12px;display:flex;align-items:center;gap:10px}
.meta a{color:#6ea8fe;text-decoration:none}
.win{display:inline-flex;border:1px solid var(--line);border-radius:6px;overflow:hidden}
.win button{background:none;border:none;color:var(--dim);font:11px system-ui;padding:3px 10px;cursor:pointer}
.win button.on{background:#242833;color:var(--fg)}
.cards{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;padding:16px 20px 0}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px}
.card b{display:block;font-size:26px;font-weight:600}.card span{color:var(--dim);font-size:12px}
main{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:16px 20px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px;min-width:0}
.panel h2{font-size:13px;margin:0 0 8px;color:var(--dim);font-weight:500}
canvas{width:100%;height:200px;display:block}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:190px}
th{color:var(--dim);font-weight:500}
.stale{color:var(--bad);font-weight:600}.ok{color:var(--dim)}
.meter{display:inline-block;width:52px;height:4px;background:#2a2d35;border-radius:2px;vertical-align:middle;margin-right:6px}
.meter i{display:block;height:4px;background:var(--accent);border-radius:2px;transition:width .5s}
.meter i.hot{background:var(--bad)}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:5px;background:var(--accent)}
.dot.done{background:#5b6070}
.alert{border-left:3px solid var(--bad);padding:8px 10px;margin:6px 0;background:rgba(229,72,77,.08);border-radius:4px;font-size:12.5px;overflow-wrap:anywhere}
.alert time{color:var(--dim);margin-right:8px}
footer{padding:10px 20px;color:var(--dim);font-size:12px;border-top:1px solid var(--line)}
@media(max-width:900px){main{grid-template-columns:1fr}.cards{grid-template-columns:repeat(2,1fr)}}
</style>
<header>
  <h1>RL Sentinel — operator console<small id="ver"></small></h1>
  <div class="meta">
    <span id="conn">connecting…</span>
    <span class="win"><button data-w="300000" class="on">5m</button><button data-w="900000">15m</button></span>
    <a href="/metrics">/metrics</a><a href="/state">/state</a>
  </div>
</header>
<div class="cards">
  <div class="card"><b id="c-active">—</b><span>活跃 runs</span></div>
  <div class="card"><b id="c-alerts">—</b><span>静默告警</span></div>
  <div class="card"><b id="c-preempt">—</b><span>抢占</span></div>
  <div class="card"><b id="c-lost">—</b><span>损失步数(浪费算力下限)</span></div>
  <div class="card"><b id="c-invalid">—</b><span>契约拒绝事件</span></div>
</div>
<main>
  <div class="panel"><h2>Producer silence(秒,自最后事件)— 红线 = 阈值,爬升 = 挂死</h2><canvas id="ch-stale"></canvas></div>
  <div class="panel"><h2>Last reward(最后指标)— 平坦 = staleness 陷阱,断线 = 指标缺失</h2><canvas id="ch-rew"></canvas></div>
  <div class="panel"><h2>Runs</h2><div style="overflow-x:auto"><table><thead><tr>
    <th>run_id</th><th>task</th><th>attempt</th><th>状态</th><th>step</th><th>silence</th><th>reward</th><th>kl</th>
  </tr></thead><tbody id="runs"></tbody></table></div></div>
  <div class="panel"><h2>Alerts(recommendation-only,仅通知人工)</h2><div id="alerts"><div class="alert ok">暂无告警</div></div></div>
</main>
<footer>数据经 schema-v2 冻结契约逐条校验;缺失指标显示为 — 或曲线断线,绝不显示为 0。本控制台仅观察,不触碰训练进程。长程分析见 Grafana。</footer>
<script>
"use strict";
const $ = id => document.getElementById(id);
$("ver").textContent = "v" + "%%VERSION%%";
let windowMs = 300000;
const CAP = 20 * 60 * 1000, MAXPTS = 5000;
const hist = new Map();
const PALETTE = ["#4fd06a","#6ea8fe","#e5b567","#c678dd","#56b6c2","#e5484d"];
let alertKey = null, latest = null;

document.querySelectorAll(".win button").forEach(b => b.onclick = () => {
  windowMs = +b.dataset.w;
  document.querySelectorAll(".win button").forEach(x => x.classList.toggle("on", x === b));
});

function push(id, stale, rew) {
  if (!hist.has(id)) hist.set(id, {stale: [], rew: []});
  const h = hist.get(id), now = Date.now();
  h.stale.push([now, stale]);
  h.rew.push([now, rew]);              // rew === null → 断线,不画 0
  while (h.stale.length && h.stale[0][0] < now - CAP) h.stale.shift();
  while (h.rew.length && h.rew[0][0] < now - CAP) h.rew.shift();
  if (h.stale.length > MAXPTS) h.stale.splice(0, h.stale.length - MAXPTS);
  if (h.rew.length > MAXPTS) h.rew.splice(0, h.rew.length - MAXPTS);
}
function fmtDur(s) { return s >= 90 ? (s / 60).toFixed(1) + "m" : s.toFixed(0) + "s"; }
function fmtNum(v) { return v >= 100 ? v.toFixed(0) : v >= 1 ? v.toFixed(2) : v.toPrecision(2); }
function fmtClock(t) { return new Date(t).toTimeString().slice(0, 8); }

function drawChart(canvasId, series, opts) {
  const cv = $(canvasId), dpr = window.devicePixelRatio || 1;
  const W = cv.clientWidth, H = cv.clientHeight;
  if (!W || !H) return;
  if (cv.width !== Math.round(W * dpr)) { cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); }
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);
  const padL = 46, padR = 10, padT = 20, padB = 18;
  const iw = W - padL - padR, ih = H - padT - padB;
  const t1 = Date.now(), t0 = t1 - windowMs;
  let max = 0, any = false;
  for (const pts of series.values())
    for (const p of pts) if (p[0] >= t0 && p[1] != null) { if (p[1] > max) max = p[1]; any = true; }
  if (opts.threshold != null && opts.threshold > max) max = opts.threshold;
  if (!any) { ctx.fillStyle = "#8b8f9a"; ctx.font = "11px system-ui"; ctx.fillText("waiting for stream…", padL, padT + 14); return; }
  max = max * 1.12 || 1;
  const X = t => padL + (t - t0) / (t1 - t0) * iw;
  const Y = v => padT + ih - v / max * ih;
  ctx.font = "10px system-ui"; ctx.lineWidth = 1;
  ctx.strokeStyle = "#262932"; ctx.fillStyle = "#8b8f9a"; ctx.textAlign = "right"; ctx.textBaseline = "middle";
  for (let i = 0; i <= 3; i++) {
    const v = max * i / 3, y = Y(v);
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke();
    ctx.fillText(opts.fmt(v), padL - 5, y);
  }
  ctx.textAlign = "center"; ctx.textBaseline = "alphabetic";
  for (let i = 0; i <= 2; i++) {
    const t = t0 + (t1 - t0) * i / 2;
    ctx.fillText(i === 2 ? "now" : fmtClock(t), Math.min(Math.max(X(t), padL + 24), W - 26), H - 5);
  }
  if (opts.threshold != null) {
    const y = Y(opts.threshold);
    ctx.strokeStyle = "#e5484d"; ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "#e5484d"; ctx.textAlign = "left";
    ctx.fillText("阈值 " + opts.fmt(opts.threshold), padL + 4, y - 5);
  }
  let li = 0;
  for (const [id, pts] of series) {
    const vis = pts.filter(p => p[0] >= t0 - 60000);
    if (vis.length < 2) continue;
    const color = PALETTE[li % PALETTE.length];
    ctx.strokeStyle = color; ctx.lineWidth = 1.8; ctx.beginPath();
    let pen = false;
    for (const [t, v] of vis) {
      if (v == null) { pen = false; continue; }
      const x = X(t), y = Y(v);
      if (pen) ctx.lineTo(x, y); else { ctx.moveTo(x, y); pen = true; }
    }
    ctx.stroke();
    if (opts.fill && !vis.some(p => p[1] == null)) {
      ctx.globalAlpha = 0.10; ctx.fillStyle = color; ctx.beginPath();
      vis.forEach(([t, v], k) => { const x = X(t), y = Y(v); k ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
      ctx.lineTo(X(vis[vis.length - 1][0]), padT + ih); ctx.lineTo(X(vis[0][0]), padT + ih);
      ctx.closePath(); ctx.fill(); ctx.globalAlpha = 1;
    }
    ctx.fillStyle = color; ctx.beginPath(); ctx.arc(padL + 4, padT + 4 + li * 13, 3, 0, 7); ctx.fill();
    ctx.fillStyle = "#d8dae0"; ctx.textAlign = "left";
    const cur = vis[vis.length - 1][1];
    ctx.fillText(id + "  " + (cur == null ? "—" : opts.fmt(cur)), padL + 11, padT + 7 + li * 13);
    li++;
  }
}

function cell(tr, text, cls) { const td = document.createElement("td"); td.textContent = text; if (cls) td.className = cls; tr.appendChild(td); }
function render(snap) {
  $("conn").textContent = "live · stale_after " + snap.stale_after + "s ·";
  $("c-active").textContent = Object.values(snap.runs).filter(r => r.started && !r.finished).length;
  $("c-alerts").textContent = snap.counters.silence_alerts;
  $("c-preempt").textContent = snap.counters.preemptions;
  $("c-lost").textContent = snap.counters.steps_lost;
  $("c-invalid").textContent = snap.counters.invalid;
  const tbody = $("runs"); tbody.textContent = "";
  for (const [id, r] of Object.entries(snap.runs)) {
    const tr = document.createElement("tr");
    cell(tr, id); cell(tr, r.task || "—"); cell(tr, r.attempt || "—");
    const st = document.createElement("td");
    const dot = document.createElement("i"); dot.className = "dot" + (r.finished ? " done" : "");
    const stx = document.createElement("span");
    stx.textContent = r.finished ? "finished" : (r.started ? "running" : "—");
    st.appendChild(dot); st.appendChild(stx); tr.appendChild(st);
    cell(tr, r.last_step == null ? "—" : r.last_step);
    const sd = document.createElement("td");
    if (r.staleness_seconds == null) { sd.textContent = "—"; sd.className = "ok"; }
    else {
      const hot = !r.finished && r.staleness_seconds > snap.stale_after;
      const wrap = document.createElement("span"); wrap.className = "meter";
      const bar = document.createElement("i");
      bar.style.width = Math.min(100, r.staleness_seconds / snap.stale_after * 100) + "%";
      if (hot) bar.className = "hot";
      wrap.appendChild(bar);
      const txt = document.createElement("span");
      txt.textContent = r.staleness_seconds.toFixed(0) + "s";
      txt.className = hot ? "stale" : "ok";
      sd.appendChild(wrap); sd.appendChild(txt);
    }
    tr.appendChild(sd);
    cell(tr, r.last_metrics.reward == null ? "—" : fmtNum(r.last_metrics.reward));
    cell(tr, r.last_metrics.approx_kl == null ? "—" : fmtNum(r.last_metrics.approx_kl));
    tbody.appendChild(tr);
    if (r.started && !r.finished) {
      push(id, Math.max(0, r.staleness_seconds || 0),
           r.last_metrics.reward == null ? null : r.last_metrics.reward);
    }
  }
  const key = snap.alerts.length + "|" + (snap.alerts.length ? snap.alerts[snap.alerts.length - 1].message : "");
  if (key !== alertKey) {
    alertKey = key;
    const box = $("alerts"); box.textContent = "";
    if (!snap.alerts.length) { const d = document.createElement("div"); d.className = "alert ok"; d.textContent = "暂无告警"; box.appendChild(d); }
    for (const a of [...snap.alerts].reverse()) {
      const d = document.createElement("div"); d.className = "alert";
      const t = document.createElement("time");
      t.textContent = (a.fired_at ? new Date(a.fired_at * 1000).toLocaleTimeString() : "—")
                      + " · " + a.type;
      d.appendChild(t); d.appendChild(document.createTextNode(a.message));
      box.appendChild(d);
    }
  }
}
const es = new EventSource("/stream");
es.onmessage = e => { latest = JSON.parse(e.data); render(latest); };
es.onerror = () => { $("conn").textContent = "stream disconnected — retrying…"; };
setInterval(() => {
  if (!latest) return;
  const s = new Map(), r = new Map();
  for (const [id, h] of hist) { s.set(id, h.stale); r.set(id, h.rew); }
  drawChart("ch-stale", s, {threshold: latest.stale_after, fmt: fmtDur, fill: true});
  drawChart("ch-rew", r, {fmt: fmtNum, fill: false});
}, 1000);
</script>
</html>
"""


def console_html(version: str) -> str:
    """Render the static console page; only the version string is interpolated."""
    safe = (version.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))
    return _TEMPLATE.replace("%%VERSION%%", safe)
