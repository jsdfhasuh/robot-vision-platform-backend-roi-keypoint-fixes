from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.core.logging import get_logger
from app.detectors.base import BaseDetector, DetectResult

logger = get_logger(__name__)


class YoloDetector(BaseDetector):
    """YOLO ONNX 目标检测器。

    使用 onnxruntime 推理，支持两类 Ultralytics ONNX 检测输出：

    1. YOLO11 / YOLOv8 / YOLOv5 传统输出
       - 常见形状: (1, 84, 8400)、(1, 8400, 84)、(1, 25200, 85)
       - 框格式: xywh
       - 后处理: confidence filter + NMS

    2. YOLO26 end-to-end 输出
       - 常见形状: (1, 300, 6)
       - 每行: [x1, y1, x2, y2, confidence, class_id]
       - 后处理: confidence filter，不做 NMS

    配置示例：
    {
      "model_path": "/app/models/end_effector.onnx",
      "model_family": "auto",          # auto / yolo11 / yolo26
      "input_size": 640,
      "target_class": null,
      "conf_threshold": 0.35,
      "iou_threshold": 0.45,
      "providers": ["CPUExecutionProvider"]
    }
    """

    detector_type = "yolo"

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.model_path = self.config.get("model_path")
        self.model_family = str(self.config.get("model_family", "auto")).lower().strip()
        self.input_size = int(self.config.get("input_size", 640))
        self.target_class = self.config.get("target_class")
        self.conf_threshold = float(self.config.get("conf_threshold", 0.35))
        self.iou_threshold = float(self.config.get("iou_threshold", 0.45))
        self.providers = self.config.get("providers") or ["CPUExecutionProvider"]
        self.prev_center: tuple[float, float] | None = None
        self.output_mode: str = "unknown"

        self.session = None
        self.input_name: str | None = None
        self.output_names: list[str] = []
        self.input_hw: tuple[int, int] = (self.input_size, self.input_size)
        self.load_error: str | None = None

        self._load_model()

    def _load_model(self) -> None:
        if not self.model_path:
            self.load_error = "missing model_path"
            logger.error("yolo onnx detector missing model_path")
            return

        model_file = Path(self.model_path)
        if not model_file.exists():
            self.load_error = f"model not found: {self.model_path}"
            logger.error("yolo onnx model not found model_path=%s", self.model_path)
            return

        try:
            import onnxruntime as ort

            logger.info(
                "loading yolo onnx model model_path=%s family=%s input_size=%s target_class=%s conf_threshold=%s iou_threshold=%s providers=%s",
                self.model_path,
                self.model_family,
                self.input_size,
                self.target_class,
                self.conf_threshold,
                self.iou_threshold,
                self.providers,
            )
            sess_options = ort.SessionOptions()
            sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self.session = ort.InferenceSession(str(model_file), sess_options=sess_options, providers=self.providers)
            input_meta = self.session.get_inputs()[0]
            self.input_name = input_meta.name
            self.output_names = [o.name for o in self.session.get_outputs()]
            self.input_hw = self._resolve_input_hw(input_meta.shape)
            output_shapes = [o.shape for o in self.session.get_outputs()]
            logger.info(
                "yolo onnx model loaded model_path=%s input=%s input_hw=%s outputs=%s output_shapes=%s active_providers=%s",
                self.model_path,
                self.input_name,
                self.input_hw,
                self.output_names,
                output_shapes,
                self.session.get_providers(),
            )
        except Exception as exc:
            self.load_error = str(exc)
            self.session = None
            logger.exception("yolo onnx detector init failed model_path=%s", self.model_path)

    def detect(self, frame: np.ndarray) -> DetectResult:
        if frame is None or frame.size == 0:
            return DetectResult(False, 0.0, 0.0, "empty frame")
        if self.session is None or self.input_name is None:
            return DetectResult(False, 0.0, 0.0, f"YOLO ONNX not ready: {self.load_error or 'unknown error'}")

        try:
            blob, ratio, pad = self._preprocess(frame)
            outputs = self.session.run(self.output_names, {self.input_name: blob})
            detections = self._postprocess(outputs, frame.shape[:2], ratio, pad)
        except Exception as exc:
            logger.exception("yolo onnx detect failed model_path=%s", self.model_path)
            return DetectResult(False, 0.0, 0.0, f"YOLO ONNX detect failed: {exc}")

        if not detections:
            return DetectResult(False, 0.0, 0.0, f"target not found mode={self.output_mode}")

        best = sorted(detections, key=lambda x: (x["confidence"], x["area"]), reverse=True)[0]
        x1, y1, x2, y2 = best["bbox"]
        center = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)

        if self.prev_center is None:
            distance = 0.0
        else:
            distance = float(np.linalg.norm(np.array(center) - np.array(self.prev_center)))
        self.prev_center = center

        return DetectResult(
            target_found=True,
            motion_distance=distance,
            confidence=float(best["confidence"]),
            message=(
                f"{self.output_mode}_class={best['class_id']}, "
                f"conf={best['confidence']:.2f}, displacement={distance:.2f}px"
            ),
            center=center,
            bbox=best["bbox"],
            metadata={
                "class_id": best["class_id"],
                "area": best["area"],
                "model_path": self.model_path,
                "backend": "onnxruntime",
                "model_family": self.model_family,
                "output_mode": self.output_mode,
                "detections": len(detections),
            },
        )

    def _resolve_input_hw(self, shape: list[Any]) -> tuple[int, int]:
        """从 ONNX 输入 shape 推断 H/W，动态维度时回退到 input_size。"""
        try:
            if len(shape) == 4:
                h, w = shape[2], shape[3]
                if isinstance(h, int) and isinstance(w, int) and h > 0 and w > 0:
                    return int(h), int(w)
        except Exception:
            pass
        return self.input_size, self.input_size

    def _preprocess(self, frame: np.ndarray) -> tuple[np.ndarray, float, tuple[float, float]]:
        """BGR frame -> letterbox RGB NCHW float32。"""
        target_h, target_w = self.input_hw
        h, w = frame.shape[:2]
        r = min(target_h / h, target_w / w)
        new_w, new_h = int(round(w * r)), int(round(h * r))
        resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

        dw = (target_w - new_w) / 2
        dh = (target_h - new_h) / 2
        top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
        left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
        padded = cv2.copyMakeBorder(resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114, 114, 114))

        rgb = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB)
        blob = rgb.astype(np.float32) / 255.0
        blob = np.transpose(blob, (2, 0, 1))[None, ...]
        return np.ascontiguousarray(blob), r, (float(left), float(top))

    def _postprocess(
        self,
        outputs: list[np.ndarray],
        original_shape: tuple[int, int],
        ratio: float,
        pad: tuple[float, float],
    ) -> list[dict[str, Any]]:
        raw = outputs[0]
        pred = np.asarray(raw)

        # 去掉 batch 维，但保留二维预测表。
        if pred.ndim == 3 and pred.shape[0] == 1:
            pred = pred[0]
        elif pred.ndim > 3:
            pred = np.squeeze(pred)

        if pred.ndim == 1:
            pred = pred.reshape(1, -1)

        if self.model_family == "yolo26":
            return self._postprocess_yolo26(pred, original_shape, ratio, pad)
        if self.model_family == "yolo11":
            return self._postprocess_traditional(pred, original_shape, ratio, pad, mode="yolo11")

        # auto：优先识别 YOLO26 end-to-end。典型输出为 (300, 6)，格式 xyxy + conf + class。
        if self._looks_like_yolo26(pred):
            return self._postprocess_yolo26(pred, original_shape, ratio, pad)

        # 已经带 NMS 的通用输出也按 yolo26/end2end 解析。
        if pred.ndim == 2 and pred.shape[1] in (6, 7) and self._looks_like_xyxy_rows(pred):
            return self._postprocess_end2end_like(pred, original_shape, ratio, pad, mode="nms_output")

        return self._postprocess_traditional(pred, original_shape, ratio, pad, mode="yolo11")

    def _looks_like_yolo26(self, pred: np.ndarray) -> bool:
        if pred.ndim != 2 or pred.shape[1] < 6:
            return False
        if pred.shape[1] in (6, 7) and pred.shape[0] <= 500:
            return self._looks_like_xyxy_rows(pred)
        return False

    def _looks_like_xyxy_rows(self, pred: np.ndarray) -> bool:
        rows = pred[:, -6:] if pred.shape[1] == 7 else pred[:, :6]
        if rows.size == 0:
            return False
        sample = rows[: min(len(rows), 20)].astype(np.float32)
        x1, y1, x2, y2, conf = sample[:, 0], sample[:, 1], sample[:, 2], sample[:, 3], sample[:, 4]
        xyxy_rate = float(np.mean((x2 >= x1) & (y2 >= y1)))
        conf_rate = float(np.mean((conf >= 0.0) & (conf <= 1.0)))
        return xyxy_rate > 0.8 and conf_rate > 0.8

    def _postprocess_yolo26(
        self,
        pred: np.ndarray,
        original_shape: tuple[int, int],
        ratio: float,
        pad: tuple[float, float],
    ) -> list[dict[str, Any]]:
        return self._postprocess_end2end_like(pred, original_shape, ratio, pad, mode="yolo26_end2end")

    def _postprocess_end2end_like(
        self,
        pred: np.ndarray,
        original_shape: tuple[int, int],
        ratio: float,
        pad: tuple[float, float],
        mode: str,
    ) -> list[dict[str, Any]]:
        self.output_mode = mode
        detections: list[dict[str, Any]] = []
        if pred.ndim != 2 or pred.shape[1] < 6:
            logger.warning("unsupported %s output shape=%s", mode, pred.shape)
            return []

        # 支持 [batch_id,x1,y1,x2,y2,score,class] 和 [x1,y1,x2,y2,score,class]
        for row in pred.astype(np.float32):
            values = row[-6:] if pred.shape[1] == 7 else row[:6]
            x1, y1, x2, y2, score, class_id = values[:6]
            self._append_detection(
                detections,
                [x1, y1, x2, y2],
                float(score),
                int(round(float(class_id))),
                original_shape,
                ratio,
                pad,
            )
        # YOLO26 本身已 NMS-free，不再做 NMS。
        return detections

    def _postprocess_traditional(
        self,
        pred: np.ndarray,
        original_shape: tuple[int, int],
        ratio: float,
        pad: tuple[float, float],
        mode: str,
    ) -> list[dict[str, Any]]:
        self.output_mode = mode

        # YOLO11/YOLOv8 常见输出: (84, 8400)，转成 (8400, 84)
        if pred.ndim == 2 and pred.shape[0] < pred.shape[1] and pred.shape[0] <= 256:
            pred = pred.T

        if pred.ndim != 2 or pred.shape[1] < 5:
            logger.warning("unsupported traditional yolo onnx output shape=%s", pred.shape)
            return []

        detections: list[dict[str, Any]] = []
        for row in pred.astype(np.float32):
            values = row
            cx, cy, bw, bh = values[:4]

            # YOLOv5: [x,y,w,h,obj,cls...]
            # YOLO11/YOLOv8: [x,y,w,h,cls...]
            if values.shape[0] >= 7:
                obj_conf = float(values[4])
                cls_scores_v5 = values[5:]
                cls_scores_v8 = values[4:]

                class_id_v5 = int(np.argmax(cls_scores_v5)) if cls_scores_v5.size else 0
                score_v5 = obj_conf * float(cls_scores_v5[class_id_v5]) if cls_scores_v5.size else obj_conf

                class_id_v8 = int(np.argmax(cls_scores_v8)) if cls_scores_v8.size else 0
                score_v8 = float(cls_scores_v8[class_id_v8]) if cls_scores_v8.size else 0.0

                # 两种解释都算一下，取分数更合理的那个。
                if score_v8 >= score_v5:
                    score = score_v8
                    class_id = class_id_v8
                else:
                    score = score_v5
                    class_id = class_id_v5
            elif values.shape[0] == 6:
                # 传统 fallback: [x,y,w,h,score,class]
                score = float(values[4])
                class_id = int(round(float(values[5])))
            else:
                score = float(values[4])
                class_id = 0

            x1 = cx - bw / 2
            y1 = cy - bh / 2
            x2 = cx + bw / 2
            y2 = cy + bh / 2
            self._append_detection(detections, [x1, y1, x2, y2], score, class_id, original_shape, ratio, pad)

        return self._nms(detections)

    def _append_detection(
        self,
        detections: list[dict[str, Any]],
        xyxy: list[float] | np.ndarray,
        score: float,
        class_id: int,
        original_shape: tuple[int, int],
        ratio: float,
        pad: tuple[float, float],
    ) -> None:
        if not np.isfinite(score) or score < self.conf_threshold:
            return
        if self.target_class is not None and class_id != int(self.target_class):
            return

        h, w = original_shape
        pad_x, pad_y = pad
        x1, y1, x2, y2 = [float(v) for v in xyxy]
        x1 = (x1 - pad_x) / ratio
        y1 = (y1 - pad_y) / ratio
        x2 = (x2 - pad_x) / ratio
        y2 = (y2 - pad_y) / ratio
        x1, y1 = max(0.0, x1), max(0.0, y1)
        x2, y2 = min(float(w - 1), x2), min(float(h - 1), y2)
        area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        if area <= 0:
            return
        detections.append({"bbox": (x1, y1, x2, y2), "confidence": float(score), "class_id": int(class_id), "area": area})

    def _nms(self, detections: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not detections:
            return []
        boxes = [d["bbox"] for d in detections]
        scores = [float(d["confidence"]) for d in detections]
        xywh = []
        for x1, y1, x2, y2 in boxes:
            xywh.append([int(x1), int(y1), int(x2 - x1), int(y2 - y1)])
        indexes = cv2.dnn.NMSBoxes(xywh, scores, self.conf_threshold, self.iou_threshold)
        if len(indexes) == 0:
            return []
        flat = np.array(indexes).reshape(-1).tolist()
        return [detections[i] for i in flat]
