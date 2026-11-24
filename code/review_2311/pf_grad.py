"""24.11: buy a pump.fun coin when the curve first holds X SOL (75-82), hold until graduation (complete: last curve price),
TP, SL or T minutes. 89 % of coins that reach 80 SOL graduate. Paper, out-of-sample halves, fees 1.25 % x2, delay and slippage."""
import csv, collections, statistics as st, sys
sys.stdout.reconfigure(encoding="utf-8")
FEE = 0.0125
coins = collections.defaultdict(lambda: dict(c=None, ev=[], done=None))
for r in csv.DictReader(open("pumpfun_events.csv", encoding="utf-8"), delimiter=";"):
    m, k, t = r["mint"], r["kind"], float(r["t"])
    if k == "create": coins[m]["c"] = t
    elif k == "complete": coins[m]["done"] = t
    elif k == "trade" and int(r["vtok"] or 0): coins[m]["ev"].append((t, int(r["vsol"]) / int(r["vtok"]), int(r["rsol"]) / 1e9))
born = [c for c in coins.values() if c["ev"]]
T0 = min(c["ev"][0][0] for c in born); T1 = max(c["ev"][-1][0] for c in born); MID = (T0 + T1) / 2
def sim(c, X, tp, sl, T, d, slip):
    ev = c["ev"]; i = next((k for k, e in enumerate(ev) if e[2] >= X), None)
    if i is None: return None, None
    j = next((k for k in range(i, len(ev)) if ev[k][0] >= ev[i][0] + d), None)
    if j is None: return None, None
    p0, t0 = ev[j][1] * (1 + slip), ev[j][0]
    for e in ev[j + 1:]:
        r = e[1] / p0
        if r >= 1 + tp or r <= 1 - sl or e[0] - t0 > T * 60:
            return r * (1 - slip) * (1 - FEE) ** 2 - 1, ev[i][0]
    return ev[-1][1] / p0 * (1 - slip) * (1 - FEE) ** 2 - 1, ev[i][0]  # graduated (or quiet): last curve price
for X in (60, 70, 75, 78, 80, 82):
    for tp, sl, T in ((9, 0.2, 30), (9, 0.3, 60), (0.15, 0.2, 30), (9, 0.5, 120)):
        for d, slip in ((1, 0.01), (3, 0.03)):
            a = [sim(c, X, tp, sl, T, d, slip) for c in born]; a = [x for x in a if x[0] is not None]
            if len(a) < 10: continue
            h1 = [x for x, t in a if t < MID]; h2 = [x for x, t in a if t >= MID]; v = [x for x, _ in a]
            print(f"X={X} TP={'выход с кривой' if tp > 5 else f'{tp:.0%}'} SL={sl:.0%} T={T} задержка {d} с/{slip:.0%}: n={len(v)} среднее {st.mean(v)*100:+.1f}% "
                  f"медиана {st.median(v)*100:+.1f}% в плюсе {sum(x>0 for x in v)*100//len(v)}%  половины {st.mean(h1)*100 if h1 else 0:+.1f}% / {st.mean(h2)*100 if h2 else 0:+.1f}%")
