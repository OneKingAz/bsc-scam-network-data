"""24.11: find BOTS in BTCUSDT trades (no trader ids on the exchange, so by fingerprints):
(1) one exact size repeated many times with the same side = a TWAP/iceberg bot; its rhythm (median interval, regularity);
(2) a bot that keeps buying (or selling) for hours: does the price move its way while it works, and after it stops?
(3) seconds within the minute: scheduled bots fire at :00. Paper only."""
import csv, collections, statistics as st, sys
sys.stdout.reconfigure(encoding="utf-8")
T, P, Q, M = [], [], [], []
for r in csv.DictReader(open("btc_trades.csv")):
    T.append(int(r["t"])); P.append(float(r["p"])); Q.append(r["q"]); M.append(r["buyer_maker"] == "1")
n = len(T); hrs = (T[-1] - T[0]) / 3.6e6
print(f"сделок {n:,} за {hrs:.1f} ч, BTC {P[0]:.0f} → {P[-1]:.0f}")
sec = collections.Counter((t // 1000) % 60 for t in T)
avg = n / 60
print("секунды минуты с объёмом сделок > ×1.3 от среднего: " + (", ".join(f":{s:02d} ×{c / avg:.2f}" for s, c in sorted(sec.items()) if c > 1.3 * avg) or "нет"))
# (1) fingerprints: (size, side) pairs that repeat
fp = collections.defaultdict(list)
for i in range(n):
    fp[(Q[i], M[i])].append(i)
common_sizes = {"0.00100000", "0.00200000", "0.01000000", "0.00500000", "0.10000000", "0.00010000", "0.02000000", "0.05000000", "1.00000000", "0.50000000"}
cand = []
for (q, m), idx in fp.items():
    if len(idx) < 40 or q in common_sizes or float(q) < 0.0005: continue
    gaps = [(T[b] - T[a]) / 1000 for a, b in zip(idx, idx[1:])]
    g = st.median(gaps); reg = sum(1 for x in gaps if abs(x - g) <= 0.2 * g + 0.5) / len(gaps)
    span = (T[idx[-1]] - T[idx[0]]) / 3.6e6
    cand.append((reg, q, "продаёт" if m else "покупает", len(idx), g, span, idx))
cand.sort(key=lambda c: -c[0] * min(c[3], 500))
print("\nБОТЫ ПО «ОТПЕЧАТКУ» (один и тот же нестандартный объём, одна сторона), топ-15:")
print("  регулярность | объём BTC | сторона | сделок | шаг, с | работал, ч | цена за время работы | после остановки 15 мин")
for reg, q, side, k, g, span, idx in cand[:15]:
    a, b = idx[0], idx[-1]
    during = (P[b] / P[a] - 1) * 100
    j = next((x for x in range(b, n) if T[x] >= T[b] + 900_000), n - 1)
    after = (P[j] / P[b] - 1) * 100
    print(f"  {reg:5.0%} | {float(q):.5f} | {side} | {k:5} | {g:7.1f} | {span:5.1f} | {during:+.2f}% | {after:+.3f}%")
# (2) do regular bots move the price their way? 60 s after each fill of the top regular bots vs random
print("\nСЛЕДУЕТ ЛИ ЦЕНА ЗА БОТОМ (ход за 60 с после его сделки, в сторону бота):")
for reg, q, side, k, g, span, idx in cand[:8]:
    if reg < 0.3: continue
    sgn = -1 if side == "продаёт" else 1
    mv = []
    for i in idx:
        j = next((x for x in range(i, min(n, i + 20000)) if T[x] >= T[i] + 60_000), None)
        if j: mv.append(sgn * (P[j] / P[i] - 1) * 100)
    if mv: print(f"  {float(q):.5f} {side}: {len(mv)} сделок, в среднем {st.mean(mv):+.4f}% за 60 с (комиссия maker за круг 0.04%)")
