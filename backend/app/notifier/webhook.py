from __future__ import annotations

import json
import urllib.request

from app.core.logging import get_logger
from app.notifier.base import AlertMessage, BaseNotifier

logger = get_logger(__name__)


class WebhookNotifier(BaseNotifier):
    name = "webhook"

    def __init__(self, url: str, timeout: float = 5.0):
        self.url = url
        self.timeout = timeout

    def send(self, msg: AlertMessage) -> bool:
        if not self.url:
            return False
        payload = json.dumps(msg.to_dict(), ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                ok = 200 <= resp.status < 300
                logger.info("webhook notification sent ok=%s status=%s event=%s", ok, resp.status, msg.event_id)
                return ok
        except Exception:
            logger.exception("webhook notification failed url=%s event=%s", self.url, msg.event_id)
            return False
