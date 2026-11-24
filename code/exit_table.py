"""TZ stage 2: real exit rules on all cached 1- and 10-BNB coins (gas_options_cache.json), offline. Unlike gas_options.py:
the entry is the REAL buy moment (GMEc/XTEN: 35-36 s after the pool, the bots' first buy can come before it), the target
tp is on, the pool-drop exit is on, the sale lands SELL_LAG blocks after the signal (hand-over to the seller + swap), and
our own stake moves the pool (constant product, 0.25% fee both ways). Token tax: none seen on GMEc/XTEN (gmec_check.py).
Out: exit_table.json (read by hop_trader.best_hold) and a table. Run: python analysis\\exit_table.py"""
import json, os, statistics as st, sys
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
SPB, FEE, SELL_LAG, SILENCE = 0.45, 0.9975, 7, 50
BOT_MIN, BACKSTOP = {1: 7, 10: 24}, {1: 25 * 60, 10: 30 * 60}
G_MIN, G_FIX = 0.000145, 0.00006
ENTRIES, TARGETS, STAKES = (20, 25, 30, 36, 45, 60), (50, 100, 150, 200, 300, None), (0.001, 0.002, 0.005, 0.01)
REAL_ENTRY = 36
cache = json.load(open(os.path.join(HERE, "gas_options_cache.json"), encoding="utf-8"))
coins = [c for c in cache.values() if c.get("kind") in (1, 10) and c.get("pts")]


def at(c, blk):
    """(BNB, tokens) of the pool at this block."""
    p = next((p for p in reversed(c["pts"]) if p[0] <= blk), c["pts"][0])
    return p[1], p[1] / p[2]


def sim(c, entry, tp, stake, lag=SELL_LAG):
    """(gross % after fee and own impact, minutes held, how it ended) or None if the entry falls after the dump."""
    e = c["t0"] + entry / SPB
    if e >= c["drain"]:
        return None
    b0, t0 = at(c, e)
    tok = stake * FEE * t0 / (b0 + stake * FEE)
    value = lambda blk: (lambda b, t: tok * FEE * b / (t + tok * FEE))(*at(c, blk))
    bot, buys, kind = None, c["buys"], c["kind"]
    for k in range(BOT_MIN[kind] - 1, len(buys)):
        q = (buys[k + 1] if k + 1 < len(buys) else c["drain"]) - buys[k]
        if q * SPB >= SILENCE and buys[k] + SILENCE / SPB > e + 60 / SPB:
            bot = buys[k] + SILENCE / SPB
            break
    limit = c["t0"] + BACKSTOP[kind] / SPB
    x, why = (bot, "боты") if bot and bot < limit else (limit, "предел")
    peak = b0
    for p in c["pts"]:  # tp and the pool-drop exit, seen at the pool's own moves
        if p[0] <= e:
            continue
        if p[0] >= x:
            break
        peak = max(peak, p[1])
        if tp is not None and value(p[0]) >= stake * (1 + tp / 100):
            x, why = p[0], "цель"
            break
        if p[1] < peak * 0.75:
            x, why = p[0], "падение"
            break
    mins = (min(x, c["drain"]) - e) * SPB / 60
    if x + lag >= c["drain"]:
        return -100.0, mins, "слив раньше"
    return (value(x + lag) / stake - 1) * 100, mins, why


table = {}
print(f"монет: 1 BNB {sum(c['kind'] == 1 for c in coins)}, 10 BNB {sum(c['kind'] == 10 for c in coins)}")
for kind in (1, 10):
    cs = [c for c in coins if c["kind"] == kind]
    print(f"\n=== {kind} BNB: момент входа, цель +100%, ставка 0.005, до газа")
    for en in ENTRIES:
        r = [x for x in (sim(c, en, 100, 0.005) for c in cs) if x]
        print(f"  вход {en:2} с: среднее {st.mean(x[0] for x in r):+6.0f}%  медиана {st.median(x[0] for x in r):+6.0f}%  "
              f"в плюсе {sum(x[0] > 0 for x in r)}/{len(r)}  сливов {sum(x[2] == 'слив раньше' for x in r)}")
    print(f"=== {kind} BNB: цели при входе {REAL_ENTRY} с (после газа, % ставки; в скобках в плюсе)")
    print("  цель".ljust(9) + "до газа ср/мед   мин  " + "".join(f"{s:>16}" for s in STAKES) + "   концовки")
    table[str(kind)] = {}
    for tp in TARGETS:
        r0 = [x for x in (sim(c, REAL_ENTRY, tp, 0.005) for c in cs) if x]
        row = []
        for s in STAKES:
            v = [x[0] - (G_FIX + G_MIN * x[1]) / s * 100 for x in (sim(c, REAL_ENTRY, tp, s) for c in cs) if x]
            row.append(f"{st.mean(v):+8.0f}% ({sum(y > 0 for y in v):2}/{len(v)})")
        ends = {}
        for x in r0:
            ends[x[2]] = ends.get(x[2], 0) + 1
        g, m = st.mean(x[0] for x in r0), st.mean(x[1] for x in r0)
        print(f"  {'нет' if tp is None else '+%d' % tp:<7}{g:+6.0f}/{st.median(x[0] for x in r0):+5.0f}%  {m:5.1f}  " + "".join(row) + f"   {ends}")
        table[str(kind)][str(tp)] = dict(gross=round(g, 1), median=round(st.median(x[0] for x in r0), 1), minutes=round(m, 2), n=len(r0))
    print("  по дням, цель +100%, ставка 0.005, после газа:")
    days = {}
    for c in cs:
        days.setdefault(c["time"][:5], []).append(c)
    for d, dc in sorted(days.items()):
        v = [x[0] - (G_FIX + G_MIN * x[1]) / 0.005 * 100 for x in (sim(c, REAL_ENTRY, 100, 0.005) for c in dc) if x]
        if v:
            print(f"    {d}: {len(v):2} монет  среднее {st.mean(v):+6.0f}%  в плюсе {sum(y > 0 for y in v)}/{len(v)}")
tmp = os.path.join(HERE, "exit_table.json.tmp")
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(dict(entry=REAL_ENTRY, table=table), f, ensure_ascii=False, indent=0)
os.replace(tmp, os.path.join(HERE, "exit_table.json"))
