from app.detectors.base import DetectResult
from app.tracker.track_buffer import TrackBuffer


def test_track_buffer_accumulates_center_displacement():
    buf = TrackBuffer(window_seconds=60, min_step_px=0)
    buf.add_result(DetectResult(target_found=True, motion_distance=0, center=(0, 0), confidence=1))
    stats = buf.add_result(DetectResult(target_found=True, motion_distance=0, center=(3, 4), confidence=1))
    assert stats.valid_points == 2
    assert stats.total_displacement == 5
    assert stats.net_displacement == 5


def test_track_buffer_ignores_jitter_under_threshold():
    buf = TrackBuffer(window_seconds=60, min_step_px=2)
    buf.add_result(DetectResult(target_found=True, motion_distance=0, center=(0, 0), confidence=1))
    stats = buf.add_result(DetectResult(target_found=True, motion_distance=0, center=(1, 1), confidence=1))
    assert stats.total_displacement == 0
