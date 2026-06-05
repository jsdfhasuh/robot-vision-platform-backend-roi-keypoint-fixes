# 后端前端兼容接口说明

本版本在保留原有后端接口的基础上，新增了前端兼容层。前端既可以继续使用当前后端的 `/api/cameras/{id}/stream.mjpg` 等接口，也可以按前端工程文档使用 `/stream/cameras/{camera_id}/mjpeg`、`/api/runtime/status`、`/api/settings` 等接口。

## 1. 通用响应格式

REST 接口现在同时兼容两种判断方式：

```json
{
  "ok": true,
  "code": 0,
  "message": "ok",
  "data": {}
}
```

前端可以使用：

```ts
if (res.code === 0 || res.ok === true) {
  // success
}
```

## 2. 摄像头 ID

后端数据库仍使用数字 ID，例如：

```text
1
2
3
```

兼容层支持字符串 ID：

```text
cam_001
cam_002
cam_003
```

以下两种写法等价：

```text
/stream/cameras/1/mjpeg
/stream/cameras/cam_001/mjpeg
```

## 3. 视频流接口

### MJPEG 流

```http
GET /stream/cameras/{camera_id}/mjpeg
GET /stream/cameras/{camera_id}/mjpeg?annotated=true
```

前端示例：

```html
<img src="/stream/cameras/cam_001/mjpeg?annotated=true" />
```

### 当前截图

```http
GET /stream/cameras/{camera_id}/snapshot
GET /stream/cameras/{camera_id}/snapshot?annotated=true
```

### 视频流信息

```http
GET /api/cameras/{camera_id}/stream-info
```

## 4. 实时状态接口

```http
GET /api/runtime/status
```

返回字段包括：

```text
camera_id
numeric_camera_id
camera_name
state
fps
valid_keypoints
moving_keypoints
mean_delta
max_delta
motion_score
stop_duration_seconds
last_update_at
rule
```

## 5. ROI 接口

```http
GET  /api/cameras/{camera_id}/roi
POST /api/cameras/{camera_id}/roi
```

前端保存 ROI 时推荐使用归一化坐标：

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
        { "x": 0.12, "y": 0.20 },
        { "x": 0.78, "y": 0.20 },
        { "x": 0.82, "y": 0.75 },
        { "x": 0.10, "y": 0.78 }
      ],
      "keypoint_indexes": [2, 3, 4, 5]
    }
  ],
  "exclude_zones": []
}
```

后端会自动：

1. 保存完整归一化 ROI 到 `detector_config.roi_config`
2. 转换外接矩形到 `camera.roi`
3. `config_version + 1`
4. Worker 自动热更新

## 6. 设置接口

```http
GET  /api/settings
POST /api/settings/save
POST /api/settings/apply
POST /api/settings/reset
```

`save` 只保存全局配置文件。  
`apply` 会把配置应用到摄像头并触发 `config_version + 1`。

如果请求体带 `camera_id` 或 `camera_ids`，只应用指定摄像头；否则应用所有摄像头。

## 7. 关节点调试接口

```http
GET /api/debug/keypoints?camera_id=cam_001
POST /api/debug/keypoints/evaluate
```

返回内容包括：

```text
bbox
keypoints[index/name/x/y/confidence/delta_px/moving/valid]
summary.valid_keypoints
summary.moving_keypoints
summary.mean_delta
summary.max_delta
summary.reason
annotated_image_url
```

## 8. 告警中心兼容接口

后端实际使用 `events` 事件中心，兼容层提供 `alarms` 别名：

```http
GET /api/alarms?page=1&page_size=20&status=unhandled
PUT /api/alarms/{alarm_id}/ack
GET /api/alarms/{alarm_id}/snapshot
```

`alarm_001` 会映射到内部事件 ID `1`。

## 9. 检测任务接口

检测任务现在是独立实体。一个摄像头可以有多个检测任务，多个任务共享同一路
RTSP 拉流：

```http
GET    /api/detection-tasks
POST   /api/detection-tasks
GET    /api/detection-tasks/{task_id}
PUT    /api/detection-tasks/{task_id}
DELETE /api/detection-tasks/{task_id}
POST   /api/detection-tasks/{task_id}/start
POST   /api/detection-tasks/{task_id}/stop
GET    /api/detection-tasks/{task_id}/last-result
```

旧 `/api/tasks` 和 `/api/cameras/{id}/start|stop|last-result` 已移除。

## 10. 推荐前端联调顺序

```text
1. GET /api/cameras
2. GET /stream/cameras/cam_001/snapshot
3. GET /stream/cameras/cam_001/mjpeg
4. GET /api/runtime/status
5. GET /api/cameras/cam_001/roi
6. POST /api/cameras/cam_001/roi
7. GET /api/settings
8. POST /api/settings/apply
9. GET /api/debug/keypoints?camera_id=cam_001
10. GET /api/alarms
```
