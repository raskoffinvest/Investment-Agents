"""Tank: news filter.

RSS -> dedupe -> keyword pre-filter -> LLM (relevance + Turkish title + short summary)
-> validator (retry once, then deterministic template) -> max N items/day to Telegram.
Also sends a deterministic CNN Fear & Greed alert on large moves (numbers from code only).
"""

from __future__ import annotations

import html
import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import httpx

from invagents.config import Config
from invagents.data import fear_greed, rss
from invagents.llm.base import LLMClient, LLMError
from invagents.notify.telegram import Notifier, SendStatus
from invagents.storage import Storage
from invagents.textutil import first_sentences, is_terminated, split_sentences, tr_lower
from invagents.timeutil import fmt_local, from_iso, local_day_start, to_iso, utcnow
from invagents.validator.rules import find_forbidden, unsupported_numbers

log = logging.getLogger(__name__)

AGENT = "tank"
TAG = "[Tank]"
KIND_NEWS = "news"
KIND_FEAR_GREED = "fear_greed"
MAX_TITLE_CHARS = 150
MAX_SUMMARY_SENTENCES = 2
TEMPLATE_MIN_KEYWORD_SCORE = 2

SYSTEM_PROMPT = """\
You translate and summarize financial news items into Turkish for a private news digest.
Return JSON matching the schema: exactly one entry per input item, with the same "id".

Fields:
- relevance: integer 0-10, how relevant the item is to these interests: {interests}.
  0 = unrelated (sports, lifestyle, ads, promotions); 10 = directly about these interests.
- title_tr: factual Turkish headline, at most {title_max} characters.
- summary_tr: ONE short Turkish paragraph of 1 or 2 COMPLETE sentences, at most {summary_max}
  characters, ending with a period. Only facts stated in the item.

Hard rules:
- Use only numbers that appear in the item's title or text. Do not add dates, years or numbers;
  do not convert currencies or units; do not round.
- No commentary, no predictions, no speculation, no buy/sell/hold advice, no price targets.
  If the item reports someone's forecast or opinion, attribute it ("X'e göre ...").
- Never use these words: beklenebilir, olabilir, işaret ediyor, sert, güçlü, endişe verici,
  muhtemelen, fırsat, tavsiye, öneri, korkutucu.
- Never stop mid-sentence. If space is short, write one shorter sentence.
"""

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "items": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "id": {"type": "STRING"},
                    "relevance": {"type": "INTEGER"},
                    "title_tr": {"type": "STRING"},
                    "summary_tr": {"type": "STRING"},
                },
                "required": ["id", "relevance", "title_tr", "summary_tr"],
            },
        }
    },
    "required": ["items"],
}


@dataclass
class TankReport:
    fetched: int = 0
    new: int = 0
    feed_errors: list[str] = field(default_factory=list)
    scored_llm: int = 0
    scored_template: int = 0
    rejected: int = 0
    sent: int = 0
    not_sent_reason: str = ""
    llm_error: str = ""
    fear_greed: str = ""

    def summary(self) -> str:
        parts = [
            f"fetched={self.fetched}",
            f"new={self.new}",
            f"llm={self.scored_llm}",
            f"template={self.scored_template}",
            f"rejected={self.rejected}",
            f"sent={self.sent}",
        ]
        if self.feed_errors:
            parts.append(f"feed_errors={len(self.feed_errors)}")
        if self.llm_error:
            parts.append(f"llm_error={self.llm_error[:120]}")
        if self.not_sent_reason:
            parts.append(f"stopped={self.not_sent_reason}")
        if self.fear_greed:
            parts.append(f"fear_greed={self.fear_greed}")
        return " ".join(parts)


def keyword_score(text: str, keywords: tuple[str, ...]) -> int:
    """Number of distinct keywords found (case-insensitive, whole word/phrase)."""
    low = tr_lower(text)
    score = 0
    for kw in keywords:
        if re.search(r"(?<!\w)" + re.escape(tr_lower(kw)) + r"(?!\w)", low):
            score += 1
    return score


