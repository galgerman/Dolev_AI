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

    # ── Rich trade journal (notification-only, no buttons) ──────────────────

    async def send_trade_open(
        self,
        pos,
        *,
        features=None,
        components: dict | None = None,
        latency_ms: int | None = None,
        slippage_bps: float | None = None,
        confidence: float | None = None,
        mode: str = "paper",
    ) -> int | None:
        """Send rich entry-of-trade journal. Returns message_id so close can edit.

        Format intentionally compact and monospaced for legibility on mobile.
        """
        text = format_trade_open(
            pos, features=features, components=components,
            latency_ms=latency_ms, slippage_bps=slippage_bps,
            confidence=confidence, mode=mode,
        )
        if self._dry_run or not self._token:
            print(f"\n[TG-DRY-RUN OPEN]\n{text}\n")
            return None
        return await self._send(text, reply_markup=None)

    async def send_trade_close(
        self,
        pos,
        *,
        mfe_pct: float | None = None,
        mae_pct: float | None = None,
        hold_minutes: float | None = None,
        exit_reason: str | None = None,
        edit_message_id: int | None = None,
    ) -> int | None:
        """Send close-of-trade summary. If edit_message_id is provided, edits the entry post."""
        text = format_trade_close(
            pos, mfe_pct=mfe_pct, mae_pct=mae_pct,
            hold_minutes=hold_minutes, exit_reason=exit_reason,
        )
        if edit_message_id is not None:
            await self.edit_message(edit_message_id, text)
            return edit_message_id
        if self._dry_run or not self._token:
            print(f"\n[TG-DRY-RUN CLOSE]\n{text}\n")
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


# ── Trade-journal formatters (module-level so tests can hit them) ──────────

_COMPONENT_ORDER = [
    "gradient", "acceleration", "roc_3m",
    "rel_strength_spy", "rel_strength_sector",
    "vwap_above", "broke_pmh",
    "rel_volume", "extension_penalty", "spread_penalty",
]


def format_trade_open(
    pos,
    *,
    features=None,
    components: dict | None = None,
    latency_ms: int | None = None,
    slippage_bps: float | None = None,
    confidence: float | None = None,
    mode: str = "paper",
) -> str:
    side = (pos.side or "").upper()
    emoji = "📈" if side == "BUY" else "📉"
    mode_label = mode.upper()
    head_bits = [f"{emoji} {mode_label} {side} ${pos.ticker}"]
    if confidence is not None:
        head_bits.append(f"conf={confidence:.1f}/100")
    head = "  ".join(head_bits)

    fill_bits = []
    if getattr(pos, "shares", None):
        fill_bits.append(f"{pos.shares} sh")
    fill_bits.append(f"@ ${pos.entry_price:.2f}")
    if getattr(pos, "stop_price", None):
        fill_bits.append(f"stop ${pos.stop_price:.2f}")
    fill_line = "  ".join(fill_bits)

    timing_bits = []
    if latency_ms is not None:
        timing_bits.append(f"latency: {latency_ms}ms")
    if slippage_bps is not None:
        timing_bits.append(f"slip: {slippage_bps:+.1f}bp")
    timing_line = "  ".join(timing_bits)

    out = [head, f"  fill: {fill_line}"]
    if timing_line:
        out.append(f"  {timing_line}")

    # Features block (compact)
    if features is not None:
        feat_lines = ["", "Why:"]
        feat_lines.extend(_features_block(features))
        out.extend(feat_lines)

    # Component contribution table
    if components:
        out.append("```")
        for name in _COMPONENT_ORDER:
            if name in components:
                val = components[name]
                out.append(f"  {name:<22} {val:+6.1f}")
        if confidence is not None:
            out.append(f"  {'':<22} {'='}")
            out.append(f"  {'total':<22} {confidence:6.1f}")
        out.append("```")
    return "\n".join(out)


def format_trade_close(
    pos,
    *,
    mfe_pct: float | None = None,
    mae_pct: float | None = None,
    hold_minutes: float | None = None,
    exit_reason: str | None = None,
) -> str:
    side = (pos.side or "").upper()
    emoji = "✅" if (pos.pnl_pct or 0) >= 0 else "❌"

    head = f"{emoji} CLOSED {side} ${pos.ticker} @ ${(pos.exit_price or 0):.2f}"

    pnl_bits = []
    if pos.pnl_pct is not None:
        pnl_bits.append(f"P&L: {pos.pnl_pct:+.2%}")
    if pos.pnl_dollars is not None:
        pnl_bits.append(f"(${pos.pnl_dollars:+,.2f})")
    pnl_line = "  ".join(pnl_bits)

    out = [head]
    if pnl_line:
        out.append(f"  {pnl_line}")
    if mfe_pct is not None or mae_pct is not None:
        mfe_str = f"MFE {mfe_pct:+.2f}%" if mfe_pct is not None else ""
        mae_str = f"MAE {mae_pct:+.2f}%" if mae_pct is not None else ""
        out.append(f"  {mfe_str}  {mae_str}".strip())
    if hold_minutes is not None:
        out.append(f"  hold: {hold_minutes:.1f}m")
    if exit_reason:
        out.append(f"  reason: {exit_reason}")
    return "\n".join(out)


def _features_block(features) -> list[str]:
    """Render a FeatureSnapshot (or dict) as compact lines."""
    def g(name, default=None):
        if isinstance(features, dict):
            return features.get(name, default)
        return getattr(features, name, default)

    lines = []
    grad = g("gradient")
    if grad is not None:
        lines.append(f"  gradient    {grad:+.3f}%/min")
    accel = g("acceleration")
    if accel is not None:
        lines.append(f"  accel       {accel:+.3f}")
    roc3 = g("roc_3m")
    if roc3 is not None:
        lines.append(f"  roc_3m      {roc3:+.2f}%")
    rs_spy = g("rel_strength_spy")
    if rs_spy is not None:
        lines.append(f"  rs_spy      {rs_spy:+.3f}")
    rs_sec = g("rel_strength_sector")
    if rs_sec is not None:
        lines.append(f"  rs_sector   {rs_sec:+.3f}")
    vwap = g("vwap_state")
    if vwap:
        lines.append(f"  vwap        {vwap}")
    pmh = g("broke_pmh", False)
    pml = g("broke_pml", False)
    if pmh:
        lines.append("  pmh         broke")
    if pml:
        lines.append("  pml         broke")
    rel_vol = g("rel_volume")
    if rel_vol is not None:
        lines.append(f"  rel_vol     {rel_vol:.2f}x")
    spread = g("spread_pct")
    if spread is not None:
        lines.append(f"  spread      {spread:.2f}%")
    return lines
