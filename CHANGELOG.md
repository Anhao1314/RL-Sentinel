# 变更日志

本文件记录 RL-Sentinel 的重要变更。格式参考 Keep a Changelog，版本号参考语义化版本（SemVer）。
说明：本仓库此前没有 LICENSE / CHANGELOG / ADR / SECURITY / ROADMAP，0 tag、0 release、
0 open issue，9 个 PR 全部合并或关闭；[0.1.0] 与 [0.2.0] 是对提交历史的回溯标注，
不对应已发布版本。原 `[Unreleased]` 段定稿为 [0.3.0]，自该版本起打 tag / 发 release。
日期以提交历史为准。

- 仓库：Anhao1314/RL-Sentinel（公开仓库，owner Anhao1314），基线 commit 5315865（2026-10-19）。
- 命名历史：RL Sentinel / rl_risk_replay / 发行包名 rl-training-risk-replay（pyproject.toml）/
  CLI 名 rl-risk。

## [Unreleased]

### 新增（训练平台层，v0.4 方向，2026-11-06）

- 【新增】`sentinel_agent/`：staleness-aware 训练守护层。严格核心 `rl_risk_replay/` 与既有结论
  零改动；事件仍由 schema-v2 契约逐条校验，agent 拒绝契约外数据（`sentinel_events_invalid_total`）。
- producer SDK：torchrun rank 感知 run_id（rank 编码进 run_id，不扩展冻结 schema）、显式
  `defer_seconds` 可用性声明（延迟信号如实申报，不伪造即时性）、周期 fsync、输出禁止覆盖
  （`open("xb")`）；`tests_agent` 验证 SDK 输出通过 `validate_events` 全量校验。
- agent：按 run 的 producer 静默检测（`--stale-after`，虚拟时钟确定性测试）、`event_id` 幂等去重
  （at-least-once 摄入安全）、崩溃后全量重放重建状态、Prometheus 文本指标（缺失指标不伪造为 0）、
  可选飞书格式 webhook（仅通知人工核查，不触碰训练进程）。
- 部署：`deploy/` 一键 compose（trainer + agent + Prometheus + Grafana 预置看板，匿名只读）。
- 框架适配：`sentinel_agent/integrations/`——SB3 与 Lightning 回调（懒加载框架，观察者语义
  永不中止训练；重复 step 与非有限值跳过而非伪造）；`MetricForwarder` 框架无关搬运逻辑以
  桩基类测试，无需安装框架；`docs/WATCHDOG_INTEGRATIONS.md` 覆盖通用循环 / SB3 / Lightning /
  torchrun 多进程（rank 编码进 run_id，每 rank 独立文件 + 独立 agent）与 HTTP 摄入模式。
- 断点续训证据：`sentinel_agent/resilience.py` 把 SIGTERM/SIGINT 转为契约合法的
  `finish("cancelled")`（fsync 落盘后恢复平台默认 kill 语义，SIGKILL 由静默告警兜底）；
  agent 按 (task, seed) 派生续跑谱系（`attempt` 序号、`resumed_after_cancelled`）与浪费算力
  下限（`sentinel_preemptions_total` / `sentinel_steps_lost_total`），零 schema 改动；
  登记声明 `agent.selftest.resilience`（verified，与 staleness 同一 selftest 复现）。
- 操作台：`sentinel_agent/console.py` 在 agent 根路径侍服单文件零依赖控制台——
  SSE（`/stream`）实时推送快照，canvas 绘制 silence/reward 双曲线、run 谱系表（attempt/
  续跑标志）、告警流；动态数据仅经 `textContent` 注入（无模板注入面），缺失指标显示 —
  而非 0；Grafana 仍负责长程分析，操作台负责"现在谁挂了"。
- 登记声明 `agent.hang-demo.staleness`（verified）：`python -m sentinel_agent selftest` 以虚拟时钟
  确定性复现「最后指标看似健康 → 静默告警 → 数据恢复后重置 → finish 后不再告警」全链路。
- CI：strict-core job 增跑 `tests_agent/`；ruff 门禁覆盖 `sentinel_agent`、`tests_agent`。

### 状态同步与文档更正（2026-11-05）

本次为冻结期内的状态同步与文档更正（2026-11-05），不改变代码、不改写 git 历史。

