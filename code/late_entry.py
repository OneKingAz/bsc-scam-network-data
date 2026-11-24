"""User's idea 05.11: enter only after the bots have started buying. Offline (deal_charts.json, gas_options_cache.json):
1) in real chains, when did the other wallets' buys come after our buy; 2) on untraded coins, the best case if the bots
did NOT react to us: buy right after the k-th bot buy, sell after the hold. Gas is the real rate seen on ORCLc, leftovers on
holders included (0.000266 BNB/min). Run: python analysis\\late_entry.py"""
import json, os, statistics as st, sys
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
SPB, FEE, LAG, GAS_MIN, GAS_FIX, STAKE = 0.45, 0.9975, 7, 0.000266, 0.00006, 0.005
deals = json.load(open(os.path.join(HERE, "deal_charts.json"), encoding="utf-8"))["deals"]
print("=== реальные цепочки: через сколько секунд после нашей покупки шли чужие покупки")
gaps = []
for d in deals:
    if d["sell"] - d["buy"] < 2:
        continue
    g = [round((m - d["buy"]) * 60) for m in d["buys"] if d["buy"] < m < d["sell"]]
    gaps += g
    print(f"  {d['sym']:<13}{d['kind']:>3} BNB держали {d['sell'] - d['buy']:5.1f} мин: {g}")
print("  в первые 30 с:", sum(x <= 30 for x in gaps), "в 30–120 с:", sum(30 < x <= 120 for x in gaps), "позже:", sum(x > 120 for x in gaps))
cache = json.load(open(os.path.join(HERE, "gas_options_cache.json"), encoding="utf-8"))
traded = {d["sym"] for d in deals}
coins = [c for c in cache.values() if c.get("kind") in (1, 10) and c.get("pts") and c.get("sym") not in traded]


def at(c, blk):
    p = next((p for p in reversed(c["pts"]) if p[0] <= blk), c["pts"][0])
    return p[1], p[1] / p[2]


def sim(c, k, hold):
    if len(c["buys"]) <= k:
        return None
    e = c["buys"][k] + 4
    if e >= c["drain"]:
        return None
    b0, t0 = at(c, e)
    tok = STAKE * FEE * t0 / (b0 + STAKE * FEE)
    x = e + hold / SPB
    gas = (GAS_FIX + GAS_MIN * hold / 60) / STAKE * 100
    if x + LAG >= c["drain"]:
        return -100 - gas
    b1, t1 = at(c, x + LAG)
    return (tok * FEE * b1 / (t1 + tok * FEE) / STAKE - 1) * 100 - gas


print("\n=== если бы боты НЕ реагировали на нас (лучший случай, ставка 0.005, после газа)")
for kind in (1, 10):
    cs = [c for c in coins if c["kind"] == kind]
    print(f"  {kind} BNB, монет {len(cs)}: покупка сразу после k-й покупки бота, держать N с")
    for k in (0, 1, 2, 3, 5, 10, 15):
        row = []
        for hold in (15, 30, 60, 120, 300):
            v = [x for x in (sim(c, k, hold) for c in cs) if x is not None]
            row.append(f"{hold}с {st.mean(v):+5.0f}% ({sum(y > 0 for y in v)}/{len(v)})" if v else f"{hold}с –")
        print(f"    k={k:<3}" + "  ".join(row))
