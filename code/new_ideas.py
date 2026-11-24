"""05.11 new strategy ideas, offline. 1) Can the next bot buy be predicted from the gaps? 2) Are bot buys visible in the public
mempool (journal_swaps wallet_source)? 3) Without passing the coin on: buy, hold H s on one wallet, sell, on untraded coins.
Paper: no reaction of the bots assumed. Run: python analysis\\new_ideas.py"""
import collections, csv, json, os, statistics as st, sys
sys.stdout.reconfigure(encoding="utf-8")
csv.field_size_limit(10**9)
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SPB, FEE, STAKE = 0.45, 0.9975, 0.005
cache = json.load(open(os.path.join(HERE, "gas_options_cache.json"), encoding="utf-8"))
done = {r["token"].lower() for r in csv.DictReader(open(os.path.join(HERE, "hop_trades.csv"), encoding="utf-8-sig"), delimiter=";")}
coins = {k: [c for t, c in cache.items() if c.get("kind") == k and c.get("buys") and c.get("pts") and t.lower() not in done] for k in (1, 10)}


def at(c, b):
    p = next((p for p in reversed(c["pts"]) if p[0] <= b), c["pts"][0])
    return p[1], p[1] / p[2]


print("=== 1. промежутки между покупками ботов, с")
for k, cs in coins.items():
    g = [(b - a) * SPB for c in cs for a, b in zip(c["buys"], c["buys"][1:])]
    q = st.quantiles(g, n=10)
    print(f"  {k} BNB: покупок {len(g) + len(cs)}, медиана {st.median(g):.0f}, 10% {q[0]:.0f}, 90% {q[8]:.0f}, мин {min(g):.0f}, макс {max(g):.0f}")
    hist = collections.Counter(int(x // 5) * 5 for x in g)
    print("    по 5 с:", [(a, n) for a, n in sorted(hist.items()) if a < 70])
    rise = []
    for c in cs:
        for b in c["buys"]:
            x, y = at(c, b - 1), at(c, b + 1)
            if x[1] and y[1]:
                rise.append(((y[0] / y[1]) / (x[0] / x[1]) - 1) * 100)
    print(f"    рост цены от одной покупки: медиана {st.median(rise):+.1f}%, 90% {st.quantiles(rise, n=10)[8]:+.1f}%")

print("\n=== 2. видны ли покупки сети в публичном мемпуле (журнал)")
guard = {r["token"].lower() for p in (os.path.join(ROOT, "old_data", "analysis", "guard_launches.csv"), os.path.join(HERE, "guard_launches.csv"))
         for r in csv.DictReader(open(p, encoding="utf-8-sig"), delimiter=";")}
cnt = collections.Counter()
for p in (os.path.join(ROOT, "old_data", "journal_swaps.csv"), os.path.join(ROOT, "journal_swaps.csv")):
    for r in csv.DictReader(open(p, encoding="utf-8-sig"), delimiter=";"):
        if r.get("token", "").lower() in guard and r.get("side") == "buy":
            cnt[r.get("wallet_source")] += 1
print("  покупки в монетах сторожа по источнику:", dict(cnt))

print("\n=== 3. без передач: покупка после k-й покупки бота, держать H с на одном кошельке, продать (ставка 0.005; только комиссия пула и ~0.00003 BNB газа)")
for kind, cs in coins.items():
    print(f"  {kind} BNB, монет {len(cs)}")
    for k in (0, 1, 2):
        row = []
        for hold in (2, 3, 5, 8, 15, 30):
            v, hit = [], 0
            for c in cs:
                if len(c["buys"]) <= k:
                    continue
                e = c["buys"][k] + 4
                x = e + hold / SPB
                if x + 2 >= c["drain"]:
                    continue
                b0, t0 = at(c, e)
                tok = STAKE * FEE * t0 / (b0 + STAKE * FEE)
                b1, t1 = at(c, x + 2)
                v.append((tok * FEE * b1 / (t1 + tok * FEE) / STAKE - 1) * 100 - 0.00003 / STAKE * 100)
                hit += any(e < b <= x for b in c["buys"])
            row.append(f"{hold}с {st.mean(v):+6.1f}% ({sum(y > 0 for y in v) * 100 // max(1, len(v))}%)")
        print(f"    k={k} " + "  ".join(row))
print("  самая ранняя метка держателя в реальных данных: 7 блоков (~3 с), часто 13–20 (~6–9 с); метка = потеря ставки")
