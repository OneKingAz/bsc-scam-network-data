"""24.11: do futures positioning extremes predict BTC 1 h / 4 h ahead? Signal = rolling-24h z-score of a metric beyond +-Z;
trade both ways (with / against the crowd), enter 6 min after the 5-min bar timestamp (bar surely closed), one position at a time.
Stable = same sign in all three thirds. Fee per round trip: taker 0.10 %, maker 0.04 %."""
import csv, statistics as st, sys, bisect, collections
sys.stdout.reconfigure(encoding="utf-8")
K = [(int(r["t"]), float(r["c"])) for r in csv.DictReader(open("btc_1m.csv"))]
KT = [k[0] for k in K]
def px(t):
    i = bisect.bisect_left(KT, t); return K[i][1] if i < len(K) else None
F = [(int(r["t"]), float(r["oi"]), float(r["top_ls"]), float(r["all_ls"]), float(r["taker_bs"])) for r in csv.DictReader(open("fut_metrics.csv"))]
feat = collections.defaultdict(list)
for i in range(12, len(F)):
    t = F[i][0]; p0, p12 = px(t), px(F[i - 12][0])
    if not p0 or not p12: continue
    feat["рост OI за час"].append((t, F[i][1] / F[i - 12][1] - 1))
    feat["лонг/шорт топ-трейдеров"].append((t, F[i][2]))
    feat["лонг/шорт всех счетов"].append((t, F[i][3]))
    feat["агрессивные покупки/продажи (5 мин)"].append((t, F[i][4]))
    feat["агрессивные покупки/продажи (час)"].append((t, st.mean(x[4] for x in F[i - 11:i + 1])))
    feat["цена за час"].append((t, p0 / p12 - 1))
    feat["OI за час при росте цены"].append((t, (F[i][1] / F[i - 12][1] - 1) * (1 if p0 > p12 else -1)))
T0, T1 = F[0][0], F[-1][0]
res = []
for name, s in feat.items():
    vals = [v for _, v in s]
    for Z in (1.5, 2.0, 2.5):
        for H in (60, 240):
            for way in (1, -1):  # 1: with the metric, -1: against
                last, h = -10**15, [[], [], []]
                for j in range(288, len(s)):
                    t, v = s[j]; win = vals[j - 288:j]; mu, sd = st.mean(win), st.pstdev(win) or 1e-12
                    z = (v - mu) / sd
                    if abs(z) < Z or t - last < H * 60_000: continue
                    a, b = px(t + 360_000), px(t + 360_000 + H * 60_000)
                    if not a or not b: continue
                    h[min(2, int((t - T0) * 3 / (T1 - T0)))].append(way * (1 if z > 0 else -1) * (b / a - 1) * 100); last = t
                al = h[0] + h[1] + h[2]
                if len(al) >= 15 and all(h): res.append((st.mean(al), name, Z, H, "по сигналу" if way > 0 else "против", len(al), [round(st.mean(x), 3) for x in h]))
res.sort(reverse=True)
print("ЛУЧШИЕ ПРАВИЛА (средний % на сделку до комиссии; трети месяца):")
for r in res[:12]: print(f"  {r[0]:+.3f}%  {r[1]} |z|≥{r[2]} {r[4]}, держать {r[3]} мин, сделок {r[5]}, трети {r[6]}")
for fee in (0.10, 0.04):
    ok = [r for r in res if all(x > fee for x in r[6])]
    print(f"после комиссии {fee}% в плюсе во ВСЕХ третях: {len(ok)} из {len(res)}" + "".join(f"\n    {r[1]} |z|≥{r[2]} {r[4]} {r[3]} мин: {r[0]-fee:+.3f}%, n={r[5]}, трети {r[6]}" for r in ok[:5]))
fr = [(int(r["t"]), float(r["rate"])) for r in csv.DictReader(open("fut_funding.csv"))]
print(f"\nплата за удержание (funding): среднее {st.mean(x for _, x in fr)*100:.4f}% за 8 ч, макс {max(x for _, x in fr)*100:.4f}%, мин {min(x for _, x in fr)*100:.4f}%")
