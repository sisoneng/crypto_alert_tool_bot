"""CLI: python -m sec_sentinel (--once | --loop [--interval N]) [--dry-run]"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from contextlib import ExitStack
from pathlib import Path

from .config import load_config
from .http import SecClient
from .loop import MIN_INTERVAL_SECONDS, run_loop
from .runner import run_once
from .telegram import StdoutSender, TelegramSender

STATE_PATH = Path("state/seen.json")
TICKERS_CACHE_PATH = Path(".cache/company_tickers.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sec_sentinel")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="run one poll cycle, then exit")
    mode.add_argument("--loop", action="store_true", help="poll forever (VPS mode)")
    parser.add_argument("--interval", type=int, default=60, help="seconds between cycles in --loop mode")
    parser.add_argument("--dry-run", action="store_true", help="print alerts to stdout instead of sending")
    parser.add_argument("--config", default="config.yaml", help="path to config.yaml")
    args = parser.parse_args(argv)
    if args.interval < MIN_INTERVAL_SECONDS:
        parser.error(f"--interval must be at least {MIN_INTERVAL_SECONDS} seconds")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its INFO lines would log the bot token URL
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    user_agent = os.environ.get("SEC_USER_AGENT", "").strip()
    if not user_agent:
        print('error: SEC_USER_AGENT is required, e.g. "sec-sentinel you@example.com"', file=sys.stderr)
        return 2
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not args.dry_run and not (token and chat_id):
        print("error: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required (or use --dry-run)", file=sys.stderr)
        return 2

    config = load_config(args.config)
    with ExitStack() as stack:
        sec = stack.enter_context(SecClient(user_agent))
        sender = stack.enter_context(StdoutSender() if args.dry_run else TelegramSender(token, chat_id))

        def cycle():
            return run_once(
                config, sec=sec, sender=sender, state_path=STATE_PATH, tickers_cache_path=TICKERS_CACHE_PATH
            )

        if args.once:
            return 1 if cycle().failed_forms else 0

        stop = threading.Event()
        # SIGTERM: systemd/docker stop; SIGBREAK: Ctrl+Break on Windows.
        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), lambda *_: stop.set())
        logging.getLogger(__name__).info("loop mode: every %ds (Ctrl+C to stop)", args.interval)
        run_loop(cycle, interval=args.interval, stop=stop, notify=sender.send)
    return 0


if __name__ == "__main__":
    sys.exit(main())
