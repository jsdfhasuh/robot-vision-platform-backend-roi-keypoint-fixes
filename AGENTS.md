# AGENTS.md

## Project Overview

This repository is the backend for a Robot Vision Platform. It is a FastAPI
service for industrial robot status monitoring over RTSP camera streams.

The backend manages cameras, reads frames from RTSP streams, applies ROI
cropping/masking, runs detector implementations, tracks motion over time,
classifies robot state, records events, saves event frames, and exposes REST,
MJPEG, static image, and WebSocket interfaces for a separate frontend.

Primary runtime stack:

- Python 3.11
- FastAPI / Uvicorn
- SQLAlchemy 2.x with SQLite by default
- Pydantic v2
- OpenCV headless
- ONNX Runtime for YOLO / pose models

The default database is under the backend code root at `data/db/app.db`.
Runtime files are stored under `data/`, model files under `models/`, and logs
under `data/logs/` relative to that backend code root. In the Docker backend
compose, the code root is mounted at `/app`, so these resolve to `/app/data`,
`/app/models`, and `/app/data/logs`.

## Repository Layout

- `backend/app/main.py` wires the FastAPI app, middleware, routers, exception
  handlers, static `/data` mount, database bootstrap, and status WebSocket
  broadcaster.
- `backend/app/api/` contains route handlers. Keep these thin: request parsing,
  dependency injection, and response wrapping belong here.
- `backend/app/services/` contains business logic for cameras, workers, events,
  snapshots, models, frontend adapters, maintenance, status, config, and video
  streaming.
- `backend/app/workers/` contains the RTSP/frame-reading worker orchestration.
  `CameraWorker` should stay focused on the processing loop and delegate logic
  to services, detectors, tracker, and rules.
- `backend/app/detectors/` contains detector implementations:
  `motion`, `aruco`, `yolo`, and `yolo_pose`.
- `backend/app/tracker/` contains trajectory and movement-statistics logic.
- `backend/app/rules/` contains status classification rules such as
  `RobotStopRule`.
- `backend/app/models/` contains SQLAlchemy models for cameras, status, events,
  event frames, and the local model registry.
- `backend/app/schemas/` contains Pydantic request/response schemas.
- `backend/tests/` contains focused tests for logic that does not require real
  RTSP cameras or ONNX inference.

## Important Concepts

- Cameras are video sources. Detection settings live on `detection_tasks`, not
  on the camera runtime path. A camera can have multiple tasks sharing one RTSP
  stream.
- Detection tasks include `config_version`. When task ROI, detector, model, or
  tracker settings change, increment task `config_version` so task runtimes reset
  detector/tracker/rule state and pick up the new runtime configuration.
- Robot stop detection is rule-based, not detector-specific. Detectors output a
  shared `DetectResult`; `SimpleTracker` turns recent motion into a movement
  score; `RobotStopRule` converts that score into `RUNNING`, `IDLE`,
  `STOPPED`, or `UNKNOWN`. `OFFLINE` is assigned by the worker when RTSP frame
  reads fail.
- Rules are shared entities under `/api/rules`. A rule declares supported
  detector types and can be bound to multiple detection tasks; editing a rule
  increments its version and affects all bound tasks.
- REST APIs generally return the shared shape from `app.core.responses`:
  `{"ok": true, "data": ..., "message": ""}` or the failure equivalent.
- Frontend compatibility routes also support a `code` style response and string
  camera IDs such as `cam_001`; internal database IDs remain numeric.
- Worker output is visible through `/api/system/workers` and
  `/api/detection-tasks/{id}/last-result`.
- Event review is centered on `events` and `event_frames`. Preserve open,
  recover, sampled, and manual frame data when changing event behavior.
- The project favors local/intranet diagnostics. External alert integrations are
  optional and disabled by default with `ALERT_ENABLED=false`.

## Common Commands

Use the configured Conda environment for local development. Prefer explicit
`conda run -n robot-vision ...` commands from automation so the system Python is
not used accidentally.

Install dependencies:

```bash
cd backend
conda activate robot-vision
python -m pip install -r requirements-dev.txt
```

Run locally:

```bash
cd backend
conda activate robot-vision
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Automation-friendly commands:

```bash
cd backend
conda run -n robot-vision python -m pip install -r requirements-dev.txt
conda run -n robot-vision python -m pytest -q
conda run -n robot-vision python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Run tests:

```bash
cd backend
conda activate robot-vision
python -m pytest -q
```

Docker:

```bash
docker compose -f docker-compose.backend.yml up -d --build
```

Use `docker-compose.backend.yml` for this backend-only repository snapshot.
`docker-compose.yml` references `frontend` and `mediamtx` services and should be
treated as a full-stack skeleton unless those directories are present.

## API Areas

Core backend routes include:

- Cameras: `/api/cameras`
- Detection tasks: `/api/detection-tasks`,
  `/api/detection-tasks/{id}/start`,
  `/api/detection-tasks/{id}/stop`,
  `/api/detection-tasks/{id}/last-result`
- Debug detection and snapshots: `/api/cameras/{id}/snapshot`,
  `/api/cameras/{id}/debug-detect`, `/api/cameras/{id}/image-detect`
- Video: `/api/cameras/{id}/frame.jpg`, `/api/cameras/{id}/stream.mjpg`
- Status: `/api/status`, `/api/status/{id}`, `/ws/status`
- Events: `/api/events`, `/api/events/summary`, `/api/events/{id}/frames`
- Models: `/api/models`, `/api/models/upload`, `/api/models/register`,
  `/api/models/bind-camera`
- Rules: `/api/rules`, `/api/rules/{id}`, `/api/rules/{id}/usage`
- Config: `/api/config/export`, `/api/config/import`
- System: `/api/system/health`, `/api/system/workers`,
  `/api/system/self-check`, `/api/system/storage`, `/api/system/backup`,
  `/api/system/cleanup`

Frontend compatibility routes include:

- `/api/runtime/status`
- `/stream/cameras/{camera_ref}/snapshot`
- `/stream/cameras/{camera_ref}/mjpeg`
- `/api/cameras/{camera_ref}/roi`
- `/api/settings`
- `/api/debug/keypoints`
- `/api/alarms`

## Development Guidelines

- Keep route modules thin and put reusable behavior in `services/`.
- Prefer extending existing detector, tracker, rule, and service boundaries over
  adding cross-layer shortcuts.
- When changing camera, ROI, model-binding, or imported configuration behavior,
  verify `config_version` handling.
- Avoid requiring real RTSP cameras in unit tests. Use focused tests for rule,
  tracker, model-service, ROI, and response behavior.
- Preserve existing API compatibility unless a change explicitly requires a
  frontend contract update.
- Do not commit runtime artifacts from `data/`, uploaded models, logs, backups,
  generated snapshots, or local virtual environments.
- Be careful with cleanup and backup code. Destructive cleanup should keep
  dry-run behavior by default.
- ONNX inference and real camera behavior are environment-dependent; document
  manual validation when automated tests cannot cover them.

## Verification Checklist

For backend logic changes, run:

```bash
cd backend
conda run -n robot-vision python -m pip install -r requirements-dev.txt
conda run -n robot-vision python -m pytest -q
```

If the shell is already inside the Conda environment:

```bash
cd backend
python -m pytest -q
```

For API or routing changes, also start the app and inspect:

```bash
cd backend
conda run -n robot-vision python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Then check:

- `http://127.0.0.1:8000/docs`
- `GET /api/system/health`
- `GET /api/system/self-check`

For video, detector, and worker changes, also validate against a real RTSP
camera or a controlled video stream when available.
