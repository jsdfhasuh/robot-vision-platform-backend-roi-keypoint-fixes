from __future__ import annotations

import os
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.database import engine
from app.models.event import Event
from app.models.event_frame import EventFrame

logger = get_logger(__name__)


def _root() -> Path:
    path = Path(settings.storage_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def backups_dir() -> Path:
    path = _root() / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _dir_size(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    total = 0
    count = 0
    for p in path.rglob("*"):
        if p.is_file():
            count += 1
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return count, total


def _human_size(num: int) -> str:
    value = float(num)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if value < 1024 or unit == "TB":
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{num} B"


def _database_file() -> Path | None:
    url = str(engine.url)
    if not url.startswith("sqlite"):
        return None
    # sqlite:////abs/path/app.db or sqlite:///relative/path/app.db
    database = engine.url.database
    if not database or database == ":memory:":
        return None
    return Path(database).resolve()


def storage_stats() -> dict[str, Any]:
    root = _root()
    sections: dict[str, Any] = {}
    for name in ["snapshots", "clips", "logs", "backups"]:
        p = root / name
        count, size = _dir_size(p)
        sections[name] = {"path": str(p), "file_count": count, "size_bytes": size, "size_human": _human_size(size)}

    db_path = _database_file()
    db_size = db_path.stat().st_size if db_path and db_path.exists() else 0
    return {
        "storage_dir": str(root),
        "sections": sections,
        "database": {
            "type": "sqlite" if db_path else "external_or_unknown",
            "path": str(db_path) if db_path else None,
            "exists": bool(db_path and db_path.exists()),
            "size_bytes": db_size,
            "size_human": _human_size(db_size),
        },
        "total_size_bytes": sum(v["size_bytes"] for v in sections.values()) + db_size,
    }


def list_backups() -> list[dict[str, Any]]:
    rows = []
    for p in sorted(backups_dir().glob("*.db"), key=lambda x: x.stat().st_mtime, reverse=True):
        stat = p.stat()
        rows.append({
            "name": p.name,
            "path": str(p),
            "size_bytes": stat.st_size,
            "size_human": _human_size(stat.st_size),
            "created_at": datetime.fromtimestamp(stat.st_mtime),
            "url": None,
        })
    return rows


def backup_sqlite_database() -> dict[str, Any]:
    db_path = _database_file()
    if not db_path or not db_path.exists():
        raise HTTPException(400, "current database is not a local sqlite file")
    target = backups_dir() / f"app_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    # sqlite backup API 比直接 copy 更适合正在运行的数据库。
    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    logger.info("sqlite database backup created source=%s target=%s", db_path, target)
    return {
        "name": target.name,
        "path": str(target),
        "size_bytes": target.stat().st_size,
        "size_human": _human_size(target.stat().st_size),
        "created_at": datetime.fromtimestamp(target.stat().st_mtime),
    }


def _referenced_paths(db: Session) -> set[str]:
    refs: set[str] = set()
    for row in db.query(Event.snapshot_path, Event.annotated_snapshot_path, Event.recovery_snapshot_path, Event.recovery_annotated_path, Event.clip_path).all():
        for value in row:
            if value:
                refs.add(str(Path(value).resolve()))
    for row in db.query(EventFrame.image_path, EventFrame.annotated_image_path).all():
        for value in row:
            if value:
                refs.add(str(Path(value).resolve()))
    return refs


def cleanup_sample_frames(db: Session, *, days: int = 30, dry_run: bool = True) -> dict[str, Any]:
    """清理事件过程采样帧，保留 open/recover 关键帧。"""
    days = max(1, int(days))
    cutoff = datetime.utcnow() - timedelta(days=days)
    rows = (
        db.query(EventFrame)
        .filter(EventFrame.frame_time < cutoff, EventFrame.frame_type.notin_(["open", "recover"]))
        .all()
    )
    paths: list[str] = []
    for row in rows:
        if row.image_path:
            paths.append(row.image_path)
        if row.annotated_image_path:
            paths.append(row.annotated_image_path)
    deleted_files = 0
    freed_bytes = 0
    if not dry_run:
        for path in paths:
            p = Path(path)
            try:
                if p.exists() and p.is_file():
                    size = p.stat().st_size
                    p.unlink()
                    deleted_files += 1
                    freed_bytes += size
            except OSError:
                logger.warning("cleanup file failed path=%s", path, exc_info=True)
        for row in rows:
            db.delete(row)
        db.commit()
    return {
        "dry_run": dry_run,
        "cutoff": cutoff,
        "matched_frames": len(rows),
        "matched_files": len(paths),
        "deleted_files": deleted_files,
        "freed_bytes": freed_bytes,
        "freed_human": _human_size(freed_bytes),
    }


def cleanup_orphan_files(db: Session, *, days: int = 14, dry_run: bool = True) -> dict[str, Any]:
    """清理 snapshots/clips 中未被事件表引用的旧文件。"""
    days = max(1, int(days))
    cutoff_ts = (datetime.utcnow() - timedelta(days=days)).timestamp()
    refs = _referenced_paths(db)
    roots = [_root() / "snapshots", _root() / "clips"]
    candidates: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            try:
                if p.stat().st_mtime >= cutoff_ts:
                    continue
            except OSError:
                continue
            if str(p.resolve()) not in refs:
                candidates.append(p)
    freed_bytes = 0
    if not dry_run:
        for p in candidates:
            try:
                size = p.stat().st_size
                p.unlink()
                freed_bytes += size
            except OSError:
                logger.warning("cleanup orphan file failed path=%s", p, exc_info=True)
    return {
        "dry_run": dry_run,
        "cutoff_days": days,
        "matched_files": len(candidates),
        "deleted_files": 0 if dry_run else len(candidates),
        "freed_bytes": freed_bytes,
        "freed_human": _human_size(freed_bytes),
    }


def cleanup_old_backups(*, keep: int = 10, dry_run: bool = True) -> dict[str, Any]:
    keep = max(1, int(keep))
    backups = sorted(backups_dir().glob("*.db"), key=lambda x: x.stat().st_mtime, reverse=True)
    targets = backups[keep:]
    freed_bytes = 0
    if not dry_run:
        for p in targets:
            try:
                size = p.stat().st_size
                p.unlink()
                freed_bytes += size
            except OSError:
                logger.warning("cleanup backup failed path=%s", p, exc_info=True)
    return {
        "dry_run": dry_run,
        "keep": keep,
        "matched_backups": len(targets),
        "deleted_backups": 0 if dry_run else len(targets),
        "freed_bytes": freed_bytes,
        "freed_human": _human_size(freed_bytes),
    }


def run_cleanup(
    db: Session,
    *,
    sample_frame_days: int = 30,
    orphan_file_days: int = 14,
    backup_keep: int = 10,
    dry_run: bool = True,
) -> dict[str, Any]:
    return {
        "dry_run": dry_run,
        "sample_frames": cleanup_sample_frames(db, days=sample_frame_days, dry_run=dry_run),
        "orphan_files": cleanup_orphan_files(db, days=orphan_file_days, dry_run=dry_run),
        "old_backups": cleanup_old_backups(keep=backup_keep, dry_run=dry_run),
        "storage_after": storage_stats() if not dry_run else None,
    }
