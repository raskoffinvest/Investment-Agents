"""CLI: python -m invagents <agent> [--dry-run] [--config config.yaml]"""

from __future__ import annotations

import argparse
import logging
import sys

from invagents import config as config_mod
from invagents.agents import tank
from invagents.llm.base import LLMClient, LLMError
from invagents.llm.gemini import build_provider
from invagents.notify.telegram import Notifier
from invagents.storage import Storage


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs full request URLs at INFO; the Telegram URL contains the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="invagents")
    parser.add_argument("agent", choices=["tank"])
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--env", default=".env")
    parser.add_argument(
        "--dry-run", action="store_true", help="print messages instead of sending them"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    cfg = config_mod.load(args.config, args.env)
    storage = Storage(cfg.database)
    notifier = Notifier(cfg.telegram, storage, cfg.timezone, dry_run=args.dry_run)
    if notifier.dry_run and not args.dry_run:
        logging.warning("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_IDS missing: running in dry-run mode")
    try:
        llm: LLMClient | None = LLMClient(
            build_provider(cfg.llm, cfg.secrets.gemini_api_key),
            storage,
            cfg.llm,
            agent=args.agent,
            tz=cfg.timezone,
        )
    except LLMError as exc:
        logging.warning("LLM disabled (%s); deterministic templates will be used", exc)
        llm = None

    if args.agent == "tank":
        report = tank.run(cfg, storage, notifier, llm)
        print(report.summary())
        return 1 if report.not_sent_reason.startswith("error") else 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
