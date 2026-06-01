# 工业机器人运行状态视觉检测平台

一个前后端分离的 RTSP 工业机器人运行/停机检测平台。当前主线定位为：**内网可视化诊断平台 + 事件复盘平台**。

平台重点不是把异常通知到外网，而是在内网让现场人员看清楚：

```text
哪台机器人当前是什么状态
为什么被判定为停机/离线/未知
当时画面是什么
检测框/关键点/轨迹是否正确
事件什么时候开始、什么时候恢复、持续多久
是否属于误报
```

## 已支持能力

- 多摄像头 RTSP 管理
- RTSP 连接测试
- 每路摄像头独立检测 Worker
- ROI 区域裁剪
- `motion / aruco / yolo / yolo_pose` 检测器
- YOLO11 / YOLO26 ONNX 目标检测
- YOLO11 Pose ONNX 关键点检测
- 轨迹缓存、累计位移、平均速度、关键点位移、姿态角变化
- RUNNING / IDLE / STOPPED / OFFLINE / UNKNOWN 状态判断
- 规则解释 `rule_detail`
- 事件中心：打开、恢复、处理、误报、备注、人工关闭
- 原始截图、标注截图、恢复截图、恢复标注图
- 本地图片检测、双图对比检测、RTSP 当前帧调试检测
- WebSocket 实时状态推送
- 后端 MJPEG 视频流：原始流和标注流
- Docker Compose 部署骨架

## 快速启动

### 后端本地启动

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

打开：

```text
http://127.0.0.1:8000/docs
```

### Docker Compose

```bash
docker compose up -d --build
```

## 关键接口

### 摄像头

```text
GET    /api/cameras
POST   /api/cameras
PUT    /api/cameras/{id}
DELETE /api/cameras/{id}
POST   /api/cameras/{id}/test
POST   /api/cameras/{id}/start
POST   /api/cameras/{id}/stop
```

### 调试

```text
POST /api/cameras/{id}/snapshot
POST /api/cameras/{id}/debug-detect
POST /api/cameras/{id}/image-detect
POST /api/cameras/{id}/image-pair-detect
GET  /api/cameras/{id}/last-result
GET  /api/system/workers
```

### 前端视频流

后端提供浏览器可直接播放的 MJPEG 流，不需要前端直接播放 RTSP。前端最简单接入方式：

```html
<img src="http://127.0.0.1:8000/api/cameras/1/stream.mjpg?annotated=false" />
```

可用接口：

```text
GET /api/cameras/{id}/stream-info
GET /api/cameras/{id}/frame.jpg?annotated=false
GET /api/cameras/{id}/frame.jpg?annotated=true
GET /api/cameras/{id}/stream.mjpg?annotated=false&fps=8&quality=80
GET /api/cameras/{id}/stream.mjpg?annotated=true&fps=8&quality=80
GET /api/system/streams
```

说明：

- `annotated=false` 返回原始摄像头画面。
- `annotated=true` 返回带 ROI、检测框、关键点、轨迹、规则解释的诊断画面。
- 视频流默认复用 Worker 已读取的最新帧，不额外重复拉 RTSP。
- 如果 Worker 未启动或还没有读到画面，接口会返回占位图，方便前端保持布局稳定。
- MJPEG 适合内网监控和调试；后续若需要更低延迟或更多路预览，可再接 MediaMTX/WebRTC。

### 事件中心

```text
GET  /api/events
GET  /api/events/summary
GET  /api/events/{id}
PUT  /api/events/{id}/handle
PUT  /api/events/{id}/unhandle
PUT  /api/events/{id}/remark
PUT  /api/events/{id}/false-alarm
PUT  /api/events/{id}/close
```

### 模型和配置

```text
GET    /api/models
POST   /api/models/upload
DELETE /api/models/{model_name}

GET    /api/config/export
POST   /api/config/import
```

## 检测器说明

