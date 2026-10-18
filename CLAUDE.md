# Investment Agents — hard constraints

Self-hosted Python agents that collect market data and news and report to the owner on Telegram.
Talk to the owner in Turkish; code, comments and docs are in English.

## Hard constraints (never violate)

1. **Numbers come from code, not from the LLM.** Prices, % changes and levels are fetched by
   deterministic fetchers. The LLM only summarizes/translates/formats text it is given and must not
   invent numbers. Every number in LLM output must appear in the LLM input (enforced by the validator).
2. **Every data point has `source` + `fetched_at`.** Data older than its configured max age, or
   missing, is shown as "veri yok". Never present yesterday's value as today's.
3. **The output contract is enforced in code**, not only in the prompt (`invagents/validator`).
   On violation: retry once with the violation list; if it still fails, use a deterministic
   template built without the LLM. Output is never silently skipped.
4. **No attack surface.** No web panel, no inbound ports, outbound calls only. Telegram messages go
   only to allowlisted chat IDs. Secrets live only in `.env` (gitignored). No exchange API keys:
   public market data only.
5. **Dead-man's switch** for the morning brief; `/status` shows last runs and errors.
6. **Notification fatigue limits:** daily caps, per-alert cooldowns, quiet hours.
7. **Cost control:** LLM provider/model configurable; daily spend cap; every prompt, response and
   cost is logged to SQLite.
8. **Accuracy audit:** raw data snapshots are stored; a weekly audit re-checks sent numbers.
9. **No financial advice.** No buy/sell/hold language, no predictions, no price targets.
   The validator rejects these.
10. **Fail-safe everywhere.** A failing fetcher yields "veri yok"; one broken agent never stops the
    others.
11. **The agents NEVER trade** and never hold keys with trade or withdraw permission.

## Agents

Every Telegram message starts with the sending agent's tag, e.g. `[Tank]`.

- `ajan-smith` ("Ajan Smith"): morning brief, 08:00 Europe/Istanbul (main agent).
- `neo` ("Neo"): deterministic price/threshold alerts on the watchlist.
- `tank` ("Tank"): news filter. RSS -> dedupe -> keyword + LLM relevance -> max 5 items/day,
  each "headline + short factual summary + link". Summaries are complete sentences (never cut
  mid-sentence). No speculation.
- `trinity` ("Trinity"): weekly summary + accuracy audit.

## Development

- Python 3.11+, config in YAML (`config.yaml`, copied from `config.example.yaml`).
- `ruff check .` and `pytest` must pass before every commit.
- Tests never touch the live network: use recorded fixtures and `httpx.MockTransport`.
