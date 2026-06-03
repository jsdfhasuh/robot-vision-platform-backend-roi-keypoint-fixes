from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.rule_api import camera_rule_router, template_router
from app.database import Base, get_db
from app.models.camera import Camera


def _client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    app = FastAPI()
    app.include_router(camera_rule_router)
    app.include_router(template_router)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    def seed(*cameras: Camera):
        db = TestingSessionLocal()
        try:
            for camera in cameras:
                db.add(camera)
            db.commit()
        finally:
            db.close()

    def fetch_camera(camera_id: int) -> Camera:
        db = TestingSessionLocal()
        try:
            return db.query(Camera).filter(Camera.id == camera_id).first()
        finally:
            db.close()

    return TestClient(app), seed, fetch_camera


def _camera(camera_id: int, *, config_version: int = 1, detector_config: dict | None = None) -> Camera:
    return Camera(
        id=camera_id,
        name=f"camera_{camera_id}",
        rtsp_url=f"rtsp://example/{camera_id}",
        detector_type="yolo_pose",
        detector_config=detector_config or {},
        motion_threshold=5.0,
        stop_seconds=30,
        config_version=config_version,
    )


def _rule_payload(movement_score: str = "keypoint_mean_step") -> dict:
    return {
        "rule": {
            "motion_threshold": 4,
            "stop_seconds": 45,
            "unknown_seconds": 8,
            "confirm_frames": 3,
            "status_hold_seconds": 2.0,
        },
        "tracker": {
            "movement_score": movement_score,
            "window_seconds": 40,
            "min_step_px": 2.5,
        },
    }


def test_get_and_put_camera_rule_preserves_detector_config():
    client, seed, fetch_camera = _client()
    seed(_camera(1, detector_config={"model_path": "./models/a.onnx", "roi_config": {"rois": []}, "frontend_meta": {"area": "A"}}))

    before = client.get("/api/cameras/cam_001/rule")
    assert before.status_code == 200
    assert before.json()["data"]["rule"]["motion_threshold"] == 5.0

    response = client.put("/api/cameras/1/rule", json=_rule_payload())
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["rule"]["motion_threshold"] == 4.0
    assert data["rule"]["stop_seconds"] == 45
    assert data["tracker"]["movement_score"] == "keypoint_mean_step"
    assert data["config_version"] == 2

    camera = fetch_camera(1)
    assert camera.motion_threshold == 4.0
    assert camera.stop_seconds == 45
    assert camera.detector_config["model_path"] == "./models/a.onnx"
    assert camera.detector_config["roi_config"] == {"rois": []}
    assert camera.detector_config["frontend_meta"] == {"area": "A"}
    assert camera.detector_config["rule"]["confirm_frames"] == 3
    assert camera.detector_config["tracker"]["window_seconds"] == 40


def test_copy_camera_rule_to_multiple_targets():
    client, seed, fetch_camera = _client()
    seed(
        _camera(1, config_version=5),
        _camera(2, detector_config={"model_path": "./models/target.onnx"}),
        _camera(3),
    )
    assert client.put("/api/cameras/1/rule", json=_rule_payload("avg_speed")).status_code == 200
    source_before_copy = fetch_camera(1).config_version

    response = client.post("/api/cameras/cam_001/rule/copy", json={"target_camera_ids": ["cam_002", 3]})
    assert response.status_code == 200
    data = response.json()["data"]
    assert [x["numeric_camera_id"] for x in data["targets"]] == [2, 3]

    source = fetch_camera(1)
    target2 = fetch_camera(2)
    target3 = fetch_camera(3)
    assert source.config_version == source_before_copy
    assert target2.detector_config["model_path"] == "./models/target.onnx"
    assert target2.detector_config["tracker"]["movement_score"] == "avg_speed"
    assert target3.detector_config["rule"]["unknown_seconds"] == 8
    assert target2.config_version == 2
    assert target3.config_version == 2


def test_rule_templates_crud_and_apply():
    client, seed, fetch_camera = _client()
    seed(_camera(1), _camera(2))

    create_payload = {
        "name": "pose stop rule",
        "description": "for yolo pose",
        "detector_type": "yolo_pose",
        **_rule_payload("keypoint_max_step"),
    }
    created = client.post("/api/rule-templates", json=create_payload)
    assert created.status_code == 200
    template_id = created.json()["data"]["id"]

    listed = client.get("/api/rule-templates")
    assert listed.status_code == 200
    assert listed.json()["data"][0]["id"] == template_id

    detail = client.get(f"/api/rule-templates/{template_id}")
    assert detail.status_code == 200
    assert detail.json()["data"]["tracker"]["movement_score"] == "keypoint_max_step"

    update_payload = {
        "name": "updated rule",
        "description": "",
        "detector_type": "motion",
        **_rule_payload("raw"),
    }
    updated = client.put(f"/api/rule-templates/{template_id}", json=update_payload)
    assert updated.status_code == 200
    assert updated.json()["data"]["name"] == "updated rule"

    applied = client.post(f"/api/rule-templates/{template_id}/apply", json={"camera_ids": ["cam_001", "cam_002"]})
    assert applied.status_code == 200
    assert [x["numeric_camera_id"] for x in applied.json()["data"]["applied"]] == [1, 2]
    assert fetch_camera(1).detector_config["tracker"]["movement_score"] == "raw"
    assert fetch_camera(2).config_version == 2

    deleted = client.delete(f"/api/rule-templates/{template_id}")
    assert deleted.status_code == 200
    assert client.get(f"/api/rule-templates/{template_id}").status_code == 404


def test_invalid_movement_score_rejected():
    client, seed, _ = _client()
    seed(_camera(1))

    response = client.put("/api/cameras/1/rule", json=_rule_payload("bad_score"))
    assert response.status_code == 422
