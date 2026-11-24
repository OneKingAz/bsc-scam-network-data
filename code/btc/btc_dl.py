"""24.11: BTCUSDT 1-minute candles, last N days, public Binance API (no key) -> btc_1m_180.csv"""
import requests, time, csv, sys
days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
end = int(time.time() * 1000); start = end - days * 86400_000
rows, t = [], start
while t < end:
    for u in ("https://api.binance.com", "https://data-api.binance.vision"):
        try:
            r = requests.get(u + "/api/v3/klines", params={"symbol": "BTCUSDT", "interval": "1m", "startTime": t, "limit": 1000}, timeout=20).json()
            if isinstance(r, list): break
        except Exception: r = None
    if not r: time.sleep(1); continue
    rows += [(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])) for k in r]
    t = r[-1][0] + 60_000
with open("btc_1m_180.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["t", "o", "h", "l", "c", "v"]); w.writerows(rows)
print("свечей", len(rows))
