"""24.11 idea: on pump.fun the curve is a program nobody of them controls (no guard, no blocked sells, no pulled LP).
Find wallets that made money in the FIRST half of the 12 h recording, then copy their buys in the SECOND half (out of sample):
we buy `delay` s after the leader (price of the first trade after that, +slip), sell `delay` s after the leader's first sell
of that coin (or after 10 min). Fees 1.25 % each side. Paper only."""
import csv, collections, statistics as st, sys, bisect
sys.stdout.reconfigure(encoding="utf-8")
FEE = 0.0125
ev = collections.defaultdict(list)   # mint -> [(t, price)]
tr = []                              # (t, mint, user, is_buy, sol)
for r in csv.DictReader(open("pumpfun_events.csv", encoding="utf-8"), delimiter=";"):
    if r["kind"] != "trade" or not int(r["vtok"] or 0): continue
    t = float(r["t"]); p = int(r["vsol"]) / int(r["vtok"])
    ev[r["mint"]].append((t, p)); tr.append((t, r["mint"], r["user"], r["is_buy"] == "1", int(r["sol"]) / 1e9))
T0, T1 = tr[0][0], tr[-1][0]; MID = (T0 + T1) / 2
times = {m: [x[0] for x in v] for m, v in ev.items()}
# train: realized PnL per (user, mint) in the first half
pos = collections.defaultdict(lambda: [0.0, 0.0])  # (user, mint) -> [sol in, sol out]
for t, m, u, b, s in tr:
    if t >= MID: break
    pos[(u, m)][0 if b else 1] += s
per = collections.defaultdict(list)
for (u, m), (i, o) in pos.items():
    if i >= 0.05 and o > 0: per[u].append(o / i - 1)
for MINC, MINWIN in ((5, 0.6), (10, 0.6), (5, 0.7), (20, 0.55)):
    leaders = {u for u, a in per.items() if len(a) >= MINC and sum(x > 0 for x in a) / len(a) >= MINWIN and st.mean(a) > 0.05}
    for delay, slip in ((1, 0.01), (3, 0.02), (5, 0.03)):
        first_buy, first_sell = {}, {}
        for t, m, u, b, s in tr:
            if t < MID or u not in leaders: continue
            if b and (u, m) not in first_buy and s >= 0.05: first_buy[(u, m)] = t
            if not b and (u, m) in first_buy and (u, m) not in first_sell: first_sell[(u, m)] = t
        res = []
        for (u, m), tb in first_buy.items():
            ts = times[m]; k = bisect.bisect_left(ts, tb + delay)
            if k >= len(ts): continue
            p0 = ev[m][k][1] * (1 + slip)
            te = first_sell.get((u, m), tb + 600) + delay
            j = min(bisect.bisect_left(ts, te), len(ts) - 1)
            res.append(ev[m][j][1] / p0 * (1 - slip) * (1 - FEE) ** 2 - 1)
        if res:
            print(f"лидеров {len(leaders):4} (≥{MINC} монет, побед ≥{MINWIN:.0%}) задержка {delay} с, проск. {slip:.0%}: сделок {len(res):5}, "
                  f"среднее {st.mean(res)*100:+.1f}%, медиана {st.median(res)*100:+.1f}%, в плюсе {sum(x>0 for x in res)*100//len(res)}%")
