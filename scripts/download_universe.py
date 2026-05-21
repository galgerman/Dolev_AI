"""Download NYSE + NASDAQ symbol lists and write config/universe.csv."""
import csv
import ftplib
import io
import pathlib

FTP_HOST = "ftp.nasdaqtrader.com"
FILES = {
    "nasdaqlisted.txt": "nasdaq",
    "otherlisted.txt": "nyse",
}
OUT = pathlib.Path(__file__).parent.parent / "config" / "universe.csv"


def _fetch(ftp: ftplib.FTP, filename: str) -> list[str]:
    buf = io.BytesIO()
    ftp.retrbinary(f"RETR /symboldirectory/{filename}", buf.write)
    return buf.getvalue().decode("utf-8").splitlines()


def main() -> None:
    symbols: set[str] = set()

    print(f"Connecting to {FTP_HOST}…")
    with ftplib.FTP(FTP_HOST) as ftp:
        ftp.login()
        for filename, exchange in FILES.items():
            lines = _fetch(ftp, filename)
            reader = csv.reader(lines, delimiter="|")
            header = next(reader)
            sym_col = 0  # Symbol is always the first column
            added = 0
            for row in reader:
                if not row or row[0].strip() in ("", "Symbol"):
                    continue
                sym = row[sym_col].strip().upper()
                # Skip test symbols and non-standard entries
                if sym and sym.isalpha() and 1 <= len(sym) <= 5:
                    symbols.add(sym)
                    added += 1
            print(f"  {exchange}: {added} symbols from {filename}")

    OUT.parent.mkdir(exist_ok=True)
    with open(OUT, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["symbol"])
        for sym in sorted(symbols):
            writer.writerow([sym])

    print(f"\nWrote {len(symbols)} symbols → {OUT}")


if __name__ == "__main__":
    main()
