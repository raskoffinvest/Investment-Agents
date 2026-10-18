import json
from datetime import UTC, datetime, timedelta

import httpx

from invagents.agents.tank import Tank, fear_greed_alert, keyword_score
from invagents.data import fear_greed
from invagents.llm.base import LLMClient
from invagents.llm.gemini import GeminiProvider
from invagents.notify.telegram import Notifier
from tests.conftest import FakeNet, fear_greed_json, gemini_response, make_config

GOOD = {
    "Nasdaq": {
        "relevance": 9,
        "title_tr": "Nasdaq %4,5 düşüşle Haziran'dan beri en kötü haftasını geçirdi",
        "summary_tr": "Nasdaq Composite bu hafta %4,5 geriledi. "
        "CNN Korku ve Açgözlülük Endeksi bir hafta önceki 46'dan 18'e indi.",
    },
    "Fed": {
        "relevance": 8,
        "title_tr": "Fed'den Waller: Aralık'ta faiz indirimi masada",
        "summary_tr": "Fed Guvernörü Christopher Waller'a göre enflasyon gerilemeyi sürdürürse "
        "Aralık'ta faiz indirimi mümkün.",
    },
}


def answer(items, pick):
    out = []
    for it in items:
        key = "Nasdaq" if it["title"].startswith("Nasdaq") else "Fed"
        out.append({"id": it["id"], **pick(key, it)})
    return httpx.Response(200, json=gemini_response({"items": out}))


def build(net, storage, **tank_overrides):
    cfg = make_config(**tank_overrides)
    http = net.client()
    provider = GeminiProvider("k", cfg.llm, http)
    llm = LLMClient(provider, storage, cfg.llm, "tank", cfg.timezone)
    notifier = Notifier(cfg.telegram, storage, cfg.timezone, client=http)
    return Tank(cfg, storage, notifier, llm, http)


def news_messages(net):
    return [m["text"] for m in net.telegram if "📰" in m["text"]]


def test_happy_path_sends_relevant_news_and_fear_greed(storage, clock):
    net = FakeNet(clock.now)
    net.gemini = lambda items, body: answer(items, lambda k, it: GOOD[k])
    report = build(net, storage).run()

    msgs = news_messages(net)
    assert len(msgs) == 2 and report.sent == 2
    assert all(m.startswith("[Tank] 📰") for m in msgs)
    assert "46'dan 18'e indi." in msgs[0]
    assert "https://example.com/news/nasdaq-worst-week" in msgs[0]
    assert "Test Markets · 16.10 13:00" in msgs[0]
    assert not any("championship" in m or "bitcoin" in m.lower() for m in msgs)
    # sports item has no keyword and never reaches the LLM; old item is skipped
    prompt = net.gemini_calls[0]["contents"][0]["parts"][0]["text"]
    assert "championship" not in prompt and "Old story" not in prompt
    assert report.new == 3

    fg_msgs = [m["text"] for m in net.telegram if "📊" in m["text"]]
    assert len(fg_msgs) == 1
    assert "Endeksi: 18</b> (aşırı korku)" in fg_msgs[0]
    assert "1 haftada 28 puan düştü" in fg_msgs[0]
    assert "aşırı korku bölgesine girdi" in fg_msgs[0]
    assert "1 hafta önce 46" in fg_msgs[0]
    assert "CNN Fear &amp; Greed" in fg_msgs[0]  # valid Telegram HTML
    snap = storage.conn.execute("SELECT * FROM snapshots").fetchone()
    assert snap["source"] == "CNN Fear & Greed"


def test_second_run_dedupes_and_respects_cooldown(storage, clock):
    net = FakeNet(clock.now)
    net.gemini = lambda items, body: answer(items, lambda k, it: GOOD[k])
    build(net, storage).run()
    sent_before = len(net.telegram)
    clock.now += timedelta(minutes=30)
    report = build(net, storage).run()
    assert report.new == 0
    assert len(net.gemini_calls) == 1
    assert len(net.telegram) == sent_before
    assert "cooldown" in report.fear_greed


def test_violation_is_retried_once_with_violation_list(storage, clock):
    net = FakeNet(clock.now)

    def gemini(items, body):
        if len(net.gemini_calls) == 1:

            def bad(k, it):
                if k == "Nasdaq":
                    return {**GOOD[k], "summary_tr": "Nasdaq sert düştü ve endeks 46'dan"}
                return GOOD[k]

            return answer(items, bad)
        assert all("violations_to_fix" in it for it in items)
        assert len(items) == 1
        return answer(items, lambda k, it: GOOD[k])

    net.gemini = gemini
    report = build(net, storage).run()
    assert len(net.gemini_calls) == 2
    assert report.scored_llm == 2 and report.scored_template == 0
    retry_prompt = net.gemini_calls[1]["contents"][0]["parts"][0]["text"]
    assert "forbidden wording: sert" in retry_prompt
    assert "does not end with a complete sentence" in retry_prompt


