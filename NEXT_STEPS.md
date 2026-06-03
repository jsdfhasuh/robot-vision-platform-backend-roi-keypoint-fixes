# 下一步行动文档

更新时间：2026-06-02

本文档用于承接当前后端项目状态，明确接下来应该先做什么、谁来做、做到什么程度算完成。

## 1. 当前结论

当前项目已经不是空架子，后端主链路基本具备联调条件：

- 摄像头 CRUD
- RTSP Worker 启停
- Worker 健康诊断
- Motion / ArUco / YOLO / YOLO Pose 检测器
- ROI 多边形配置和关键点过滤
- MJPEG 原始流和标注流
- 事件中心、事件关键帧和事件处理
- 模型上传、注册、绑定和单图测试
- 前端兼容接口
- 系统自检、备份、清理和存储诊断

当前主要短板不是“功能完全没有”，而是：

- 前端工程不在本仓库内，需要外部前端工程师按接口接入。
- 已新增 `docker-compose.backend.yml` 作为纯后端部署入口；原 `docker-compose.yml` 仍是全栈骨架，会引用当前仓库未包含的 `frontend` 和 `mediamtx`。
- 自动化测试覆盖偏少，尤其缺少 API 级测试。
- 真实 RTSP 摄像头、多路运行、长时间稳定性还需要现场验证。
- HLS/WebRTC 没做，当前视频方案以 MJPEG 为主。
- 后端接口没有鉴权，现场部署前建议补最小权限控制。

## 2. 当前最新文档

优先阅读顺序：

1. `FRONTEND_INTEGRATION.md`
   - 给前端工程师使用。
   - 包含 Vite 代理、响应格式、摄像头、视频流、ROI、关键点、模型、事件、系统接口和 TS 类型草稿。

2. `AGENTS.md`
   - 给后续维护者或 AI agent 使用。
   - 说明项目定位、目录职责、运行命令、测试命令和开发注意事项。

3. `PROGRESS.md`
   - 历史进度记录。
   - 能看到每轮后端能力是如何演进的，以及当前估算完成度。

4. `ARCHITECTURE.md`
   - 后端分层说明。
   - 重点看 API、service、worker、detector、tracker、rules 的职责边界。

5. `FRONTEND_COMPAT.md`
   - 旧版前端兼容接口说明。
   - 如果前端已有一套接口命名，可以参考这里。

6. `README.md`
   - 项目总览和启动说明。
   - 适合快速了解接口和本地运行方式。

## 3. P0：下一步必须先做

这些是进入前后端联调前最该完成的事情。

### 3.1 建立可运行的后端开发环境

目标：前端和后端开发人员都能稳定启动后端。

执行：

```bash
cd backend
python -m venv .venv
pip install -r requirements-dev.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

验收：

- `GET http://127.0.0.1:8000/api/system/health` 正常。
- `GET http://127.0.0.1:8000/api/system/self-check` 能返回目录、数据库、OpenCV、ONNX Runtime 检查结果。
- `http://127.0.0.1:8000/docs` 可打开。

### 3.2 跑通现有测试

目标：确认依赖文件和基础逻辑没坏。

执行：

```bash
cd backend
pip install -r requirements-dev.txt
pytest -q
```

验收：

- 当前 `backend/tests/` 内测试全部通过。
- 如果真实环境无法安装 ONNX Runtime，需要记录原因，不要直接跳过依赖问题。

### 3.3 纯后端 Compose 部署

当前已新增：

```text
docker-compose.backend.yml
```

启动：

```bash
docker compose -f docker-compose.backend.yml up -d --build
```

验收：

- 单独后端容器可以启动。
- `data/` 和 `models/` volume 正常挂载。
- `/api/system/self-check` 在容器内通过。

### 3.4 前端工程师开始按接入文档做页面

前端优先页面顺序：

1. 摄像头列表页：`GET /api/cameras`
2. 摄像头详情页：视频流、状态、last-result
3. ROI 设置页：截图 + 多边形绘制 + 保存
4. 关键点调试页：`GET /api/debug/keypoints`
5. 事件列表和事件详情页：`/api/events`
6. 模型管理页：上传、注册、绑定、单图测试
7. 系统诊断页：self-check、workers、streams、storage

验收：

- 前端不直接播放 RTSP，只使用 MJPEG 或 snapshot。
- 前端能处理 `RUNNING / IDLE / STOPPED / OFFLINE / UNKNOWN`。
- 前端能显示 `X-Frame-Placeholder: 1` 的暂无画面状态。
- ROI 保存后能看到 `config_version` 变化。

## 4. P1：联调阶段要补