- 【事实】所有者于 2026-11-05 确认该飞书 webhook 已在飞书侧轮换并停用，历史 URL 不再有效；
  `.security-allowlist.json` 三条登记的状态由 `recorded-pending-rotation` 改为 `revoked`
  （已撤销），登记条目与 `audit/secrets.py` 的 `feishu_webhook` 检测规则保留；
  SECURITY.md、ADR 0001、ADR 0002、RESTART.md、ARCHIVE.md 与 CHANGELOG 状态同步。
- 【事实】清理残留：5 份 RETIRED 历史文档（PROJECT_CONTEXT.md、PHASE_RECORD.md、
  QUANT_PLAN.md、DATA_DICTIONARY.md、MATH_LIBRARY.md）中过时的「飞书通知已移除」状态行
  中性化为「外部通知已移除」，其余正文与 RETIRED 横幅未改动。如实记录：这偏离了 [0.3.0]
  中「正文一字不改」的声明；该改动属于冻结期允许的「文档更正」，改动范围仅上述 5 行。
- 【事实】保留 SECURITY.md、ADR 0001 / 0002、`.security-allowlist.json` 与
  `audit/secrets.py` 的 `feishu_webhook` 检测规则；不改写 git 历史、不删事件记录、
  不删检测能力。ADR 0002 的不改写历史决策维持，轮换完成后改写收益进一步降低。
- 【事实】git 历史中的明文值仍可取回（至少 3 个提交、4 次 diff、1 个不同凭据值），但凭据
  已失效；当前 HEAD 工作树无该值。本仓库没有、也不会清理 git 历史。
- 【待确认】该 webhook 的调用日志是否已被核查、是否曾被非授权调用；是否另有 allowlist 之外
  的泄露渠道。仓库与所有者均未提供该信息。

## [0.3.0] - 2026-11-05

> 后续状态更新见 [Unreleased]。

本版本合并两批改造。第一批（PR #10，main 合并提交 `0e5cf3a`）把仓库定位收敛为
「RL 实验时间正确性 / 证据链的最小参考实现」，停止按原目标（在线预测 + 建议制止损）
产品化投入；第二批在 `refactor/archive-v1` 分支完成 v1 物理归档与发版。两批均不改写
git 历史、不轮换 webhook（见「已知限制 / 未在本批次完成」）。

### 新增
- 治理文件：LICENSE（MIT，版权行 `Copyright (c) 2026 Anhao1314`；如需更换由所有者决定）、
  CHANGELOG.md、SECURITY.md、RESTART.md、docs/adr/0001-from-prediction-to-evidence.md。
- `rl_risk_replay/policy.py`：单一规则定义源。两套算法分别注册为
  `rules-v2/strict-replay@1` 与 `rules-v2/cohort-backtest@1`；显示名 `rules-v2` 保留，
  以维持既有结果与报告可复现，但引用结论必须给出 rule_id。
- `CLAIMS.json`：对外结论台账（4 条 verified、1 条 unverified、1 条 retired），
  每条含分母、适用边界与复现命令；`python -m audit claims --verify` 在 CI 重算，数字漂移即失败。
- `.security-allowlist.json`：唯一登记的历史凭据事件（fingerprint / commit / path / reason），
  任何未登记命中阻断 CI。
- `audit/`：自审 CLI（claims / rules / secrets / env）；`contracts/time_isolation.py`：
  可移植的未来扰动与标签隔离契约，配套负对照测试
  （`tests_v2/test_contracts.py`、`tests_research/test_asof_contract.py`）。

### 变更
- 研究层不再把「按 step 过滤」表述为严格 as-of：`factor_panel` 新增 `asof_mode`
  （strict / degraded）；`compute_checkpoint_factors` 支持外部传入 `as_of` 门槛；
  `run_backtest` 的 readiness 样本门槛只计入 strict 行，并把 `data_sufficiency`
  与 `policy_effectiveness` 分开输出（后者样本不足时为 N/A，不写 0）。
- 证据包：manifest 增加确定性 provenance（code_revision / environment /
  input_manifest / rule_registry），`verify` 强制校验；as-of 输入按 `input_sha256`
  去重存入 `views.jsonl`，决策行只保留引用；`report.html` 不再内嵌全量结果。