| detector_type | 说明 | 是否需要模型 | 推荐场景 |
|---|---|---|---|
| motion | ROI 运动检测 | 否 | 快速跑通平台闭环 |
| aruco | ArUco 标记中心点检测 | 否 | 允许贴标记，稳定性最好 |
| yolo | YOLO11 / YOLO26 ONNX 目标检测 | 是 | 检测末端执行器/夹具/机器人部件 |
| yolo_pose | YOLO11 Pose ONNX 关键点检测 | 是 | 检测机器人关节/末端姿态变化 |

## yolo_pose 配置示例

```json
{
  "name": "robot_pose_01",
  "rtsp_url": "rtsp://user:pass@192.168.1.100:554/stream1",
  "location": "产线A-机器人1",
  "enabled": true,
  "fps_limit": 3,
  "roi": [100, 80, 900, 600],
  "detector_type": "yolo_pose",
  "detector_config": {
    "model_path": "/app/models/robot_pose.onnx",
    "model_family": "yolo11_pose",
    "input_size": 640,
    "num_keypoints": 6,
    "class_count": 1,
    "target_keypoints": [2, 3, 4, 5],
    "motion_mode": "mean",
    "keypoint_conf_threshold": 0.25,
    "providers": ["CPUExecutionProvider"],
    "tracker": {
      "window_seconds": 30,
      "min_step_px": 1.5,
      "movement_score": "keypoint_mean_step"
    },
    "rule": {
      "confirm_frames": 2,
      "status_hold_seconds": 1,
      "unknown_seconds": 10
    }
  },
  "motion_threshold": 4,
  "stop_seconds": 30
}
```

## 状态说明

| 状态 | 说明 |
|---|---|
| RUNNING | 有明显运动 |
| IDLE | 静止但未超过停机时间 |
| STOPPED | 超过停机阈值无运动 |
| OFFLINE | RTSP 断线或不可读 |
| UNKNOWN | 检测不到目标或不确定 |

## 事件记录内容

事件中心会记录：

- 事件类型：STOPPED / OFFLINE / UNKNOWN
- 事件状态：OPEN / RECOVERED
- 开始时间、恢复时间、持续时间
- 原始截图
- 标注截图
- 恢复截图
- 恢复标注图
- 检测器类型
- 规则解释 `reason`
- 规则明细 `rule_detail`
- 是否已处理
- 是否误报
- 备注

## 标注图内容

标注图用于现场调试，会画出：

- ROI
- bbox
- center
- keypoints
- 关键点编号
- 关键点连线
- 最近轨迹线
- 当前状态
- 事件类型
- 检测器类型
- 运动分数
- 累计位移
- 平均速度
- 关键点位移
- 姿态角变化
- 阈值和停机时间
- 规则解释原因

## 通知系统说明

通知模块保留为可选插件，但内网部署默认关闭：

```bash
ALERT_ENABLED=false
```

当前主线不再围绕 Telegram、Webhook、企业微信继续扩展。

## 日志

默认日志：

```text
./data/logs/backend.log
```

调试时可设置：

```bash
LOG_LEVEL=DEBUG
```

## 当前进度

查看：

```text
PROGRESS.md
```

## 后端架构优化说明

当前版本已经将后端拆成 API 层、service 层、worker 层和检测算法层。详细说明见：

```text
ARCHITECTURE.md
```

核心变化：

```text
api/        只负责路由
services/   负责业务逻辑
workers/    负责后台拉流和检测主循环
detectors/  负责检测算法
tracker/    负责轨迹和位移统计
rules/      负责状态判断
```

保留原有接口路径，例如：

```text
POST /api/cameras/{id}/start
POST /api/cameras/{id}/debug-detect
POST /api/cameras/{id}/image-detect
GET  /api/cameras/{id}/last-result
```

所以已有前端一般不需要因为本次后端架构拆分而改接口。

## API 统一响应格式

当前后端 REST API 统一返回：

```json
{
  "ok": true,
  "data": {},
  "message": ""
}
```

失败返回：

```json
{
  "ok": false,
  "data": null,
  "message": "错误原因"
}
```

例如：

```bash
curl http://127.0.0.1:8000/api/system/workers
```

返回：

