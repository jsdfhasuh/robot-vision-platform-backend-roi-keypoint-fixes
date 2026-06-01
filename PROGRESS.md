# 项目进度

## 当前版本：后端架构优化版

本版本只优化后端结构，不继续实现前端。

## 完成情况

| 模块 | 当前完成度 | 说明 |
|---|---:|---|
| 前后端分离 | 100% | 后端接口独立，前端可单独维护 |
| API 拆分 | 90% | 摄像头、Worker、Debug、事件、模型、配置已拆分 |
| Service 层 | 80% | 新增 camera/status/event/snapshot/debug/model/config/worker/runtime 服务 |
| Worker 主循环 | 85% | `stream_worker.py` 已瘦身，只负责编排 |
| RTSP 拉流抽象 | 70% | 新增 `FrameReader`，后续可替换 FFmpeg/GStreamer |
| 检测器体系 | 90% | motion/aruco/yolo/yolo_pose 已插件化 |
| Layer2 跟踪层 | 75% | 轨迹缓存、位移统计、关键点运动统计已有 |
| Layer3 规则层 | 80% | 状态判断、防抖、规则解释已有 |
| 事件中心 | 85% | OPEN/RECOVERED、截图、标注图、误报、备注已有 |
| 调试能力 | 85% | RTSP截图、本地图片、双图对比、last-result 已有 |
| 模型管理 | 65% | 文件级模型上传/列表/删除已有 |
| 配置导入导出 | 75% | 摄像头配置导入导出已有 |
| 日志系统 | 85% | 关键节点日志已有 |

## 本次架构优化完成内容

1. 新增 `backend/app/services/` 服务层。
2. 拆分原 `camera_api.py`：
   - `camera_api.py`
   - `worker_api.py`
   - `debug_api.py`
3. 重写 `event_api.py`，事件逻辑下沉到 `event_service.py`。
4. 重写 `model_api.py`，模型逻辑下沉到 `model_service.py`。
5. 重写 `config_api.py`，配置逻辑下沉到 `config_service.py`。
6. 重写 `stream_worker.py`，主循环调用服务层，不再直接堆业务逻辑。
7. 新增 `workers/frame_reader.py`，封装 RTSP 读取与重连。
8. 新增 `services/runtime_service.py`，统一管理 detector/tracker/rule 的创建和热更新。
9. 新增 `ARCHITECTURE.md`，说明后端架构。
10. 保持原 API 路径基本兼容，前端不需要因为此次拆分大改。

## 下一步建议

1. 增加服务层单元测试。
2. 给 API 响应做统一包装，逐步统一历史接口风格。
3. 增强模型元数据管理，例如模型类型、关键点数量、类别名称。
4. 增强事件关键帧/短视频回放。
5. 根据现场规模决定是否接入 PostgreSQL。
6. 多目标场景再引入 ByteTrack / SORT。

---

## 当前版本：后端稳定性增强版

本轮继续只实现后端，不实现前端。

### 本轮新增/完善

1. **统一 REST API 响应格式**
   - 所有主要 REST 接口统一返回：
     ```json
     {"ok": true, "data": {}, "message": ""}
     ```
   - HTTPException、参数校验错误、未捕获异常统一返回：
     ```json
     {"ok": false, "data": null, "message": "错误原因"}
     ```
   - 保留原有 API 路径，方便已有前端继续对接。

2. **config_version 配置热更新增强**
   - `Camera` 表已有 `config_version` 字段。
   - 摄像头配置发生变化时自动 `config_version + 1`。
   - Worker 检测到版本变化后会重置 tracker/rule，并清理旧检测结果。
   - 配置导出时会包含 `config_version`，方便前端判断配置版本。

3. **Worker 健康状态增强**
   - `/api/system/workers` 和 `/api/cameras/{id}/last-result` 现在会返回更完整的运行诊断信息：
     - `running`
     - `rtsp_connected`
     - `last_frame_time`
     - `last_detect_time`
     - `fps_actual`
     - `detect_fps_actual`
     - `loop_count`
     - `frames_read`
     - `detect_count`
     - `error_count`
     - `reconnect_count`
     - `consecutive_read_failures`
     - `last_error`
     - `last_error_time`
     - `runtime.reset_count`
     - `runtime.last_reset_reason`

4. **异常处理增强**
   - Worker 主循环单帧异常不会直接退出。
   - RTSP 读取失败会记录错误计数、连续失败次数和重连次数。
   - 检测器异常会写入 `last_error`，并返回 UNKNOWN/检测失败结果。

### 当前完成度更新

| 模块 | 当前完成度 | 说明 |
|---|---:|---|
| API 拆分 | 92% | 主要接口已按模块拆分 |
| API 响应统一 | 85% | 主要 REST 接口已统一，WebSocket 仍保持原格式 |
| Service 层 | 82% | 业务逻辑继续下沉 |
| Worker 健康诊断 | 85% | 已支持运行状态、FPS、错误、重连、配置版本诊断 |
| config_version 热更新 | 85% | 配置变更可触发运行时重置 |
| 检测器体系 | 90% | motion/aruco/yolo/yolo_pose 已有 |
| Layer2 跟踪层 | 75% | 轨迹缓存和位移统计已有 |
| Layer3 规则层 | 80% | 状态判断和规则解释已有 |
| 事件中心 | 85% | 事件打开/恢复/处理/误报/截图已有 |
| 内网平台后端完整度 | 82% | 已进入联调和稳定性增强阶段 |

