"""Telegram alerter — sends trade signals via Bot API."""
from __future__ import annotations

import logging
import os

import httpx

from dolev_ai.models import Signal

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

_SIDE_EMOJI = {"buy": "🟢", "sell": "🔴"}


def _format_message(sig: Signal) -> str:
    emoji = _SIDE_EMOJI.get(sig.side, "⚪")
    size_pct = round(sig.suggested_size_pct * 100, 1)
    conviction_pct = round(sig.conviction * 100)
    drivers = "\n".join(f"  • {url}" for url in sig.key_drivers[:5])
    return (
        f"{emoji} *{sig.side.upper()}  ${sig.ticker}*\n"
        f"Conviction: {conviction_pct}%  |  Size hint: {size_pct}%\n\n"
        f"{sig.rationale}\n\n"
        f"*Key tweets:*\n{drivers}"
    )


class TelegramAlerter:
    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
        dry_run: bool = False,
    ) -> None:
        self._token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
        self._dry_run = dry_run

    def send(self, sig: Signal) -> bool:
        message = _format_message(sig)
        if self._dry_run:
            print(f"\n[DRY-RUN ALERT]\n{message}\n")
            return True
        if not self._token or not self._chat_id:
            logger.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
            return False
        try:
            url = _TELEGRAM_API.format(token=self._token)
            resp = httpx.post(
                url,
                json={
                    "chat_id": self._chat_id,
                    "text": message,
                    "parse_mode": "Markdown",
                },
                timeout=10,
            )
            resp.raise_for_status()
            return True
        except httpx.HTTPError as e:
            logger.error(f"Telegram send failed for {sig.ticker}: {e}")
            return False
