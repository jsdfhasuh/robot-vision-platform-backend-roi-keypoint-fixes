from __future__ import annotations

import os
from app.core.config import settings


def storage_path_to_url(path: str | None) -> str | None:
    """把 ./data 或 /app/data 下的文件路径转换成前端可访问的 /data URL。"""
    if not path:
        return None
    norm = os.path.normpath(path)
    storage = os.path.normpath(settings.storage_dir)
    try:
        rel = os.path.relpath(norm, storage)
        if not rel.startswith(".."):
            return "/data/" + rel.replace(os.sep, "/")
    except Exception:
        pass
    # 兼容容器绝对路径中包含 data 的情况。
    parts = norm.replace("\\", "/").split("/")
    if "data" in parts:
        idx = len(parts) - 1 - list(reversed(parts)).index("data")
        return "/data/" + "/".join(parts[idx + 1 :])
    return None
