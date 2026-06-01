# 后端测试说明

当前测试重点覆盖不依赖真实 RTSP 摄像头的核心逻辑：

- `RobotStopRule` 状态判断
- `TrackBuffer` 轨迹统计
- `model_service` 模型文件名和默认类型推断

运行方式：

```bash
cd backend
pip install -r requirements.txt pytest
pytest -q
```

真实摄像头、ONNX 推理和图片调试接口建议在现场通过 `/docs` 或前端联调验证。
