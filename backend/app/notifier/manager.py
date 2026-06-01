from __future__ import annotations

from datetime import datetime, timedelta

from app.core.config import settings
from app.core.logging import get_logger
from app.notifier.base import AlertMessage
from app.notifier.telegram import TelegramNotifier
from app.notifier.webhook import WebhookNotifier

logger = get_logger(__name__)


class NotifierManager:
    def __init__(self):
        self.last_sent: dict[str, datetime] = {}

    def _enabled_notifiers(self):
        notifiers = []
        if settings.webhook_url:
            notifiers.append(WebhookNotifier(settings.webhook_url))
        if settings.telegram_bot_token and settings.telegram_chat_id:
            notifiers.append(TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id))
        return notifiers

    def _cooldown_key(self, msg: AlertMessage) -> str:
        return f"{msg.camera_id}:{msg.event_type}:{msg.action}"

    def _allow_send(self, msg: AlertMessage) -> bool:
        if msg.action == "RECOVERED" and not settings.notify_recovery:
            return False
        key = self._cooldown_key(msg)
        now = datetime.utcnow()
        last = self.last_sent.get(key)
        if last and now - last < timedelta(seconds=settings.alert_cooldown_seconds):
            logger.info("notification skipped by cooldown key=%s", key)
            return False
        self.last_sent[key] = now
        return True

    def send(self, msg: AlertMessage) -> dict:
        if not settings.alert_enabled:
            logger.debug("notification disabled event=%s action=%s", msg.event_id, msg.action)
            return {"enabled": False, "sent": 0, "results": []}
        if not self._allow_send(msg):
            return {"enabled": True, "sent": 0, "results": [], "skipped": "cooldown_or_recovery_disabled"}
        results = []
        for notifier in self._enabled_notifiers():
            ok = notifier.send(msg)
            results.append({"name": notifier.name, "ok": ok})
        logger.info("notification result event=%s action=%s results=%s", msg.event_id, msg.action, results)
        return {"enabled": True, "sent": sum(1 for x in results if x.get("ok")), "results": results}


notifier_manager = NotifierManager()
