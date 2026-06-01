from pathlib import Path

from app.services.model_service import infer_default_type, safe_name


def test_safe_name_blocks_bad_extension():
    try:
        safe_name("bad.txt")
    except Exception as exc:
        assert "unsupported model extension" in str(exc)
    else:
        raise AssertionError("expected exception")


def test_infer_pose_model_type_from_name():
    model_type, family = infer_default_type("robot_pose.onnx")
    assert model_type == "yolo_pose"
    assert family == "yolo11_pose"