### 4.1 API 自动化测试

当前已有规则、轨迹、模型服务测试，但接口级测试不足。

优先补这些：

- `GET /api/system/health`
- `GET /api/cameras`
- `POST /api/cameras`
- `PUT /api/cameras/{id}`
- `GET /api/runtime/status`
- `GET /api/cameras/{id}/roi`
- `POST /api/cameras/{id}/roi`
- `GET /api/events`
- `GET /api/models`

验收：

- 测试不依赖真实 RTSP。
- 测试使用临时 SQLite 数据库。
- 覆盖统一响应格式 `ok/code/data/message`。

### 4.2 实际 RTSP 摄像头联调

目标：确认现场摄像头可以稳定拉流。

联调顺序：

1. 新增摄像头。
2. 调用 RTSP test。
3. 启动 Worker。
4. 打开 raw MJPEG。
5. 打开 annotated MJPEG。
6. 查看 `/api/system/workers`。
7. 查看 `/api/cameras/{id}/last-result`。

验收：

- Worker 不频繁退出。
- `rtsp_connected=true`。
- `fps_actual` 和 `detect_fps_actual` 稳定。
- 断流后能重连并记录错误诊断。

### 4.3 模型现场验证

目标：确认现场 ONNX 模型能被后端加载和推理。

联调顺序：

1. 上传或注册 `.onnx` 模型。
2. 调用 `POST /api/models/{model_id}/test-image`。
3. 检查 `annotated_image_url`。
4. 绑定模型到摄像头。
5. 启动 Worker 看检测结果。

验收：

- `target_found` 能符合样张预期。
- bbox 或 keypoints 位置正确。
- `providers` 至少有 `CPUExecutionProvider` 可用。

### 4.4 事件流程联调

目标：确认事件打开、恢复、处理、误报、关键帧显示都能跑通。

验收：

- 停机或离线能产生 `OPEN` 事件。
- 恢复后事件变为 `RECOVERED`。
- `GET /api/events/{event_id}/frames` 能看到 `open / recover / sample`。
- 前端能处理 `handled`、`false_alarm`、`remark`。

## 5. P2：上线前增强

### 5.1 最小鉴权

当前后端没有鉴权。

建议至少做一种：

- 反向代理 Basic Auth。
- 后端 API token。
- 内网统一网关鉴权。

需要保护的接口：

- 摄像头增删改
- Worker 启停
- 模型上传和删除
- 设置应用
- 系统清理
- 数据库备份

### 5.2 长时间运行压测

建议压测：

- 单路 RTSP 连续 24 小时。
- 多路 RTSP 同时运行。
- 多页面同时打开 MJPEG。
- 断流、恢复、摄像头不可达。
- 事件 sample 关键帧持续写入。
- cleanup dry-run 和真实清理。

记录指标：

- CPU
- 内存
- 磁盘增长
- 实际 FPS
- 检测 FPS
- 重连次数
- 错误日志

### 5.3 正式数据库迁移方案

当前 SQLite 字段补丁是 MVP 方式。

如果后续还会频繁改表，建议引入 Alembic。

如果现场摄像头数量和事件量变大，再考虑 PostgreSQL。

## 6. P3：后续产品化方向

这些不是当前联调必需，但适合后续版本规划。

- HLS / WebRTC / MediaMTX 视频方案。
- 事件短视频回放。
- 多机器人同画面时接 ByteTrack / SORT / DeepSORT。
- 更细的模型版本管理。
- 告警通知系统重新启用并产品化。
- 操作审计日志。
- 用户角色和权限。
- 前端大屏和现场巡检模式。

## 7. 建议本周执行顺序

1. 安装依赖并跑 `pytest -q`。
2. 使用 `docker-compose.backend.yml` 验证纯后端容器部署。
3. 后端补 5 到 10 个核心 API 测试。
4. 前端工程师按 `FRONTEND_INTEGRATION.md` 做摄像头列表和详情页。
5. 找一条真实 RTSP 流做 Worker 和 MJPEG 联调。
6. 用一张现场样图测试模型接口。
7. 联调 ROI 保存和关键点调试页。
8. 联调事件列表、事件详情和关键帧展示。

## 8. 当前不建议优先做

- 不建议现在先做 HLS/WebRTC，MJPEG 足够支撑内网早期联调。
- 不建议先大改数据库为 PostgreSQL，除非现场规模已经明确超过 SQLite 能力。
- 不建议先做复杂通知系统，当前主线是内网可视化诊断和事件复盘。
- 不建议前端直接接 RTSP，浏览器播放和跨平台兼容会变复杂。
