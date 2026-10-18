"""YAML config + .env secrets loading."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Feed:
    name: str
    url: str


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "gemini"
    model: str = "gemini-2.5-flash"
    thinking_budget: int | None = 0
    thinking_level: str | None = None
    max_output_tokens: int = 4096
    temperature: float = 0.2
    timeout_seconds: float = 60.0
    daily_spend_cap_usd: float = 0.50
    price_per_mtok_input: float = 0.30
    price_per_mtok_output: float = 2.50


@dataclass(frozen=True)
class FearGreedConfig:
    enabled: bool = True
    week_change_threshold: float = 20
    extreme_fear_below: float = 25
    extreme_greed_above: float = 75
    max_age_hours: float = 26
    cooldown_hours: float = 24


@dataclass(frozen=True)
class TankConfig:
    enabled: bool = True
    max_items_per_day: int = 5
    min_relevance: int = 6
    max_item_age_hours: float = 24
    max_candidates_per_run: int = 15
    summary_max_chars: int = 320
    keywords: tuple[str, ...] = ()
    feeds: tuple[Feed, ...] = ()
    fear_greed: FearGreedConfig = field(default_factory=FearGreedConfig)


@dataclass(frozen=True)
class TelegramConfig:
    max_messages_per_day: int = 15
    quiet_start: str = "23:00"
    quiet_end: str = "07:30"
    bot_token: str = ""
    chat_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class Secrets:
    gemini_api_key: str = ""


@dataclass(frozen=True)
class Config:
    timezone: str = "Europe/Istanbul"
    database: str = "data/invagents.sqlite"
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    tank: TankConfig = field(default_factory=TankConfig)
    secrets: Secrets = field(default_factory=Secrets)


def _flatten_keywords(raw: Any) -> tuple[str, ...]:
    if isinstance(raw, dict):
        return tuple(str(k) for group in raw.values() for k in (group or []))
    return tuple(str(k) for k in (raw or []))


def _parse_chat_ids(raw: str) -> tuple[int, ...]:
    return tuple(int(part) for part in raw.replace(" ", "").split(",") if part)


def from_dict(data: dict[str, Any], env: dict[str, str] | None = None) -> Config:
    env = dict(os.environ) if env is None else env
    tg = data.get("telegram") or {}
    quiet = tg.get("quiet_hours") or {}
    llm = data.get("llm") or {}
    tank = data.get("tank") or {}
    fg = tank.get("fear_greed") or {}
    llm_fields = set(LLMConfig.__dataclass_fields__)
    fg_fields = set(FearGreedConfig.__dataclass_fields__)
    return Config(
        timezone=data.get("timezone", "Europe/Istanbul"),
        database=data.get("database", "data/invagents.sqlite"),
        telegram=TelegramConfig(
            max_messages_per_day=int(tg.get("max_messages_per_day", 15)),
            quiet_start=str(quiet.get("start", "23:00")),
            quiet_end=str(quiet.get("end", "07:30")),
            bot_token=env.get("TELEGRAM_BOT_TOKEN", ""),
            chat_ids=_parse_chat_ids(env.get("TELEGRAM_CHAT_IDS", "")),
        ),
        llm=LLMConfig(**{k: v for k, v in llm.items() if k in llm_fields}),
        tank=TankConfig(
            enabled=bool(tank.get("enabled", True)),
            max_items_per_day=int(tank.get("max_items_per_day", 5)),
            min_relevance=int(tank.get("min_relevance", 6)),
            max_item_age_hours=float(tank.get("max_item_age_hours", 24)),
            max_candidates_per_run=int(tank.get("max_candidates_per_run", 15)),
            summary_max_chars=int(tank.get("summary_max_chars", 320)),
            keywords=_flatten_keywords(tank.get("keywords")),
            feeds=tuple(Feed(name=f["name"], url=f["url"]) for f in tank.get("feeds") or []),
            fear_greed=FearGreedConfig(**{k: v for k, v in fg.items() if k in fg_fields}),
        ),
        secrets=Secrets(gemini_api_key=env.get("GEMINI_API_KEY", "")),
    )


def load(path: str | Path = "config.yaml", env_file: str | Path = ".env") -> Config:
    from dotenv import dotenv_values

    env = {**{k: v for k, v in dotenv_values(env_file).items() if v is not None}, **os.environ}
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return from_dict(data, env)
