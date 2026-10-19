# Research layer：从训练 run 到因子研究

`research/` 是 RL-Sentinel 的量化研究层。它把 Go2W 训练 run 和旧 CSV 历史数据变成：

```text
版本化 dataset → checkpoint 因子面板 → as-of 回测 → 静态研究报告
```

严格事件核心 `rl_risk_replay/` 保持 stdlib-only，继续负责事件合同和审计级回放；研究层不修改它，也不把宽松假设写回严格核心。

## 1. 架构

```text
Go2W run
  events.jsonl / stage*/eval_log.csv / evaluation/summary.json / _job/spec.json
        │
        ▼
research/dataset.py
  runs / events / eval_points / labels / factor_panel / factor_summary
  Parquet + DuckDB + manifest（dataset_version）
        │
        ▼
research/factors.py
  24 个 checkpoint 因子；只使用 cutoff 前可见样本
        │
        ▼
research/backtest.py
  rules-v2 / always-continue / always-stop
  observed cohort 与 legacy/offline cohort 分开
        │
        ▼
research/report.py
  静态 HTML（Claude Code 风格），无外部脚本
```

## 2. 数据合同

### 2.1 输入

| 输入 | 用途 | 时间语义 |
| --- | --- | --- |
| `events.jsonl` | 观测 cohort、as-of 因子 | `event_time` / `available_at` |
| `stage*/eval_log.csv` | 学习曲线、eval 因子 | 离线聚合，无逐行可用时间 |
| `evaluation/summary.json` | 最终 verdict、success_rate | 最终验收，只用于标签 |
| `_job/spec.json` | 源提交、命令、环境 | provenance |
| `data/datasets/*.csv` | legacy 历史 cohort | 无事件时间，标记 offline |

### 2.2 输出

```text
research/data/<dataset_version>/
  runs.parquet
  events.parquet
  eval_points.parquet
  labels.parquet
  factor_panel.parquet
  factor_summary.parquet
  factor_registry.parquet
  research.duckdb
  manifest.json
```

`dataset_version` 由 run IDs、输入哈希、label、factor version、checkpoints 和研究源码哈希共同决定。相同输入得到相同版本；已存在的版本目录直接复用，不覆盖。

### 2.3 严格边界

- **observed cohort**：来自 `events.jsonl`，有事件级时间；可进入严格 as-of 讨论。
- **legacy cohort**：来自旧 CSV，`strict_asof=False`；只做历史因子/回测参考，不计入操作门槛。
- **label 只用于评分**：因子面板和决策不包含 label。
- **不做时间推断**：legacy 没有 `available_at`，不补造。
- **不混淆分母**：`N/A` 表示分母不存在，不写成 0。

## 3. 因子面板

每个 run 在 `30% / 50% / 70%` 预算 checkpoint 计算因子。所有因子只使用 `step <= checkpoint_step` 的样本。

| 类别 | 因子 |
| --- | --- |
| convergence | reward_last, reward_peak, reward_drawdown, reward_slope, reward_neg_ratio |
| stability | reward_std_recent |
| health | kl_last, kl_mean_recent, kl_max_recent, kl_divergent_streak, value_loss_last, value_loss_slope |
| progress | progress_last, progress_slope, step_coverage |
| execution | success_rate_last, success_rate_slope, segment_last, segment_changes |
| coverage | sample_count |
| evaluation | eval_success_rate_last, eval_success_rate_slope, eval_mean_reward_last, eval_mean_x_last |

`factor_summary` 计算：

- coverage / n / mean / std
- `ic_pass`：因子与 pass/fail 的 Spearman IC（至少 5 个有标签样本）
- `ic_success`：因子与最终 success_rate 的 Spearman IC
- 不足样本或因子为常数时输出 `N/A`

## 4. 回测

当前策略：

| 策略 | 规则 |
| --- | --- |
| `always-continue` | 永不停 |
| `always-stop` | 每个 checkpoint 都停 |
| `rules-v2` | drawdown ≥ 0.30、KL ≥ 0.10、KL 连续 ≥3、progress slope ≤0（coverage ≥0.3） |

输出：

- 每次决策：`run_id / checkpoint / policy / decision / reason / input_sha256`
- 每个 policy × checkpoint：pass/fail、true stops、false stops、pass kill、fail recall、coverage、readiness
- 操作下限：pass ≥ 5 且 fail ≥ 10；否则 `blocked`

这是离线 cohort 回测，不是严格事件回放。严格回放仍使用 `rl_risk_replay replay`。

## 5. 报告/面板

`research/report.py` 生成单文件 HTML，视觉风格参考 Claude Code：

- 暖色纸面、ink 文本、clay 强调色
- serif 标题 + mono 指标
- KPI、Readiness、Run Explorer、Learning signal、Factor Lab、Backtest、Evidence
- 自动 light/dark（`prefers-color-scheme`）
- 无外部脚本、字体或图片
- legacy cohort 放在折叠区，并明确标注“不计入 observed 门槛”

## 6. 命令

安装研究依赖：

```bash
python -m pip install -r requirements-research.txt
```

构建版本化数据集：

```bash
python -m research build \
  --runs-root /tmp/go2w-fetched \
  --legacy-data data/datasets \
  --out research/data
```

渲染报告：

```bash
python -m research report \
  --dataset research/data/<dataset_version> \
  --out research/reports/<report-name>
```

一次完成 build + report + registry：

```bash
python -m research run \
  --runs-root /tmp/go2w-fetched \
  --legacy-data data/datasets \
  --out research/data \
  --report-out research/reports/<report-name> \
  --name <experiment-name> \
  --hypothesis "drawdown at 50% predicts failure"
```

## 7. 当前预览状态（2026-10-19）

- 数据集：1 个 observed smoke（label `unknown`）+ 48 个 legacy runs
- observed readiness：`blocked`（0 pass / 0 fail / 1 unknown）
- legacy cohort：存在历史标签，但 `strict_asof=False`，不计入门槛
- 报告：`research/reports/go2w-preview*/report.html`
- 预览图：`docs/assets/research-report-preview.png`

这说明研究流水线已跑通，但不构成早停有效性结论。

## 8. 下一步：18 条 observed run

要进入可讨论的策略对照，至少需要：

```text
6 pass + 12 fail = 18 observed runs
```

建议矩阵：

- 成功组：multi-segment / junction / high-level-curve × seeds 0,1
- 失败组：无 BC / 无 pre-brake / 短预算 × seeds 0,1
- 可选：C++ S0 消融

每条 run 完成后：

1. `tools/win_train.py fetch <job>`
2. `scripts/collect_run_evidence.py`（Go2W 仓库）
3. `python -m research build --runs-root <fetched-root> ...`
4. `python -m research report ...`
5. 注册 experiment

## 9. 限制

- 研究层使用 pandas/DuckDB；严格核心仍 stdlib-only。
- 因子只覆盖事件/评估可见字段；奖励分量、完整轨迹、per-episode stage eval 尚未采集。
- legacy 数据没有事件时间，不能用于严格 as-of 结论。
- IC 在小样本下不稳定；当前预览的 IC 多为 N/A。
- 不自动停止训练，不声称生产级早停或真实算力节省。
- dataset_version 是内容哈希，不是密码学签名。
