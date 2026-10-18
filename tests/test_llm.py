import httpx
import pytest

from invagents.config import LLMConfig
from invagents.llm.base import BudgetExceeded, LLMClient, LLMError, TruncatedResponse
from invagents.llm.gemini import GeminiProvider
from tests.conftest import gemini_response

SCHEMA = {"type": "OBJECT", "properties": {"x": {"type": "INTEGER"}}}


def make_client(storage, handler, **cfg):
    llm_cfg = LLMConfig(model="gemini-test", **cfg)
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return LLMClient(GeminiProvider("secret", llm_cfg, http), storage, llm_cfg, "tank", "UTC")


def test_request_shape_and_logging(storage, clock):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["key"] = request.headers["x-goog-api-key"]
        seen["body"] = request.read()
        return httpx.Response(200, json=gemini_response({"x": 1}))

    client = make_client(storage, handler, thinking_budget=0)
    assert client.generate_json("sys", "hello", SCHEMA) == {"x": 1}
    assert seen["url"].endswith("/models/gemini-test:generateContent")
    assert "secret" not in seen["url"]  # key goes in a header, never in the URL
    assert seen["key"] == "secret"
    assert b'"thinkingBudget": 0' in seen["body"] or b'"thinkingBudget":0' in seen["body"]
    row = storage.conn.execute("SELECT * FROM llm_calls").fetchone()
    assert row["input_tokens"] == 1000 and row["output_tokens"] == 200
    assert row["cost_usd"] == pytest.approx((1000 * 0.30 + 200 * 2.50) / 1e6)
    assert row["error"] is None


def test_thinking_tokens_count_as_output(storage, clock):
    resp = gemini_response({"x": 1})
    resp["usageMetadata"]["thoughtsTokenCount"] = 300
    resp["candidates"][0]["content"]["parts"].insert(0, {"text": "thinking...", "thought": True})
    client = make_client(storage, lambda r: httpx.Response(200, json=resp))
    assert client.generate_json("s", "p", SCHEMA) == {"x": 1}
    assert storage.conn.execute("SELECT output_tokens FROM llm_calls").fetchone()[0] == 500


def test_max_tokens_is_reported_as_truncated(storage, clock):
    resp = gemini_response(None, finish="MAX_TOKENS", text='{"items": [{"summary_tr": "Endeks')
    client = make_client(storage, lambda r: httpx.Response(200, json=resp))
    with pytest.raises(TruncatedResponse):
        client.generate_json("s", "p", SCHEMA)
    assert "MAX_TOKENS" in storage.conn.execute("SELECT error FROM llm_calls").fetchone()[0]


def test_http_error_and_invalid_json(storage, clock):
    client = make_client(storage, lambda r: httpx.Response(429, text="quota"))
    with pytest.raises(LLMError, match="429"):
        client.generate_json("s", "p", SCHEMA)
    bad = gemini_response(None, text="not json")
    client = make_client(storage, lambda r: httpx.Response(200, json=bad))
    with pytest.raises(LLMError, match="invalid JSON"):
        client.generate_json("s", "p", SCHEMA)


def test_daily_spend_cap_blocks_calls(storage, clock):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json=gemini_response({"x": 1}))

    client = make_client(storage, handler, daily_spend_cap_usd=0.0005)
    client.generate_json("s", "p", SCHEMA)  # costs 0.0008
    with pytest.raises(BudgetExceeded):
        client.generate_json("s", "p", SCHEMA)
    assert len(calls) == 1


def test_missing_api_key():
    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        GeminiProvider("", LLMConfig())
