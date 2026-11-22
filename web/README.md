# web　网页整理台

网页 MVP 由 FastAPI 同域托管，不需要 Node、npm 或前端构建步骤。

## 组成

- `index.html`：整理台、关系图、检索与修订视图。
- `styles.css`：暖纸张视觉、桌面/平板/手机响应式布局。
- `app.js`：调用结构服务 API，处理候选改选、拖拽、批量确认和搜索。
- `assets/demo/`：7 张匿名高数样张，由 `scripts/generate_demo_assets.py` 可重复生成。

## 边界

网页默认处于离线演示模式。样张使用固定识别结果，任意上传图片进入人工确认，不会伪造 OCR 输出。真实识别服务可在保持现有 API 的前提下增加适配器。
