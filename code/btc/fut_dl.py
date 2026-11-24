"""24.11: Binance futures public metrics for BTCUSDT, 5-min, last ~30 days (no key): open interest, top traders' long/short (positions),
all accounts long/short, taker buy/sell volume ratio; funding history. -> fut_metrics.csv, fut_funding.csv"""
import requests, time, csv
B = "https://fapi.binance.com"; S = requests.Session()
end = int(time.time() * 1000); start = end - 29 * 86400_000
def pull(path, key):
    out, t = {}, start
    while t < end:
        r = S.get(B + path, params={"symbol": "BTCUSDT", "period": "5m", "limit": 500, "startTime": t, "endTime": min(end, t + 500 * 300_000)}, timeout=20).json()
        if not isinstance(r, list): print(path, r); break
        for x in r: out[int(x["timestamp"])] = x
        t += 500 * 300_000; time.sleep(0.2)
    print(path, len(out)); return out
oi = pull("/futures/data/openInterestHist", "oi")
tp = pull("/futures/data/topLongShortPositionRatio", "tp")
ga = pull("/futures/data/globalLongShortAccountRatio", "ga")
tk = pull("/futures/data/takerlongshortRatio", "tk")
with open("fut_metrics.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["t", "oi", "top_ls", "all_ls", "taker_bs"])
    for t in sorted(oi):
        if t in tp and t in ga and t in tk:
            w.writerow([t, oi[t]["sumOpenInterest"], tp[t]["longShortRatio"], ga[t]["longShortRatio"], tk[t]["buySellRatio"]])
fr = S.get(B + "/fapi/v1/fundingRate", params={"symbol": "BTCUSDT", "startTime": start, "limit": 1000}, timeout=20).json()
with open("fut_funding.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["t", "rate"]); [w.writerow([x["fundingTime"], x["fundingRate"]]) for x in fr]
print("funding", len(fr))
