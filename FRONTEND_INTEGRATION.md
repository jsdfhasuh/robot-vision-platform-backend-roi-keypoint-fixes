# 前端接入文档

本文档给前端工程师使用，说明如何接入当前机器人视觉检测后端。

后端定位：提供摄像头管理、检测任务控制、实时状态、MJPEG 视频流、ROI 配置、模型管理、事件复盘和前端兼容接口。前端项目可以独立维护，只通过 HTTP API、MJPEG `<img>`、WebSocket 接入。

## 1. 基础信息

默认后端地址：

```text
http://127.0.0.1:8000
```

接口文档：

```text
http://127.0.0.1:8000/docs
```

开发环境 CORS 默认允许：

```text
http://localhost:5173
http://127.0.0.1:5173
http://localhost:8080
```

如果前端使用 Vite，建议配置代理：

```ts
// vite.config.ts
export default {
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/stream": "http://127.0.0.1:8000",
      "/data": "http://127.0.0.1:8000",
      "/ws": {
        target: "ws://127.0.0.1:8000",
        ws: true,
      },
    },
  },
};
```

## 2. 响应格式

主要 REST 接口返回：

```json
{
  "ok": true,
  "code": 0,
  "data": {},
  "message": "ok"
}
```

部分前端兼容接口只返回：

```json
{
  "code": 0,
  "message": "ok",
  "data": {}
}
```

前端统一判断建议：

```ts
export function isApiSuccess(res: any) {
  return res?.ok === true || res?.code === 0;
}
```

错误响应通常是：

```json
{
  "ok": false,
  "code": 40001,
  "data": null,
  "message": "error message"
}
```

## 3. ID 约定

后端数据库内部使用数字 ID：

```text
1
2
3
```

前端兼容层支持字符串 ID：

```text
cam_001
cam_002
cam_003
```

`GET /api/cameras` 返回中同时有：

```json
{
  "id": "cam_001",
  "numeric_id": 1
}
```

建议：

- 页面展示、路由参数可以使用 `id`，例如 `cam_001`。
- 调用核心接口时优先使用 `numeric_id`，例如更新摄像头、创建检测任务、绑定模型。
- 视频流、ROI、runtime、settings、alarms 兼容接口可以继续使用 `cam_001`。

## 4. 推荐接入顺序

1. `GET /api/system/health`：确认后端可用。
2. `GET /api/cameras`：获取摄像头卡片列表。
3. `GET /api/detection-tasks?camera_id={numeric_id}`：获取该摄像头的检测任务。
4. `POST /api/detection-tasks/{task_id}/start`：启动检测任务，后端自动启动对应摄像头共享拉流。
5. `<img src="/stream/cameras/cam_001/mjpeg?annotated=true" />`：显示视频流。
6. `GET /api/runtime/status`：轮询实时状态聚合。
7. `GET /api/cameras/cam_001/roi` 和 `POST /api/cameras/cam_001/roi`：接 ROI 绘制保存。
8. `GET /api/debug/keypoints?camera_id=cam_001`：接关键点调试面板。
9. `GET /api/events` 或 `GET /api/alarms`：接事件/告警列表。
9. `GET /api/models`、`POST /api/models/upload`、`POST /api/models/bind-camera`：接模型管理。

## 5. 摄像头管理

### 5.1 获取摄像头列表

```http
GET /api/cameras
```

返回 `data` 示例：

```json
[
  {
    "id": "cam_001",
    "numeric_id": 1,
    "name": "robot_pose_01",
    "area": "产线A",
    "line": "工位1",
    "location": "产线A-工位1",
    "robot_id": "robot_001",
    "robot_name": "robot_pose_01",
    "enabled": true,
    "status": "online",
    "runtime_state": "RUNNING",
    "stream_type": "mjpeg",
    "last_online_at": "2026-06-01 10:00:00",
    "rtsp_url_masked": "rtsp://****:****@192.168.1.100:554/stream1",
    "stream_urls": {
      "mjpeg": "/stream/cameras/cam_001/mjpeg",
      "mjpeg_annotated": "/stream/cameras/cam_001/mjpeg?annotated=true",
      "snapshot": "/stream/cameras/cam_001/snapshot"
    },
    "detector_type": "yolo_pose",
    "fps_limit": 3,
    "motion_threshold": 4,
    "stop_seconds": 30,
    "config_version": 2
  }
]
```

