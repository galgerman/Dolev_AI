"""Risk management rules applied before every order."""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class OrderSpec:
    ticker: str
    side: str          # 'buy' | 'sell'
    shares: int
    entry_price: float
    stop_price: float
    dollar_value: float


class RiskManager:
    """Enforces position-level risk rules.

    Rules (all configurable via settings.yaml broker.risk block):
      - max_position_pct : max % of portfolio per trade  (default 2%)
      - stop_loss_pct    : stop placed N% below/above fill (default 2%)
      - max_open_positions: hard cap on concurrent positions (default 5)
    """

    def __init__(
        self,
        max_position_pct: float = 2.0,
        stop_loss_pct: float = 2.0,
        max_open_positions: int = 5,
    ) -> None:
        self.max_position_pct = max_position_pct
        self.stop_loss_pct = stop_loss_pct
        self.max_open_positions = max_open_positions

    def size(self, portfolio_value: float, entry_price: float) -> int:
        """Return share count such that notional ≤ max_position_pct% of portfolio."""
        if entry_price <= 0 or portfolio_value <= 0:
            return 0
        dollar_budget = portfolio_value * (self.max_position_pct / 100.0)
        shares = int(dollar_budget / entry_price)
        return max(shares, 0)

    def stop_price(self, entry_price: float, side: str) -> float:
        """Stop placed stop_loss_pct% against the trade direction."""
        factor = 1.0 - self.stop_loss_pct / 100.0 if side == "buy" else 1.0 + self.stop_loss_pct / 100.0
        return round(entry_price * factor, 4)

    def build_order(
        self,
        ticker: str,
        side: str,
        entry_price: float,
        portfolio_value: float,
        open_position_count: int,
    ) -> OrderSpec | None:
        """Return an OrderSpec if the trade passes all risk checks, else None."""
        if open_position_count >= self.max_open_positions:
            logger.warning(
                f"Risk gate: {ticker} blocked — already {open_position_count} open positions "
                f"(max {self.max_open_positions})"
            )
            return None

        shares = self.size(portfolio_value, entry_price)
        if shares == 0:
            logger.warning(f"Risk gate: {ticker} blocked — 0 shares at ${entry_price:.2f}")
            return None

        stop = self.stop_price(entry_price, side)
        dollar_value = shares * entry_price
        logger.info(
            f"Risk: {side.upper()} {shares} × {ticker} @ ${entry_price:.2f}  "
            f"notional=${dollar_value:,.0f} ({self.max_position_pct}% of ${portfolio_value:,.0f})  "
            f"stop=${stop:.2f} ({self.stop_loss_pct}% {'below' if side == 'buy' else 'above'})"
        )
        return OrderSpec(
            ticker=ticker,
            side=side,
            shares=shares,
            entry_price=entry_price,
            stop_price=stop,
            dollar_value=dollar_value,
        )
