"""Signal-quality calibration report.

Reads signal_observations + paper_positions from data/dolev.db and prints:
  - signal count + continuation probability per confidence bucket
  - median return at each horizon, per bucket
  - false-breakout rate (return_5m of opposite sign)
  - average MFE / MAE
  - per-feature Pearson correlation with return_5m
  - per-sector breakdown

Usage:
  py -3.12 scripts/report_signals.py                # last 7 days
  py -3.12 scripts/report_signals.py --since 30d
  py -3.12 scripts/report_signals.py --csv out.csv  # also dump raw rows
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import pathlib
import statistics
import sys
from datetime import datetime, timedelta

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dolev_ai.db import SignalObservationRow, init_db  # noqa: E402

HORIZON_COLS = [
    ("return_30s", "30s"),
    ("return_1m", "1m"),
    ("return_3m", "3m"),
    ("return_5m", "5m"),
    ("return_15m", "15m"),
    ("return_30m", "30m"),
]
BUCKETS = [(30, 50), (50, 70), (70, 90), (90, 101)]


def parse_since(since: str) -> datetime:
    if since.endswith("d"):
        return datetime.utcnow() - timedelta(days=int(since[:-1]))
    if since.endswith("h"):
        return datetime.utcnow() - timedelta(hours=int(since[:-1]))
    raise SystemExit(f"Unknown --since format: {since}")


def bucket_label(score: float) -> str | None:
    for lo, hi in BUCKETS:
        if lo <= score < hi:
            return f"{lo}-{hi}"
    return None


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    if all(x == xs[0] for x in xs) or all(y == ys[0] for y in ys):
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if den_x == 0 or den_y == 0:
        return None
    return num / (den_x * den_y)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default="7d", help="e.g. 7d, 24h, 30d")
    parser.add_argument("--db", default=str(ROOT / "data" / "dolev.db"))
    parser.add_argument("--csv", default=None, help="optional CSV dump path")
    args = parser.parse_args()

    db_path = pathlib.Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    SessionLocal = init_db(db_path)
    since = parse_since(args.since)

    with SessionLocal() as session:
        rows = (
            session.query(SignalObservationRow)
            .filter(SignalObservationRow.generated_at >= since)
            .all()
        )
        # Detach from session by copying fields we need
        records = [
            {
                "id": r.id, "ticker": r.ticker, "side": r.side,
                "generated_at": r.generated_at, "entry_price": r.entry_price,
                "confidence": r.confidence,
                "components": json.loads(r.components_json or "{}"),
                "features": json.loads(r.features_json or "{}"),
                "sector_etf": r.sector_etf,
                "status": r.status,
                "mfe_pct": r.mfe_pct, "mae_pct": r.mae_pct,
                **{c: getattr(r, c) for c, _ in HORIZON_COLS},
            }
            for r in rows
        ]

    if not records:
        print(f"No signal_observations since {since.isoformat()}")
        return

    print(f"Signal report ({len(records)} observations since {since.date()})")
    print("=" * 72)

    # ── Per confidence bucket ─────────────────────────────────────────────
    print("\nBy confidence bucket")
    print(f"  {'bucket':<10} {'n':>5} {'cont%':>7} {'med5m':>8} {'med30m':>8} "
          f"{'MFE':>7} {'MAE':>7} {'falseBR%':>9}")
    for lo, hi in BUCKETS:
        bucket = [r for r in records if r["confidence"] is not None and lo <= r["confidence"] < hi]
        if not bucket:
            print(f"  {f'{lo}-{hi}':<10} {0:>5}")
            continue
        ret5 = [r["return_5m"] for r in bucket if r["return_5m"] is not None]
        ret30 = [r["return_30m"] for r in bucket if r["return_30m"] is not None]
        mfes = [r["mfe_pct"] for r in bucket if r["mfe_pct"] is not None]
        maes = [r["mae_pct"] for r in bucket if r["mae_pct"] is not None]
        cont_pct = (sum(1 for v in ret5 if v > 0) / len(ret5) * 100) if ret5 else 0
        false_br = (sum(1 for v in ret5 if v < -0.3) / len(ret5) * 100) if ret5 else 0
        print(f"  {f'{lo}-{hi}':<10} {len(bucket):>5} "
              f"{cont_pct:>6.1f}% "
              f"{(statistics.median(ret5) if ret5 else 0):>+7.2f}% "
              f"{(statistics.median(ret30) if ret30 else 0):>+7.2f}% "
              f"{(statistics.mean(mfes) if mfes else 0):>+6.2f}% "
              f"{(statistics.mean(maes) if maes else 0):>+6.2f}% "
              f"{false_br:>8.1f}%")

    # ── Median return per horizon (all signals) ────────────────────────────
    print("\nMedian return per horizon (all signals)")
    for col, label in HORIZON_COLS:
        vals = [r[col] for r in records if r[col] is not None]
        if vals:
            print(f"  {label:>4}: n={len(vals):>4}  med={statistics.median(vals):+.3f}%  "
                  f"mean={statistics.mean(vals):+.3f}%")

    # ── Pearson correlation of each component with return_5m ──────────────
    print("\nFeature → return_5m correlation (Pearson)")
    with_5m = [r for r in records if r["return_5m"] is not None]
    if not with_5m:
        print("  no return_5m samples yet")
    else:
        all_keys: set[str] = set()
        for r in with_5m:
            all_keys.update(r["components"].keys())
        ret = [r["return_5m"] for r in with_5m]
        for key in sorted(all_keys):
            vals = [r["components"].get(key, 0.0) for r in with_5m]
            corr = pearson(vals, ret)
            if corr is not None:
                marker = " ✓" if abs(corr) > 0.15 else ""
                print(f"  {key:<22} r={corr:+.3f}  n={len(vals)}{marker}")

    # ── Per-sector breakdown ──────────────────────────────────────────────
    by_sector: dict[str, list[float]] = {}
    for r in with_5m:
        sector = r.get("sector_etf") or "?"
        by_sector.setdefault(sector, []).append(r["return_5m"])
    if by_sector:
        print("\nBy sector ETF (return_5m)")
        for sec, vals in sorted(by_sector.items(), key=lambda kv: -len(kv[1])):
            print(f"  {sec:<6} n={len(vals):>4}  med={statistics.median(vals):+.3f}%  "
                  f"win%={sum(1 for v in vals if v > 0) / len(vals) * 100:.1f}%")

    # ── CSV dump ──────────────────────────────────────────────────────────
    if args.csv:
        out = pathlib.Path(args.csv)
        keys = [
            "id", "ticker", "side", "generated_at", "entry_price", "confidence",
            "sector_etf", "status", "mfe_pct", "mae_pct",
            *[c for c, _ in HORIZON_COLS],
        ]
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for r in records:
                w.writerow({k: r.get(k) for k in keys})
        print(f"\nWrote {len(records)} rows → {out}")


if __name__ == "__main__":
    main()
