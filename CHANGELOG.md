# 变更日志

本文件记录 RL-Sentinel 的重要变更。格式参考 Keep a Changelog，版本号参考语义化版本（SemVer）。
说明：本仓库此前没有 LICENSE / CHANGELOG / ADR / SECURITY / ROADMAP，0 tag、0 release、
0 open issue，9 个 PR 全部合并或关闭；下列版本号是对提交历史的回溯标注，不对应已发布版本。
日期以提交历史为准。

- 仓库：Anhao1314/RL-Sentinel（公开仓库，owner Anhao1314），基线 commit 5315865（2026-10-19）。
- 命名历史：RL Sentinel / rl_risk_replay / 发行包名 rl-training-risk-replay（pyproject.toml）/
  CLI 名 rl-risk。

## [Unreleased]

本次为「收尾式改造」：将仓库定位收敛为「RL 实验时间正确性 / 证据链的最小参考实现」；
停止按原目标（在线预测 + 建议制止损）产品化投入。**本批次只含可逆改动**；
git 历史改写、data/ 产物迁移、v1 归档与发版属第二批，需单独确认。

### 新增
- 治理文件：LICENSE（MIT，版权行 `Copyright (c) 2026 Anhao1314`；如需更换由所有者决定）、
  CHANGELOG.md、SECURITY.md、RESTART.md、docs/adr/0001-from-prediction-to-evidence.md。
- `rl_risk_replay/policy.py`：单一规则定义源。两套算法分别注册为
  `rules-v2/strict-replay@1` 与 `rules-v2/cohort-backtest@1`；显示名 `rules-v2` 保留，
  以维持既有结果与报告可复现，但引用结论必须给出 rule_id。
- `CLAIMS.json`：对外结论台账（3 条 verified、1 条 unverified、1 条 retired），
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
  `requirements*.txt` 保留为 CI 快照。
- CI：新增 `research-and-audit` job（`fetch-depth: 0`），覆盖研究层测试、
  时间隔离契约、结论重算、规则单一源、全历史密钥扫描、最小致命规则 lint 与环境记录。

### 实测（本批次，Python 3.13.12 / macOS）
- 三套测试 **342 passed + 29 subtests passed**；文档一致性检查 75 个相对链接、4 条命令通过。
- 默认实验证据包 18 runs：**15 MB → 2.1 MB**；36 runs：**59 MB → 8.0 MB**。
- 注入旧 mtime 空锁文件后 `experiment` 自愈成功；`audit` 四个子命令全部通过。

### 已知限制 / 未在本批次完成
- 历史 CSV 无 `available_at`，研究层多数行仍为 degraded；严格 as-of 需训练端
  producer 提供独立 availability clock（RESTART.md 判据②）。
- `views.jsonl` 中的 `view.history` 仍随 run 数线性增长，整体有残余平方项；
  本批次消除的是「每条 decision 内嵌完整 View」与「HTML 内嵌全量 results.json」两项主要来源。
- 未执行：git 历史改写（webhook 轮换后由所有者决定）、`data/` 142 份产物迁移、
  v1 模块归档、发 tag / release、顶层旧文档加 RETIRED 横幅。
- 全仓 ruff 全量规则仍有 39 条风格建议；CI 目前只强制致命规则集（E9/F63/F7/F82）。

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
