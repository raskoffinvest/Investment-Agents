"""Telegram sender: allowlisted chats only, global daily cap, quiet hours. Outbound only."""

from __future__ import annotations

import logging
from enum import StrEnum

import httpx

from invagents.config import TelegramConfig
from invagents.storage import Storage
from invagents.timeutil import in_quiet_hours, local_day_start, utcnow

log = logging.getLogger(__name__)

API = "https://api.telegram.org"
MAX_LEN = 4096


class SendStatus(StrEnum):
    SENT = "sent"
    DRY_RUN = "dry_run"
    QUIET_HOURS = "quiet_hours"
    DAILY_CAP = "daily_cap"
    ERROR = "error"


class Notifier:
    def __init__(
        self,
        cfg: TelegramConfig,
        storage: Storage,
        tz: str,
        *,
        dry_run: bool = False,
        client: httpx.Client | None = None,
    ) -> None:
        self.cfg = cfg
        self.storage = storage
        self.tz = tz
        self.dry_run = dry_run or not (cfg.bot_token and cfg.chat_ids)
        self.client = client or httpx.Client(timeout=20)

    def can_send(self, *, urgent: bool = False) -> SendStatus | None:
        """Returns a blocking status, or None if sending is allowed now."""
        now = utcnow()
        if not urgent and in_quiet_hours(now, self.tz, self.cfg.quiet_start, self.cfg.quiet_end):
            return SendStatus.QUIET_HOURS
        sent_today = self.storage.count_messages_since(local_day_start(now, self.tz))
        if sent_today >= self.cfg.max_messages_per_day:
            return SendStatus.DAILY_CAP
        return None

    def send(self, agent: str, kind: str, text: str, *, urgent: bool = False) -> SendStatus:
        """Send `text` (Telegram HTML) to every allowlisted chat. `urgent` bypasses quiet hours."""
        blocked = self.can_send(urgent=urgent)
        if blocked:
            log.info("[%s] %s not sent: %s", agent, kind, blocked)
            return blocked
        text = text[:MAX_LEN]
        if self.dry_run:
            print(f"--- DRY RUN ({agent}/{kind}) ---\n{text}\n")
            return SendStatus.DRY_RUN
        errors: list[str] = []
        for chat_id in self.cfg.chat_ids:
            try:
                resp = self.client.post(
                    f"{API}/bot{self.cfg.bot_token}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                )
                if resp.status_code != 200:
                    errors.append(f"chat {chat_id}: HTTP {resp.status_code} {resp.text[:200]}")
            except httpx.HTTPError as exc:
                # Never log the URL: it contains the bot token.
                errors.append(f"chat {chat_id}: {type(exc).__name__}")
        ok = not errors
        self.storage.log_message(agent, kind, text, ok, "; ".join(errors))
        if not ok:
            log.error("[%s] telegram send failed: %s", agent, errors)
        return SendStatus.SENT if ok else SendStatus.ERROR
