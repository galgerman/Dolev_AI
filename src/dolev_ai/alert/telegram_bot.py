"""Interactive Telegram bot with inline keyboards and long-polling callback handler."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Callable, Awaitable

import httpx

from dolev_ai.models import Signal

logger = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/{method}"

_SIDE_EMOJI = {"buy": "🟢", "sell": "🔴"}


def _api(token: str, method: str) -> str:
    return _API.format(token=token, method=method)


class TelegramBot:
    """
    Interactive Telegram bot. Sends inline-keyboard prompts for trade approvals
    and polls for callback queries via getUpdates long-polling.
    """

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
        dry_run: bool = False,
        on_callback: Callable[[int, str], Awaitable[None]] | None = None,
    ) -> None:
        self._token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
        self._dry_run = dry_run
        self._on_callback = on_callback  # async (approval_id, decision) → None
        self._poll_task: asyncio.Task | None = None
        self._update_offset: int = 0
        self._client: httpx.AsyncClient | None = None

    # ── lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=35)
        if not self._dry_run and self._token:
            self._poll_task = asyncio.create_task(self._poll_loop(), name="tg-poll")
            logger.info("Telegram bot polling started.")

    async def stop(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        if self._client:
            await self._client.aclose()
            self._client = None

    # ── public send methods ───────────────────────────────────────────────────

    async def send_open_prompt(self, sig: Signal, approval_id: int) -> int | None:
        emoji = _SIDE_EMOJI.get(sig.side, "⚪")
        size_pct = round(sig.suggested_size_pct * 100, 1)
        text = (
            f"{emoji} *{sig.side.upper()} signal: ${sig.ticker}*\n"
            f"Conviction: {round(sig.conviction * 100)}%  |  Size hint: {size_pct}%\n\n"
            f"{sig.rationale}\n\n"
            f"_Open a paper position?_"
        )
        buttons = [[
            {"text": f"✅ Approve {sig.side}", "callback_data": f"approve:open:{approval_id}"},
            {"text": "❌ Reject", "callback_data": f"reject:open:{approval_id}"},
        ]]
        return await self._send_with_keyboard(text, buttons)

    async def send_close_prompt(
        self, position, opposing_sig: Signal, approval_id: int
    ) -> int | None:
        pnl_est = (opposing_sig.conviction - 0.5) * 10  # rough estimate, not real
        text = (
            f"🔄 *Close position: ${position.ticker}?*\n"
            f"Held since: {position.opened_at.strftime('%Y-%m-%d %H:%M')} UTC\n"
            f"Entry: ${position.entry_price:.2f}\n\n"
            f"Opposing signal: {opposing_sig.side.upper()}  "
            f"conviction {round(opposing_sig.conviction * 100)}%\n"
            f"{opposing_sig.rationale[:200]}"
        )
        buttons = [[
            {"text": "✅ Close position", "callback_data": f"approve:close:{approval_id}"},
            {"text": "⏸ Hold", "callback_data": f"hold:close:{approval_id}"},
        ]]
        return await self._send_with_keyboard(text, buttons)

    async def send_text(self, text: str) -> int | None:
        if self._dry_run or not self._token:
            print(f"\n[TG-DRY-RUN]\n{text}\n")
            return None
        return await self._send(text, reply_markup=None)

    async def edit_message(self, message_id: int, text: str) -> None:
        if self._dry_run or not self._token:
            print(f"\n[TG-EDIT msg={message_id}] {text}\n")
            return
        try:
            await self._client.post(  # type: ignore[union-attr]
                _api(self._token, "editMessageText"),
                json={
                    "chat_id": self._chat_id,
                    "message_id": message_id,
                    "text": text,
                    "parse_mode": "Markdown",
                },
            )
        except Exception as e:
            logger.warning(f"editMessageText failed: {e}")

    # ── internal helpers ──────────────────────────────────────────────────────

    async def _send_with_keyboard(self, text: str, buttons: list) -> int | None:
        if self._dry_run or not self._token:
            print(f"\n[TG-DRY-RUN PROMPT]\n{text}\nButtons: {buttons}\n")
            return None
        reply_markup = {"inline_keyboard": buttons}
        return await self._send(text, reply_markup=reply_markup)

    async def _send(self, text: str, reply_markup=None) -> int | None:
        payload: dict = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        try:
            resp = await self._client.post(  # type: ignore[union-attr]
                _api(self._token, "sendMessage"),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["result"]["message_id"]
        except Exception as e:
            logger.error(f"Telegram sendMessage failed: {e}")
            return None

    # ── long-polling loop ─────────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        logger.info("Starting Telegram getUpdates long-polling loop.")
        while True:
            try:
                resp = await self._client.get(  # type: ignore[union-attr]
                    _api(self._token, "getUpdates"),
                    params={"offset": self._update_offset, "timeout": 30, "allowed_updates": ["callback_query"]},
                    timeout=35,
                )
                resp.raise_for_status()
                updates = resp.json().get("result", [])
                for update in updates:
                    self._update_offset = update["update_id"] + 1
                    if "callback_query" in update:
                        await self._handle_callback(update["callback_query"])
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Telegram poll error: {e}")
                await asyncio.sleep(5)

    async def _handle_callback(self, cq: dict) -> None:
        cq_id = cq["id"]
        data = cq.get("data", "")
        # Ack immediately so Telegram stops spinning
        try:
            await self._client.post(  # type: ignore[union-attr]
                _api(self._token, "answerCallbackQuery"),
                json={"callback_query_id": cq_id},
                timeout=5,
            )
        except Exception:
            pass

        # Parse callback_data: "<decision>:<kind>:<approval_id>"
        # e.g. "approve:open:42", "reject:open:42", "approve:close:7", "hold:close:7"
        parts = data.split(":")
        if len(parts) != 3:
            logger.warning(f"Unexpected callback_data: {data}")
            return

        raw_decision, _kind, approval_id_str = parts
        try:
            approval_id = int(approval_id_str)
        except ValueError:
            return

        # Normalise: "hold" → "rejected" for approval table
        decision = "approved" if raw_decision == "approve" else "rejected"

        if self._on_callback:
            await self._on_callback(approval_id, decision)
