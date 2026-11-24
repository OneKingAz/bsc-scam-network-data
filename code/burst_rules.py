"""10.11 (user's outside review): what triggers the coordinated sell bursts of a family (default 2941). Read-only, bot_watch*.csv only.
Burst = a block with >= 3 sells from different wallets in one pool (drains excluded).
For every block with activity we compute the state since the previous burst (or the launch): buys, buy volume, unique buyers,
pool BNB change from swaps only, price change (pool BNB squared ratio, constant product), seconds since the last burst, the family's
token position (from the 'tokens' column, new file only). Burst moments are compared with all other moments (control):
medians, and the best single threshold per feature (how many bursts it catches and how many quiet moments it fires on).
Hypothesis A: position/volume threshold. B: one clock for all pools (bursts in different pools in the same/near blocks).
C: pool state (BNB delta / price change), absolute and relative to the pool.
Out: burst_rules.txt. Usage: python burst_rules.py [family=2941] [burst size=3]"""
import collections
import csv
import os
import statistics as st
import sys

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
FAM = sys.argv[1] if len(sys.argv) > 1 else "2941"
K = int(sys.argv[2]) if len(sys.argv) > 2 else 3
SPB = 0.45
rows = []
for fn in ("bot_watch_v1.csv", "bot_watch.csv"):
    p = os.path.join(HERE, fn)
    if os.path.exists(p):
        rows += [r for r in csv.DictReader(open(p, encoding="utf-8-sig"), delimiter=";") if FAM in r["family"]]
seen = set()
ev = []
for r in rows:
    key = (r["tx"], r["pair"])
    if key in seen:
        continue
    seen.add(key)
    r["block"], r["bnb"], r["pool"] = int(r["block"]), float(r["bnb"]), float(r["pool_after"])
    r["tok"] = float(r["tokens"]) if r.get("tokens") not in (None, "") else None
    ev.append(r)
L = []
say = lambda s: (print(s), L.append(s))
pairs = sorted({r["pair"] for r in ev})
say(f"семья {FAM}: событий {len(ev)}, пулов {len(pairs)}; залп = {K}+ продаж разных кошельков в одном блоке")

samples = []  # (is_burst, features)
bursts_all = []  # (block, pair)
for p in pairs:
    e = sorted((r for r in ev if r["pair"] == p), key=lambda r: (r["block"], int(r["idx"])))
    if not e:
        continue
    by_b = collections.defaultdict(list)
    for r in e:
        by_b[r["block"]].append(r)
    drain = next((r["block"] for r in e if r["kind"] == "слив"), None)
    start = e[0]["block"]
    base_b, base_pool = start, e[0]["pool"]
    buys = vol = 0.0
    nb = 0
    buyers = set()
    pos = 0.0
    pos_ok = True
    for b in sorted(by_b):
        if drain and b >= drain:
            break
        rs = by_b[b]
        sells = {r["sender"] for r in rs if r["kind"] == "продажа"}
        pool_before = next((x["pool"] - x["bnb"] for x in rs), None)
        f = dict(buys=nb, vol=vol, uniq=len(buyers), dpool=(pool_before - base_pool) if pool_before else 0,
                 rel=(pool_before / base_pool - 1) * 100 if pool_before and base_pool else 0,
                 price=((pool_before / base_pool) ** 2 - 1) * 100 if pool_before and base_pool else 0,
                 since=(b - base_b) * SPB, age=(b - start) * SPB, pos=pos if pos_ok else None, pool=pool_before or 0)
        is_b = len(sells) >= K
        samples.append((is_b, f))
        if is_b:
            bursts_all.append((b, p))
            base_b = b
            buys = vol = 0.0
            nb = 0
            buyers = set()
        for r in rs:
            if r["kind"] == "покупка" and r["role"] != "мы":
                nb += 1
                vol += r["bnb"]
                buyers.add(r["sender"])
            if r["role"] == "бот семьи" and r["tok"] is not None:
                pos += r["tok"]
            elif r["role"] == "бот семьи":
                pos_ok = False
        if is_b:
            base_pool = rs[-1]["pool"]

nb_ = sum(1 for s in samples if s[0])
say(f"моментов с активностью {len(samples)}, из них залпов {nb_}")
if nb_ < 10:
    say("залпов меньше 10 — вывод делать рано")
names = dict(buys="покупок с прошлого залпа", vol="объём покупок, BNB", uniq="разных покупателей", dpool="рост пула, BNB",
             rel="рост пула, %", price="рост цены, %", since="секунд с прошлого залпа", age="возраст монеты, с", pos="позиция ботов, токенов")
say("\nпризнак: медиана перед залпом | медиана в остальные моменты | лучший порог: ловит залпов / ложных срабатываний")
for k, nm in names.items():
    a = [s[1][k] for s in samples if s[0] and s[1][k] is not None]
    c = [s[1][k] for s in samples if not s[0] and s[1][k] is not None]
    if not a or not c:
        say(f"  {nm}: нет данных")
        continue
    best = None
    for th in sorted(set(a)):
        hit = sum(1 for x in a if x >= th) / len(a)
        false = sum(1 for x in c if x >= th) / len(c)
        score = hit - false
        if best is None or score > best[0]:
            best = (score, th, hit, false)
    say(f"  {nm}: {st.median(a):.2f} | {st.median(c):.2f} | от {best[1]:.2f}: {best[2] * 100:.0f}% залпов, {best[3] * 100:.0f}% тихих моментов")

# B: one clock across pools?
say("\nГипотеза B (общие часы): залпы в разных пулах, которые жили одновременно")
life = {p: (min(r["block"] for r in ev if r["pair"] == p), max(r["block"] for r in ev if r["pair"] == p)) for p in pairs}
near = total = 0
for b, p in bursts_all:
    others = [q for q in pairs if q != p and life[q][0] <= b <= life[q][1]]
    if not others:
        continue
    total += 1
    if any(abs(b - b2) <= 2 for b2, q in bursts_all if q in others):
        near += 1
if total:
    import random
    rnd = random.Random(1)
    sh = 0
    for _ in range(200):  # chance level: the same bursts moved at random inside their pool's life
        fake = [(rnd.randint(*life[p]), p) for b, p in bursts_all]
        hits = 0
        for b, p in fake:
            others = [q for q in pairs if q != p and life[q][0] <= b <= life[q][1]]
            if others and any(abs(b - b2) <= 2 for b2, q in fake if q in others):
                hits += 1
        sh += hits
    say(f"  залпов при живой соседней монете {total}; у соседа залп в пределах ±2 блоков: {near} ({near * 100 // total}%), случайно ожидалось ~{sh / 200 * 100 / total:.0f}%")
else:
    say("  одновременно живущих пулов с залпами нет")
open(os.path.join(HERE, "burst_rules.txt"), "w", encoding="utf-8").write("\n".join(L))
