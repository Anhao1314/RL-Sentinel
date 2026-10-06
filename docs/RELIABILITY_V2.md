# 0.2 协议、迁移与实验说明

## 一、两个版本，不混用证据

基线提交为 `3be020e1df6a7a0fcd9e3af62c629126b658fd6e`。
新核心位于 `rl_risk_replay/`；旧实现保留在 `legacy/`，旧文件路径仅作兼容入口。
不是把旧 CSV 加上一列推算时间就宣称消除了泄漏。历史 schema、风险因子、人工标签、
原始数据与报告均保留；旧 Python API 的测试验证的是兼容性，不是新协议的时间正确性。

旧命令需要明确选择历史语义：

```bash
python backtest_engine.py --legacy-retrospective --today 2026-09-12 --out artifacts/legacy-replay
python backtest_rules.py --legacy-retrospective --today 2026-09-12 --out artifacts/legacy-rules
```

上面仍然是事后诊断路径，包括旧指标口径和已知元信息泄漏。不用于新在线预测结论。
`run_pipeline.py` 等历史分析工具保持原语义；它们没有被悄悄替换为 v2。

## 二、时间与事件合同

每条 JSONL 的首行声明来源，后续是事件。时间统一为非负、有限的 Unix 秒。
来源枚举为 `observed`（生产者声明的观测）、`controlled`（真实受控实验）、
`synthetic`（测试夹具）。来源是声明，不是密码学认证。

```json
{"schema_version":2,"origin":"observed"}
{"schema_version":2,"event_id":"e1","run_id":"attempt-1","kind":"start","event_time":100,"available_at":100,"payload":{"task":"navigation","seed":"0","planned_steps":2000000}}
```

| 事件 | payload | 约束 |
| --- | --- | --- |
| start | task、seed、planned_steps | 每个 run 只能有一次，计划步数必须为正整数。 |
| sample | step；可选 reward、approx_kl、value_loss、memory_percent | 只允许白名单字段；按事件时间排序后 step 严格递增；采样时汇总同一 step 的指标。 |
| finish | status：completed / failed / cancelled | 最多一次；是运行生命周期信息，不自动等于验收失败标签。 |
| label | verdict：pass / fail / unknown；source：manual / evaluation | 必须在已可见的 finish 之后；同来源同可用时刻的冲突修订拒绝。 |

`event_time` 是源事件发生时间，`available_at` 是生产者可观察该事件的时间。
必须满足 `event_time <= available_at`。记录器使用本机当前时间记录可用时间，不根据 step 反推。
这不是外部仪表盘的发布时间，也不是远端时钟同步服务。跨主机需确保时钟可比较；
不满足约束时拒绝，不默默调整。NaN/Infinity 遥测是合同错误，不被视为“健康”记录。

标签修订以新事件追加，旧值不可更新。人工非空标签在其可见后优先于自动标签；
人工 unknown 是明确撤销有效标签，不能被未来自动标签悄悄覆盖。
CSV 的人工空字段保留自动值仍由旧采集器负责；v2 不导入不可追溯的旧标签时间。

同一 run 的重启、step 归零或新预算必须建立新 run_id。这一版不建模预算随时间修订，
不把多个 attempt 拼成一个伪单调轨迹。

## 三、预测器究竟可以看到什么

目标视图只含截止时刻、启动事件、可见 sample，以及严格早于目标启动的历史训练成员。
目标的 finish、label、最终时长和其他未来 run 均不进入视图。

历史成员需同时满足：已知启动、finish 可用时间早于目标开始、标签可用时间也早于目标开始。
成员及其标签在目标开始时冻结；之后的人工修订、迟到遥测和同任务分布变化不能改变历史输入。
不根据估计结束时间、全量奖励排名或最终 fail_score 选择历史样本。

决策检查点取首次**可见**达到预算比例的观测可用时间。迟到的低 step 记录不会提前出现。
该时刻已经观察到 finish 时，跳过建议并记录原因。`--evaluation-at` 限制整个评估可见范围。
评估器只能在预测器返回后接入最终标签；预测输入哈希只覆盖该时刻真正可见的信息。
未来标签可以改变事后评分，但不能改变当时的输入哈希或建议。

这是可信调用者之间的数据接口边界，不是恶意 Python 插件的进程沙箱。
自定义预测器若自行读磁盘、网络或外部闭包数据，不在本合同的隔离保证内。

## 四、规则与缺失数据

当前 `rules-v2` 是受版本控制的最小规则子集：正奖励峰值后的相对回撤、KL、内存压力。
它不声称等价迁移全部旧风险规则。`value_loss` 允许记录，但这一版不据它判定失败。
样本不足或奖励峰值非正时，对回撤判断 abstain，而不是捏造可靠概率。

默认配置：至少 3 个奖励点；回撤 stop 阈值 0.3；KL watch/tune 为 0.03/0.1；
memory resize 为 95%。这些是可调的演示规则，不是跨任务最优阈值。

```json
{"min_reward_points":3,"drawdown_stop":0.3,"kl_watch":0.03,"kl_tune":0.1,"memory_resize":95}
```