字段说明：

- `status`：前端卡片状态，`online / offline / error`。
- `runtime_state`：后端判断状态，`RUNNING / IDLE / STOPPED / OFFLINE / UNKNOWN`。
- `stream_urls`：前端可直接用于 `<img>`。
- `config_version`：配置版本，ROI、模型、检测参数变更后递增。

### 5.2 新增摄像头

```http
POST /api/cameras
Content-Type: application/json
```

请求体：

```json
{
  "name": "robot_pose_01",
  "rtsp_url": "rtsp://user:pass@192.168.1.100:554/stream1",
  "area": "产线A",
  "line": "工位1",
  "robot_id": "robot_001",
  "robot_name": "一号机器人",
  "enabled": true,
  "fps_limit": 3,
  "detector_type": "motion",
  "motion_threshold": 5,
  "stop_seconds": 30
}
```

可选 detector：

```text
motion
aruco
yolo
yolo_pose
```

### 5.3 修改摄像头

```http
PUT /api/cameras/{numeric_id}
Content-Type: application/json
```

请求体可以只传变化字段：

```json
{
  "name": "robot_pose_01",
  "enabled": true,
  "fps_limit": 5,
  "motion_threshold": 4,
  "stop_seconds": 30
}
```

### 5.4 删除摄像头

```http
DELETE /api/cameras/{numeric_id}
```

后端会先停止该摄像头 Worker，再删除摄像头。

### 5.5 测试 RTSP 连接

```http
POST /api/cameras/{numeric_id}/test
```

返回：

```json
{
  "connected": true
}
```

## 6. 检测任务控制和运行状态

### 6.1 启动检测任务

```http
POST /api/detection-tasks/{task_id}/start
```

### 6.2 停止检测任务

```http
POST /api/detection-tasks/{task_id}/stop
```

### 6.3 获取任务最近检测结果

```http
GET /api/detection-tasks/{task_id}/last-result
```

返回内容包含任务是否运行、共享 RTSP 流是否连通、FPS、错误次数、最新检测结果、tracker/rule 细节等。详情页和调试页建议使用这个接口。

### 6.4 获取所有 Worker 诊断

```http
GET /api/system/workers
```

适合做“运行诊断”页面。

## 7. 视频流和截图

后端当前推荐使用 MJPEG。HLS 接口存在占位，但未实现。

### 7.1 MJPEG 视频流

原始流：

```html
<img src="/stream/cameras/cam_001/mjpeg" />
```

标注流：

```html
<img src="/stream/cameras/cam_001/mjpeg?annotated=true" />
```

可选参数：

```text
annotated=true|false
fps=8
quality=80
max_width=1280
```

核心后端路径也可用：

```text
GET /api/cameras/{camera_id}/stream.mjpg?annotated=true&fps=8&quality=80
```

### 7.2 当前单帧截图

```http
GET /stream/cameras/cam_001/snapshot?annotated=true
```

核心后端路径：

```http
GET /api/cameras/1/frame.jpg?annotated=true
```

如果 Worker 未启动或还没有帧，后端会返回占位图，并带响应头：

```text
X-Frame-Placeholder: 1
```

前端可以用这个头提示“暂无视频帧”。

### 7.3 视频流信息

```http
GET /api/cameras/cam_001/stream-info
```

返回包含：

- `mjpeg_url`
- `annotated_mjpeg_url`
- `snapshot_url`
- `annotated_snapshot_url`
- `online`
- `width`
- `height`
- `cache`

注意：`hls_url` 可能返回，但 HLS 当前未实现，不要依赖。

## 8. 实时状态

### 8.1 轮询聚合状态

```http
GET /api/runtime/status
```

返回 `data` 示例：