- 崩溃恢复：锁文件带创建时间，超过 300 秒的陈旧锁可回收；写入仍为 staging + 原子 rename。
- 依赖声明：`pyproject.toml` 增加 `dev` extra 并注明其为唯一权威声明；
  研究层 6 个运行时依赖从可选 `research` extra 移入 `dependencies`，修复
  `pip install .` 后 `rl-research` 裸装 ImportError；v1 的 `requirements.txt` /
  `requirements-dev.txt` 归档，`requirements-core-dev.txt` 与
  `requirements-research.txt` 保留为钉住版本的 CI 快照，由 `audit deps` 校验一致性。
- CI：新增 `research-and-audit` job（`fetch-depth: 0`），覆盖研究层测试、
  时间隔离契约、结论重算、规则单一源、全历史密钥扫描、依赖一致性、
  活跃代码全量 lint、环境记录，并检出 `archive/v1` 校验归档产物哈希。

### 第二批：v1 物理归档与发版

- 【事实】v1 资产物理归档到 `archive/v1` 分支：该分支为 main `0e5cf3a` 的完整快照，
  已创建并推送，可 `git checkout archive/v1` 完整检出；main 上移走 18 个根目录 v1 模块、
  `legacy/`、`tests/`（265 个 v1 测试）、`data/` 142 份产物（66 csv / 39 md / 24 png /
  13 json，约 9.5 MB）、`config.json`、`run_windows.bat`、`WINDOWS.md`、
  `requirements.txt`、`requirements-dev.txt` 与 5 个 v1 专用 scripts。main 保留 `rl_risk_replay/`、`research/`、
  `audit/`、`contracts/`、`tests_v2/`、`tests_research/`、`docs/`、
  `scripts/check_documentation.py`、`scripts/validate_reliability_v2.py` 与治理文件。
  范围与检出方式见 ARCHIVE.md。
- 【事实】`data/manifest.json` 保留在 main，含全部 142 个产物的路径、SHA-256 与字节数，
  作为内容寻址锚点；配套 `python -m audit data --root <archive/v1 检出路径>` 校验。
  这是把项目自身的「内容哈希证据包」机制用在仓库自身；清单是 provenance 锚点，
  不表示归档 CSV 可用于严格回放（历史 CSV 无 `available_at`）。
- 【事实】7 份历史文档（`PROJECT_CONTEXT.md`、`AGENTS.md`、`PHASE_RECORD.md`、
  `QUANT_PLAN.md`、`DATA_DICTIONARY.md`、`MATH_LIBRARY.md`、
  `docs/portfolio-validation.md`）保留在 main 原位，仅在文件开头加 RETIRED 横幅，
  正文一字不改；保留而非删除，以免破坏可追溯性与文档相对链接校验。
- 【事实】CI 移除 `legacy-regression` job（v1 测试已随 `tests/` 归档）。
- 【事实】依赖声明收口：`pyproject.toml` 为唯一权威源；研究层依赖进入
  `dependencies`（修复 `rl-research` 裸装失效），版本号升至 `0.3.0`（含
  `rl_risk_replay.__version__` 与证据包 provenance 一致）；
  `python -m audit deps` 校验两份 CI 快照的包集合、并阻止 v1 requirements 回到 main。
- 【事实】`tests_v2/test_legacy_gate.py` 由「运行旧 CLI 并要求拒绝」改为归档契约门禁：
  断言旧 shim 与 `tests/`、`legacy/` 不在 main、归档索引与 ADR 0002 存在、
  `data/manifest.json` 内部自洽、`data/` 只剩清单一份文件。
- 【事实】`scripts/validate_reliability_v2.py --include-legacy` 与
  `rl-risk audit-legacy` 不再静默失败：明确报错并指向 `archive/v1` 与哈希校验命令。
- 【事实】新增 `docs/adr/0002-no-git-history-rewrite.md`：记录不改写 git 历史来清除
  已泄露 webhook 的决策、三条论证与备选方案（见「已知限制 / 未在本批次完成」）。
- 【事实】新增 `ARCHIVE.md`（归档索引）；`SECURITY.md` 补「处置清单与责任边界」，
  `RESTART.md` 补当前冻结状态与 webhook 轮换开放问题。
- 【事实】打出 `v0.3.0` tag / release。