### 下一步建议

1. 模型元数据管理：把模型文件升级成模型表，记录模型类型、family、关键点数量等。
2. 事件关键帧：为事件保存多张关键帧，而不是只有开始/恢复图。
3. 接口测试：补充 API 和 service 单元测试，防止后续重构破坏现有能力。
4. 性能压测：模拟多路 RTSP，观察 CPU/GPU、FPS 和重连稳定性。

## 2026-06-01 后端继续完善：模型注册 + 事件关键帧 + 基础测试

本轮继续按“内网诊断平台”方向增强，不新增前端、不扩展通知系统。

### 已完成

1. **模型元数据注册表**
   - 新增 `model_registry` 表。
   - 新增 `ModelRegistry` 模型。
   - 模型不再只是 `models/` 目录里的文件，可以保存：
     - `model_type`: `yolo / yolo_pose`
     - `model_family`: `auto / yolo11 / yolo26 / yolo11_pose`
     - `input_size`
     - `class_count`
     - `num_keypoints`
     - `labels`
     - `metadata`
   - 支持上传模型时直接写入元数据。
   - 支持注册已存在模型文件。
   - 支持更新模型元数据。
   - 支持把模型绑定到摄像头，并自动写入 `detector_type / detector_config / config_version`。

2. **事件关键帧**
   - 新增 `event_frames` 表。
   - 新增 `EventFrame` 模型。
   - 事件打开时自动生成 `open` 关键帧。
   - 事件恢复时自动生成 `recover` 关键帧。
   - 每个关键帧保存：
     - 原始图
     - 标注图
     - 当前状态
     - 检测器类型
     - 规则解释
   - 新增接口：
     - `GET /api/events/{event_id}/frames`

3. **配置导入导出增强**
   - `GET /api/config/export` 现在会导出摄像头配置和模型元数据。
   - `POST /api/config/import` 支持导入模型元数据。
   - 如果导入机器上模型文件不存在，会跳过模型元数据，不影响摄像头配置导入。

4. **后端测试骨架**
   - 新增 `backend/tests/`。
   - 覆盖：
     - `RobotStopRule`
     - `TrackBuffer`
     - `model_service`
   - 当前测试重点是不依赖真实 RTSP、真实 ONNX 模型的核心逻辑。

### 新增接口

```text
POST /api/models/register
PUT  /api/models/registry/{model_id}
POST /api/models/bind-camera
GET  /api/events/{event_id}/frames
```

### 当前建议进度

| 模块 | 完成度 |
|---|---:|
| 检测器体系 | 90% |
| YOLO11 / YOLO26 / YOLO Pose | 85% |
| Layer2 轨迹缓存与运动统计 | 75% |
| Layer3 状态规则 | 80% |
| 规则解释能力 | 85% |
| 事件中心 | 88% |
| 事件关键帧复盘 | 70% |
| 标注图诊断 | 85% |
| 模型管理 | 78% |
| 配置导入导出 | 82% |
| Worker 健康诊断 | 85% |
| 后端架构完整度 | 85% |
| 内网平台后端完整度 | 84% |

### 下一步建议

1. 增加事件关键帧采样策略，例如停机中每隔 N 秒保存一张关键帧。
2. 增强模型测试接口，例如上传图片并指定模型直接测试，不依赖摄像头。
3. 增加数据库备份/恢复接口，适合内网单机 SQLite 部署。
4. 增加系统自检接口，检查模型目录、数据库、存储目录、OpenCV、ONNXRuntime provider。

### 追加完成：系统自检接口

新增：

```text
GET /api/system/self-check
```

用于内网部署后快速检查：

- 存储目录是否可写
- 模型目录是否可写
- 日志目录是否可写
- 数据库是否可连
- OpenCV 是否正常
- ONNXRuntime 是否正常
- ONNXRuntime 可用 providers

这对现场部署排查很有用。


## 2026-06-01 后端继续完善：前端视频流接口

本轮继续只实现后端，不实现前端。

### 已完成

1. **MJPEG 原始视频流**
   - 新增接口：`GET /api/cameras/{id}/stream.mjpg?annotated=false`。
   - 前端可以直接使用 `<img>` 标签播放。
   - 视频帧来源于 Worker 最新帧缓存，避免前端每开一个页面就重新拉一路 RTSP。

2. **MJPEG 标注视频流**
   - 新增接口：`GET /api/cameras/{id}/stream.mjpg?annotated=true`。
   - 标注流会显示 ROI、bbox、center、keypoints、轨迹、状态、运动分数和规则解释。
   - 适合前端“检测调试页面”和“单机详情页”。

