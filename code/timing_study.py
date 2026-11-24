"""Idea 1 (05.11), offline: no hand-overs, one wallet: buy W s after a bot buy, hold at most H s, sell after the next bot buy.
How soon does the guard mark a wallet that bought and did not move? (marks joined with buys in journal_swaps; network test
buyers left out.) A mark before the sale = the whole stake lost. Paper: assumes the bots do not react to the buy.
Run: python analysis\\timing_study.py"""
import csv, json, os, statistics as st, sys
sys.stdout.reconfigure(encoding="utf-8")
csv.field_size_limit(10**9)
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SPB, FEE, STAKE, GAS, LAG = 0.45, 0.9975, 0.005, 0.00003, 2.0
TEST = ("0x8737a10d", "0x426603ba")
marks = {}
for p in (os.path.join(ROOT, "old_data", "analysis", "guard_marks.csv"), os.path.join(HERE, "guard_marks.csv")):
    for r in csv.DictReader(open(p, encoding="utf-8-sig"), delimiter=";"):
        if r["method"] == "095ea7b3" and not r["wallet"].lower().startswith(TEST):
            k = (r["token"].lower(), r["wallet"].lower())
            marks[k] = min(marks.get(k, 10**12), int(r["block"]))
buys = {}
for p in (os.path.join(ROOT, "old_data", "journal_swaps.csv"), os.path.join(ROOT, "journal_swaps.csv")):
    for r in csv.DictReader(open(p, encoding="utf-8-sig"), delimiter=";"):
        if r.get("side") == "buy" and r.get("block"):
            k = (r["token"].lower(), r["wallet"].lower())
            if k in marks:
                buys[k] = min(buys.get(k, 10**12), int(r["block"]))
delays = sorted((marks[k] - b) for k, b in buys.items() if 0 <= marks[k] - b < 2000)
print(f"покупатели, которых пометили и которых видел журнал: {len(delays)}")
if delays:
    q = lambda f: delays[int(len(delays) * f)]
    print(f"задержка метки после покупки, блоков: мин {delays[0]}, 10% {q(.1)}, 25% {q(.25)}, медиана {q(.5)}, 75% {q(.75)}; с: мин {delays[0] * SPB:.1f}, 10% {q(.1) * SPB:.1f}, медиана {q(.5) * SPB:.1f}")
risk = lambda sec: sum(d * SPB <= sec for d in delays) / max(1, len(delays))
cache = json.load(open(os.path.join(HERE, "gas_options_cache.json"), encoding="utf-8"))
done = {r["token"].lower() for r in csv.DictReader(open(os.path.join(HERE, "hop_trades.csv"), encoding="utf-8-sig"), delimiter=";")}
coins = {k: [c for t, c in cache.items() if c.get("kind") == k and c.get("buys") and c.get("pts") and t.lower() not in done] for k in (1, 10)}


def at(c, b):
    p = next((p for p in reversed(c["pts"]) if p[0] <= b), c["pts"][0])
    return p[1], p[1] / p[2]


print("\nкупить через W с после покупки бота, держать до H с, продать через 2 с после следующей покупки; ставка 0.005, газ 0.00003")
print("(без меток — только цена; с метками — вычтен риск потери ставки по задержкам выше; попытка на каждый промежуток)")
for kind, cs in coins.items():
    print(f"  {kind} BNB, монет {len(cs)}")
    for H in (3, 5, 8, 12):
        for W in (14, 17, 20, 23):
            v, hits, held = [], 0, []
            for c in cs:
                for i in range(len(c["buys"]) - 1):
                    b, nb = c["buys"][i], c["buys"][i + 1]
                    e, x = b + W / SPB, b + (W + H) / SPB
                    if e >= c["drain"] - 20:
                        continue
                    if e < nb <= x:
                        sell, hits = nb + LAG / SPB, hits + 1
                    elif nb <= e:
                        continue  # the next buy came before our entry: we would see it and not buy
                    else:
                        sell = x
                    if sell + 2 >= c["drain"]:
                        v.append(-100.0)
                        continue
                    b0, t0 = at(c, e)
                    tok = STAKE * FEE * t0 / (b0 + STAKE * FEE)
                    b1, t1 = at(c, sell)
                    held.append((sell - e) * SPB)
                    v.append((tok * FEE * b1 / (t1 + tok * FEE) / STAKE - 1) * 100 - GAS / STAKE * 100)
            if not v:
                continue
            m, hd = st.mean(v), st.mean(held) if held else H
            r = risk(hd)
            net = m * (1 - r) - 100 * r
            print(f"    H={H:<2} W={W:<2}: попыток {len(v):4}, попали {hits * 100 // len(v):2}%, без меток {m:+5.1f}%, держим ~{hd:.1f} с, риск метки {r * 100:3.0f}%, с метками {net:+6.1f}%")
