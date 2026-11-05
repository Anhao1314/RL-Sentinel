# 安全策略

## 支持范围

本仓库现定位为「RL 实验时间正确性 / 证据链的最小参考实现」。本文件的支持范围仅限该参考实现
（rl_risk_replay/、research/、相关脚本与文档），**不覆盖训练端 producer**：训练端如何采集、
上报事件、保管凭据，不在本仓库安全承诺之内（见 RESTART.md 判据②）。

## 已知历史事件：飞书 webhook 明文入库

【事实】git 历史中 `config.json` 曾明文提交飞书 webhook 完整 URL：

- commit `ff83740` 引入该值；
- commit `647fa89`（「移除外部通知并隔离本机配置」）删除该值，并新增 `config.example.json`。

【事实】当前 HEAD（基线 commit `5315865`）的 `config.json` 已不含该 webhook 值。

【事实】在该事件发生时及此后，仓库没有 SECURITY.md、没有密钥扫描门禁、没有事件记录；
该事件此前只能在 git 历史中被追溯，而无治理层留痕。这本身是项目自身 provenance
（来源与决策链）缺失的一个例证，也是本次补齐治理文档与审计门禁的动机之一
（见 ADR 0001、CHANGELOG.md）。

【待确认】**该 webhook 是否已在飞书侧轮换或停用，无人确认**。本文件不声称该暴露已被修复：
从仓库删除历史值不等于凭据失效，git 历史仍可被克隆。

## 建议处置（供所有者执行）

- 在飞书侧轮换或停用旧 webhook，使历史 URL 失效。
- 检查该 webhook 的调用日志，确认是否被非授权调用。
- 不要复用历史值：新配置使用新凭据，且仅写入本地、不提交。
- 核验 `config.json` 是否应从版本控制移除；带真实值的配置不再入库。

## 本次新增的防护（已接入 CI）

- `audit/secrets.py` 扫描 `git log --all -p`（排除 `data/`）与当前工作树的文本文件，
  命中项按 `commit + path + 值` 生成 fingerprint。未登记命中使
  `python -m audit secrets` 返回非零；CI 的 `research-and-audit` job 以
  `fetch-depth: 0` 运行它，命中即阻断合并。
- `.security-allowlist.json` 目前只登记 1 条：commit
  `ff837403c0189f2255780a92579677a6f44a58c0` 中 `config.json` 的飞书 webhook，
  fingerprint `0f6c5b265ebf4826`，状态 `recorded-pending-rotation`。这是**显式登记**
  而非静默放行：轮换完成后应更新状态；若选择改写 git 历史，应删除该条并重新全历史扫描。
- 扫描器自身（`audit/secrets.py`）与 allowlist 文件因包含检测模式而被排除；
  排除清单会在每份扫描报告中输出，保持可审计。
- 配置约定：只提交不含真实凭据的 `config.example.json`。`config.json` 目前仍在版本控制中
  （不含该 webhook 值），是否移除属【待确认】。

【事实】本批次**未**改写 git 历史、**未**轮换 webhook；轮换必须在飞书侧由所有者执行。
【事实】当前 `python -m audit secrets` 报告：1 条已登记历史事件、0 条未登记命中。

## 报告渠道

- 优先使用 GitHub 私有漏洞报告（仓库 Security → Report a vulnerability）提交，避免在公开场合披露细节。
- 若该入口未启用，可先开一个不含任何敏感细节的 issue 询问报告渠道，再按回复转入私下沟通。
- 请勿在公开 issue 中粘贴密钥、webhook URL、内部地址或日志原文。
- 本文件不提供、也不代为编造任何联系邮箱。
