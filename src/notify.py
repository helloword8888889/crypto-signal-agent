"""Notifications: console + log file + optional Telegram.

Telegram is enabled only when both TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
exist in the environment (see .env.example). Otherwise alerts print to
stdout and append to the log file.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import requests


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def send(message: str, log_file: str | None = None) -> None:
    line = f"[{_ts()}] {message}"
    print(line, flush=True)
    if log_file:
        os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat_id:
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message},
                timeout=10,
            )
        except Exception as e:
            print(f"[notify] Telegram send failed: {e}", flush=True)
