from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.responses import ok
from app.detectors.factory import list_detectors
from app.services import worker_service, system_service, maintenance_service
from app.database import get_db
from app.services.video_stream_service import frame_cache

router = APIRouter(prefix="/api/system", tags=["system"])
logger = get_logger(__name__)


class CleanupPayload(BaseModel):
    dry_run: bool = True
    sample_frame_days: int | None = None
    orphan_file_days: int | None = None
    backup_keep: int | None = None


@router.get("/health")
def health():
    logger.debug("health check")
    return ok(
        {
            "app_name": settings.app_name,
            "storage_dir": settings.storage_dir,
            "model_dir": settings.model_dir,
            "alert_enabled": settings.alert_enabled,
        },
        "healthy",
    )


@router.get("/workers")
def workers():
    data = worker_service.list_workers()
    logger.debug("list workers count=%s", len(data))
    return ok(data)


@router.get("/detectors")
def detectors():
    data = list_detectors()
    logger.debug("list detectors count=%s", len(data))
    return ok(data)


@router.get("/self-check")
def self_check():
    data = system_service.self_check()
    return ok(data, "self check finished")


@router.get("/streams")
def streams():
    """查看当前视频流缓存状态。"""
    return ok(frame_cache.info())


@router.get("/storage")
def storage_stats():
    """查看后端本地数据目录容量，用于内网长期运行维护。"""
    return ok(maintenance_service.storage_stats())


@router.get("/backups")
def backups():
    """列出 SQLite 数据库备份文件。"""
    return ok(maintenance_service.list_backups())


@router.post("/backup")
def backup_database():
    """创建 SQLite 数据库备份。PostgreSQL 等外部数据库应使用外部备份方案。"""
    return ok(maintenance_service.backup_sqlite_database(), "database backup created")


@router.post("/cleanup")
def cleanup_storage(payload: CleanupPayload | None = None, db: Session = Depends(get_db)):
    """执行数据清理。默认 dry_run=true，只预估不删除。"""
    payload = payload or CleanupPayload()
    data = maintenance_service.run_cleanup(
        db,
        sample_frame_days=payload.sample_frame_days or settings.cleanup_sample_frame_days,
        orphan_file_days=payload.orphan_file_days or settings.cleanup_orphan_file_days,
        backup_keep=payload.backup_keep or settings.backup_keep_count,
        dry_run=payload.dry_run,
    )
    return ok(data, "cleanup dry-run finished" if payload.dry_run else "cleanup finished")