```json
[
  {
    "camera_id": "cam_001",
    "numeric_camera_id": 1,
    "camera_name": "robot_pose_01",
    "robot_id": "robot_001",
    "robot_name": "robot_pose_01",
    "state": "RUNNING",
    "fps": 2.9,
    "valid_keypoints": 4,
    "moving_keypoints": 3,
    "mean_delta": 5.2,
    "max_delta": 12.7,
    "motion_score": 5.2,
    "stop_duration_seconds": 0,
    "last_update_at": "2026-06-01 10:00:00",
    "message": "running",
    "rule": {}
  }
]
```

状态枚举：

```text
RUNNING  运行中
IDLE     静止但未超过停机阈值
STOPPED  超过停机阈值
OFFLINE  RTSP 断线或不可读
UNKNOWN  检测不到目标或关键点不足
```

### 8.2 WebSocket 状态推送

```text
ws://127.0.0.1:8000/ws/status
```

消息示例：

```json
{
  "type": "status",
  "data": [
    {
      "camera_id": 1,
      "status": "RUNNING",
      "last_frame_time": "2026-06-01T10:00:00",
      "last_motion_time": "2026-06-01T10:00:00",
      "confidence": 0.9,
      "message": "running",
      "updated_at": "2026-06-01T10:00:00"
    }
  ]
}
```

注意：当前 WebSocket 连接后需要客户端保持连接即可，后端约每 2 秒广播一次状态。

## 9. ROI 接口

### 9.1 获取 ROI

```http
GET /api/cameras/cam_001/roi
```

返回：

```json
{
  "camera_id": "cam_001",
  "numeric_camera_id": 1,
  "image_width": 1920,
  "image_height": 1080,
  "rois": [
    {
      "id": "roi_1",
      "name": "机器人本体区域",
      "enabled": true,
      "type": "polygon",
      "points": [
        { "x": 0.12, "y": 0.2 },
        { "x": 0.78, "y": 0.2 },
        { "x": 0.82, "y": 0.75 },
        { "x": 0.1, "y": 0.78 }
      ],
      "keypoint_indexes": [2, 3, 4, 5]
    }
  ],
  "exclude_zones": [],
  "pixel_roi": [100, 80, 900, 600]
}
```

### 9.2 保存 ROI

```http
POST /api/cameras/cam_001/roi
Content-Type: application/json
```

请求体：

```json
{
  "camera_id": "cam_001",
  "image_width": 1920,
  "image_height": 1080,
  "rois": [
    {
      "id": "roi_1",
      "name": "机器人本体区域",
      "enabled": true,
      "type": "polygon",
      "points": [
        { "x": 0.12, "y": 0.2 },
        { "x": 0.78, "y": 0.2 },
        { "x": 0.82, "y": 0.75 },
        { "x": 0.1, "y": 0.78 }
      ],
      "keypoint_indexes": [2, 3, 4, 5]
    }
  ],
  "exclude_zones": []
}
```

前端建议：

- ROI 点位优先使用归一化坐标，`x/y` 范围是 `0..1`。
- `image_width/image_height` 使用当前截图实际尺寸。
- 保存 ROI 后后端会自动更新摄像头 `config_version`，Worker 会热更新。

## 10. 设置接口

### 10.1 获取设置

```http
GET /api/settings
```

### 10.2 保存设置但不应用到摄像头

```http
POST /api/settings/save
Content-Type: application/json
```

### 10.3 保存并应用设置

```http
POST /api/settings/apply
Content-Type: application/json
```

请求体可以带 `camera_id` 或 `camera_ids`，不带则应用到全部摄像头：

```json
{
  "camera_id": "cam_001",
  "detect": {
    "detector_type": "yolo_pose",
    "motion_threshold": 4,
    "stop_duration_seconds": 30
  },
  "detector_config": {
    "model_path": "./models/robot_pose.onnx",
    "model_family": "yolo11_pose",
    "input_size": 640,
    "num_keypoints": 6,
    "class_count": 1,
    "target_keypoints": [2, 3, 4, 5],
    "motion_mode": "mean",
    "keypoint_conf_threshold": 0.25,
    "providers": ["CPUExecutionProvider"]
  },
  "video": {
    "target_fps": 3
  }
}
```

