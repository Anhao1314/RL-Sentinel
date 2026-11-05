> **RETIRED · 历史记录（v1）** 本文描述的是已归档的第一代系统（在线预测 / 建议制止损 / CSV 分析管线），
> 不代表本仓库当前定位。当前定位见 [README](README.md)
> 与 [ADR 0001](docs/adr/0001-from-prediction-to-evidence.md)。
> 代码与数据已归档至 `archive/v1` 分支，见 [ARCHIVE.md](ARCHIVE.md)。

# 开发约定

- Python/文档 UTF-8 无 BOM，编辑时保留 CRLF；Linux shell 脚本使用 LF。CSV 用 utf-8-sig。
- 因子优先纯函数，不重命名现有因子；阈值配置驱动。
- 保持既有 CSV schema 和风险规则，行为变化必须明确说明。
- 提交前运行 `pytest tests/`；新增行为补正常、边界及失败测试。
- 离线样例回归产物写入独立目录，不覆盖历史报告。真实 Linux 采集和 Windows 验证无法执行时明确记录。

## 人工标签与同步

`data/datasets/manual_labels.csv` 为人工字段权威层，按 `(task, seed)` 非空覆盖 verdict、success_rate、label_source、label_updated_at。空值保留自动值；重复键或非法标签报错。

自动同步仅允许数据目录改动，禁止混入其他暂存内容。远端领先或分叉时停止，人工核对后同步；不要按文件名批量选择冲突版本。

rebase 冲突中 `ours` 通常是重放到的上游状态，`theirs` 是当前被重放的提交。结合实际冲突确认，不能将其简单等同于本机/远端。

## 安全与数据边界

- 凭据与机器配置只放被忽略的本地配置，不提交公开仓库。
- 监控仅输出本地建议、表格和日志，不发送消息，不自动停止训练。
- 事后分析包含最终验收数据，不能声称在线预测能力。
- 单 CSV 原子替换不保证多表批次一致；采集与分析避免并发。
- 本轮不重写 Git 历史，已公开凭据需在服务侧撤销。
