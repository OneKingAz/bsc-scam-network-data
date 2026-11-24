import csv, json, sys, statistics as st
from collections import defaultdict
D = r"D:\bot"
tr = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8")]
sig = {}
for x in csv.DictReader(open(D + r"\journal_signals.csv", encoding="utf-8-sig"), delimiter=";"):
    sig.setdefault(x["token"].lower(), x)
rug = {}
for x in csv.DictReader(open(D + r"\rug_mempool.csv", encoding="utf-8-sig"), delimiter=";"):
    rug.setdefault(x["token"].lower(), x)
def sim(path, delay=30, tp=200, sl=50, hold=300):
    e = next(((t, p) for t, p in path if t >= delay and p > -90), None)
    if e is None or e[1] < -10: return None
    base = 1 + e[1] / 100
    q = [(t - e[0], ((1 + p / 100) / base - 1) * 100) for t, p in path if t >= e[0]]
    for i, (t, pct) in enumerate(q):
        nxt = q[min(i + 1, len(q) - 1)][1]
        if pct >= tp or pct <= -sl or t >= hold: return nxt
    return q[-1][1]
STRATS = {"снайп 3с/держ 15с": dict(delay=3, tp=100, sl=50, hold=15),
          "3с/держ 30с": dict(delay=3, tp=100, sl=50, hold=30),
          "30с TP200": dict(delay=30, tp=200, sl=50, hold=300),
          "150с/90с TP100 SL30": dict(delay=150, tp=100, sl=30, hold=90)}
g = defaultdict(list)
for r in tr:
    s = sig.get(r["token"]); 
    if not s: continue
    f = json.loads(s["features"]); path = json.loads(s["path"])
    rg = rug.get(r["token"])
    key = r["funder"][:10] if r["funder"] else "нет кассы"
    g[key].append(dict(bnb=r["bnb"], pool=f.get("bnb_in_pool"), safe=s["safe"], path=path,
                       held=float(rg["held_s"]) if rg and rg["held_s"] else None, rug=bool(rg), hour=s["time"][11:13]))
print("касса | n | ден.BNB | пул | изъят | held_s медиана [мин-макс] | " + " | ".join(STRATS))
for k, v in sorted(g.items(), key=lambda kv: -len(kv[1])):
    hs = [x["held"] for x in v if x["held"] is not None]
    cols = []
    for p in STRATS.values():
        res = [y for y in (sim(x["path"], **p) for x in v) if y is not None]
        cols.append(f"{st.mean(res):+.0f}% n{len(res)}" if res else "-")
    bnbs = sorted({round(x["bnb"], 2) for x in v})
    pools = [x["pool"] for x in v if x["pool"]]
    print(f"{k} | {len(v)} | {bnbs[:3]} | {st.median(pools) if pools else '-'} | {sum(x['rug'] for x in v)} | "
          f"{(f'{st.median(hs):.0f} [{min(hs):.0f}-{max(hs):.0f}]' if hs else '-')} | " + " | ".join(cols))