### 10.4 重置设置

```http
POST /api/settings/reset
```

## 11. 共享规则面板

规则面板用于配置机器人停机判断。检测任务运行时调用绑定的共享规则：

```text
task detector -> DetectResult
task tracker  -> 运动分数
shared rule   -> RUNNING / IDLE / STOPPED / UNKNOWN + detail
```

`OFFLINE` 不是规则分数判断出来的，而是摄像头共享拉流读不到 RTSP 帧时直接判定。

### 11.1 规则列表

```http
GET /api/rules
```

### 11.2 创建规则

```http
POST /api/rules
Content-Type: application/json
```

```json
{
  "name": "motion 停机规则",
  "description": "适用于 motion 检测任务",
  "supported_detector_types": ["motion"],
  "rule_config": {
    "motion_threshold": 4,
    "stop_seconds": 30,
    "unknown_seconds": 10,
    "confirm_frames": 2,
    "status_hold_seconds": 1.0
  }
}
```

### 11.3 更新规则

```http
PUT /api/rules/{rule_id}
Content-Type: application/json
```

```json
{
  "rule_config": {
    "motion_threshold": 6,
    "stop_seconds": 20
  }
}
```

保存后规则 `version` 会递增。所有绑定该规则的检测任务会在运行时热更新。

字段说明：

- `motion_threshold`：运动分数超过该值，判定 `RUNNING`。
- `stop_seconds`：运动分数持续低于阈值超过该秒数，判定 `STOPPED`。
- `unknown_seconds`：目标/关键点短暂丢失宽限时间。
- `confirm_frames`：状态切换需要连续确认的帧数。
- `status_hold_seconds`：状态最短保持时间，防止抖动。

### 11.4 查看规则影响范围

```http
GET /api/rules/{rule_id}/usage
```

前端在编辑共享规则前应展示影响任务数量和任务列表，避免用户无意影响多个任务。

## 12. 关键点调试

### 12.1 获取关键点调试数据

```http
GET /api/debug/keypoints?camera_id=cam_001
```

返回包含：

- `frame_width / frame_height`
- `bbox`
- `keypoints[]`
- `summary.valid_keypoints`
- `summary.moving_keypoints`
- `summary.mean_delta`
- `summary.max_delta`
- `summary.reason`
- `annotated_image_url`
- `worker`

关键点字段示例：

```json
{
  "index": 2,
  "name": "kp_2",
  "x": 0.45,
  "y": 0.52,
  "x_px": 864,
  "y_px": 561.6,
  "confidence": 0.88,
  "delta_px": 5.2,
  "avg_delta_px": 4.6,
  "moving": true,
  "valid": true
}
```

### 12.2 评估关键点设置

```http
POST /api/debug/keypoints/evaluate
Content-Type: application/json
```

请求体：

```json
{
  "camera_id": "cam_001",
  "settings": {
    "motion_threshold": 4,
    "stop_duration_seconds": 30
  }
}
```

## 13. 模型管理

后端当前只支持 `.onnx` 模型。

### 13.1 获取模型列表

```http
GET /api/models
```

模型字段：

```json
{
  "id": 1,
  "name": "robot_pose",
  "file_name": "robot_pose.onnx",
  "file_path": "./models/robot_pose.onnx",
  "model_type": "yolo_pose",
  "model_family": "yolo11_pose",
  "input_size": 640,
  "class_count": 1,
  "num_keypoints": 6,
  "labels": null,
  "metadata": null,
  "file_exists": true,
  "size_bytes": 123456
}
```

### 13.2 上传模型

```http
POST /api/models/upload
Content-Type: multipart/form-data
```

表单字段：

```text
file: robot_pose.onnx
name: robot_pose
model_type: yolo_pose
model_family: yolo11_pose
input_size: 640
class_count: 1
num_keypoints: 6
labels: ["base","joint1","joint2"]
metadata: {"source":"site-a"}
```

### 13.3 注册已存在模型文件

