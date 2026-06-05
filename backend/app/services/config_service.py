from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.camera import Camera
from app.models.status import CameraStatus
from app.models.model_registry import ModelRegistry
from app.services import model_service

logger = get_logger(__name__)

CAMERA_FIELDS = [
    "name", "rtsp_url", "location", "enabled", "fps_limit", "roi",
    "detector_type", "detector_config", "motion_threshold", "stop_seconds",
]


def export_config(db: Session) -> dict:
    cameras = []
    for camera in db.query(Camera).order_by(Camera.id.asc()).all():
        item = {field: getattr(camera, field) for field in CAMERA_FIELDS}
        item["id"] = camera.id
        item["config_version"] = getattr(camera, "config_version", 1) or 1
        item["created_at"] = camera.created_at.isoformat() if camera.created_at else None
        item["updated_at"] = camera.updated_at.isoformat() if camera.updated_at else None
        cameras.append(item)

    models = []
    for model in db.query(ModelRegistry).order_by(ModelRegistry.id.asc()).all():
        item = model_service.model_to_dict(model)
        # 导出配置时不强依赖本机绝对路径，保留 file_name 方便迁移后重新注册。
        models.append(item)

    data = {
        "version": 2,
        "exported_at": datetime.utcnow().isoformat(),
        "cameras": cameras,
        "models": models,
    }
    logger.info("config exported cameras=%s models=%s", len(cameras), len(models))
    return data


def import_config(db: Session, payload: dict) -> dict:
    cameras = payload.get("cameras") if isinstance(payload, dict) else None
    if not isinstance(cameras, list):
        raise HTTPException(400, "payload.cameras must be a list")

    mode = payload.get("mode", "upsert_by_name")
    imported = 0
    updated = 0
    created_ids = []

    # 模型元数据先导入；如果模型文件不存在，会跳过，不影响摄像头导入。
    imported_models = 0
    skipped_models = 0
    for item in payload.get("models") or []:
        if not isinstance(item, dict):
            continue
        try:
            model_service.register_existing_model(db, {
                "file_name": item.get("file_name") or item.get("name"),
                "file_path": item.get("file_path") or item.get("path"),
                "display_name": item.get("name"),
                "model_type": item.get("model_type"),
                "model_family": item.get("model_family"),
                "input_size": item.get("input_size") or 640,
                "class_count": item.get("class_count") or 1,
                "num_keypoints": item.get("num_keypoints") or 0,
                "labels": item.get("labels"),
                "metadata": item.get("metadata"),
            })
            imported_models += 1
        except Exception as exc:
            skipped_models += 1
            logger.warning("model metadata import skipped item=%s error=%s", item.get("file_name") or item.get("name"), exc)

    for item in cameras:
        if not isinstance(item, dict):
            continue
        data = {field: item.get(field) for field in CAMERA_FIELDS if field in item}
        if not data.get("name") or not data.get("rtsp_url"):
            continue

        row = None
        if mode == "upsert_by_id" and item.get("id"):
            row = db.query(Camera).filter(Camera.id == int(item["id"])).first()
        if row is None and mode in {"upsert_by_name", "upsert_by_id"}:
            row = db.query(Camera).filter(Camera.name == data["name"]).first()

        if row:
            changed = False
            for key, value in data.items():
                if getattr(row, key) != value:
                    setattr(row, key, value)
                    changed = True
            if changed:
                row.config_version = (row.config_version or 1) + 1
            updated += 1
        else:
            row = Camera(**data)
            db.add(row)
            db.flush()
            db.add(CameraStatus(camera_id=row.id, status="UNKNOWN", message="imported"))
            created_ids.append(row.id)
        imported += 1

    db.commit()
    from app.services import detection_task_service

    for camera_id in created_ids:
        row = db.query(Camera).filter(Camera.id == camera_id).first()
        if row:
            detection_task_service.ensure_default_task_for_camera(db, row)
    logger.info(
        "config imported cameras=%s updated=%s created=%s models=%s skipped_models=%s",
        imported,
        updated,
        created_ids,
        imported_models,
        skipped_models,
    )
    return {
        "imported": imported,
        "updated": updated,
        "created_ids": created_ids,
        "imported_models": imported_models,
        "skipped_models": skipped_models,
    }