用 `--rule-config path.json` 显式传入；参数记录到报告，未知键和非法范围直接拒绝。
R0–R3 是规则严重程度，不是概率。所有操作为建议；CLI 没有停止进程、重启训练或发送通知的能力。

## 五、指标分母与无法测量的内容

每个策略、每个预算检查点独立汇总，同一 run 不可重复累计。

| 指标 | 分母与意义 |
| --- | --- |
| stop_precision | 正确停止的失败 run / 已有标签的 stop。 |
| false_stop_share | 被误停的成功 run / 已有标签的 stop。 |
| pass_kill_rate | 被误停的成功 run / 可评估的成功 run，含 abstain。 |
| fail_recall | 正确停止的失败 run / 可评估的失败 run，含 abstain。 |
| decision_accuracy | 二元 stop/非 stop 正确数 / 作出决策且有标签的 run。 |
| decision_coverage | 作出建议而非 abstain 的 run / 全部目标 run，含未达到检查点。 |

无分母时输出 JSON null，不写 NaN，也不把“无成功样本”写成“0% 误杀”。
提供成功样本误杀率的描述性 Wilson 95% 区间，但不由此假定 run 独立。

`counterfactual_failed_run_remaining_wall_seconds` 只累计正确停止建议之后到记录 finish 的
剩余墙钟时间。它不是 GPU 利用时间或实际费用节省。误停侧也只报告剩余墙钟时间，
没有把它假装成失败机会成本或因果 regret。`realized_compute_savings` 保持 null。

5 pass / 10 fail 是报告准备度的操作下限，不是统计充分性。存在 abstain、跳过、未知标签，
或来源为 synthetic / controlled 时，准备度保持 blocked。即使 observed 数据满足所有门槛，
也只得到 `candidate_for_external_validation`，而不是已证明生产有效。

## 六、事务、产物与安全边界

SQLite 中事件只追加；同 ID 同内容的重试幂等，同 ID 不同内容拒绝。
一批记录先校验，再在 `BEGIN IMMEDIATE` 事务中提交；任何插入失败回滚整批。
普通 SQL 更新/删除事件被触发器拒绝。数据库拥有者仍能修改 schema，因此不是防恶意管理员的审计账本。

输出先写临时目录，成员写入成功后写完成清单，再发布到新目录。协作式写锁阻止两个发布者
争用同名目标；已有目录和符号链接目标不覆盖。失败暂存不显示为完整产物。
`verify` 检查清单、精确文件集合、长度、SHA-256，并拒绝路径穿越和成员符号链接。
这保护正常进程级发布，不宣称所有文件系统或掉电情形下都具有完整持久性保证。

参考实现语义：[Python sqlite3](https://docs.python.org/3.12/library/sqlite3.html) 与
[SQLite transaction](https://sqlite.org/lang_transaction.html)。

新核心目前对事件集做内存校验，追加也会读取并验证已有事件。
这优先保证小规模证据正确性，不能声称已适配海量长期遥测；后续需做有基线的索引与增量校验优化。

## 七、实验与可复核产物

```bash
python -m pip install -r requirements-core-dev.txt
python -m pytest tests_v2/ -q
python -m pyright --project pyright-core.json
python scripts/validate_reliability_v2.py --out artifacts/release-check
```

完整旧环境安装 `requirements-dev.txt` 后，可加 `--include-legacy`。
该模式额外复现旧视图泄漏反例，并对仓库 CSV 做只读清单审计、核对前后哈希不变。
旧数据没有被“清洗到通过”，也不向其补造时间字段。

实验包括 200 组未来事件/标签/未来 run/输入行序扰动，核对输入哈希和建议不变；
反向对照修改可见奖励，必须同时改变哈希和建议，防止用一个恒定输出假装隔离成功。
事务中途插入失败、fsync 失败、并发追加、同名输出、缺字段、非法数值、迟到标签等由单元测试覆盖。

真实 RL 实验由 `controlled.py` 执行。训练使用 epsilon=0.2、学习率=0.3、discount=0.95，
每 run 4000 个环境步。观察期评估 20 回合，最终使用不同 RNG 种子的 30 回合评估。
场景不变，因此不是场景外泛化。正常、关闭学习、后期清空策略三种条件不进入目标预测视图。
最终 Q 表、逐回合成绩、全部时间戳、决策输入与规则结果一并存档。

`environment.lock.txt` 是对应运行的实际安装清单，不是预先声称全平台可复现的依赖解析锁。
运行时记录源码提交与工作树状态。CI 分别验收 Linux/Windows 新核心与 Linux 旧套件，
将 JUnit、类型诊断、实验记录和完整 bundle 上传为独立 artifact。

## 八、仍未完成

没有收集新的 Go2W 多任务训练数据，没有证明跨任务早停效果，没有用合成数据冒充生产样本。
没有训练已校准的失败预测器，没有真实算力节省结论。旧全仓类型债务、旧采集链路的多表事务、
大规模索引、真实训练框架回调集成仍需独立迭代。新事件 API 可接入训练观察者，
不等于已在远端训练集群上线。
