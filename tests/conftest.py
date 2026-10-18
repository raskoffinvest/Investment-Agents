"""Shared test helpers. Tests never touch the live network."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from invagents import config as config_mod
from invagents.storage import Storage

FIXTURES = Path(__file__).parent / "fixtures"
# Friday 16 Oct 2026, 15:00 Istanbul: outside quiet hours.
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)

TIME_MODULES = [
    "invagents.storage",
    "invagents.notify.telegram",
    "invagents.agents.tank",
    "invagents.llm.base",
    "invagents.data.rss",
    "invagents.data.fear_greed",
]


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    c = Clock(NOW)
    for mod in TIME_MODULES:
        monkeypatch.setattr(f"{mod}.utcnow", c)
    monkeypatch.setattr("invagents.data.http.time.sleep", lambda _s: None)
    return c


@pytest.fixture
def storage() -> Storage:
    s = Storage(":memory:")
    yield s
    s.close()


def make_config(**tank_overrides: Any) -> config_mod.Config:
    data = {
        "timezone": "Europe/Istanbul",
        "telegram": {"max_messages_per_day": 15, "quiet_hours": {"start": "23:00", "end": "07:30"}},
        "llm": {"model": "gemini-test", "daily_spend_cap_usd": 0.5},
        "tank": {
            "max_items_per_day": 5,
            "min_relevance": 6,
            "keywords": {
                "tickers": ["Nasdaq", "S&P 500"],
                "macro": ["Fed", "rate cut", "inflation"],
                "crypto": ["bitcoin"],
                "sentiment": ["Fear & Greed"],
            },
            "feeds": [{"name": "Test Markets", "url": "https://feeds.test/markets.xml"}],
            **tank_overrides,
        },
    }
    env = {"TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_CHAT_IDS": "111", "GEMINI_API_KEY": "k"}
    return config_mod.from_dict(data, env)


def feed_xml(now: datetime) -> bytes:
    text = (FIXTURES / "feed_markets.xml").read_text()
    text = text.replace("{RECENT}", format_datetime(now - timedelta(hours=2)))
    text = text.replace("{OLD}", format_datetime(now - timedelta(days=6)))
    return text.encode()


def fear_greed_json(as_of: datetime, **overrides: Any) -> dict[str, Any]:
    data = json.loads((FIXTURES / "fear_greed.json").read_text())
    data["fear_and_greed"]["timestamp"] = as_of.isoformat()
    data["fear_and_greed"].update(overrides)
    return data


def gemini_response(payload: Any, finish: str = "STOP", text: str | None = None) -> dict:
    return {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [{"text": text if text is not None else json.dumps(payload)}],
                },
                "finishReason": finish,
            }
        ],
        "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 200},
    }


GeminiFn = Callable[[list[dict[str, Any]], dict[str, Any]], httpx.Response]


class FakeNet:
    """Routes httpx requests to fakes and records what was sent."""

    def __init__(self, now: datetime) -> None:
        self.now = now
        self.feed_status = 200
        self.fear_greed: dict[str, Any] | None = fear_greed_json(now - timedelta(hours=1))
        self.gemini: GeminiFn | None = None
        self.gemini_calls: list[dict[str, Any]] = []
        self.telegram: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "feeds.test":
            if self.feed_status != 200:
                return httpx.Response(self.feed_status)
            return httpx.Response(200, content=feed_xml(self.now))
        if host == "production.dataviz.cnn.io":
            if self.fear_greed is None:
                return httpx.Response(503)
            return httpx.Response(200, json=self.fear_greed)
        if host == "generativelanguage.googleapis.com":
            body = json.loads(request.content)
            self.gemini_calls.append(body)
            prompt = body["contents"][0]["parts"][0]["text"]
            items = json.loads(prompt[prompt.index("{") :])["items"]
            assert self.gemini is not None
            return self.gemini(items, body)
        if host == "api.telegram.org":
            self.telegram.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True})
        raise AssertionError(f"unexpected request to {request.url}")

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))
