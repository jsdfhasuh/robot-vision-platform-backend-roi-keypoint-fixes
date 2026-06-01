# 后端本轮修复：摄像头列表、ROI、逐关键点位移、模型格式

本轮针对前端联调和检测调试修复 4 个重点。

## 1. `/api/cameras` 返回结构与 RTSP 脱敏

`GET /api/cameras` 现在返回前端卡片结构，而不是原始数据库对象。

返回字段包含：

- `id`: `cam_001` 格式
- `numeric_id`: 后端数据库整数 ID
- `name`
- `area`
- `line`
- `robot_id`
- `robot_name`
- `enabled`
- `status`
- `runtime_state`
- `stream_type`
- `stream_urls`
- `rtsp_url_masked`
- `detector_type`
- `fps_limit`
- `motion_threshold`
- `stop_seconds`
- `config_version`

不会再把完整 `rtsp_url` 暴露给前端列表接口。

`POST /api/cameras` 和 `PUT /api/cameras/{id}` 也支持前端传入：

```json
{
  "area": "一车间",
  "line": "A产线",
  "robot_id": "robot_001",
  "robot_name": "机器人1"
}
```

这些字段会保存到 `detector_config.frontend_meta`，当前不新增数据库列。

---

## 2. 真正的 ROI polygon / keypoint filter

新增：

```text
backend/app/services/roi_service.py
```

现在 ROI 不再只是外接矩形。

检测前：

- 根据前端保存的归一化 polygon ROI 生成 mask
- ROI 外区域会被置黑
- `exclude_zones` 会被排除

检测后：

- `yolo_pose` 会按 polygon 过滤关键点
- 支持每个 ROI 的 `keypoint_indexes`
- ROI 外关键点置信度会置为 `0`
- 如果启用 `roi_filter_mode=filter_keypoints` 且无有效关键点，则 `target_found=false`

保存 ROI 接口仍然是：

```text
GET  /api/cameras/{camera_id}/roi
POST /api/cameras/{camera_id}/roi
```

---

## 3. per-keypoint delta

`TrackBuffer` 现在会输出逐关键点位移：

```json
{
  "keypoint_deltas": [
    {
      "index": 3,
      "delta_px": 8.2,
      "avg_delta_px": 5.6,
      "moving_by_min_step": true,
      "valid": true,
      "confidence": 0.91
    }
  ]
}
```

`GET /api/debug/keypoints?camera_id=cam_001` 现在返回每个关键点自己的：

- `delta_px`
- `avg_delta_px`
- `moving`
- `valid`
- `confidence`
- `x/y` 归一化全图坐标
- `x_px/y_px` 全图像素坐标
- `x_roi_px/y_roi_px` ROI 内像素坐标

---

## 4. 模型格式只允许 ONNX

当前检测器全部基于 ONNXRuntime，因此模型管理限制为：

```text
.onnx
```

不再允许上传或注册：

```text
.pt
.engine
```

避免用户上传后误以为后端可以直接推理。

---

## 代码检查

已执行：

```bash
cd backend
python -m compileall app
```

结果：通过。

`pytest` 当前运行失败是因为当前环境没有安装 `sqlalchemy` 依赖，不是本轮代码语法问题。部署环境执行 `pip install -r requirements.txt` 后再运行测试。
