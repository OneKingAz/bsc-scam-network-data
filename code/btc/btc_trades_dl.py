"""24.11: every BTCUSDT trade (aggTrades: ms time, price, qty, buyer-is-maker) for the last N hours, public Binance API -> btc_trades.csv"""
import requests, time, csv, sys
hours = float(sys.argv[1]) if len(sys.argv) > 1 else 24
end = int(time.time() * 1000); t = end - int(hours * 3600_000)
S = requests.Session(); n = 0; last_id = None
with open("btc_trades.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["id", "t", "p", "q", "buyer_maker"])
    while True:
        prm = {"symbol": "BTCUSDT", "limit": 1000}
        if last_id is None: prm.update(startTime=t, endTime=t + 3600_000)
        else: prm["fromId"] = last_id + 1
        try:
            r = S.get("https://api.binance.com/api/v3/aggTrades", params=prm, timeout=20)
            if r.status_code == 429 or r.status_code == 418: time.sleep(30); continue
            r = r.json()
        except Exception:
            time.sleep(2); continue
        if not r:
            if last_id is None: t += 3600_000; continue
            break
        for a in r: w.writerow([a["a"], a["T"], a["p"], a["q"], int(a["m"])])
        n += len(r); last_id = r[-1]["a"]
        if r[-1]["T"] >= end: break
        if n % 200000 < 1000: print(f"сделок {n}, дошли до {time.strftime('%d.%m %H:%M', time.localtime(r[-1]['T'] / 1000))}", flush=True)
        time.sleep(0.05)
print("готово, сделок", n)
