"""24.11: out-of-sample check of «follow the top traders' long/short extreme» on 6 months of the Binance archive.
The rule was found on the last 30 days (fut_bt.py); here the 5 months BEFORE them are unseen data. Per month. Fee 0.04 % (maker) and 0.10 %."""
import csv, glob, statistics as st, sys, bisect, calendar, time, collections
sys.stdout.reconfigure(encoding="utf-8")
rows = []
for f in glob.glob("metrics/*.csv"):
    for r in csv.DictReader(open(f)):
        t = calendar.timegm(time.strptime(r["create_time"], "%Y-%m-%d %H:%M:%S")) * 1000
        rows.append((t, float(r["sum_toptrader_long_short_ratio"] or 0), float(r["count_toptrader_long_short_ratio"] or 0)))
rows.sort()
api = {int(r["t"]): float(r["top_ls"]) for r in csv.DictReader(open("fut_metrics.csv"))}
m = [(v, api[t]) for t, v, _ in rows if t in api][:5]
print("сверка с API (архив sum_toptrader vs API topLongShortPositionRatio):", m)
K = [(int(r["t"]), float(r["c"])) for r in csv.DictReader(open("btc_1m_180.csv"))]; KT = [k[0] for k in K]
def px(t):
    i = bisect.bisect_left(KT, t); return K[i][1] if i < len(K) else None
cut = api and min(api)  # the 30 days the rule was found on
for col, cname in ((1, "позиции топ-трейдеров"), (2, "счета топ-трейдеров")):
    vals = [r[col] for r in rows]
    for Z, H in ((2.0, 60), (2.5, 60), (2.0, 240)):
        by = collections.defaultdict(list); last = -10**15
        for j in range(288, len(rows)):
            t = rows[j][0]; win = vals[j - 288:j]; sd = st.pstdev(win)
            if not sd: continue
            z = (vals[j] - st.mean(win)) / sd
            if abs(z) < Z or t - last < H * 60_000: continue
            a, b = px(t + 360_000), px(t + 360_000 + H * 60_000)
            if not a or not b: continue
            by["ПОИСК (30 дн.)" if t >= cut else time.strftime("%Y-%m", time.gmtime(t / 1000))].append((1 if z > 0 else -1) * (b / a - 1) * 100); last = t
        oos = [x for k, v in by.items() if not k.startswith("ПОИСК") for x in v]
        print(f"\n{cname}, |z|≥{Z}, держать {H} мин: НЕВИДЕННЫЕ 5 мес. — сделок {len(oos)}, до комиссии {st.mean(oos):+.3f}%, "
              f"после 0.04% {st.mean(oos)-0.04:+.3f}%, после 0.1% {st.mean(oos)-0.1:+.3f}%, в плюсе до комиссии {sum(x>0 for x in oos)*100//len(oos)}%")
        print("   по месяцам (до комиссии): " + ", ".join(f"{k} {st.mean(v):+.3f}% ({len(v)})" for k, v in sorted(by.items())))
