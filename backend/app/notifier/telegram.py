from __future__ import annotations

import json
import urllib.parse
import urllib.request

from app.core.logging import get_logger
from app.notifier.base import AlertMessage, BaseNotifier

logger = get_logger(__name__)


class TelegramNotifier(BaseNotifier):
    name = "telegram"

    def __init__(self, bot_token: str, chat_id: str, timeout: float = 5.0):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout = timeout

    def send(self, msg: AlertMessage) -> bool:
        if not self.bot_token or not self.chat_id:
            return False
        url = f"https://api.telegram.org/bot{urllib.parse.quote(self.bot_token)}/sendMessage"
        payload = json.dumps({"chat_id": self.chat_id, "text": msg.to_text()}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                ok = 200 <= resp.status < 300
                logger.info("telegram notification sent ok=%s status=%s event=%s", ok, resp.status, msg.event_id)
                return ok
        except Exception:
            logger.exception("telegram notification failed event=%s", msg.event_id)
            return False
