"""Shared Telegram command polling for the gold-macro-dashboard.

Why this module exists
----------------------
Three processes used to poll Telegram ``getUpdates`` independently
(jev_paper_engine.py, telegram_engine.py and live_telegram_listener.py),
each with its own offset, so a command could be answered twice or be
consumed by one poller and missed by another. All command handling now
goes through this module, which keeps ONE shared offset in
``telegram_offset.json`` so an update is consumed exactly once, no matter
which process picks it up.

Security
--------
Every update is checked against the configured ``TELEGRAM_CHAT_ID``.
Updates from any other chat are ignored (the offset is still advanced so
they are not re-processed). If no chat id is configured, NO command is
accepted — fail closed.
"""

import json
import logging
import os

import requests

logger = logging.getLogger(__name__)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OFFSET_FILE = os.path.join(SCRIPT_DIR, "telegram_offset.json")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()


def sender_is_authorized(message: dict, chat_id: str | None = None) -> bool:
    """True only if the message came from the configured Telegram chat.

    ``message`` is one Telegram ``message`` object from getUpdates.
    An empty / missing configured chat id authorizes nobody.
    The chat id is resolved at call time (not as a default argument),
    so tests and config changes that set TELEGRAM_CHAT_ID after import
    still take effect.
    """
    if chat_id is None:
        chat_id = TELEGRAM_CHAT_ID
    if not chat_id:
        return False
    sender_chat_id = str(message.get("chat", {}).get("id", ""))
    return sender_chat_id == str(chat_id)


def load_offset() -> int:
    try:
        with open(OFFSET_FILE, "r") as f:
            return int(json.load(f).get("last_update_id", 0))
    except Exception:
        return 0


def save_offset(update_id: int) -> None:
    try:
        with open(OFFSET_FILE, "w") as f:
            json.dump({"last_update_id": int(update_id)}, f, indent=2)
    except Exception as exc:
        logger.warning("Could not save Telegram offset: %s", exc)


def fetch_new_updates(token: str = TELEGRAM_BOT_TOKEN) -> list:
    """Fetch unprocessed Telegram updates, advancing the shared offset.

    Returns a list of ``(update, authorized)`` tuples. Unauthorized
    updates are included with ``authorized=False`` so callers can log
    (not answer) them; the offset is advanced either way.
    """
    if not token:
        return []
    last_id = load_offset()
    try:
        res = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params={"offset": last_id + 1, "timeout": 3},
            timeout=6,
        )
        if res.status_code != 200:
            logger.warning("Telegram getUpdates failed: HTTP %s", res.status_code)
            return []
        updates = res.json().get("result", [])
    except Exception as exc:
        logger.warning("Telegram getUpdates error: %s", exc)
        return []

    result = []
    max_id = last_id
    for update in updates:
        update_id = update.get("update_id", 0)
        max_id = max(max_id, update_id)
        message = update.get("message", {})
        result.append((update, sender_is_authorized(message)))
    if max_id > last_id:
        save_offset(max_id)
    return result


def log_unauthorized(update: dict) -> None:
    """Log (never answer) a command from an unauthorized sender."""
    message = update.get("message", {})
    sender = message.get("chat", {}).get("id", "unknown")
    text = (message.get("text") or "").strip()
    logger.warning(
        "Ignored Telegram command from unauthorized chat %s: %r", sender, text
    )
