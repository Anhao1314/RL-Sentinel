# RL Sentinel

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/sentinel-hero-dark.svg" />
  <img src="docs/assets/sentinel-hero-light.svg" alt="观察、回放、核验。时间边界把 T 时刻已知的信息与之后才可用的事件分开。" width="100%" />
</picture>

**训练可靠性，像量化研究一样被测量。**

RL Sentinel 记录强化学习训练在每个 checkpoint 真正可见的信息，把这段记录变成可版本化的研究数据集，再用当时可用的证据复核早停建议。

<p><code>Python 3.12+</code> · <code>严格核心零第三方依赖</code> · <code>DuckDB + Parquet 研究层</code> · <a href="https://github.com/Anhao1314/RL-Sentinel/actions">CI / Actions ↗</a></p>

[快速开始](#quick-start) · [研究系统](#research-system) · [实时训练](#live-training) · [证据](#evidence) · [文档导航](docs/README.md) · [English](README.md)

> 只输出本地建议，不自动停止训练。受控实验证明的是工作流，不是生产环境的早停效果。

![RL Sentinel 研究报告](docs/assets/research-report-preview.png)

*报告来自当前 Go2W 数据集：18 条 observed run（6 pass / 12 fail）+ 48 条 legacy/offline run。Observed readiness 为 `candidate_for_external_validation`；规则回测仍漏掉大部分失败。*

## 为什么需要它

多数训练面板回答的是事后问题：*发生了什么？* RL Sentinel 回答的是一个时点问题：**在 checkpoint T，我们究竟知道什么？基于这些信息，什么决策是合理的？**

这个区别很重要：早停用后见之明很容易做对，在线做对却很难。一个最终失败的 run 在 50% 时可能看起来很健康；一个最终通过的 run 也可能经历暂时回撤。因此系统严格区分：

- 有事件级时间戳的 **observed** run；
- 没有 `available_at` 的 **legacy/offline** run，永远不计入操作门槛。

标签只用于评分，不作为因子输入。

<a id="what-it-does"></a>
## 它包含什么

| 层 | 职责 | 主要产物 |
| --- | --- | --- |
| **严格核心**（`rl_risk_replay/`） | 事件合同、as-of 回放、决策策略、证据包。仅使用标准库。 | SQLite、JSON、单文件 HTML |
| **研究层**（`research/`） | 版本化数据集、checkpoint 因子、cohort 回测、静态研究报告、Live 页面。 | Parquet、DuckDB、HTML |
| **生产者适配** | SB3 callback 与 Go2W run 输出。 | `events.jsonl`、快照 JSON |
| **历史路径**（`legacy/` 与根目录模块） | 旧的 CSV 分析流程，保留用于审计。 | CSV、Markdown |

<a id="architecture"></a>
## 架构

```text
training run
  events.jsonl + eval logs + acceptance summary + job spec
        │
        ├── rl_risk_replay/         strict event contract, as-of replay, evidence
        │
        └── research/
              dataset.py            versioned runs / events / labels / factors
                    │
              factors.py            24 checkpoint factors at 30 / 50 / 70 %
                    │
              backtest.py           rules-v2 vs always-continue / always-stop
                    │
              report.py             static research note
                    │
              live.py               read-only training snapshot page
```

严格核心没有第三方运行时依赖；研究层使用 DuckDB、pandas、PyArrow、NumPy 和 SciPy。两层有意分开：审计级核心不引入研究依赖，研究层也不把宽松假设写回核心。

<a id="quick-start"></a>
## 快速开始

<!-- sentinel:quickstart -->
```bash
git clone https://github.com/Anhao1314/RL-Sentinel.git
cd RL-Sentinel
python -m rl_risk_replay experiment --out artifacts/sentinel-demo
python -m rl_risk_replay verify --bundle artifacts/sentinel-demo
```
<!-- /sentinel:quickstart -->

demo 会真实执行 18 次 Q-learning 训练，覆盖三种条件，再比较三种建议策略。用浏览器打开 `artifacts/sentinel-demo/report.html`。输出目录必须是新目录，已有证据不会被覆盖。

需要 Python 3.12+。严格核心不需要 GPU、API Key 或第三方运行时依赖。仓库名与展示名是 **RL Sentinel**；Python 模块仍是 `rl_risk_replay`，以保持兼容。

<a id="research-system"></a>
## 研究系统

研究层把训练 run 变成一条可复现的流水线：

```text
版本化 dataset → checkpoint 因子面板 → as-of cohort 回测 → 静态研究报告
```

### 版本化数据集

`research/dataset.py` 输出 `runs`、`events`、`eval_points`、`labels`、`factor_panel`、`factor_summary` 和 `factor_registry` 七张 Parquet 表，以及 `research.duckdb` 和 `manifest.json`。

`dataset_version` 是对 run 输入、标签、因子版本、checkpoint 和研究源码的内容哈希。相同输入得到相同版本；已存在的版本目录直接复用，不覆盖。

### 因子面板

在每个 run 预算的 30%、50% 和 70% 处，面板只使用 `step <= checkpoint_step` 的样本计算 24 个因子：

| 类别 | 因子 |
| --- | --- |
| 收敛 | reward_last, reward_peak, reward_drawdown, reward_slope, reward_neg_ratio |
| 稳定 | reward_std_recent |
| 健康 | kl_last, kl_mean_recent, kl_max_recent, kl_divergent_streak, value_loss_last, value_loss_slope |
| 进度 | progress_last, progress_slope, step_coverage |
| 执行 | success_rate_last, success_rate_slope, segment_last, segment_changes |
| 覆盖 | sample_count |
| 评估 | eval_success_rate_last, eval_success_rate_slope, eval_mean_reward_last, eval_mean_x_last |

标签永远不会进入因子。因子覆盖率与 Spearman IC 只在样本支持时报告；否则输出 `N/A`，而不是 0。

### as-of 回测

`research/backtest.py` 在每个 checkpoint 回放三种策略：

- `always-continue`：永不停；
- `always-stop`：每个 checkpoint 都停；
- `rules-v2`：drawdown ≥ 0.30、KL ≥ 0.10、KL 连续发散 ≥ 3，或 progress slope ≤ 0 且覆盖足够。

操作门槛刻意设得很严：至少 **5 条有标签的 pass run** 和 **10 条 fail run**。未达到之前，readiness 保持 `blocked`。当前矩阵已满足门槛，readiness 为 `candidate_for_external_validation`；这是数据充分性结果，不代表规则有效。observed cohort 与 legacy cohort 分开报告；legacy 没有事件时间，永远不计入门槛。

### 报告与 Live 页面

`research/report.py` 生成单文件 HTML 研究报告，视觉语言参考 Claude Code：暖色纸面、ink 文本、clay 强调色、serif 标题与 mono 指标。报告包含 run explorer、learning signal、factor lab、backtest、readiness 和 evidence，无外部脚本、字体或图片。

`research/live.py` 从训练快照生成只读页面，每 15 秒刷新，展示 curriculum 进度、event budget 覆盖、stage 状态、最新 eval、throughput、ETA 与最终 label。

<a id="live-training"></a>
## 实时训练接入

第一个生产者是 MoRA-Train 项目中的 Go2W 轮足机器狗。训练 run 写 `events.jsonl`；Windows 侧 job runner 可以在不修改 run 目录的前提下返回只读快照：

```text
Windows run directory
  → win_job_runner.py snapshot
  → snapshot JSON
  → research/live.py
  → research/reports/live/index.html
```

![RL Sentinel Live 训练页面](docs/assets/research-live-preview.png)

*Live 页面是诊断工具，不是证据。它不会执行 stop、tune 或 resize；run 完成后仍以 fetch 回来的 run 和 SHA-256 manifest 为准。*

<a id="evidence"></a>
## 证据

### 受控故障注入（v0.2）

6 个 seed、3 种条件、每次训练 4,000 步、一个 4×4 网格。正常学习产生 6 次成功；关闭学习、在 55% 预算处清空策略分别产生 6 次失败。在 70% checkpoint：

| 策略 | 识别失败 | 误停成功 run | 决策覆盖 |
| --- | ---: | ---: | ---: |
| 始终继续 | 0 / 12 | 0 / 6 | 18 / 18 |
| 始终停止 | 12 / 12 | 6 / 6 | 18 / 18 |
| **Rules-v2** | **6 / 12** | **0 / 6** | **17 / 18** |

Rules-v2 识别了 6 次后期策略清空失败，却漏掉全部 6 次关闭学习的失败；另有 1 次拒绝判断。在 30% 和 50% checkpoint，没有停止建议。

6 个成功样本中没有误停，**不等于**总体误杀率为零。所有受控结果仍为 `readiness: blocked`；不声称实际节省了算力。

### Observed Go2W matrix（2026-10-19）

当前数据集 `5fd1ca77bcbf4230` 包含 **18 条 observed run（6 pass / 12 fail）** + 48 条 legacy/offline run。Readiness 为 `candidate_for_external_validation`：样本量已经足够进入策略讨论，但还不足以声称生产有效。

- Pass 组：1 条 full-budget run、2 条意外通过的 short-budget/no-BC pilot，以及 3 条 full-budget seeds。
- Fail 组：12 条 zero-S0 消融 run。高层接口不变，但低层控制器输出零命令，机器人无法前进。

规则回测如实报告：在 30% checkpoint，`rules-v2` 命中 2 条失败、误停 1 条、漏掉 12 条中的 10 条；在 50% 和 70% checkpoint，没有停止建议。Readiness 门槛只说明样本量足够，不说明规则有效。

最强的 checkpoint 因子 IC 很高（`value_loss_last` 0.87、`kl_last` 0.85、`progress_last` 0.85），但它们区分的是受控 zero-S0 失败模式与健康 run，不能外推到自然训练失败。[矩阵协议与结果 →](docs/MATRIX_2026-10-19.md)

<a id="design-rules"></a>
## 设计原则

1. **时间边界是数据的一部分。** 一个事件只有在既被记录（`event_time`）又可被使用（`available_at`）时才成立。
2. **标签用于评分，不进入决策输入。** 目标 run 的最终结果永远不会进入它自己的预测输入。
3. **Cohort 不混用。** 有事件时间的 observed run 与没有事件时间的 legacy/offline run 分开报告。
4. **缺失时间不补造。** 没有 `available_at` 的历史 CSV 只留在离线路径。
5. **`N/A` 不是 0。** 分母不存在时显示缺失。
6. **只给建议。** 不自动 stop、tune 或 resize。
7. **证据按内容寻址。** SHA-256 manifest 检测变化，不是密码学签名。

<a id="development"></a>
## 开发与验证

```bash
# 严格核心
python -m pip install -r requirements-core-dev.txt
python -m pytest tests_v2 -q
python -m pyright --project pyright-core.json

# 研究层
python -m pip install -r requirements-research.txt
python -m pytest tests_research -q

# 历史回归
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
```

严格核心的类型门槛只覆盖 `rl_risk_replay/`；研究层与历史模块不在该强制门槛内。

<a id="repository-map"></a>
## 仓库地图

| 路径 | 内容 |
| --- | --- |
| `rl_risk_replay/` | 严格事件合同、as-of 回放、策略、证据包 |
| `research/` | 数据集、因子、回测、静态报告、Live 页面 |
| `tests_v2/` | 严格核心单元测试与对抗测试 |
| `tests_research/` | 数据集、因子、回测与报告测试 |
| `docs/` | 协议、研究系统、验收记录、使用指南 |
| `legacy/` | 归档的 CSV 分析路径 |
| 根目录模块 | 历史量化分析与 dashboard |

<a id="documentation"></a>
## 文档

- [开始使用](docs/GETTING_STARTED.zh-CN.md) · [Getting started](docs/GETTING_STARTED.md)
- [研究系统](docs/RESEARCH_SYSTEM.md)
- [v0.2 协议与迁移](docs/RELIABILITY_V2.md)
- [2026-10-06 验收记录](docs/VALIDATION_V2_2026-10-06.md)
- [2026-10-19 observed 矩阵](docs/MATRIX_2026-10-19.md)
- [历史验收与指标审计](docs/portfolio-validation.md)
- [文档导航](docs/README.md)

<a id="scope"></a>
## 范围与边界

**已经建立：** 事件合同、按时间筛选的回放、明确的决策指标、版本化研究数据集、checkpoint 因子、带 readiness 门槛的回测，以及带哈希的证据包。

**尚未建立：** 生产级安全干预、跨任务预测泛化、大规模采集、来源认证，以及真实算力节省。哈希只能核对内容与清单，不能替代签名；可比较的生产者时钟和正确的 run 身份仍是前提。

---

由 [Anhao1314](https://github.com/Anhao1314) 与 [Claude-Anhao](https://github.com/Claude-Anhao) 维护。
