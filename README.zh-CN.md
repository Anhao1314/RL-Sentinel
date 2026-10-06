# RL Sentinel

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/sentinel-hero-dark.svg" />
  <img src="docs/assets/sentinel-hero-light.svg" alt="观察、回放、核验。时间边界将 T 时刻已知的信息与之后才可用的事件分开。" width="100%" />
</picture>

**强化学习实验的可靠性与证据层，只提供建议，不自动干预。**
记录训练观测，重建决策时刻真正可用的信息，再把建议与支持它的证据一起复核。

<p><code>Python 3.12+</code> · <code>stdlib runtime</code> · <a href="https://github.com/Anhao1314/RL-Sentinel/actions">CI / Actions ↗</a></p>

[开始使用](#quick-start) · [实验结果](#results) · [接入训练](#integration) · [文档导航](docs/README.md) · [English](README.md)

> RL Sentinel 只输出本地建议，不会自动停止训练。当前实验验证的是受控场景中的工作流程，不是生产环境中的早停效果。

<a id="quick-start"></a>
## 先跑一次完整实验

使用 **Python 3.12+**。严格新核心可直接从仓库运行，无需 GPU、API Key 或第三方运行依赖。

<!-- sentinel:quickstart -->
```bash
git clone https://github.com/Anhao1314/RL-Sentinel.git
cd RL-Sentinel
python -m rl_risk_replay experiment --out artifacts/sentinel-demo
python -m rl_risk_replay verify --bundle artifacts/sentinel-demo
```
<!-- /sentinel:quickstart -->

用浏览器打开 `artifacts/sentinel-demo/report.html`。程序会真实执行 **18 次 Q-learning 训练**，再比较三种建议策略。输出目录必须是新目录，已有证据不会被覆盖。

仓库名称是 **RL-Sentinel**；为保持兼容，Python 模块仍是 **`rl_risk_replay`**。[安装、产物说明与常见问题 →](docs/GETTING_STARTED.zh-CN.md)

## 它可以帮你做什么

**复盘一次训练。** 查看给出建议时已经可用的观测与历史样本，而不是拿最终结果倒推当时的判断。

**比较不同建议策略。** 对同一批运行回放规则、始终继续、始终停止三种策略，同时保留漏检、误停与拒绝判断的情况。

**交付可核验的实验。** 把事件流、决策输入、配置与结果放进同一个证据包，附上文件清单和哈希，便于他人复核。

<a id="time-boundary"></a>
## 最重要的是这条时间边界

事件发生了，不代表系统当时已经知道。决策之后才生成的报告，即使描述的是更早的训练步数，也不应该影响过去的建议。

RL Sentinel 同时检查 **`event_time` 与 `available_at`**。目标运行的最终结果只用于事后评分，不进入预测输入；可用历史样本在目标启动时冻结。

<details>
<summary><strong>展开查看：观测 → 决策 → 证据</strong></summary>

<picture>
  <source media="(max-width: 600px)" srcset="docs/assets/replay-pipeline-mobile.svg" />
  <img src="docs/assets/replay-pipeline-architecture.svg" alt="观测写入 SQLite，as-of 视图筛选当时可见的输入，策略产生建议。目标运行的结束与标签事件仅在评分侧接入。旧 CSV 独立保留为审计路径。" width="100%" />
</picture>

这是可信生产者与预测器之间的数据接口边界，不是防恶意代码的沙箱。[事件字段、人工标签优先级与时间语义 →](docs/RELIABILITY_V2.md)

</details>

<a id="results"></a>
## 实验发现了什么

**已归档验证：2026 年 10 月 6 日，v0.2。** 同一张 4×4 网格，6 个种子、3 种条件，每次训练 4,000 个环境步；最终使用不同随机数流评估 30 回合。这是受控故障注入，不是 Go2W 留出场景基准。[协议与原始证据 →](docs/VALIDATION_V2_2026-10-06.md)

正常学习产生 6 次成功；关闭学习、在 55% 预算处清空策略分别产生 6 次失败。在 **70% 预算检查点**：

| 策略 | 识别失败 | 误停成功运行 | 决策覆盖 |
| --- | ---: | ---: | ---: |
| 始终继续 | 0 / 12 | 0 / 6 | 18 / 18 |
| 始终停止 | 12 / 12 | 6 / 6 | 18 / 18 |
| **Rules-v2** | **6 / 12** | **0 / 6** | **17 / 18** |

**结论也包括盲区：** rules-v2 识别了 6 次后期策略清空故障，却漏掉全部 6 次关闭学习的失败；另有 1 次拒绝判断。在 30% 和 50% 检查点，没有停止建议。

6 个成功样本中没有误停，**不等于总体误杀率为零**。所有受控结果仍为 `readiness: blocked`，不声称实际节省了算力。

<details>
<summary><strong>展开查看：验证范围与复现命令</strong></summary>

同一份 v0.2 验收记录中，Linux 和 Windows 分别通过 **62 个核心测试方法 + 25 个子测试**，Linux 另通过 **265 项历史回归**，严格新核心 Pyright 通过。跨平台重复运行不计为更多独立测试。

**200 组未来信息扰动**均未改变历史输入哈希和建议；修改已经可见的信息，两者均改变，作为负对照。这些是约定范围内的测试，不是所有可能输入的形式化证明。

```bash
python -m pip install -r requirements-core-dev.txt
python -m pytest tests_v2/ -q
python -m pyright --project pyright-core.json
python scripts/validate_reliability_v2.py --out artifacts/core-validation
```

完整历史套件还需安装 `requirements-dev.txt`。全仓类型检查仍为非阻断检查，历史债务未清零；严格门槛仅覆盖 `rl_risk_replay/`。

</details>

<a id="integration"></a>
## 接入你自己的训练观测

在可信的观察回调里使用 `EventStore.record`，分别记录 `start`、步数递增的 `sample`、`finish` 与 `label`。只有实际评估或人工复核产生了标签，才记录它。这是事件 API，不是已经完成的 SB3、ROS 或远端集群集成。

对已经存在的事件库运行：

```bash
python -m rl_risk_replay replay --db artifacts/training.sqlite --out artifacts/training-review
```

[接入观察器 →](docs/GETTING_STARTED.zh-CN.md#integration) · [完整事件协议 →](docs/RELIABILITY_V2.md)

<a id="boundaries"></a>
## 能力边界与旧版本兼容

**严格 v0.2 路径：** 事件校验、按时间筛选输入、明确的决策指标、SQLite 批次事务和可核验的证据包。新规则只覆盖一个子集，不等于旧规则全部迁移，也没有校准失败概率。

**历史路径：** 原 CSV、人工标签和报告保持不变，不推算缺失的可用时间戳。现有历史运行没有直接满足严格回放要求的样本；旧 Python API 保留已知的事后信息泄漏，旧回放 CLI 必须显式使用 `--legacy-retrospective`。

**尚未验证：** 生产环境安全干预、跨任务预测泛化、大规模采集与来源认证。哈希只能核对内容与清单，不能替代签名；可比较的生产者时钟和正确的运行身份仍是前提。

[浏览文档 →](docs/README.md) · [迁移说明与未完成项 →](docs/RELIABILITY_V2.md) · [核心源码 →](rl_risk_replay/)

---

由 [Anhao1314](https://github.com/Anhao1314) 维护。本次更名更新项目品牌，不改变 Python 导入路径、历史证据或许可证状态。