def validate_item(out: dict[str, Any], title: str, description: str, summary_max: int) -> list[str]:
    """Output contract for one news item. Empty list = valid."""
    violations: list[str] = []
    relevance = out.get("relevance")
    if not isinstance(relevance, int) or not 0 <= relevance <= 10:
        violations.append("relevance must be an integer 0-10")
    title_tr = str(out.get("title_tr", "")).strip()
    summary = str(out.get("summary_tr", "")).strip()
    if not title_tr:
        violations.append("title_tr is empty")
    elif len(title_tr) > MAX_TITLE_CHARS:
        violations.append(f"title_tr longer than {MAX_TITLE_CHARS} chars")
    if not summary:
        violations.append("summary_tr is empty")
    else:
        if len(summary) > summary_max:
            violations.append(f"summary_tr longer than {summary_max} chars ({len(summary)})")
        if not is_terminated(summary):
            violations.append("summary_tr does not end with a complete sentence")
        if len(split_sentences(summary)) > MAX_SUMMARY_SENTENCES:
            violations.append(f"summary_tr has more than {MAX_SUMMARY_SENTENCES} sentences")
    text = f"{title_tr}\n{summary}"
    if "<" in text or ">" in text or "http" in text:
        violations.append("no HTML or links in title_tr/summary_tr")
    for word in find_forbidden(text):
        violations.append(f"forbidden wording: {word}")
    missing = unsupported_numbers(text, f"{title}\n{description}")
    if missing:
        violations.append(f"numbers not in the source item: {', '.join(missing)}")
    return violations


def template_summary(description: str, max_chars: int) -> str:
    """Deterministic fallback: whole leading sentences of the RSS text, never cut mid-sentence."""
    return first_sentences(description, max_chars, MAX_SUMMARY_SENTENCES)


def format_news(row: sqlite3.Row, tz: str) -> str:
    title = row["title_tr"] or row["title"]
    summary = row["summary_tr"] or ""
    when = fmt_local(from_iso(row["published_at"] or row["fetched_at"]), tz)
    meta = f"{row['feed']} · {when}"
    if row["summary_source"] == "template":
        meta += " · LLM özeti yok"
    lines = [f"{TAG} 📰 <b>{html.escape(title, quote=False)}</b>"]
    if summary:
        lines.append(html.escape(summary, quote=False))
    lines.append(f"<i>{html.escape(meta, quote=False)}</i>")
    lines.append(html.escape(row["link"], quote=False))
    return "\n".join(lines)


def _fmt_num(value: float) -> str:
    return f"{value:.0f}"


def fear_greed_alert(fg: fear_greed.FearGreed, cfg_fg: Any, tz: str) -> str | None:
    """Deterministic alert text, or None if no condition is met. All numbers come from `fg`."""
    reasons: list[str] = []
    if fg.previous_1_week is not None:
        change = fg.score - fg.previous_1_week
        if abs(change) >= cfg_fg.week_change_threshold:
            direction = "düştü" if change < 0 else "yükseldi"
            reasons.append(f"1 haftada {_fmt_num(abs(change))} puan {direction}")
    prev = fg.previous_close
    if fg.score < cfg_fg.extreme_fear_below and (prev is None or prev >= cfg_fg.extreme_fear_below):
        reasons.append("aşırı korku bölgesine girdi")
    if fg.score > cfg_fg.extreme_greed_above and (
        prev is None or prev <= cfg_fg.extreme_greed_above
    ):
        reasons.append("aşırı açgözlülük bölgesine girdi")
    if not reasons:
        return None
    lines = [
        f"{TAG} 📊 <b>ABD Korku ve Açgözlülük Endeksi: {_fmt_num(fg.score)}</b> ({fg.rating_tr})",
        "Endeks " + ", ".join(reasons) + ".",
    ]
    history = []
    if prev is not None:
        history.append(f"önceki kapanış {_fmt_num(prev)}")
    if fg.previous_1_week is not None:
        history.append(f"1 hafta önce {_fmt_num(fg.previous_1_week)}")
    if history:
        lines.append(" · ".join(history).capitalize())
    source = html.escape(f"Kaynak: {fg.source} · {fmt_local(fg.as_of, tz)}", quote=False)
    lines.append(f"<i>{source}</i>")
    return "\n".join(lines)


