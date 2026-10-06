# v0.2 实测验收记录：2026-10-06

本记录描述实际运行，不把历史成绩、测试夹具或受控实验混为生产效果。

- 实现提交：`e609bc4d537b4d30949b9b004707bc0dfa75d398`
- 基线提交：`3be020e1df6a7a0fcd9e3af62c629126b658fd6e`
- [PR #1](https://github.com/Anhao1314/rl-training-risk-replay/pull/1)
- [实测 CI #4](https://github.com/Anhao1314/rl-training-risk-replay/actions/runs/37391585025)
- CI 实际检出的合并预览提交：`20310cd68676330aac7c733dfb476840330be483`。这是合并预览，不是已合并 main 的声明。
- 本文件是后续文档提交；以上运行验证的是该实现提交，不把之后的提交自动算作已测试。

## 1. 测试与环境

| 范围 | 实际结果 | 环境 |
| --- | --- | --- |
| 新核心 Linux | 62 个测试方法 + 25 个子测试，通过，无跳过 | Ubuntu 24.04 / Python 3.12.14 |
| 新核心 Windows | 同一组 62 个方法 + 25 个子测试，通过，无跳过 | Windows Server 2025 / Python 3.12.10 |
| 严格核心类型检查 | 两个平台的 required Pyright 步骤均通过 | Pyright 1.1.408，strict，范围 `rl_risk_replay/` |
| 历史离线回归 | 265 项通过，无跳过 | Linux / Python 3.12.14 |
| 全仓类型检查 | 52 个文件，1718 errors、0 warnings | 明确为 advisory，未清零 |

测试计数不能将跨平台重复执行累加成不同测试；新旧合计为 327 个测试方法，另有 25 个子测试。
pytest 的 JUnit 文件将新核心记录为 87 条（62 + 25），与控制台的方法/子测试分开显示一致。
本地 Python 3.13.5 也运行了同组核心测试与受控实验；跨平台结论来自上述 CI。

首轮 CI 曾失败：Linux 严格类型检查发现两处多余 cast；Windows 的两个 SQL 故障测试夹具未关闭连接，导致临时数据库清理失败。修复后重跑通过，没有删除测试、跳过失败用例或放宽严格类型门槛。

全仓类型错误来自仍保留的历史实现和测试等外围代码，不能把新核心通过说成整个仓库类型检查通过。原始完整诊断在历史回归 artifact 中。

## 2. 时间隔离的正反对照

对有效事件集做 200 组扰动：修改截止时刻之后的奖励、最终标签、未来运行，增加迟到人工标签修订，并打乱输入行序。

- 新核心在固定历史决策时刻的输入哈希和建议均保持不变：200 组，0 失败。
- 负对照修改当时已经可见的奖励：输入哈希和建议均改变，排除“恒定输出假装无泄漏”。
- 修改目标的最终标签可以改变事后评分，但不能改变当时的建议与输入。
- 历史 v1 反例仍复现：`online_view(..., T=50)` 暴露未来 s2 和目标最终 fail 标签。这个反例使用明确标记的合成夹具，不是训练效果实验。

这些结果支持本事件协议和测试范围内的数据可见性隔离，不是形式化证明，也不是恶意 Python 插件的进程沙箱。生产者时间戳、运行身份和任务指标语义仍须可信。

## 3. 真实 Q-learning 受控实验

实际训练，而非生成预设奖励日志：同一 4×4 网格、6 个种子、三种条件，每次 4000 个训练环境步，共 18 次训练。观察评估与最终评估使用不同随机种子；场景本身没有留出变化。

| 注入条件 | 最终验收通过 | 最终验收失败 |
| --- | --- | --- |
| 正常学习 | 6 | 0 |
| 关闭参数学习 | 0 | 6 |
| 55% 预算处清空策略并冻结学习 | 0 | 6 |
| 合计 | 6 | 12 |

最终标签由训练后 30 回合成功率是否达到 0.9 产生。故障条件保留在评估记录，不加入目标预测输入。

同一批运行分别重放三个策略，各预算检查点单独计算，不把同一个 run 的多个检查点当成独立样本：

| 策略 | 检查点 | 正确停止失败运行 | 误停成功运行 | abstain | 失败召回 |
| --- | --- | --- | --- | --- | --- |
| rules-v2 | 30% | 0/12 | 0/6 | 1/18 | 0% |
| rules-v2 | 50% | 0/12 | 0/6 | 1/18 | 0% |
| rules-v2 | 70% | 6/12 | 0/6 | 1/18 | 50% |
| always-continue | 任一上述检查点 | 0/12 | 0/6 | 0/18 | 0% |
| always-stop | 任一上述检查点 | 12/12 | 6/6 | 0/18 | 100% |

规则在后期检测到了清空策略造成的退化，却没有识别另外 6 次关闭学习的失败。30%/50% 时尚未发生后期清空故障，也没有检测到关闭学习失败。这个盲区保留，没有事后调阈值来美化结果。

0/6 误停仅描述这六个成功样本，不等于总体误杀率为零。描述性 Wilson 95% 区间上界约为 39%；也没有由此假定各运行统计独立。

Linux 与 Windows 下载后的全部最终 rollout 记录和 Q 表逐项一致。UUID、观察时间、耗时因实际运行而不同，不声称实验输出字节级一致。

所有上述实验的来源为 `controlled`，operational readiness 保持 `blocked`。这不是 Go2W、多任务、真实训练集群或自动早停部署验证。没有执行停止动作，也没有实际 GPU 节省结论。

## 4. 历史数据只读核查

仓库数据包含 48 条运行记录。按本次清单的声明标签口径（人工非空覆盖运行标签）为 12 pass、23 fail、13 unknown；这不是重新验收后的新标签。

- 带 snapshots 的运行只有 5 fail、1 unknown，没有 pass。
- snapshots 30000 行；eval_points 1762 行；tb_points 12303 行。
- 旧 schema 缺少逐事件可用时间和不可变启动版本，本次严格回放可直接使用的历史运行数为 0。
- 八个原始 CSV 在审计前后 SHA-256 一致；没有删除坏样本、修改人工标签、推算可用时间或覆盖旧报告。

这个清单不替代原有六类数据质量检查，也没有把历史数据改称为干净 benchmark。

## 5. 原始产物与校验

CI #4 的三个 artifact 已下载，ZIP SHA-256 与 GitHub 返回值一致；各自受控实验 bundle 又通过成员、字节数和 SHA-256 校验。

| Artifact | ID | ZIP SHA-256 |
| --- | --- | --- |
| reliability-v2-ubuntu-24.04 | 11380904206 | `7682287458cbc1756115f98e79d2dba431b6f6ee4fbfe3e9d02d7c08e1b1b105` |
| reliability-v2-windows-latest | 11381625679 | `59306f101507a702f9d71d2f2e35ad0c3fd215973692f32e7ac18ded5f47cc39` |
| historical-regression-and-inventory | 11381541074 | `9c7b6646efe57434a2c6e2edcebe2ddc065ccfc8eab216a3f9b9fe359dc03289` |

每份包括 JUnit、validation.json、对应环境安装清单、事件流、最终 Q 表、实验协议、逐次决策完整输入、结果 JSON、离线 HTML 报告和 bundle manifest。历史 artifact 另有全仓类型诊断及原始数据清单。

这些哈希用于核对产物，不是对可同时改写内容和清单的攻击者的签名认证。

## 6. 复现

```bash
python -m pip install -r requirements-core-dev.txt
python -m pytest tests_v2/ -q
python -m pyright --project pyright-core.json
python -m rl_risk_replay experiment --out artifacts/my-controlled-run
python -m rl_risk_replay verify --bundle artifacts/my-controlled-run
```

完整历史回归还需安装 `requirements-dev.txt`。输出目录必须为新目录。打开 `artifacts/my-controlled-run/report.html` 可离线查看结果；事件 API、迁移路径及未完成项见 [协议文档](RELIABILITY_V2.md)。
