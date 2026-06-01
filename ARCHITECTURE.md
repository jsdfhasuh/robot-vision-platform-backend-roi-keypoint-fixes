# 后端架构说明

本项目当前按后端分层架构组织，前端项目可以独立维护，只通过 HTTP API / WebSocket 调用后端。

## 1. 总体分层

```text
api/        接口层：只负责路由、参数、响应
services/   业务服务层：摄像头、状态、事件、调试、模型、配置等核心逻辑
workers/    后台任务层：RTSP 拉流、检测主循环、WebSocket 广播
models/     数据库模型
schemas/    Pydantic 请求/响应结构
detectors/  Layer1 检测层：motion / aruco / yolo / yolo_pose
tracker/    Layer2 跟踪层：轨迹缓存、位移统计、姿态变化统计
rules/      Layer3 规则层：RUNNING / IDLE / STOPPED / OFFLINE / UNKNOWN
utils/      标注图、路径等工具
core/       配置、日志、通用能力
```

## 2. 检测链路

```text
RTSP Frame
  ↓
FrameReader
  ↓
RuntimeContext
  ├── DetectorFactory 创建检测器
  ├── SimpleTracker 轨迹统计
  └── RobotStopRule 状态判断
  ↓
StatusService 写 camera_status
  ↓
EventService 打开/恢复事件
  ↓
SnapshotService 保存原图/标注图
```

## 3. API 拆分

```text
camera_api.py        摄像头 CRUD、RTSP 测试
worker_api.py        启动/停止 Worker、last-result
debug_api.py         snapshot/debug-detect/image-detect/image-pair-detect
status_api.py        状态查询、WebSocket
event_api.py         事件查询、处理、误报、人工关闭
model_api.py         模型列表、上传、删除
config_api.py        配置导入、导出
system_api.py        健康检查、worker 列表、detector 列表
```

## 4. Worker 职责变化

架构优化前，`stream_worker.py` 同时负责拉流、检测、规则、事件、截图、状态写库等逻辑。

架构优化后，`CameraWorker` 只做主循环编排：

```text
读取 Camera 配置
拉取一帧
裁剪 ROI
调用 RuntimeContext 执行检测/跟踪/规则
调用 StatusService 写状态
调用 EventService 处理事件闭环
调用 SnapshotService 保存标注图
```

## 5. 后续扩展建议

1. 如果要换 RTSP 拉流方案，只替换 `workers/frame_reader.py`。
2. 如果要新增检测器，只在 `detectors/` 新增实现并注册到 `DetectorFactory`。
3. 如果要增强事件逻辑，只改 `services/event_service.py`。
4. 如果要增强本地图片调试，只改 `services/debug_service.py`。
5. 如果要增加 PostgreSQL，不影响业务层，只需要改 `DATABASE_URL` 和依赖。

---

## 6. 统一响应格式

REST API 当前统一返回：

```json
{
  "ok": true,
  "data": {},
  "message": ""
}
```

错误返回：

```json
{
  "ok": false,
  "data": null,
  "message": "错误原因"
}
```

WebSocket 推送仍保持事件流格式：

```json
{
  "type": "status",
  "data": []
}
```

## 7. 配置热更新

摄像头表使用 `config_version` 表示配置版本。前端每次修改摄像头配置后，后端会自动递增版本号。

Worker 每轮读取摄像头配置，如果发现 `config_version` 变化，会：

```text
重置 tracker
重置 rule
清理上一轮检测结果
必要时重建 detector
继续使用新配置检测
```

## 8. Worker 健康诊断

`/api/system/workers` 和 `/api/cameras/{id}/last-result` 会返回 Worker 健康信息：

```text
running
rtsp_connected
last_frame_time
last_detect_time
fps_actual
detect_fps_actual
frames_read
detect_count
error_count
reconnect_count
consecutive_read_failures
last_error
last_config_version
runtime.reset_count
runtime.last_reset_reason
```

这些字段用于前端展示“摄像头是否真的在跑、实际 FPS、最近错误和配置是否已生效”。

## 模型注册与事件关键帧扩展

### 模型注册层

新增 `model_registry` 表，解决原先模型只是文件列表的问题。后端现在可以保存模型元数据：

```text
model_registry
- id
- name
- file_name
- file_path
- model_type
- model_family
- input_size
- class_count
- num_keypoints
- labels
- metadata_json
```

摄像头绑定模型后，后端自动生成对应的 `detector_config`，并递增 `config_version`，Worker 会自动热更新。

### 事件关键帧层

新增 `event_frames` 表，用于事件复盘：

```text
event_frames
- id
- event_id
- camera_id
- frame_time
- frame_type: open / recover / manual / sampled
- status
- image_path
- annotated_image_path
- detector_type
- reason
- rule_detail
```

事件打开和恢复时会自动保存关键帧，前端可通过：

```text
GET /api/events/{event_id}/frames
```

展示事件全过程。
