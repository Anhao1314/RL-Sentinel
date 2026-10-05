# RL 训练可靠性与风险回放

这是训练器之外的**可靠性与证据层**，不是交易回测，也不是已经验证有效的自动早停器。
0.2 版把主入口升级为 `python -m rl_risk_replay`：记录当时真正可见的数据，
重放建议，衡量误停与漏检，并保存可以复核的实验产物。系统仍只输出本地建议。

[English](README.md) · [协议、使用与迁移](docs/RELIABILITY_V2.md) · [历史验证记录](docs/portfolio-validation.md)

## 先运行一次真实实验

在仓库根目录，使用 Python 3.12 或以上，无需 GPU，也无需安装第三方运行依赖：

```bash
python -m rl_risk_replay experiment --out artifacts/controlled-run
python -m rl_risk_replay verify --bundle artifacts/controlled-run
```

程序实际执行 18 次表格型 Q-learning：同一 4×4 网格场景、6 个种子，分别使用
正常学习、关闭学习、后期清空策略并冻结学习三种条件。每次训练 4000 个环境步，
每 250 步记录评估，最终用不同随机种子的 30 回合评估产生标签。
在预算 30%、50%、70% 比较规则、始终继续、始终停止三个策略。

**这是真实运行的受控故障实验，不是生成的假日志，也不是 Go2W 或实际部署效果验证。**
故障条件保存在评估侧，不放进预测器的目标输入。所有产物保留 `controlled` 标记，
即使成功/失败样本数量达到门槛，也不允许被标记为已证明实际效果。

输出目录包含可离线打开的 `report.html`、逐次决策与输入的 `results.json`、
事件流、最终 Q 表、实验协议与 SHA-256 清单。目录已存在则拒绝覆盖。

## 改造的核心

| 原问题 | 新协议 |
| --- | --- |
| 整张 runs 表进入在线视图 | 目标输入仅含白名单启动字段与截止时刻可见的观测，不含最终标签、时长和未来 run。 |
| 结束即被认为已有标签 | 结束、验收标签、人工修订分别记录；历史训练成员需要在目标开始前已经结束且标签可见。 |
| 低 step 的迟到遥测可能被提前看见 | 同时检查事件时间与可用时间，不能只按训练步数截断。 |
| 误杀率分母不清 | 明确区分停止精确率、误停占比、成功样本误杀率、失败召回率。 |
| 缺数据被误当成无风险 | 输出 abstain，单独记录覆盖率、跳过原因、未知标签。 |
| CSV 分批写入不一致 | 新事件存储采用 SQLite 事务；输出先暂存，完成后以清单校验的目录发布。 |
| 原始历史数据时间证据不够 | 保留只读审计，不从文件时间、step 或最终结果反推 available_at。 |

## 接入新训练

```python
from pathlib import Path
from rl_risk_replay.storage import EventStore

store = EventStore.create(Path("artifacts/training.sqlite"))
store.record("attempt-001", "start", {
    "task": "go2w-navigation", "seed": "1", "planned_steps": 2_000_000,
})
# 在训练观察回调中，每个增长的 step 记录一个汇总样本。
store.record("attempt-001", "sample", {
    "step": 100_000, "reward": 15.2, "approx_kl": 0.02,
})
# 完成时与得到验收标签时分开记录，不要提前写最终结果。
store.record("attempt-001", "finish", {"status": "completed"})
store.record("attempt-001", "label", {"verdict": "pass", "source": "evaluation"})
```

这个示例只说明事件 API，不宣称一次 sample 足以支持风险判断。记录器用当前本机时间
标记可用时间；远端事件需要可比较的时钟。重启或 step 归零必须使用新的 run_id。

```bash
python -m rl_risk_replay replay --db artifacts/training.sqlite --out artifacts/replay-001
python -m rl_risk_replay audit-legacy --dataset data/datasets --out artifacts/historical-audit
```

新核心默认不依赖 pandas、MuJoCo 或训练服务器。支持安装为 `rl-risk` 命令，
也支持直接从仓库运行。事件字段、迟到数据、人工标签优先级与指标定义见协议文档。

## 验证边界

Linux 和 Windows 的新核心测试、200 组未来信息扰动及真实受控实验由 CI 执行；
新核心严格类型检查是硬门槛。历史全量类型检查仍是 advisory，不会因新核心通过就被说成清零。
旧测试在 Linux 上单独回归；实际测试数量、环境、结果与原始产物以对应提交的 Actions 为准。

旧 `backtest_engine.py`、`backtest_rules.py` 的命令行需要显式增加
`--legacy-retrospective`。旧 Python API 保留历史语义，实际实现归档在 `legacy/`。
**旧路径仍有已知时间泄漏，不能作为在线预测依据。** 原采集器、CSV schema、人工标签覆盖规则、
风险因子和历史报告不改写；新协议不假装修复过去的时间证据。

样本门槛不是统计显著性。当前规则并不覆盖所有失败类型，受控实验不代表跨任务泛化。
剩余墙钟时间不是实际 GPU 费用节省。当前没有自动停止训练、真实机器人干预、外部通知、
新的公开部署或许可证修改。
