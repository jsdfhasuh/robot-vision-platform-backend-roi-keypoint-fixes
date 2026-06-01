from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.camera import Camera
from app.models.model_registry import ModelRegistry
from app.detectors.factory import create_detector
from app.services import snapshot_service
from app.utils.annotator import draw_detection

logger = get_logger(__name__)

ALLOWED_EXTS = {".onnx"}


def model_dir() -> Path:
    path = Path(settings.model_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_name(name: str) -> str:
    base = os.path.basename(name or "")
    if not base or base in {".", ".."}:
        raise HTTPException(400, "invalid filename")
    ext = Path(base).suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(400, f"unsupported model extension: {ext}; only .onnx is supported by current ONNXRuntime detectors")
    return base


def parse_labels(value: Any) -> list | None:
    if value is None or value == "":
        return None
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            return [x.strip() for x in value.split(",") if x.strip()]
    return None


def parse_metadata(value: Any) -> dict | None:
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            raise HTTPException(400, "metadata must be valid json object")
    return None


def model_to_dict(model: ModelRegistry, *, file_exists: bool | None = None) -> dict[str, Any]:
    path = Path(model.file_path)
    if file_exists is None:
        file_exists = path.exists()
    size = path.stat().st_size if path.exists() else 0
    return {
        "id": model.id,
        "name": model.name,
        "file_name": model.file_name,
        "file_path": model.file_path,
        "path": model.file_path,
        "model_type": model.model_type,
        "model_family": model.model_family,
        "input_size": model.input_size,
        "class_count": model.class_count,
        "num_keypoints": model.num_keypoints,
        "labels": model.labels,
        "metadata": model.metadata_json,
        "file_exists": file_exists,
        "size_bytes": size,
        "created_at": model.created_at,
        "updated_at": model.updated_at,
    }


def infer_default_type(file_name: str, model_type: str | None = None, model_family: str | None = None) -> tuple[str, str]:
    mt = (model_type or "").strip().lower()
    mf = (model_family or "").strip().lower()
    if not mt:
        lower = file_name.lower()
        mt = "yolo_pose" if "pose" in lower or "keypoint" in lower else "yolo"
    if not mf:
        mf = "yolo11_pose" if mt == "yolo_pose" else "auto"
    return mt, mf


def upsert_registry(
    db: Session,
    *,
    file_name: str,
    file_path: str,
    name: str | None = None,
    model_type: str | None = None,
    model_family: str | None = None,
    input_size: int = 640,
    class_count: int = 1,
    num_keypoints: int = 0,
    labels: Any = None,
    metadata: Any = None,
) -> ModelRegistry:
    safe = safe_name(file_name)
    mt, mf = infer_default_type(safe, model_type, model_family)
    row = db.query(ModelRegistry).filter(ModelRegistry.file_name == safe).first()
    if row is None:
        row = ModelRegistry(file_name=safe, file_path=file_path, name=name or Path(safe).stem)
        db.add(row)
    row.name = name or row.name or Path(safe).stem
    row.file_path = file_path
    row.model_type = mt
    row.model_family = mf
    row.input_size = int(input_size or 640)
    row.class_count = int(class_count or 1)
    row.num_keypoints = int(num_keypoints or 0)
    row.labels = parse_labels(labels)
    row.metadata_json = parse_metadata(metadata)
    db.commit()
    db.refresh(row)
    logger.info("model registered id=%s file=%s type=%s family=%s", row.id, row.file_name, row.model_type, row.model_family)
    return row


def list_models(db: Session | None = None) -> list[dict[str, Any]]:
    root = model_dir()
    files: dict[str, Path] = {}
    for p in sorted(root.iterdir(), key=lambda x: x.name.lower()):
        if p.is_file() and p.suffix.lower() in ALLOWED_EXTS:
            files[p.name] = p

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    if db is not None:
        for model in db.query(ModelRegistry).order_by(ModelRegistry.id.desc()).all():
            seen.add(model.file_name)
            rows.append(model_to_dict(model, file_exists=model.file_name in files))

    # 兼容旧行为：没有注册元数据的模型文件仍然展示出来。
    for name, path in files.items():
        if name in seen:
            continue
        stat = path.stat()
        mt, mf = infer_default_type(name)
        rows.append({
            "id": None,
            "name": path.stem,
            "file_name": name,
            "file_path": str(path),
            "path": str(path),
            "model_type": mt,
            "model_family": mf,
            "input_size": 640,
            "class_count": 1,
            "num_keypoints": 0,
            "labels": None,
            "metadata": None,
            "file_exists": True,
            "size_bytes": stat.st_size,
            "modified_at": stat.st_mtime,
            "ext": path.suffix.lower(),
            "registered": False,
        })
    logger.debug("list models count=%s dir=%s", len(rows), root)
    return rows


async def upload_model(
    db: Session,
    file: UploadFile,
    *,
    name: str | None = None,
    model_type: str | None = None,
    model_family: str | None = None,
    input_size: int = 640,
    class_count: int = 1,
    num_keypoints: int = 0,
    labels: Any = None,
    metadata: Any = None,
) -> dict[str, Any]:
    safe = safe_name(file.filename or "")
    root = model_dir()
    target = root / safe
    tmp = root / f".{safe}.tmp"
    with tmp.open("wb") as f:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
    tmp.replace(target)
    model = upsert_registry(
        db,
        file_name=safe,
        file_path=str(target),
        name=name,
        model_type=model_type,
        model_family=model_family,
        input_size=input_size,
        class_count=class_count,
        num_keypoints=num_keypoints,
        labels=labels,
        metadata=metadata,
    )
    logger.info("model uploaded name=%s path=%s", safe, target)
    return model_to_dict(model, file_exists=True)


def register_existing_model(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    file_name = safe_name(payload.get("file_name") or payload.get("name") or "")
    path = Path(payload.get("file_path") or model_dir() / file_name)
    if not path.exists():
        raise HTTPException(404, "model file not found")
    model = upsert_registry(
        db,
        file_name=file_name,
        file_path=str(path),
        name=payload.get("display_name") or payload.get("name"),
        model_type=payload.get("model_type"),
        model_family=payload.get("model_family"),
        input_size=payload.get("input_size") or 640,
        class_count=payload.get("class_count") or 1,
        num_keypoints=payload.get("num_keypoints") or 0,
        labels=payload.get("labels"),
        metadata=payload.get("metadata"),
    )
    return model_to_dict(model)


def update_metadata(db: Session, model_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    model = db.query(ModelRegistry).filter(ModelRegistry.id == model_id).first()
    if not model:
        raise HTTPException(404, "model registry not found")
    if "name" in payload:
        model.name = str(payload.get("name") or model.name)
    for key in ["model_type", "model_family"]:
        if key in payload and payload[key] is not None:
            setattr(model, key, str(payload[key]))
    for key in ["input_size", "class_count", "num_keypoints"]:
        if key in payload and payload[key] is not None:
            setattr(model, key, int(payload[key]))
    if "labels" in payload:
        model.labels = parse_labels(payload.get("labels"))
    if "metadata" in payload:
        model.metadata_json = parse_metadata(payload.get("metadata"))
    db.commit()
    db.refresh(model)
    logger.info("model metadata updated id=%s", model.id)
    return model_to_dict(model)


def delete_model(db: Session | None, name: str, *, delete_file: bool = True) -> dict[str, Any]:
    safe = safe_name(name)
    target = model_dir() / safe
    deleted_file = False
    if delete_file and target.exists():
        target.unlink()
        deleted_file = True
    row = None
    if db is not None:
        row = db.query(ModelRegistry).filter(ModelRegistry.file_name == safe).first()
        if row:
            db.delete(row)
            db.commit()
    if not deleted_file and row is None:
        raise HTTPException(404, "model not found")
    logger.info("model deleted name=%s deleted_file=%s", safe, deleted_file)
    return {"name": safe, "deleted_file": deleted_file}


def get_model_or_404(db: Session, model_id: int) -> ModelRegistry:
    row = db.query(ModelRegistry).filter(ModelRegistry.id == model_id).first()
    if not row:
        raise HTTPException(404, "model registry not found")
    return row


def detector_config_from_model(model: ModelRegistry, extra_config: dict | None = None) -> dict[str, Any]:
    config = {
        "model_path": model.file_path,
        "model_family": model.model_family,
        "input_size": model.input_size,
        "class_count": model.class_count,
    }
    if model.model_type == "yolo_pose":
        config["num_keypoints"] = model.num_keypoints
        config.setdefault("keypoint_conf_threshold", 0.25)
    if extra_config:
        config.update(extra_config)
    return config


def bind_model_to_camera(db: Session, *, camera_id: int, model_id: int, extra_config: dict | None = None) -> dict[str, Any]:
    model = get_model_or_404(db, model_id)
    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if not camera:
        raise HTTPException(404, "camera not found")
    camera.detector_type = model.model_type
    camera.detector_config = detector_config_from_model(model, extra_config)
    camera.config_version = (camera.config_version or 1) + 1
    db.commit()
    db.refresh(camera)
    logger.info("model bound camera_id=%s model_id=%s detector=%s config_version=%s", camera.id, model.id, camera.detector_type, camera.config_version)
    return {
        "camera_id": camera.id,
        "model": model_to_dict(model),
        "detector_type": camera.detector_type,
        "detector_config": camera.detector_config,
        "config_version": camera.config_version,
    }



async def test_model_image(
    db: Session,
    *,
    model_id: int,
    file: UploadFile,
    extra_config: Any = None,
) -> dict[str, Any]:
    """使用指定模型直接检测一张本地图片。

    这个接口不依赖摄像头配置，适合前端/现场人员上传样张测试模型是否能跑、
    bbox/关键点是否正确、ONNX Runtime provider 是否可用。
    """
    model = get_model_or_404(db, model_id)
    config = detector_config_from_model(model, parse_metadata(extra_config) if isinstance(extra_config, str) else extra_config)
    detector = create_detector(model.model_type, config)
    content = await file.read()
    if not content:
        raise HTTPException(400, "empty image file")
    frame = snapshot_service.decode_upload_image(content)
    if frame is None:
        raise HTTPException(400, "invalid image file")
    result = detector.detect(frame)
    annotated = draw_detection(
        frame,
        result,
        "DEBUG",
        rule_detail={"reason": "model single image test", "model_id": model.id, "model_name": model.name},
        camera_name="model-test",
        detector_type=model.model_type,
    )
    path = snapshot_service.save_image(annotated, camera_id=0, suffix=f"model_{model.id}_test")
    metadata = dict(result.metadata or {})
    keypoints = metadata.get("keypoints")
    return {
        "model": model_to_dict(model),
        "filename": file.filename,
        "image_size": {"width": int(frame.shape[1]), "height": int(frame.shape[0])},
        "detector_type": model.model_type,
        "detector_config": config,
        "annotated_image_path": path,
        "annotated_image_url": snapshot_service.storage_url(path),
        "result": {
            "target_found": result.target_found,
            "motion_distance": result.motion_distance,
            "confidence": result.confidence,
            "message": result.message,
            "center": result.center,
            "bbox": result.bbox,
            "keypoints_count": len(keypoints) if isinstance(keypoints, list) else 0,
            "metadata": metadata,
        },
    }