class Tank:
    def __init__(
        self,
        cfg: Config,
        storage: Storage,
        notifier: Notifier,
        llm: LLMClient | None,
        http: httpx.Client,
    ) -> None:
        self.cfg = cfg
        self.tcfg = cfg.tank
        self.storage = storage
        self.notifier = notifier
        self.llm = llm
        self.http = http

    # --- pipeline ---------------------------------------------------------
    def run(self) -> TankReport:
        report = TankReport()
        self.ingest(report)
        self.score(report)
        self.dispatch(report)
        if self.tcfg.fear_greed.enabled:
            try:
                self.check_fear_greed(report)
            except Exception as exc:  # fail-safe: never break the news part
                log.exception("fear & greed check failed")
                report.fear_greed = f"error: {exc}"
        return report

    def ingest(self, report: TankReport) -> None:
        now = utcnow()
        max_age = timedelta(hours=self.tcfg.max_item_age_hours)
        items: dict[str, rss.NewsItem] = {}
        for res in rss.fetch_all(self.http, self.tcfg.feeds):
            if res.error:
                report.feed_errors.append(f"{res.feed.name}: {res.error}")
            for it in res.items:
                report.fetched += 1
                if it.published_at and now - it.published_at > max_age:
                    continue
                items.setdefault(it.id, it)
        known = self.storage.known_news_ids(items)
        for it in items.values():
            if it.id in known:
                continue
            score = keyword_score(f"{it.title} {it.description}", self.tcfg.keywords)
            self.storage.insert_news(
                {
                    "id": it.id,
                    "feed": it.feed,
                    "title": it.title,
                    "link": it.link,
                    "description": it.description,
                    "published_at": to_iso(it.published_at) if it.published_at else None,
                    "fetched_at": to_iso(it.fetched_at),
                    "keyword_score": score,
                    "status": "new" if score > 0 else "ignored",
                }
            )
            report.new += 1

    def score(self, report: TankReport) -> None:
        pending = self.storage.news_by_status("new")[: self.tcfg.max_candidates_per_run]
        if not pending:
            return
        rows = {f"n{i}": row for i, row in enumerate(pending, 1)}
        outputs: dict[str, dict[str, Any]] = {}
        violations: dict[str, list[str]] = {}
        try:
            outputs = self._call_llm(rows)
            violations = self._validate(rows, outputs)
            failed = {k: v for k, v in violations.items() if v}
            if failed:
                log.info("tank: retrying %d items with violations %s", len(failed), failed)
                retry = self._call_llm(
                    {k: rows[k] for k in failed}, previous=outputs, violations=failed
                )
                outputs.update(retry)
                violations.update(self._validate({k: rows[k] for k in failed}, retry))
        except LLMError as exc:
            log.warning("tank: LLM unavailable, using template: %s", exc)
            report.llm_error = str(exc)
        for key, row in rows.items():
            out = outputs.get(key)
            errs = violations.get(key, ["no LLM output"])
            if out is not None and not errs:
                if out["relevance"] < self.tcfg.min_relevance:
                    self.storage.update_news(
                        row["id"], status="rejected", relevance=out["relevance"]
                    )
                    report.rejected += 1
                    continue
                self.storage.update_news(
                    row["id"],
                    status="scored",
                    relevance=out["relevance"],
                    title_tr=out["title_tr"].strip(),
                    summary_tr=out["summary_tr"].strip(),
                    summary_source="llm",
                )
                report.scored_llm += 1
                continue
            # Deterministic fallback: original headline + whole leading sentences of the feed text.
            if row["keyword_score"] < TEMPLATE_MIN_KEYWORD_SCORE:
                self.storage.update_news(
                    row["id"], status="rejected", violations=json.dumps(errs, ensure_ascii=False)
                )
                report.rejected += 1
                continue
            self.storage.update_news(
                row["id"],
                status="scored",
                summary_tr=template_summary(row["description"], self.tcfg.summary_max_chars),
                summary_source="template",
                violations=json.dumps(errs, ensure_ascii=False),
            )
            report.scored_template += 1

    def dispatch(self, report: TankReport) -> None:
        now = utcnow()
        sent_today = self.storage.count_messages_since(
            local_day_start(now, self.cfg.timezone), agent=AGENT, kind=KIND_NEWS
        )
        remaining = self.tcfg.max_items_per_day - sent_today
        if remaining <= 0:
            report.not_sent_reason = "tank daily cap"
            return
        max_age = timedelta(hours=self.tcfg.max_item_age_hours)
        candidates = [
            r
            for r in self.storage.news_by_status("scored")
            if now - from_iso(r["published_at"] or r["fetched_at"]) <= max_age
        ]
        candidates.sort(key=lambda r: (r["relevance"] or 0, r["keyword_score"]), reverse=True)
        for row in candidates[:remaining]:
            status = self.notifier.send(AGENT, KIND_NEWS, format_news(row, self.cfg.timezone))
            if status is SendStatus.SENT:
                self.storage.update_news(row["id"], status="sent", sent_at=to_iso(utcnow()))
                report.sent += 1
            elif status is SendStatus.DRY_RUN:
                report.sent += 1
            else:
                report.not_sent_reason = str(status)
                break

    def check_fear_greed(self, report: TankReport) -> None:
        fcfg = self.tcfg.fear_greed
        fg = fear_greed.fetch(self.http)
        if fg is None:
            report.fear_greed = "veri yok"
            return
        self.storage.save_snapshot(AGENT, KIND_FEAR_GREED, fg.source, fg.fetched_at, fg.raw)
        if fg.is_stale(fcfg.max_age_hours):
            report.fear_greed = f"stale (as_of {to_iso(fg.as_of)})"
            return
        text = fear_greed_alert(fg, fcfg, self.cfg.timezone)
        report.fear_greed = f"score={fg.score:.0f}"
        if text is None:
            return
        if self.storage.cooldown_active(KIND_FEAR_GREED, fcfg.cooldown_hours):
            report.fear_greed += " (cooldown)"
            return
        status = self.notifier.send(AGENT, KIND_FEAR_GREED, text)
        if status is SendStatus.SENT:
            self.storage.mark_fired(KIND_FEAR_GREED)
        report.fear_greed += f" alert={status}"

    # --- LLM helpers ------------------------------------------------------
    def _call_llm(
        self,
        rows: dict[str, sqlite3.Row],
        previous: dict[str, dict[str, Any]] | None = None,
        violations: dict[str, list[str]] | None = None,
    ) -> dict[str, dict[str, Any]]:
        if self.llm is None:
            raise LLMError("LLM is not configured")
        system = SYSTEM_PROMPT.format(
            interests=", ".join(self.tcfg.keywords) or "global markets",
            title_max=MAX_TITLE_CHARS,
            summary_max=self.tcfg.summary_max_chars,
        )
        items = []
        for key, row in rows.items():
            entry: dict[str, Any] = {
                "id": key,
                "source": row["feed"],
                "title": row["title"],
                "text": row["description"],
            }
            if previous and key in previous:
                entry["your_previous_output"] = previous[key]
                entry["violations_to_fix"] = (violations or {}).get(key, [])
            items.append(entry)
        intro = (
            "Fix the listed violations and return corrected entries.\n"
            if previous
            else "Summarize these news items.\n"
        )
        prompt = intro + json.dumps({"items": items}, ensure_ascii=False)
        data = self.llm.generate_json(system, prompt, RESPONSE_SCHEMA)
        result: dict[str, dict[str, Any]] = {}
        for out in data.get("items", []) if isinstance(data, dict) else []:
            if isinstance(out, dict) and out.get("id") in rows:
                result[out["id"]] = out
        return result

    def _validate(
        self, rows: dict[str, sqlite3.Row], outputs: dict[str, dict[str, Any]]
    ) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for key, row in rows.items():
            out = outputs.get(key)
            if out is None:
                result[key] = ["missing from model output"]
            else:
                result[key] = validate_item(
                    out, row["title"], row["description"], self.tcfg.summary_max_chars
                )
        return result


def run(cfg: Config, storage: Storage, notifier: Notifier, llm: LLMClient | None) -> TankReport:
    """Entry point with run bookkeeping. Never raises."""
    run_id = storage.start_run(AGENT)
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as http:
            report = Tank(cfg, storage, notifier, llm, http).run()
        storage.finish_run(run_id, "ok", report.summary())
        log.info("tank: %s", report.summary())
        return report
    except Exception as exc:
        log.exception("tank run failed")
        storage.finish_run(run_id, "error", repr(exc))
        return TankReport(not_sent_reason=f"error: {exc}")


__all__ = ["Tank", "TankReport", "run", "validate_item", "keyword_score", "fear_greed_alert"]
