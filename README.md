# Investment Agents

Self-hosted agents that collect market data and news and send short, factual messages to your
Telegram. They **never trade** and **never give investment advice**. See [CLAUDE.md](CLAUDE.md) for
the hard design rules.

Status: **Tank** (news filter) is implemented. Ajan Smith, Neo and Trinity come next.

## Tank — news filter

Every run:

1. Fetches the RSS feeds in `config.yaml` (a broken feed is logged and skipped).
2. Dedupes items (by normalized title) and drops items older than `max_item_age_hours`.
3. Keyword pre-filter against your tickers/topics. Items with no keyword never reach the LLM.
4. One Gemini call per run: relevance score 0-10, Turkish headline and a 1-2 sentence summary.
5. The validator checks every item in code: complete sentences (no mid-sentence cut-off), length,
   forbidden forecast/advice wording, and that every number exists in the source item. Violations
   are retried once with the violation list; if they still fail, a deterministic template is sent
   (original headline + whole leading sentences of the feed text, marked "LLM özeti yok").
6. Sends at most `max_items_per_day` (default 5) items, never during quiet hours, within the global
   daily message cap.
7. Checks the CNN Fear & Greed index (numbers from CNN's data, not the LLM) and sends an alert on a
   large weekly move or when entering extreme fear/greed, at most once per 24h.

Message format:

```
[Tank] 📰 <Turkish headline>
<1-2 sentence factual summary>
<feed> · <published time, Europe/Istanbul>
<link>
```

### Why summaries used to get cut off (and how this avoids it)

Gemini 2.5+ models "think" before answering and those thinking tokens count against the output
limit, so a small `max_output_tokens` can end the answer mid-sentence. Tank sets a generous limit,
disables thinking for 2.5 models (`thinking_budget: 0`), treats `finishReason == MAX_TOKENS` as a
failure, rejects any summary that does not end with sentence punctuation, and never truncates text
by character count.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp config.example.yaml config.yaml   # add your tickers to tank.keywords
cp .env.example .env                 # fill in tokens (never commit .env)
```

- **Gemini key:** create one at <https://aistudio.google.com/apikey> and put it in `.env` as
  `GEMINI_API_KEY`. Check the model id and prices in `config.yaml` against Google's current
  pricing page; the spend cap uses those prices.
- **Telegram:** create a bot with @BotFather, send it a message, then get your chat id from
  `https://api.telegram.org/bot<TOKEN>/getUpdates`. Put both in `.env`. Only chat ids in
  `TELEGRAM_CHAT_IDS` ever receive messages.

Without a Telegram token the agents run in dry-run mode and print messages to the console.

## Run

```bash
.venv/bin/python -m invagents tank --dry-run   # print instead of sending
.venv/bin/python -m invagents tank             # real run
```

Schedule it every 30 minutes (cron example; run `crontab -e`):

```
*/30 * * * * cd /path/to/Investment-Agents && mkdir -p logs && .venv/bin/python -m invagents tank >> logs/tank.log 2>&1
```

## Data stored (SQLite, `data/invagents.sqlite`)

`news_items` (every fetched item and what happened to it), `llm_calls` (every prompt, response,
token count and cost), `messages` (everything sent), `snapshots` (raw Fear & Greed data), `runs`.

## Development

```bash
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/pytest
```

Tests use recorded fixtures and `httpx.MockTransport`; they never touch the network.