### 实测（第一批，Python 3.13.12 / macOS）
- 三套测试 **342 passed + 29 subtests passed**；文档一致性检查 75 个相对链接、4 条命令通过。
- 默认实验证据包 18 runs：**15 MB → 2.1 MB**；36 runs：**59 MB → 8.0 MB**。
- 注入旧 mtime 空锁文件后 `experiment` 自愈成功；`audit` 四个子命令全部通过。
- CI 全历史密钥扫描（`fetch-depth: 0`）比浅克隆发现更多命中：同一飞书 webhook 值
  在 3 个提交中重复出现（共 4 次 diff 命中、1 个不同凭据值），已逐条显式登记于
  `.security-allowlist.json`；当前 HEAD 不含该值，但**轮换状态仍未确认**。

### 实测（第二批，Python 3.13.12 / macOS）
- 归档后活跃套件 **80 passed + 29 subtests passed**（`tests_v2/` + `tests_research/`）；
  v1 的 265 个测试随其覆盖的代码退役，保留在 `archive/v1`。
- `ruff check` 对 `rl_risk_replay research audit contracts tests_v2 tests_research scripts`
  **全绿**（0 errors）；`pyright --project pyright-core.json` **0 errors**。
- `python -m audit data --root <archive/v1 检出>`：**142/142 产物哈希与字节数匹配**，
  0 缺失、0 不一致，合计 9,655,860 字节。
- `audit claims --verify`：4 条 verified 全部重算通过（新增归档数量一条）；
  `rules` / `deps` / `secrets` / `env` 均返回 0。
- 文档检查：92 个相对链接、6 份 SVG、4 条可执行命令通过；
  `quick start` 与 `verify` 通过；`pip install -e .` 后 `rl-research --help` 可用。
- 修掉两处真实潜在缺陷：`controlled.py` 的循环变量晚绑定闭包（B023）、
  `engine.py` 的 dataclass 默认值函数调用（RUF009）；其余 46 项为自动风格修复。

### 已知限制 / 未在本批次完成
- 历史 CSV 无 `available_at`，研究层多数行仍为 degraded；严格 as-of 需训练端
  producer 提供独立 availability clock（RESTART.md 判据②）。
- `views.jsonl` 中的 `view.history` 仍随 run 数线性增长，整体有残余平方项；
  本批次消除的是「每条 decision 内嵌完整 View」与「HTML 内嵌全量 results.json」两项主要来源。
- 【事实】本版本（含两批）**未做**：未改写 git 历史（决策与论证见 ADR 0002）、
  未在飞书侧轮换 / 停用 webhook（轮换状态【待确认】，见 SECURITY.md）。
  这两项不由本仓库执行，也不声称漏洞已修复。

### 冻结
- main 只接受安全修复、复现性修复、文档更正三类改动；重启判据见 RESTART.md。

## [0.2.0] - 2026-10-19

证据基础设施（rl_risk_replay/ 与 research/）：目标已收窄为「忠实记录某 checkpoint 当时真正知道什么，
产出可版本化、可校验的证据链」。日期以提交历史为准。

### 新增
- 事件契约与 append-only 存储。
- as-of 回放：按事件 available_at 做时间正确性回放。
- SHA-256 证据包。
- 受控实验与未来扰动对抗（见 docs/RELIABILITY_V2.md）。
- DuckDB 研究层（research/）。
- 双语文档与 CI（.github/workflows）。

### 已知边界
- 18 个 observed run（6 pass / 12 fail），12 个 fail 全部为 zero-S0 受控消融；
  48 个 legacy run；仅 multi-segment 一个 trainer 接入 v0.2 事件流；
  历史 CSV 无 available_at 字段，严格回放可用的历史运行数为 0。
- docs/RELIABILITY_V2.md:152-156：没有收集新的多任务数据、没有证明跨任务早停效果、
  没有在远端训练集群上线；另记「没有真实算力节省结论」。

## [0.1.0] - 2026-08

v1（RETIRED）：Go2W/MuJoCo RL 训练中预测最终水平 / 剩余时长 / 失败概率，给 R0–R3 建议制止损。
日期以提交历史为准。

### 新增
- 采集、因子、规则（R0–R3）、回测、日报，以及顶层中文文档与口径库（如 MATH_LIBRARY.md）。
- v1 的 P0–P4 计划见 PHASE_RECORD.md。

### 状态
- RETIRED：保留不删，不再作为产品方向演进（见 ADR 0001、RESTART.md）。
- 是否正式废弃 v1 的 P0–P4 计划仍待所有者确认。