3. **单帧 JPEG 接口**
   - 新增接口：`GET /api/cameras/{id}/frame.jpg`。
   - 支持 `annotated=true/false`。
   - 可用于 ROI 画框前获取当前画面，也可用于前端低频刷新。

4. **视频流状态接口**
   - 新增接口：`GET /api/cameras/{id}/stream-info`。
   - 新增接口：`GET /api/system/streams`。
   - 可查看当前是否已有 raw/annotated 缓存帧、画面尺寸、更新时间和帧年龄。

### 新增接口

```text
GET /api/cameras/{id}/stream-info
GET /api/cameras/{id}/frame.jpg?annotated=false
GET /api/cameras/{id}/frame.jpg?annotated=true
GET /api/cameras/{id}/stream.mjpg?annotated=false&fps=8&quality=80
GET /api/cameras/{id}/stream.mjpg?annotated=true&fps=8&quality=80
GET /api/system/streams
```

### 当前建议进度

| 模块 | 完成度 |
|---|---:|
| 后端视频流能力 | 75% |
| 原始 MJPEG 流 | 85% |
| 标注 MJPEG 流 | 80% |
| 视频流状态诊断 | 80% |
| Worker 帧缓存复用 | 75% |

### 后续建议

1. 如果多路摄像头同时预览很多路，再考虑 MediaMTX/WebRTC。
2. 增加限流和鉴权，避免内网页面被大量打开导致 CPU 占用升高。
3. 增加事件短视频或关键帧采样策略。
4. 增加数据库备份/恢复接口，适合 SQLite 单机部署。

## 2026-06-01 后端持续运行能力增强

本轮只修改后端，新增长期运行维护能力和模型调试能力：

### 新增事件关键帧采样

- 当 `STOPPED / OFFLINE / UNKNOWN` 等事件处于 `OPEN` 状态时，Worker 会按固定间隔保存 `sample` 关键帧。
- 事件打开和恢复仍然保存 `open / recover` 关键帧。
- 新增环境变量：
  - `EVENT_FRAME_SAMPLE_SECONDS=10`
  - `EVENT_FRAME_MAX_PER_EVENT=60`

### 新增数据维护接口

- `GET /api/system/storage`：查看 snapshots、clips、logs、backups、SQLite 数据库容量。
- `POST /api/system/backup`：创建 SQLite 数据库备份。
- `GET /api/system/backups`：列出数据库备份。
- `POST /api/system/cleanup`：清理旧 sample 关键帧、旧孤儿文件、旧备份。默认 `dry_run=true`，不会直接删除。

### 新增模型单图测试接口

- `POST /api/models/{model_id}/test-image`
- 支持上传一张本地图片，使用指定模型直接推理。
- 返回 bbox、关键点、置信度、检测 metadata、标注图地址。
- 适合现场验证 ONNX 模型是否能被后端正常加载和推理。

### 当前后端重点进度

- 后端整体完成度：约 87%
- 内网现场可调试程度：约 83%
- 长期运行维护能力：约 75%

---

## 2026-06-01 后端继续完善：前端兼容层与 ROI/设置接口

本轮新增：

1. REST 响应同时支持 `ok/data/message` 和 `code/message/data`。
2. 新增 `/stream/cameras/{camera_id}/mjpeg` 前端兼容视频流。
3. 新增 `/stream/cameras/{camera_id}/snapshot` 前端兼容截图。
4. 新增 `/api/runtime/status` 实时状态聚合接口。
5. 新增 `/api/cameras/{camera_id}/roi` GET/POST，支持归一化多边形 ROI。
6. 新增 `/api/settings`、`/api/settings/save`、`/api/settings/apply`、`/api/settings/reset`。
7. 新增 `/api/debug/keypoints` 和 `/api/debug/keypoints/evaluate`。
8. 新增 `/api/alarms` 兼容事件中心。
9. 新增 `/api/tasks` 兼容摄像头 Worker。
10. 支持 `cam_001` 形式的摄像头 ID，与数字 ID 兼容。

本轮后端整体完成度估计：**89%**。

接下来建议：

1. 补充接口级自动化测试。
2. 做实际 RTSP 摄像头联调。
3. 做长时间运行压测与磁盘清理验证。
4. 如果现场多机器人同画面，再升级 ByteTrack/SORT 多目标跟踪。

## 本轮更新：ROI / Keypoint / Camera API 修复

- `/api/cameras` 已改为前端卡片结构，并对 RTSP 做脱敏。
- 摄像头创建/更新支持 `area / line / robot_id / robot_name`，保存到 `detector_config.frontend_meta`。
- 新增 `roi_service.py`，支持 polygon mask、exclude zones、ROI 内关键点过滤。
- `TrackBuffer` 新增 `keypoint_deltas`，支持逐关键点 delta。
- `/api/debug/keypoints` 现在返回每个关键点自己的 `delta_px / avg_delta_px / moving / valid`。
- 模型管理限制只允许 `.onnx`，与当前 ONNXRuntime 推理后端一致。
