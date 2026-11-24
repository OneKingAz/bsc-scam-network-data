"""23.11: robustness of the best pump.fun rules: entry delay 1/3/5/10 s, extra slippage, cap of the exit at TP (no jump bonus)."""
import csv, collections, statistics as st, sys
sys.stdout.reconfigure(encoding="utf-8")
FEE = 0.0125
coins = collections.defaultdict(lambda: dict(created=None, ev=[]))
for r in csv.DictReader(open("pumpfun_events.csv", encoding="utf-8"), delimiter=";"):
    m, k, t = r["mint"], r["kind"], float(r["t"])
    if k == "create":
        coins[m]["created"] = t
    elif k == "trade" and int(r["vtok"] or 0):
        coins[m]["ev"].append((t, int(r["vsol"]) / int(r["vtok"]), int(r["rsol"]) / 1e9))
born = [c for c in coins.values() if c["created"] and c["ev"]]
T0 = min(c["created"] for c in born); T1 = max(c["ev"][-1][0] for c in born); h = (T1 - T0) / 3
def sim(c, X, tp, sl, T, delay, slip, cap):
    ev = c["ev"]; i = next((k for k, e in enumerate(ev) if e[2] >= X), None)
    if i is None: return None
    t0 = ev[i][0] + delay
    j = next((k for k in range(i, len(ev)) if ev[k][0] >= t0), None)
    if j is None: return None
    p0 = ev[j][1] * (1 + slip)
    for e in ev[j + 1:]:
        r = e[1] / p0
        if e[0] - t0 > T * 60 or r >= 1 + tp or r <= 1 - sl:
            if cap and r > 1 + tp: r = 1 + tp
            return r * (1 - slip) * (1 - FEE) ** 2 - 1
    return ev[-1][1] / p0 * (1 - slip) * (1 - FEE) ** 2 - 1
print(f"монет {len(born)}, часов {(T1 - T0) / 3600:.1f}")
for X, tp, sl, T in ((5, .1, .1, 3), (2, .1, .3, 30), (10, .2, .2, 10), (20, .3, .3, 10)):
    print(f"\nПРАВИЛО: вход при {X} SOL, TP {tp*100:.0f}%, SL {sl*100:.0f}%, предел {T} мин")
    for delay in (1, 3, 5, 10):
        for slip, cap in ((0.01, False), (0.03, False), (0.03, True)):
            a = [sim(c, X, tp, sl, T, delay, slip, cap) for c in born]
            a = [x for x in a if x is not None]
            thirds = [st.mean([sim(c, X, tp, sl, T, delay, slip, cap) for c in born if T0 + k * h <= c["created"] < T0 + (k + 1) * h and sim(c, X, tp, sl, T, delay, slip, cap) is not None] or [0]) for k in range(3)]
            print(f"  задержка {delay:2} с, проскальз. {slip*100:.0f}%{', без прыжков' if cap else '            '}: n={len(a)} среднее {st.mean(a)*100:+.1f}% медиана {st.median(a)*100:+.1f}% "
                  f"в плюсе {sum(1 for x in a if x>0)*100//len(a)}%  по третям {' / '.join(f'{x*100:+.1f}%' for x in thirds)}")
