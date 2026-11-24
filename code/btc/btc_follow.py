"""24.11: is following a fingerprint bot better than following ANY aggressive trade? We enter 1 s after the bot's fill (next trade price),
exit 60 s later. Halves of the day separately. Baseline: all taker buys / sells (sampled)."""
import csv, statistics as st, sys, bisect
sys.stdout.reconfigure(encoding="utf-8")
T, P, Q, M = [], [], [], []
for r in csv.DictReader(open("btc_trades.csv")):
    T.append(int(r["t"])); P.append(float(r["p"])); Q.append(r["q"]); M.append(r["buyer_maker"] == "1")
n = len(T); mid = (T[0] + T[-1]) / 2
def fwd(i, d=1000, h=60000):
    a = bisect.bisect_left(T, T[i] + d); b = bisect.bisect_left(T, T[i] + d + h)
    if b >= n: return None
    return (P[b] / P[a] - 1) * 100
def run(name, idx, sgn):
    last = -10**12; h = [[], []]
    for i in idx:
        if T[i] - last < 60000: continue  # one position at a time
        x = fwd(i)
        if x is None: continue
        h[T[i] >= mid].append(sgn * x); last = T[i]
    a = h[0] + h[1]
    print(f"  {name}: позиций {len(a)}, в среднем {st.mean(a):+.4f}%, половины {st.mean(h[0]) if h[0] else 0:+.4f}% / {st.mean(h[1]) if h[1] else 0:+.4f}%, в плюсе {sum(x>0 for x in a)*100//max(len(a),1)}%")
print("вход через 1 с после сделки бота, выход через 60 с; комиссия maker за круг 0.04%, taker 0.10%")
for q, m, nm in (("0.01400000", False, "покупки 0.014"), ("0.01200000", False, "покупки 0.012"), ("0.03200000", True, "продажи 0.032"), ("0.01300000", False, "покупки 0.013")):
    run(nm, [i for i in range(n) if Q[i] == q and M[i] == m], -1 if m else 1)
run("ЛЮБАЯ агрессивная покупка (база)", [i for i in range(0, n, 7) if not M[i]], 1)
run("ЛЮБАЯ агрессивная продажа (база)", [i for i in range(0, n, 7) if M[i]], -1)
