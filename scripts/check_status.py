"""Quick agent status check."""
import sqlite3
import datetime
from zoneinfo import ZoneInfo

db = sqlite3.connect("data/dolev.db")
today = datetime.date.today().isoformat()

# ET time
et = datetime.datetime.now(ZoneInfo("America/New_York"))
print(f"Current ET time: {et.strftime('%H:%M:%S')}  ({'MARKET OPEN' if 9*60+30 <= et.hour*60+et.minute < 16*60 else 'MARKET CLOSED'})")

# Non-zero gradients today
rows = db.execute(
    "SELECT ticker, pct_change, gradient, captured_at FROM ticker_movements "
    "WHERE captured_at >= ? AND gradient != 0 ORDER BY ABS(gradient) DESC LIMIT 10",
    (today,)
).fetchall()
print(f"\nMovers with non-zero gradient today: {len(rows)}")
for r in rows:
    print(f"  {r[0]:8} {float(r[1]):+.2f}%  grad={float(r[2]):+.4f}  {r[3]}")

# Latest captured_at (tells us if scanner is running)
latest = db.execute(
    "SELECT MAX(captured_at) FROM ticker_movements WHERE captured_at >= ?", (today,)
).fetchone()[0]
print(f"\nLatest market data: {latest}")

# Signal observations today
count = db.execute(
    "SELECT COUNT(*) FROM signal_observations WHERE generated_at >= ?", (today,)
).fetchone()[0]
print(f"Signal observations today: {count}")

db.close()
