# RL Sentinel 使用指南

[项目首页](../README.zh-CN.md) · [文档导航](README.md) · [English](GETTING_STARTED.md)

## 1. 从仓库直接运行

先检查 `python --version`，严格新核心需要 **Python 3.12 或以上**。如果兼容的解释器命令是 `python3`，将下列命令中的 `python` 替换为 `python3`。

```bash
git clone https://github.com/Anhao1314/RL-Sentinel.git
cd RL-Sentinel
python -m rl_risk_replay experiment --out artifacts/sentinel-demo
python -m rl_risk_replay verify --bundle artifacts/sentinel-demo
```

无需先运行 pip 安装。程序只使用 Python 标准库和 CPU，不下载模型、不使用 GPU，也不需要 API 凭据。已克隆旧仓库时，保留本地修改，只更新远端地址即可：

```bash
git remote set-url origin https://github.com/Anhao1314/RL-Sentinel.git
```

已有本地目录不必改名。Python 导入与模块命令继续使用 `rl_risk_replay`，安装后的短命令仍是 `rl-risk`。可选安装命令为 `python -m pip install --no-deps -e .`，构建后端可能需要网络。

## 2. 阅读产物

直接用浏览器打开 `artifacts/sentinel-demo/report.html`，不需要启动网页服务器。先看数据来源与准备度，再看某个策略、某个检查点的汇总，最后展开对应决策及其输入。

| 文件 | 内容 |
| --- | --- |
| `report.html` | 离线报告、指标汇总和逐次决策证据 |
| `results.json` | 机器可读结果、输入与运行环境信息 |
| `events.jsonl` | 带版本的来源声明与实验事件 |
| `protocol.json` | 受控实验配置 |
| `q_tables.json` | 训练完成后的 Q 表 |
| `manifest.json` | 完成状态、精确文件集合、字节数与 SHA-256 |

上表是 `experiment` 的产物。普通 `replay` 只有结果、事件、报告和清单，不会重新训练策略或生成新的 Q 表。验证脚本会在自己的输出目录另行记录依赖安装清单。

每次运行使用新目录。不要为修改标题或外观而直接编辑证据包，否则原哈希会失效。`verify` 通过只说明产物符合清单，不代表科学结论正确，也不是生产者身份认证。

## 3. 回放冻结事件

```bash
python -m rl_risk_replay replay --events artifacts/sentinel-demo/events.jsonl --out artifacts/frozen-review --policy all --progress 0.3,0.5,0.7
python -m rl_risk_replay verify --bundle artifacts/frozen-review
```

各检查点单独评分，同一个 run 的多次决策不是独立样本。`abstain` 表示策略没有给出证据支持的二元建议，不代表这次训练是健康的。

`--evaluation-at` 接受 Unix 时间戳，用于限制可观察信息范围。它是真正的截止时刻，不是历史命令中的 `--today` 报告日期标签。[协议文档](RELIABILITY_V2.md)说明历史成员、迟到观测与标签修订的语义。

<a id="integration"></a>
## 4. 接入可信观察器

先执行 `python -m rl_risk_replay init --db artifacts/training.sqlite`，创建新的事件库。Python 中打开这个已有数据库：

```python
from pathlib import Path
from rl_risk_replay.storage import EventStore

store = EventStore(Path("artifacts/training.sqlite"))
```

在训练观察器里调用 `store.record(run_id, kind, payload)`，按实际发生的生命周期记录：

| 事件 | 何时记录 | 必需字段 |
| --- | --- | --- |
| `start` | 新的一次训练启动 | `task`、`seed`、`planned_steps` |
| `sample` | 汇总观测变得可用 | `step`，可附支持的指标 |
| `finish` | 这次训练结束 | `status` |
| `label` | 实际评估或复核产生结论 | `verdict`、`source` |

使用真实观测，不复制受控实验中的标签。观测步数必须严格递增；重新启动或调整总预算时使用新的 run ID。结束与标签分开记录；标签更正使用追加修订，不覆盖旧值。完整字段白名单及值域见 [RELIABILITY_V2.md](RELIABILITY_V2.md)。

记录器以本机时间标记可用时间，跨机器时钟必须可比较；未来时间的事件会被拒绝。v0.2 提供事件 API，不附带现成训练框架回调，也不会自动停止或重启训练。

## 5. 检查开发环境

需要运行核心测试与类型检查时，再安装开发工具：

```bash
python -m pip install -r requirements-core-dev.txt
python -m pytest tests_v2/ -q
python -m pyright --project pyright-core.json
```

完整历史套件另需安装 `requirements-dev.txt`。[v0.2 验收记录](VALIDATION_V2_2026-10-06.md)分别记录核心验证、历史债务和受控实验结果。

## 常见问题

| 现象 | 检查方式 |
| --- | --- |
| `No module named rl_risk_replay` | 回到仓库根目录运行，或使用可编辑安装；不要把 Python 模块名替换成 `RL-Sentinel`。 |
| 提示输出目录已存在 | 改用新目录，保留原证据。 |
| 没有建议或检查点被跳过 | 查看样本缺失、奖励峰值非正、预算进度，以及结束事件是否已可见。 |
| 清单核验失败 | 比较文件集合与哈希，不要修改清单来掩盖失败。 |
| 旧数据无法严格回放 | 缺少可用时间证据是实际限制，可使用只读历史审计，不能补造时间。 |

[返回 RL Sentinel](../README.zh-CN.md) · [事件协议](RELIABILITY_V2.md) · [验收证据](VALIDATION_V2_2026-10-06.md)
