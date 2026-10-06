"""Offline review artifacts; all experiment strings are HTML-escaped."""
from __future__ import annotations

import html
import platform
import subprocess
from pathlib import Path
from typing import cast

from . import __version__
from .events import canonical, mapping


def runtime_info() -> dict[str, object]:
    root = Path(__file__).resolve().parents[1]
    try:
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,
                                  capture_output=True, text=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=root, check=True,
                                    capture_output=True, text=True).stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        revision = "unavailable"
        dirty = None
    return {"package_version": __version__, "python": platform.python_version(),
            "platform": platform.platform(), "source_revision": revision,
            "worktree_dirty": dirty}


def table(raw: object, columns: tuple[str, ...]) -> str:
    if not isinstance(raw, list):
        return ""
    rows = cast(list[object], raw)
    head = "".join(f"<th>{html.escape(col)}</th>" for col in columns)
    body: list[str] = []
    for item in rows:
        row = mapping(item)
        cells: list[str] = []
        for col in columns:
            value = row.get(col)
            text = "N/A" if value is None else f"{value:.4g}" if isinstance(value, float) else str(value)
            cells.append(f"<td>{html.escape(text)}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def render_html(result: dict[str, object]) -> str:
    payload = html.escape(canonical(result))
    title = html.escape(str(result.get("title", "RL Training Reliability")))
    summaries = table(result.get("summaries"), ("policy", "progress", "n_pass", "n_fail",
                       "true_stops", "false_stops", "pass_kill_rate", "fail_recall", "n_abstained", "readiness"))
    trials = table(result.get("trials"), ("run_id", "fault", "seed", "success_rate", "verdict"))
    decisions = table(result.get("decisions"), ("run_id", "policy", "progress", "status", "reason", "input_sha256"))
    return f'''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>
body{{font:16px/1.65 system-ui,sans-serif;max-width:1250px;margin:3rem auto;padding:0 1.4rem}}
h1{{font-size:2rem;line-height:1.2}}.note{{border-left:4px solid;padding:1rem;background:#f5f5f5}}
.scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{border-bottom:1px solid #ddd;padding:.6rem;text-align:left;vertical-align:top}}
th{{background:#f5f5f5;white-space:nowrap}}td:last-child{{max-width:25rem;overflow-wrap:anywhere}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.5 ui-monospace,monospace}}
summary{{cursor:pointer;font-weight:600;padding:1rem 0}}footer{{border-top:1px solid #ddd;margin-top:2rem;padding-top:1rem}}
</style><h1>{title}</h1><p class="note">仅提供本地建议，不停止训练。合成数据、受控实验与实际部署数据分开标记。
样本门槛通过也不等于预测有效；剩余墙钟时间不是实际算力节省。比例显示为 0–1，N/A 表示分母不存在。</p>
<h2>策略对照</h2>{summaries}<h2>训练试验</h2>{trials}<h2>逐次决策与输入标识</h2>{decisions}
<details><summary>完整机器可读证据（包括每次决策的实际输入）</summary><pre>{payload}</pre></details>
<footer>完整结果见 results.json；manifest.json 记录文件校验值。离线报告不加载外部脚本。</footer></html>'''
