"""LLM interface, errors, and the budget/logging wrapper every agent uses."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from invagents.config import LLMConfig
from invagents.storage import Storage
from invagents.timeutil import local_day_start, utcnow

log = logging.getLogger(__name__)


class LLMError(Exception):
    """Any LLM failure. Callers fall back to a deterministic template."""


class TruncatedResponse(LLMError):
    """The model stopped because it hit the output token limit (text is incomplete)."""


class BudgetExceeded(LLMError):
    """The daily spend cap is reached; no call was made."""


@dataclass(frozen=True)
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    finish_reason: str


class Provider(Protocol):
    name: str
    model: str

    def generate_json(self, system: str, prompt: str, schema: dict[str, Any]) -> LLMResult: ...


class LLMClient:
    """Wraps a provider: enforces the daily spend cap and logs every call to SQLite."""

    def __init__(
        self, provider: Provider, storage: Storage, cfg: LLMConfig, agent: str, tz: str
    ) -> None:
        self.provider = provider
        self.storage = storage
        self.cfg = cfg
        self.agent = agent
        self.tz = tz

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens * self.cfg.price_per_mtok_input
            + output_tokens * self.cfg.price_per_mtok_output
        ) / 1_000_000

    def spent_today(self) -> float:
        return self.storage.llm_spend_since(local_day_start(utcnow(), self.tz))

    def generate_json(self, system: str, prompt: str, schema: dict[str, Any]) -> Any:
        spent = self.spent_today()
        if spent >= self.cfg.daily_spend_cap_usd:
            raise BudgetExceeded(
                f"daily LLM cap reached: ${spent:.4f} >= ${self.cfg.daily_spend_cap_usd:.2f}"
            )
        row: dict[str, Any] = {
            "agent": self.agent,
            "provider": self.provider.name,
            "model": self.provider.model,
            "prompt": f"[system]\n{system}\n\n[user]\n{prompt}",
        }
        try:
            result = self.provider.generate_json(system, prompt, schema)
        except LLMError as exc:
            self.storage.log_llm_call(**row, error=str(exc))
            raise
        except Exception as exc:  # network etc.
            self.storage.log_llm_call(**row, error=repr(exc))
            raise LLMError(str(exc)) from exc
        row.update(
            response=result.text,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=self.cost(result.input_tokens, result.output_tokens),
            finish_reason=result.finish_reason,
        )
        if result.finish_reason == "MAX_TOKENS":
            self.storage.log_llm_call(**row, error="truncated (MAX_TOKENS)")
            raise TruncatedResponse("model output hit max_output_tokens")
        try:
            data = json.loads(result.text)
        except json.JSONDecodeError as exc:
            self.storage.log_llm_call(**row, error=f"invalid JSON: {exc}")
            raise LLMError(f"invalid JSON from model: {exc}") from exc
        self.storage.log_llm_call(**row)
        return data
