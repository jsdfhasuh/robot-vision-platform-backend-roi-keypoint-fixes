from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response, StreamingResponse

from app.core.responses import ok
from app.services.video_stream_service import frame_cache, get_latest_jpeg, mjpeg_generator
from app.services.frontend_adapter_service import parse_camera_ref

router = APIRouter(prefix="/api/cameras", tags=["video"])


@router.get("/{camera_id}/stream-info")
def stream_info(camera_id: str):
    """返回前端可用的视频流地址和当前缓存状态。"""
    cid = parse_camera_ref(camera_id)
    return ok(
        {
            "camera_id": cid,
            "raw_mjpeg_url": f"/api/cameras/{cid}/stream.mjpg?annotated=false",
            "annotated_mjpeg_url": f"/api/cameras/{cid}/stream.mjpg?annotated=true",
            "raw_frame_url": f"/api/cameras/{cid}/frame.jpg?annotated=false",
            "annotated_frame_url": f"/api/cameras/{cid}/frame.jpg?annotated=true",
            "cache": frame_cache.info(cid),
        }
    )


@router.get("/{camera_id}/frame.jpg")
def latest_frame_jpg(
    camera_id: str,
    annotated: bool = Query(False, description="true 返回带检测框/关键点/规则解释的标注图"),
    quality: int = Query(85, ge=10, le=100),
    max_width: int | None = Query(None, ge=160, le=4096),
):
    """返回最新一帧 JPEG。

    适合前端在详情页按需刷新，或用于 ROI 画框前获取当前画面。
    """
    cid = parse_camera_ref(camera_id)
    jpg, ts, is_placeholder = get_latest_jpeg(
        cid,
        annotated=annotated,
        quality=quality,
        max_width=max_width,
        placeholder=True,
    )
    if not jpg:
        raise HTTPException(404, "no frame available")
    headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "X-Frame-Placeholder": "1" if is_placeholder else "0",
    }
    if ts:
        headers["X-Frame-Time"] = ts.isoformat()
    return Response(content=jpg, media_type="image/jpeg", headers=headers)


@router.get("/{camera_id}/stream.mjpg")
def camera_mjpeg_stream(
    camera_id: str,
    annotated: bool = Query(False, description="true 返回标注视频流"),
    fps: float = Query(8.0, ge=0.2, le=25.0),
    quality: int = Query(80, ge=10, le=100),
    max_width: int | None = Query(None, ge=160, le=4096),
):
    """MJPEG 视频流。

    前端最简单接入方式：
    <img src="/api/cameras/1/stream.mjpg?annotated=true" />
    """
    return StreamingResponse(
        mjpeg_generator(
            parse_camera_ref(camera_id),
            annotated=annotated,
            fps=fps,
            quality=quality,
            max_width=max_width,
        ),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )
