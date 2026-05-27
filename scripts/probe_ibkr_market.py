"""Probe IBKR market data: scanner + gradient.

Run with TWS open and logged in (paper or live account):
    py -3.12 scripts/probe_ibkr_market.py

Optional args:
    --port 7496     (live TWS; default 7497 paper)
    --n 10          (top N per side; default 5)
    --gradient 5    (compute gradient for top N; default 5)
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dolev_ai.broker.ibkr import IBKRBroker
from dolev_ai.broker.market_data import IBKRMarketSource


async def main(port: int, n: int, gradient_n: int) -> None:
    print(f"Connecting to TWS on port {port}...")
    broker = IBKRBroker(port=port, client_id=10)
    connected = await broker.connect()
    if not connected:
        print("ERROR: Could not connect. Make sure TWS is running and API is enabled.")
        print("  TWS > Edit > Global Configuration > API > Enable ActiveX and Socket Clients")
        return

    pv = await broker.portfolio_value()
    print(f"Portfolio value: ${pv:,.2f}" if pv else "Portfolio value: unavailable")

    src = IBKRMarketSource(
        broker=broker,
        top_n_per_side=n,
        gradient_top_n=gradient_n,
        gradient_bars=10,
    )

    print(f"\nFetching top {n} gainers + losers with gradient for top {gradient_n}...\n")
    movers = await src.fetch_movers()

    if not movers:
        print("No movers returned. Check that market is open or use delayed data.")
        await broker.disconnect()
        return

    gainers = sorted([m for m in movers if m.side == "gainer"],
                     key=lambda x: abs(x.pct_change), reverse=True)
    losers  = sorted([m for m in movers if m.side == "loser"],
                     key=lambda x: abs(x.pct_change), reverse=True)

    print(f"{'GAINERS':}")
    print(f"  {'Ticker':<8} {'%chg':>7} {'$/min':>8} {'bars':>5} {'last':>8}")
    print(f"  {'-'*45}")
    for m in gainers[:n]:
        grad = f"{m.gradient:+.3f}" if m.gradient_bars > 0 else "  n/a "
        print(f"  {m.ticker:<8} {m.pct_change:>+7.2f}% {grad:>8} {m.gradient_bars:>5} ${m.last_price:>7.2f}")

    print(f"\n{'LOSERS':}")
    print(f"  {'Ticker':<8} {'%chg':>7} {'$/min':>8} {'bars':>5} {'last':>8}")
    print(f"  {'-'*45}")
    for m in losers[:n]:
        grad = f"{m.gradient:+.3f}" if m.gradient_bars > 0 else "  n/a "
        print(f"  {m.ticker:<8} {m.pct_change:>+7.2f}% {grad:>8} {m.gradient_bars:>5} ${m.last_price:>7.2f}")

    # Highlight highest-gradient movers (fastest moving RIGHT NOW)
    with_grad = [m for m in movers if m.gradient_bars > 0]
    if with_grad:
        top_grad = sorted(with_grad, key=lambda x: abs(x.gradient), reverse=True)[:5]
        print(f"\nFastest moving RIGHT NOW (by gradient):")
        for m in top_grad:
            print(f"  {m.ticker:<8} {m.gradient:+.4f}%/min  ({m.pct_change:+.2f}% total  {m.gradient_bars} bars)")

    await broker.disconnect()
    print("\nDisconnected.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=7497)
    parser.add_argument("--n", type=int, default=5)
    parser.add_argument("--gradient", type=int, default=5)
    args = parser.parse_args()
    asyncio.run(main(args.port, args.n, args.gradient))
