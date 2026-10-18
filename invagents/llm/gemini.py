"""Google Gemini provider (REST generateContent, structured JSON output)."""

from __future__ import annotations

from typing import Any

import httpx

from invagents.config import LLMConfig
from invagents.llm.base import LLMError, LLMResult

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, cfg: LLMConfig, client: httpx.Client | None = None) -> None:
        if not api_key:
            raise LLMError("GEMINI_API_KEY is not set in .env")
        self.api_key = api_key
        self.cfg = cfg
        self.model = cfg.model
        self.client = client or httpx.Client(timeout=cfg.timeout_seconds)

    def build_body(self, system: str, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        gen: dict[str, Any] = {
            "temperature": self.cfg.temperature,
            "maxOutputTokens": self.cfg.max_output_tokens,
            "responseMimeType": "application/json",
            "responseSchema": schema,
        }
        thinking: dict[str, Any] = {}
        if self.cfg.thinking_budget is not None:
            thinking["thinkingBudget"] = self.cfg.thinking_budget
        if self.cfg.thinking_level:
            thinking["thinkingLevel"] = self.cfg.thinking_level
        if thinking:
            gen["thinkingConfig"] = thinking
        return {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": gen,
        }

    def generate_json(self, system: str, prompt: str, schema: dict[str, Any]) -> LLMResult:
        url = f"{BASE_URL}/{self.model}:generateContent"
        resp = self.client.post(
            url,
            headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
            json=self.build_body(system, prompt, schema),
        )
        if resp.status_code != 200:
            raise LLMError(f"Gemini HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        block = (data.get("promptFeedback") or {}).get("blockReason")
        if block:
            raise LLMError(f"Gemini blocked the prompt: {block}")
        candidates = data.get("candidates") or []
        if not candidates:
            raise LLMError("Gemini returned no candidates")
        cand = candidates[0]
        parts = (cand.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        usage = data.get("usageMetadata") or {}
        return LLMResult(
            text=text,
            input_tokens=int(usage.get("promptTokenCount", 0)),
            output_tokens=int(usage.get("candidatesTokenCount", 0))
            + int(usage.get("thoughtsTokenCount", 0)),
            finish_reason=str(cand.get("finishReason", "")),
        )


def build_provider(cfg: LLMConfig, api_key: str) -> GeminiProvider:
    if cfg.provider != "gemini":
        raise LLMError(f"unsupported LLM provider: {cfg.provider}")
    return GeminiProvider(api_key, cfg)