```http
POST /api/models/register
Content-Type: application/json
```

```json
{
  "file_name": "robot_pose.onnx",
  "model_type": "yolo_pose",
  "model_family": "yolo11_pose",
  "input_size": 640,
  "class_count": 1,
  "num_keypoints": 6
}
```

### 13.4 绑定模型到摄像头

```http
POST /api/models/bind-camera
Content-Type: application/json
```

```json
{
  "camera_id": 1,
  "model_id": 1,
  "extra_config": {
    "providers": ["CPUExecutionProvider"],
    "target_keypoints": [2, 3, 4, 5],
    "keypoint_conf_threshold": 0.25
  }
}
```

绑定后后端会更新：

- `camera.detector_type`
- `camera.detector_config`
- `camera.config_version`

### 13.5 模型单图测试

```http
POST /api/models/{model_id}/test-image
Content-Type: multipart/form-data
```

表单字段：

```text
file: test.jpg
extra_config: {"providers":["CPUExecutionProvider"]}
```

返回包含 `result`、`bbox`、关键点数量和 `annotated_image_url`。

## 14. 事件和告警

后端核心事件接口是 `/api/events`，前端兼容告警接口是 `/api/alarms`。

### 14.1 事件列表

```http
GET /api/events?camera_id=1&status=OPEN&limit=200
```

可选查询参数：

```text
camera_id
event_type=STOPPED|OFFLINE|UNKNOWN
status=OPEN|RECOVERED
handled=true|false
false_alarm=true|false
start_time=2026-06-01T00:00:00
end_time=2026-06-02T00:00:00
limit=200
```

事件字段：

```json
{
  "id": 1,
  "camera_id": 1,
  "event_type": "STOPPED",
  "status": "OPEN",
  "start_time": "2026-06-01T10:00:00",
  "end_time": null,
  "duration_seconds": 120.5,
  "snapshot_url": "/data/snapshots/xxx.jpg",
  "annotated_snapshot_url": "/data/snapshots/xxx_annotated.jpg",
  "recovery_snapshot_url": null,
  "recovery_annotated_url": null,
  "clip_url": null,
  "reason": "超过停机阈值",
  "rule_detail": {},
  "detector_type": "yolo_pose",
  "handled": false,
  "false_alarm": false,
  "remark": ""
}
```

图片 URL 是后端相对路径，前端如果没有配置代理，需要拼接后端 base URL。

### 14.2 事件统计

```http
GET /api/events/summary?days=1
```

### 14.3 事件关键帧

```http
GET /api/events/{event_id}/frames
```

`frame_type` 可能是：

```text
open
recover
sample
manual
debug
```

### 14.4 处理事件

```http
PUT /api/events/{event_id}/handle
Content-Type: application/json
```

```json
{
  "remark": "现场已确认"
}
```

### 14.5 标记误报

```http
PUT /api/events/{event_id}/false-alarm
Content-Type: application/json
```

```json
{
  "false_alarm": true,
  "remark": "人员遮挡导致"
}
```

### 14.6 手动关闭事件

```http
PUT /api/events/{event_id}/close
Content-Type: application/json
```

```json
{
  "remark": "人工关闭"
}
```

### 14.7 前端兼容告警接口

```http
GET /api/alarms?page=1&page_size=20&status=unhandled
PUT /api/alarms/alarm_001/ack
GET /api/alarms/alarm_001/snapshot?annotated=true
```

如果前端已有“告警中心”概念，可以接 `/api/alarms`；如果是新做页面，建议直接接 `/api/events`。

## 15. 调试接口

### 15.1 RTSP 当前帧截图

```http
POST /api/cameras/{numeric_id}/snapshot
```

### 15.2 对当前帧做一次检测

```http
POST /api/cameras/{numeric_id}/debug-detect
```

### 15.3 上传单图检测

```http
POST /api/cameras/{numeric_id}/image-detect
Content-Type: multipart/form-data
```

表单字段：

```text
file: test.jpg
```

### 15.4 上传前后两张图对比检测

```http
POST /api/cameras/{numeric_id}/image-pair-detect
Content-Type: multipart/form-data
```

