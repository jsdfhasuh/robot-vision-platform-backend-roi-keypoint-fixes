from __future__ import annotations

import threading
from datetime import datetime

from app.core.logging import get_logger
from app.workers.stream_worker import CameraWorker

logger = get_logger(__name__)


class CameraManager:
    def __init__(self):
        self.workers: dict[int, CameraWorker] = {}
        self.threads: dict[int, threading.Thread] = {}
        self._lock = threading.RLock()

    def start(self, camera_id: int):
        with self._lock:
            if camera_id in self.threads and self.threads[camera_id].is_alive():
                logger.warning("worker already running camera_id=%s", camera_id)
                return False, "worker already running"
            worker = CameraWorker(camera_id)
            thread = threading.Thread(target=worker.run, daemon=True, name=f"camera-worker-{camera_id}")
            self.workers[camera_id] = worker
            self.threads[camera_id] = thread
            thread.start()
            logger.info("worker started camera_id=%s", camera_id)
            return True, "worker started"

    def stop(self, camera_id: int):
        with self._lock:
            worker = self.workers.get(camera_id)
            if not worker:
                logger.warning("stop requested but worker not running camera_id=%s", camera_id)
                return False, "worker not running"
            worker.stop()
            logger.info("worker stopping camera_id=%s", camera_id)
            return True, "worker stopping"

    def list_workers(self):
        with self._lock:
            rows = []
            for cid, thread in self.threads.items():
                worker = self.workers.get(cid)
                state = worker.get_debug_state() if worker else None
                rows.append(
                    {
                        "camera_id": cid,
                        "alive": thread.is_alive(),
                        "thread_name": thread.name,
                        "checked_at": datetime.utcnow(),
                        "health": state,
                    }
                )
            return rows

    def get_debug_state(self, camera_id: int):
        with self._lock:
            worker = self.workers.get(camera_id)
            if not worker:
                return None
            return worker.get_debug_state()


camera_manager = CameraManager()
