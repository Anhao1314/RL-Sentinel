# RL 训练可靠性与风险回放

<p align="center">
  <img src="docs/assets/social-preview.svg" alt="RL 训练可靠性与风险回放" width="100%" />
</p>

<p align="center">
  <a href="https://github.com/Anhao1314/rl-training-risk-replay/actions/workflows/ci.yml"><img alt="Reliability validation" src="https://github.com/Anhao1314/rl-training-risk-replay/actions/workflows/ci.yml/badge.svg" /></a>
  <img alt="Python 3.12+" src="https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white" />
  <img alt="Release v0.2" src="https://img.shields.io/badge/reliability%20core-v0.2-2563EB" />
  <img alt="Recommendation only" src="https://img.shields.io/badge/mode-recommendation--only-0F172A" />
</p>

<p align="center">
  <strong>给强化学习实验加上一层“时间正确”的可靠性与证据基础设施。</strong><br/>
  重建决策时刻真正可见的信息，给出只读风险建议，再用最终结果回放这些建议，而不是偷偷读取未来。
</p>

<p align="center">
  <a href="README.md">English</a> ·
  <a href="docs/RELIABILITY_V2.md">协议与迁移</a> ·
  <a href="docs/VALIDATION_V2_2026-10-06.md">v0.2 实测验收</a> ·
  <a href="docs/portfolio-validation.md">历史验证</a>
</p>

---

## 为什么要做这个项目

强化学习训练往往在很早的时候就已经出现问题，却要几个小时以后才被人发现。更麻烦的是，事后分析很容易“无意中作弊”：读到了最终 verdict、后来才开始的 run，或者当时尚未到达的数据。

这个项目把 **训练可靠性** 当成一个独立系统问题：

| 时间正确回放 | 决策质量 | 可检查证据 |
| --- | --- | --- |
| 用不可变事件，同时检查事件时间和可用时间，构建真正的 as-of 视图。 | 分开计算停止精确率、误停占比、成功样本误杀率、失败召回率、覆盖率和拒绝判断。 | 保存事件流、逐次决策输入、实验协议、环境信息、报告和 SHA-256 清单。 |

当前系统仍然是 **recommendation-only**，不会自动停止训练。

## v0.2 已验证快照

验证日期：**2026-10-06**。下面是本次候选版本实际跑出来的结果，不是永远不会过期的宣传数字。

| 检查 | 实测结果 |
| --- | --- |
| Linux 严格新核心 | **62 个测试方法 + 25 个子测试通过**，required strict Pyright 通过 |
| Windows 严格新核心 | **同一组测试通过**，required strict Pyright 通过 |
| 历史回归 | Linux **265 项通过** |
| 未来信息扰动 | **200 / 200** 组未来修改没有改变历史输入哈希和建议 |
| 受控 RL 实验 | **18 次真实 Q-learning**：6 pass / 12 fail |
| rules-v2，70% 训练预算 | 抓住 **6 / 12 失败**，误停 **0 / 6 成功**，**1 / 18** 拒绝判断 |
| 历史 Go2W 数据 | 因旧 schema 缺少 available-at 证据，**严格可回放 run = 0** |

> 受控实验很小。它证明的是回放和证据链能工作，**不是已经证明生产早停有效，更不是 Go2W 泛化结论**。完整记录见 [v0.2 实测验收](docs/VALIDATION_V2_2026-10-06.md)。

## 架构

<p align="center">
  <img src="docs/assets/replay-pipeline-architecture.svg" alt="RL 训练可靠性 v0.2 架构" width="100%" />
</p>

严格路径故意保持窄而清楚：

```text
训练观察器
    ↓
不可变 start / sample / finish / label 事件
    ↓
SQLite 事务 EventStore
    ↓
截止时刻 T 的 as-of 快照
    ↓
建议策略
    ↓
使用最终结果做 chronological replay
    ↓
指标 + 证据 bundle
```

历史 CSV 仍保留分析能力，但被明确隔离为 **retrospective-only**。旧 schema 无法证明每个字段当时什么时候真正可用，因此不伪造迁移。

## 直接跑完整实验

使用 Python **3.12+**。严格核心不需要 GPU，也不依赖 pandas、MuJoCo 等第三方运行库。

```bash
python -m pip install -r requirements-core-dev.txt
python -m pip install --no-deps -e .

python -m rl_risk_replay experiment --out artifacts/controlled-run
python -m rl_risk_replay verify --bundle artifacts/controlled-run
```

然后打开：

```text
artifacts/controlled-run/report.html
```

产物包括：

```text
report.html          可离线打开的报告
results.json         指标 + 每次决策证据
events.jsonl         不可变实验事件流
q_tables.json        最终 Q 表
protocol.json        受控实验精确协议
manifest.json        文件、字节数与 SHA-256
```

如果输出目录已经存在，程序会拒绝覆盖。

## v0.2 的可靠性契约

