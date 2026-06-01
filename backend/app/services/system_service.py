from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.core.config import settings
from app.database import engine


def _path_status(path: str, *, create: bool = False) -> dict[str, Any]:
    p = Path(path)
    if create:
        p.mkdir(parents=True, exist_ok=True)
    exists = p.exists()
    is_dir = p.is_dir()
    writable = False
    if exists and is_dir:
        try:
            test = p / ".write_test"
            test.write_text("ok", encoding="utf-8")
            test.unlink(missing_ok=True)
            writable = True
        except Exception:
            writable = False
    usage = shutil.disk_usage(str(p if exists else p.parent if p.parent.exists() else "."))
    return {
        "path": str(p),
        "exists": exists,
        "is_dir": is_dir,
        "writable": writable,
        "disk_total_bytes": usage.total,
        "disk_free_bytes": usage.free,
    }


def self_check() -> dict[str, Any]:
    checks: dict[str, Any] = {}
    checks["storage_dir"] = _path_status(settings.storage_dir, create=True)
    checks["model_dir"] = _path_status(settings.model_dir, create=True)
    checks["log_dir"] = _path_status(settings.log_dir, create=True)

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = {"ok": True, "url": str(engine.url)}
    except Exception as exc:
        checks["database"] = {"ok": False, "url": str(engine.url), "error": str(exc)}

    try:
        import cv2  # type: ignore
        checks["opencv"] = {"ok": True, "version": getattr(cv2, "__version__", "unknown")}
    except Exception as exc:
        checks["opencv"] = {"ok": False, "error": str(exc)}

    try:
        import onnxruntime as ort  # type: ignore
        checks["onnxruntime"] = {"ok": True, "providers": ort.get_available_providers()}
    except Exception as exc:
        checks["onnxruntime"] = {"ok": False, "error": str(exc)}

    overall = all(
        [
            checks["storage_dir"].get("writable"),
            checks["model_dir"].get("writable"),
            checks["log_dir"].get("writable"),
            checks["database"].get("ok"),
            checks["opencv"].get("ok"),
        ]
    )
    return {"ok": bool(overall), "checks": checks}
