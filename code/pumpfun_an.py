"""21.11: pump.fun bonding curve — analysis of analysis/pumpfun_events.csv (pumpfun_rec.py). Paper only.
The curve price is a formula of its reserves (price = virtual SOL / virtual tokens); nobody can block selling or pull the curve's SOL.
Questions: how many coins leave the curve (complete), how predictable is it from the SOL already on the curve, and does any simple
rule «enter when the curve first holds X SOL, exit at TP / SL / time / graduation» pay after fees (1.25 % each side, ~0.0001 SOL tx).
Usage: python pumpfun_an.py   Out: pumpfun_an.txt"""
import csv, collections, os, statistics as st, sys
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
FEE = 0.0125
coins = collections.defaultdict(lambda: dict(created=None, ev=[], done=None))
for r in csv.DictReader(open(os.path.join(HERE, "pumpfun_events.csv"), encoding="utf-8"), delimiter=";"):
    m, k, t = r["mint"], r["kind"], float(r["t"])
    if k == "create":
        coins[m]["created"] = t
    elif k == "complete":
        coins[m]["done"] = t
    elif k == "trade":
        vsol, vtok = int(r["vsol"]), int(r["vtok"])
        if vtok:
            coins[m]["ev"].append((t, r["is_buy"] == "1", vsol / vtok, int(r["rsol"]) / 1e9, int(r["sol"]) / 1e9, r["user"]))
born = {m: c for m, c in coins.items() if c["created"] and c["ev"]}
T0 = min(c["created"] for c in born.values()) if born else 0
T1 = max(c["ev"][-1][0] for c in born.values()) if born else 0
mid = (T0 + T1) / 2
q = lambda a, p: a[min(len(a) - 1, int(p * len(a)))] if a else 0
L = [f"записано: монет создано {sum(1 for c in coins.values() if c['created'])}, из них с торговлей {len(born)}, "
     f"выходов с кривой {sum(1 for c in born.values() if c['done'])}; часов {(T1 - T0) / 3600:.1f}"]
mx = sorted(max(e[3] for e in c["ev"]) for c in born.values())
for th in (1, 5, 10, 20, 40, 60, 70, 80):
    n = [c for c in born.values() if max(e[3] for e in c["ev"]) >= th]
    g = sum(1 for c in n if c["done"])
    L.append(f"  дошли до {th:>2} SOL на кривой: {len(n):5}; из них вышли с кривой {g:4} ({g * 100 // max(len(n), 1)}%)")


def sim(c, X, tp, sl, T):
    ev = c["ev"]
    i = next((k for k, e in enumerate(ev) if e[3] >= X), None)
    if i is None:
        return None
    t0, p0 = ev[i][0] + 1.0, ev[i][2] * 1.01  # we come ~1 s later, a little higher
    last = p0
    for e in ev[i + 1:]:
        if e[0] < t0:
            continue
        r = e[2] / p0
        last = e[2]
        if e[0] - t0 > T * 60 or r >= 1 + tp or r <= 1 - sl:
            return r * (1 - FEE) ** 2 - 1
    return last / p0 * (1 - FEE) ** 2 - 1  # end of the curve data (graduated or quiet): the last curve price


res = []
for X in (2, 5, 10, 20, 30, 40, 50, 60, 70, 75, 80):
    for tp in (0.1, 0.2, 0.3, 0.5, 1.0):
        for sl in (0.1, 0.2, 0.3, 0.5):
            for T in (1, 3, 10, 30):
                a, h1, h2 = [], [], []
                for c in born.values():
                    r = sim(c, X, tp, sl, T)
                    if r is None:
                        continue
                    a.append(r)
                    (h1 if c["created"] < mid else h2).append(r)
                if len(a) >= 30 and h1 and h2:
                    res.append((st.mean(a), X, tp, sl, T, len(a), sum(1 for r in a if r > 0) / len(a), st.mean(h1), st.mean(h2), st.median(a)))
res.sort(reverse=True)
L.append("\nСЕТКА (среднее за сделку | вход при X SOL на кривой | TP | SL | предел мин | сделок | в плюсе | 1-я/2-я половина | медиана):")
for r in res[:15]:
    L.append(f"  {r[0] * 100:+.1f}%  X={r[1]} TP={r[2] * 100:.0f}% SL={r[3] * 100:.0f}% T={r[4]}  n={r[5]}  плюс {r[6] * 100:.0f}%  "
             f"половины {r[7] * 100:+.1f}% / {r[8] * 100:+.1f}%  медиана {r[9] * 100:+.1f}%")
stable = [r for r in res if r[7] > 0 and r[8] > 0]
L.append(f"вариантов {len(res)}, в плюсе {sum(1 for r in res if r[0] > 0)}, в плюсе в ОБЕИХ половинах {len(stable)}; медиана варианта "
         f"{st.median(r[0] for r in res) * 100 if res else 0:+.1f}%")
out = "\n".join(L)
print(out)
open(os.path.join(HERE, "pumpfun_an.txt"), "w", encoding="utf-8").write(out)