```json
{
  "ok": true,
  "data": [
    {
      "camera_id": 1,
      "alive": true,
      "health": {
        "running": true,
        "rtsp_connected": true,
        "fps_actual": 2.9,
        "detect_fps_actual": 2.8,
        "last_config_version": 3,
        "error_count": 0,
        "reconnect_count": 0
      }
    }
  ],
  "message": ""
}
```

## 配置热更新

摄像头表包含 `config_version` 字段。每次调用：

```text
PUT /api/cameras/{id}
POST /api/config/import
```

如果配置发生变化，后端会自动递增 `config_version`。Worker 会检测版本变化并重置 tracker/rule，避免旧轨迹影响新配置。

## Worker 健康状态

重点查看：

```text
GET /api/system/workers
GET /api/cameras/{id}/last-result
```

这些接口适合前端做“运行诊断面板”，字段包括：

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
last_error
reconnect_count
consecutive_read_failures
last_config_version
runtime.reset_count
runtime.last_reset_reason
```

## 模型元数据管理

### 上传模型并注册元数据

```bash
curl -X POST http://localhost:8000/api/models/upload \
  -F "file=@./robot_pose.onnx" \
  -F "model_type=yolo_pose" \
  -F "model_family=yolo11_pose" \
  -F "input_size=640" \
  -F "class_count=1" \
  -F "num_keypoints=6"
```

### 注册已经存在的模型文件

```bash
curl -X POST http://localhost:8000/api/models/register \
  -H "Content-Type: application/json" \
  -d '{
    "file_name": "robot_pose.onnx",
    "model_type": "yolo_pose",
    "model_family": "yolo11_pose",
    "input_size": 640,
    "class_count": 1,
    "num_keypoints": 6
  }'
```

### 绑定模型到摄像头

```bash
curl -X POST http://localhost:8000/api/models/bind-camera \
  -H "Content-Type: application/json" \
  -d '{
    "camera_id": 1,
    "model_id": 1,
    "extra_config": {
      "providers": ["CPUExecutionProvider"],
      "target_keypoints": [2, 3, 4, 5]
    }
  }'
```

绑定后会自动更新摄像头：

```text
camera.detector_type
camera.detector_config
camera.config_version
```

Worker 会检测 `config_version` 变化并自动重置运行时。

## 事件关键帧

查看某个事件的关键帧：

```bash
curl http://localhost:8000/api/events/1/frames
```

事件打开时会保存 `open` 关键帧，事件恢复时会保存 `recover` 关键帧。

## 后端基础测试

```bash
cd backend
pip install -r requirements.txt pytest
pytest -q
```

## 系统自检

```bash
curl http://localhost:8000/api/system/self-check
```

自检内容包括：

- `storage_dir` 是否存在、可写、剩余空间
- `model_dir` 是否存在、可写、剩余空间
- `log_dir` 是否存在、可写、剩余空间
- 数据库是否可连接
- OpenCV 是否可导入
- ONNX Runtime 是否可导入、可用 provider 列表

## 后端维护接口

### 查看存储容量

```bash
curl http://127.0.0.1:8000/api/system/storage
```

### 创建 SQLite 数据库备份

```bash
curl -X POST http://127.0.0.1:8000/api/system/backup
```

备份文件默认保存到：

```text
./data/backups/
```

### 清理旧数据

默认只预估，不删除：

```bash
curl -X POST http://127.0.0.1:8000/api/system/cleanup \
  -H 'Content-Type: application/json' \
  -d '{"dry_run": true, "sample_frame_days": 30, "orphan_file_days": 14, "backup_keep": 10}'
```

真正删除：

```bash
curl -X POST http://127.0.0.1:8000/api/system/cleanup \
  -H 'Content-Type: application/json' \
  -d '{"dry_run": false, "sample_frame_days": 30, "orphan_file_days": 14, "backup_keep": 10}'
```

### 模型单图测试

```bash
curl -X POST "http://127.0.0.1:8000/api/models/1/test-image" \
  -F "file=@./test.jpg"
```

返回内容包含检测结果和标注图地址。