表单字段：

```text
before: before.jpg
after: after.jpg
```

## 16. 系统和维护接口

### 16.1 健康检查

```http
GET /api/system/health
```

### 16.2 系统自检

```http
GET /api/system/self-check
```

检查内容包括：

- 存储目录
- 模型目录
- 日志目录
- 数据库连接
- OpenCV
- ONNX Runtime providers

### 16.3 检测器列表

```http
GET /api/system/detectors
```

### 16.4 视频缓存状态

```http
GET /api/system/streams
```

### 16.5 存储容量

```http
GET /api/system/storage
```

### 16.6 数据库备份和清理

```http
GET  /api/system/backups
POST /api/system/backup
POST /api/system/cleanup
```

清理接口默认 `dry_run=true`，前端做危险操作时应二次确认：

```json
{
  "dry_run": true,
  "sample_frame_days": 30,
  "orphan_file_days": 14,
  "backup_keep": 10
}
```

## 17. 前端类型草稿

```ts
export type ApiResp<T> = {
  ok?: boolean;
  code: number;
  data: T;
  message: string;
};

export type RuntimeState =
  | "RUNNING"
  | "IDLE"
  | "STOPPED"
  | "OFFLINE"
  | "UNKNOWN";

export type CameraCard = {
  id: string;
  numeric_id: number;
  name: string;
  area: string;
  line: string;
  location: string;
  robot_id: string;
  robot_name: string;
  enabled: boolean;
  status: "online" | "offline" | "error";
  runtime_state: RuntimeState;
  stream_type: "mjpeg";
  last_online_at: string | null;
  rtsp_url_masked: string;
  stream_urls: {
    mjpeg: string;
    mjpeg_annotated: string;
    snapshot: string;
  };
  detector_type: "motion" | "aruco" | "yolo" | "yolo_pose";
  fps_limit: number;
  motion_threshold: number;
  stop_seconds: number;
  config_version: number;
};

export type RuntimeStatus = {
  camera_id: string;
  numeric_camera_id: number;
  camera_name: string;
  robot_id: string;
  robot_name: string;
  state: RuntimeState;
  fps: number;
  valid_keypoints: number;
  moving_keypoints: number;
  mean_delta: number;
  max_delta: number;
  motion_score: number;
  stop_duration_seconds: number;
  last_update_at: string | null;
  message: string;
  rule: Record<string, unknown>;
};

export type EventItem = {
  id: number;
  camera_id: number;
  event_type: "STOPPED" | "OFFLINE" | "UNKNOWN";
  status: "OPEN" | "RECOVERED";
  start_time: string;
  end_time: string | null;
  duration_seconds: number;
  snapshot_url: string | null;
  annotated_snapshot_url: string | null;
  recovery_snapshot_url: string | null;
  recovery_annotated_url: string | null;
  clip_url: string | null;
  reason: string;
  rule_detail: Record<string, unknown> | null;
  detector_type: string;
  handled: boolean;
  false_alarm: boolean;
  remark: string;
};
```

## 18. 注意事项

- 当前后端没有鉴权。若前端部署到现场环境，建议由网关或反向代理补基础鉴权。
- 当前视频推荐用 MJPEG，HLS 路径是占位，返回 501。
- 多路视频同时打开会增加 CPU 压力，列表页建议只开低 FPS 或只显示 snapshot，详情页再开 annotated MJPEG。
- 模型上传只允许 `.onnx`。
- 所有 `/data/...` 图片地址都是后端静态文件地址；跨域部署时要拼接后端 base URL 或配置代理。
- 修改摄像头、ROI、模型绑定、settings apply 后，后端会递增 `config_version`，Worker 会自动热更新，但前端最好刷新一次详情数据。
- `STOPPED / OFFLINE / UNKNOWN` 是事件状态来源，`RUNNING / IDLE` 通常表示恢复或正常。
- 真实 RTSP、ONNX 推理和多路性能依赖现场环境，前端联调时先用 `/api/system/self-check` 和 `/api/system/workers` 排查。