def test_persistent_violation_falls_back_to_template(storage, clock):
    net = FakeNet(clock.now)
    net.gemini = lambda items, body: answer(
        items, lambda k, it: {**GOOD[k], "summary_tr": f"{k} 2008'den beri en kötü hafta."}
    )
    report = build(net, storage).run()
    assert len(net.gemini_calls) == 2
    assert report.scored_template == 2
    msgs = news_messages(net)
    nasdaq = next(m for m in msgs if "Nasdaq falls" in m)
    assert "LLM özeti yok" in nasdaq
    assert "The Nasdaq Composite fell 4.5% this week, its worst week since late June." in nasdaq
    assert "2008" not in "".join(msgs)


def test_truncated_llm_output_never_reaches_telegram(storage, clock):
    net = FakeNet(clock.now)
    truncated = gemini_response(None, finish="MAX_TOKENS", text='{"items": [{"id": "n1", "su')
    net.gemini = lambda items, body: httpx.Response(200, json=truncated)
    report = build(net, storage).run()
    assert "max_output_tokens" in report.llm_error
    for m in news_messages(net):
        body = m.split("\n")
        assert body[1].endswith(".")  # summary line is whole sentences
        assert "LLM özeti yok" in m


def test_low_relevance_is_not_sent(storage, clock):
    net = FakeNet(clock.now)
    net.gemini = lambda items, body: answer(items, lambda k, it: {**GOOD[k], "relevance": 3})
    report = build(net, storage).run()
    assert report.rejected == 2 and news_messages(net) == []


def test_daily_item_cap(storage, clock):
    net = FakeNet(clock.now)
    net.gemini = lambda items, body: answer(items, lambda k, it: GOOD[k])
    report = build(net, storage, max_items_per_day=1).run()
    assert report.sent == 1
    clock.now += timedelta(hours=1)
    report = build(net, storage, max_items_per_day=1).run()
    assert report.sent == 0 and report.not_sent_reason == "tank daily cap"
    clock.now += timedelta(days=1)  # next day: the held item is older than 24h and expires
    report = build(net, storage, max_items_per_day=1).run()
    assert report.sent == 0


def test_quiet_hours_hold_items_until_morning(storage, clock):
    clock.now = datetime(2026, 10, 16, 21, 0, tzinfo=UTC)  # 00:00 Istanbul
    net = FakeNet(clock.now)
    net.gemini = lambda items, body: answer(items, lambda k, it: GOOD[k])
    report = build(net, storage).run()
    assert report.sent == 0 and report.not_sent_reason == "quiet_hours"
    clock.now = datetime(2026, 10, 17, 5, 0, tzinfo=UTC)  # 08:00 Istanbul
    report = build(net, storage).run()
    assert report.sent == 2


def test_broken_feed_and_missing_fear_greed_do_not_crash(storage, clock):
    net = FakeNet(clock.now)
    net.feed_status = 500
    net.fear_greed = None
    report = build(net, storage).run()
    assert report.feed_errors and report.fear_greed == "veri yok"
    assert net.telegram == []


def test_stale_fear_greed_sends_nothing(storage, clock):
    net = FakeNet(clock.now)
    net.gemini = lambda items, body: answer(items, lambda k, it: GOOD[k])
    net.fear_greed = fear_greed_json(clock.now - timedelta(hours=30))
    report = build(net, storage).run()
    assert report.fear_greed.startswith("stale")
    assert not any("📊" in m["text"] for m in net.telegram)


def test_fear_greed_alert_conditions():
    cfg = make_config().tank.fear_greed
    now = datetime(2026, 10, 16, 12, tzinfo=UTC)

    def fg(score, prev, week):
        data = fear_greed_json(now, score=score, previous_close=prev, previous_1_week=week)
        return fear_greed.parse(data, now)

    assert fear_greed_alert(fg(40, 42, 45), cfg, "UTC") is None
    assert fear_greed_alert(fg(20, 22, 30), cfg, "UTC") is None  # already in extreme fear
    assert "aşırı korku bölgesine girdi" in fear_greed_alert(fg(20, 26, 30), cfg, "UTC")
    assert "1 haftada 25 puan yükseldi" in fear_greed_alert(fg(70, 68, 45), cfg, "UTC")
    assert "aşırı açgözlülük" in fear_greed_alert(fg(80, 74, 70), cfg, "UTC")


def test_keyword_score_is_whole_word_and_case_insensitive():
    kws = ("Fed", "S&P 500", "bitcoin")
    assert keyword_score("FED holds; S&P 500 flat; Bitcoin up", kws) == 3
    assert keyword_score("federal fedora", kws) == 0


def test_prompt_contains_rules_and_no_secrets(storage, clock):
    net = FakeNet(clock.now)
    net.gemini = lambda items, body: answer(items, lambda k, it: GOOD[k])
    build(net, storage).run()
    body = net.gemini_calls[0]
    system = body["systemInstruction"]["parts"][0]["text"]
    assert "Never stop mid-sentence" in system and "beklenebilir" in system
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert "123:abc" not in json.dumps(body)