| 风险 | 新核心怎么处理 |
| --- | --- |
| predictor 看见未来 run 或目标最终信息 | 目标输入只允许白名单字段；最终 verdict 和 duration 不进入预测输入。 |
| run 一结束就假定标签已经存在 | finish 和 label 是不同事件，label 只有在自己的 `available_at` 到达后才可见。 |
| 迟到遥测 step 很小，被错误放进过去 | 同时检查 `event_time` 和 `available_at`。 |
| 历史训练成员事后发生变化 | 历史 run 与标签相对于目标开始时刻冻结。 |
| 缺数据被偷偷当成“健康” | 策略可以 abstain，覆盖率和拒绝判断单独记录。 |
| “误杀率”到底除什么不清楚 | 分开输出 `stop_precision`、`false_stop_share`、`pass_kill_rate`、`fail_recall`。 |
| 多文件写到一半形成混合版本 | SQLite 批次要么全部提交，要么全部回滚。 |
| 报告文件被独立覆盖 | 先暂存，再发布到新目录，并用 manifest 校验。 |

## 受控实验到底发现了什么

实验真实执行 **6 个种子 × 3 种条件 × 4,000 环境步**，都在同一 4×4 网格里。最终标签来自训练结束后的 30 回合评估，并使用不同评估 RNG。

| 条件 | Pass | Fail |
| --- | ---: | ---: |
| 正常学习 | 6 | 0 |
| 关闭学习 | 0 | 6 |
| 训练到 55% 时清空策略并冻结学习 | 0 | 6 |
| **合计** | **6** | **12** |

在 70% 预算检查点：

| 策略 | 失败召回 | 误停成功 run | 覆盖 |
| --- | ---: | ---: | ---: |
| Always continue | 0 / 12 | 0 / 6 | 18 / 18 |
| Always stop | 12 / 12 | 6 / 6 | 18 / 18 |
| **Rules-v2** | **6 / 12** | **0 / 6** | **17 / 18** |

真正有意思的结论不是“零误杀”。成功样本只有 6 个，把 0/6 宣传成总体零误杀属于让统计学提前下班。

有价值的结论是：rules-v2 抓到了 **后期策略被破坏** 的 6 次失败，却完全漏掉了 **从来没有学会** 的另外 6 次失败。这个盲区被原样留下，作为下一轮研究对象，而不是看完答案以后再调阈值。

## 接入一条新的训练任务

初始化事件库：

```bash
python -m rl_risk_replay init --db artifacts/training.sqlite
```

把生命周期和观测分开记录：

```bash
python -m rl_risk_replay record --db artifacts/training.sqlite --run-id run-001 \
  --kind start --payload '{"task":"go2w-navigation","seed":"1","planned_steps":2000000}'

python -m rl_risk_replay record --db artifacts/training.sqlite --run-id run-001 \
  --kind sample --payload '{"step":100000,"reward":15.2,"approx_kl":0.02}'

python -m rl_risk_replay record --db artifacts/training.sqlite --run-id run-001 \
  --kind finish --payload '{"status":"completed"}'

python -m rl_risk_replay record --db artifacts/training.sqlite --run-id run-001 \
  --kind label --payload '{"verdict":"pass","source":"evaluation"}'
```

只回放当时真正可见的信息：

```bash
python -m rl_risk_replay replay \
  --db artifacts/training.sqlite \
  --out artifacts/replay-001
```

训练重启或 step 归零时必须使用新的 run ID。远端 producer 需要可比较的时钟；未来时间事件会被拒绝，不会静默“修正”。

## 仓库导航

| 路径 | 作用 |
| --- | --- |
| `rl_risk_replay/` | v0.2 严格核心：事件、回放、指标、存储、报告 |
| `tests_v2/` | 严格核心、对抗时间泄漏、事务与跨平台测试 |
| `scripts/validate_reliability_v2.py` | 受控 RL、未来扰动和历史数据审计 |
| `legacy/` | 冻结的历史回放实现 |
| `data/datasets/` | 原样保留的历史 CSV |
| `docs/RELIABILITY_V2.md` | 事件协议、API、迁移与边界 |
| `docs/VALIDATION_V2_2026-10-06.md` | v0.2 实测证据 |

## 证据边界

v0.2 **已经支持**：

- 已测协议范围内按 available-at 隔离的 as-of 输入；
- 追加式标签修订和冻结历史成员；
- 事务存储与可校验证据 bundle；
- Linux / Windows 严格新核心运行；
- 可复现的 RL 故障注入受控实验。

v0.2 **尚未证明**：

- 校准后的失败概率；
- 可以安全自动执行的生产早停；
- 实际 GPU 成本节省；
- 跨任务或 Go2W 预测泛化；
- 面对恶意 producer 的认证型 provenance；
- 整个历史仓库类型债务清零。

历史外围代码仍有 advisory 类型问题。新核心 strict Pyright 通过，只说明**新核心**通过硬门槛，并不会让旧代码在夜里自动顿悟。

## 复现实测

```bash
python -m pytest tests_v2/ -q
python -m pyright --project pyright-core.json
python scripts/validate_reliability_v2.py --out artifacts/reliability-v2
```

历史套件：

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests/ -q
python scripts/validate_reliability_v2.py --include-legacy --out artifacts/legacy-validation
```

精确环境、首轮失败与修复记录、artifact 哈希和声明边界见 [v0.2 实测验收记录](docs/VALIDATION_V2_2026-10-06.md)。
