# server　云端结构服务

Python + FastAPI。当前已落地一个离线可演示的结构服务，负责把素材变成树上的一个节点。

## 管道（对应 §2.3.3）

```
图 → 裁边/矫正 → 版面分区 → 文字识别 / 公式识别(LaTeX) → MathML 符号索引
                                                    ↓
                                        候选生成三路 → 关系判定 → 置信度分流
```

## 刻意不做的事

- **不自研模型**：版面检测、公式识别都调现成服务，我们只写对齐逻辑、未归类箱、修订反馈这三块纯软件逻辑（§3.1）。
- **不存原图**：云端只存结构化结果（节点、边、LaTeX），原图 24 小时内清除（§2.3.7）。
- **不生成答案**：赛题要的是助教，不是代做（§1.5 创新点三）。

## 每张图必须留一条 trace

分区耗时、识别耗时、对齐置信度、是否被用户修正。这是定位错误和答辩讲数据的唯一依据。

## 本地跑起来

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1        # PowerShell；Git Bash 用 source .venv/Scripts/activate
pip install -r server\requirements.txt
uvicorn server.main:app --reload --host 127.0.0.1 --port 8765
```

密钥一律读环境变量：`OCR_API_KEY` / `LLM_API_KEY` 之类，**不要写进代码，也不要提交 `.env`**。

## 当前实现

- SQLite 四张表：`assets`、`nodes`、`edges`、`revisions`。
- 上传原图只写本机 `server/data/uploads/`，不上传外部服务。
- 内置高数样张提供固定分区结果；任意上传图片进入 `manual_required`，等待人工补文字。
- 候选路：字符二元组余弦、字面相似度、LaTeX 符号交集。
- 阈值：`≥0.80` 自动执行，`0.55–0.80` 标黄待确认，`<0.55` 进入未归类箱。
- 单条修正、批量确认、节点改挂和编辑均写入 `revisions`。

运行测试：

```powershell
pip install -r server\requirements-dev.txt
python -m pytest -c server\pytest.ini
```
