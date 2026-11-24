"""18.11 night (user): deep study of the families where outsiders win (family_study.txt): the 4249-byte template (many hashes, one
template), 1526 (63ad5291cd), 2046 (758b9e30ae), 2966 (36a1210ec5), 3557 (2b02b533df). Read-only, public nodes.
For up to N coins of each: price path (Sync reserves), launch, peak, drain (time, how, by whom), every swap with its tx sender, block
offset from the launch and position in the block. Then:
  STRATEGY GRID  buy at E s after the launch, sell at X s (or before the drain), stake S BNB, Pancake fee 0.25 % each way, price impact
                 by the constant product; a drain before X = the stake is lost (sold into the empty pool).
  WINNERS        wallets in plus across coins: coins, total, median entry and hold, launch-block buys, gas price.
Out: family_deep.txt, family_deep.json, family_deep_swaps.csv.
Usage: python family_deep.py [coins per family=12]"""
import collections
import csv
import json
import os
import statistics as st
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
N = int(sys.argv[1]) if len(sys.argv) > 1 else 12
NODES = ["https://rpc-bsc.48.club", "https://rpc.48.club", "https://bsc-dataseed.bnbchain.org", "https://bsc-rpc.publicnode.com"]
WBNB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
SWAP = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
SYNC = "0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1"
BT = 0.45  # s per block
P = lambda *a: print(*a, flush=True)


def call(m, p, tries=6):
    err = None
    for k in range(tries):
        u = NODES[(k + hash(str(p)) ) % len(NODES)]
        try:
            r = json.loads(urllib.request.urlopen(urllib.request.Request(u, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": m, "params": p}).encode(),
                                                                         headers={"Content-Type": "application/json"}), timeout=40).read())
            if "error" in r:
                raise RuntimeError(str(r["error"])[:120])
            return r["result"]
        except Exception as e:
            err = e
            time.sleep(0.4 + k)
    raise RuntimeError(f"{m}: {str(err)[:100]}")


def logs(addr, topics, b0, b1):
    out, b = [], b0
    while b <= b1:
        e = min(b + 399, b1)
        out += call("eth_getLogs", [{"address": addr, "fromBlock": hex(b), "toBlock": hex(e), "topics": topics}]) or []
        b = e + 1
    return out


HEAD = int(call("eth_blockNumber", []), 16)
ev = [r for r in csv.DictReader((l.replace("\x00", "") for l in open(os.path.join(HERE, "net_finder_events.csv"), encoding="utf-8-sig", errors="replace")), delimiter=";")
      if (r.get("t") or "").isdigit() and (r.get("block") or "").isdigit() and (r.get("token") or "").startswith("0x") and r.get("hash")]
FAM = {"T4249": lambda r: r["size"] == "4249", "1526": lambda r: r["hash"] == "63ad5291cd", "2046": lambda r: r["hash"] == "758b9e30ae",
       "2966": lambda r: r["hash"] == "36a1210ec5", "3557": lambda r: r["hash"] == "2b02b533df"}
want = sys.argv[2:] or list(FAM)


def coin(tok, pair, first_seen):
    t0 = call("eth_call", [{"to": pair, "data": "0x0dfe1681"}, "latest"])
    wbnb0 = ("0x" + t0[-40:]).lower() == WBNB
    b0 = first_seen - 2000  # the finder may see a pool some minutes after its launch; the first Sync = the launch
    b1 = min(HEAD, b0 + 11000)  # ~82 min
    lg = logs(pair, [[SWAP, SYNC]], b0, b1)
    syncs, swaps = [], []
    for l in lg:
        d = l["data"][2:]
        w = [int(d[i:i + 64], 16) for i in range(0, len(d), 64)]
        blk, idx = int(l["blockNumber"], 16), int(l["transactionIndex"], 16)
        if l["topics"][0] == SYNC:
            rb, rt = (w[0], w[1]) if wbnb0 else (w[1], w[0])
            syncs.append((blk, idx, rb / 1e18, rt, l["transactionHash"]))
        else:
            a0i, a1i, a0o, a1o = w
            bin_, bout = ((a0i, a0o) if wbnb0 else (a1i, a1o))
            tin, tout = ((a1i, a1o) if wbnb0 else (a0i, a0o))
            swaps.append(dict(blk=blk, idx=idx, buy=bin_ > 0, bnb=(bin_ or bout) / 1e18, tok=tin or tout, tx=l["transactionHash"]))
    syncs = [s for s in syncs if s[3] > 0]
    if not syncs:
        return None
    launch = syncs[0]
    L0 = launch[0]
    drain = None
    for a, b in zip(syncs, syncs[1:]):
        if a[2] >= 0.1 and b[2] < 0.25 * a[2]:
            drain = b
            break
    peak = max(syncs[: syncs.index(drain)] if drain else syncs, key=lambda s: s[2])
    # tx senders
    hs = list({s["tx"] for s in swaps} | ({drain[4]} if drain else set()) | {launch[4]})
    with ThreadPoolExecutor(8) as ex:
        txs = dict(zip(hs, ex.map(lambda h: call("eth_getTransactionByHash", [h]), hs)))
    creator = txs[launch[4]]["from"].lower()
    for s in swaps:
        t = txs.get(s["tx"]) or {}
        s["from"] = (t.get("from") or "").lower()
        s["via"] = (t.get("to") or "").lower()
        s["gwei"] = int(t.get("gasPrice", "0x0"), 16) / 1e9
        s["t"] = (s["blk"] - L0) * BT
    dr = None
    if drain:
        t = txs[drain[4]]
        dr = dict(t=(drain[0] - L0) * BT, by=t["from"].lower(), creator=t["from"].lower() == creator, sel=t["input"][2:10], from_bnb=round(syncs[syncs.index(drain) - 1][2], 3))
    path = [((s[0] - L0) * BT, s[2], s[3]) for s in syncs]  # t, BNB reserve, token reserve
    return dict(token=tok, pair=pair, launch_block=L0, creator=creator, start=launch[2], peak=peak[2], peak_t=(peak[0] - L0) * BT, drain=dr, path=path, swaps=swaps,
                end_t=(b1 - L0) * BT)


def at(path, t):
    """reserves just after time t (last sync at or before t)"""
    r = None
    for p in path:
        if p[0] <= t:
            r = p
        else:
            break
    return r


def trade(path, drain_t, e, x, stake):
    """buy stake BNB at e s, sell all at x s; constant product with 0.25 % fee; drain before the sell -> -100 %."""
    a = at(path, e)
    if not a or a[1] <= 0.05:
        return None
    rb, rt = a[1], a[2]
    got = rt * stake * 0.9975 / (rb + stake * 0.9975)
    if drain_t is not None and drain_t <= x + 1:
        return -1.0
    b = at(path, x)
    if not b or b[1] <= 0.05:
        return -1.0
    rb2, rt2 = b[1] + stake, b[2] - got  # our buy is still in the pool
    out = rb2 * got * 0.9975 / (rt2 + got * 0.9975)
    return out / stake - 1


R = {}
for fam in want:
    rows = [r for r in ev if FAM[fam](r)]
    first = {}
    for r in rows:
        first[r["token"]] = min(first.get(r["token"], 10 ** 12), int(r["block"]))
    pairs = {r["token"]: r["pair"] for r in rows}
    toks = sorted((t for t in first if HEAD - first[t] > 9000), key=lambda t: -first[t])[:N]  # complete lives, newest first
    P(f"{fam}: монет в поиске {len(first)}, беру {len(toks)}")
    out = []
    for t in toks:
        try:
            c = coin(t, pairs[t], first[t])
            if c:
                out.append(c)
                P(f"  {t} старт {c['start']:.2f} пик {c['peak']:.2f} на {c['peak_t'] / 60:.1f} мин, слив " +
                  (f"{c['drain']['t'] / 60:.1f} мин ({'создатель' if c['drain']['creator'] else c['drain']['by'][:10]}, {c['drain']['sel']})" if c["drain"] else "нет") +
                  f", свапов {len(c['swaps'])}")
        except Exception as e:
            P(f"  {t} ошибка {str(e)[:100]}")
    R[fam] = out

with open(os.path.join(HERE, "family_deep_swaps.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f, delimiter=";")
    w.writerow(["family", "token", "t_s", "block", "idx", "side", "bnb", "from", "via", "gwei", "creator"])
    for fam, cs in R.items():
        for c in cs:
            for s in c["swaps"]:
                w.writerow([fam, c["token"], round(s["t"], 1), s["blk"], s["idx"], "buy" if s["buy"] else "sell", round(s["bnb"], 6), s["from"], s["via"], s["gwei"],
                            int(s["from"] == c["creator"])])

L = []
for fam, cs in R.items():
    if not cs:
        continue
    L.append(f"\n################ {fam}: {len(cs)} монет ################")
    dts = [c["drain"]["t"] / 60 for c in cs if c["drain"]]
    pts = [c["peak_t"] / 60 for c in cs]
    L.append(f"старт BNB: {sorted(round(c['start'], 2) for c in cs)}")
    L.append(f"пик: x{st.median(c['peak'] / c['start'] for c in cs):.2f} от старта (BNB), на мин: {sorted(round(p, 1) for p in pts)}")
    L.append(f"слив, мин: {sorted(round(d, 1) for d in dts)} (без слива в окне: {sum(1 for c in cs if not c['drain'])})")
    hows = collections.Counter((('создатель' if c['drain']['creator'] else 'другой'), c['drain']['sel']) for c in cs if c['drain'])
    L.append(f"как: {dict(hows)}")
    # price path: median price ratio vs launch by minute
    curve = []
    for m in [0.25, 0.5, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 15, 20, 25, 30, 40, 50]:
        v = []
        for c in cs:
            if c["drain"] and c["drain"]["t"] <= m * 60:
                v.append(0.0)
                continue
            a, b = at(c["path"], 0.1), at(c["path"], m * 60)
            if a and b and b[2] > 0:
                v.append((b[1] / b[2]) / (a[1] / a[2]))
        if v:
            curve.append(f"{m}м x{st.median(v):.2f}")
    L.append("цена (медиана, к цене старта; 0 = слит): " + ", ".join(curve))
    # strategy grid
    L.append("СТРАТЕГИЯ (ставка 0.05 BNB): вход E с → выход X с: среднее % | медиана % | в плюсе/всего | обнулений")
    best = []
    for e in [3, 10, 20, 30, 60, 120, 180, 300]:
        for x in [60, 120, 180, 240, 300, 360, 420, 480, 540, 600, 900, 1200, 1500, 1800]:
            if x <= e:
                continue
            res = [trade(c["path"], c["drain"]["t"] if c["drain"] else None, e, x, 0.05) for c in cs]
            res = [r for r in res if r is not None]
            if len(res) < max(3, len(cs) // 2):
                continue
            best.append((st.mean(res), e, x, st.median(res), sum(r > 0 for r in res), len(res), sum(r <= -0.99 for r in res)))
    for m, e, x, md, win, n, zero in sorted(best, reverse=True)[:12]:
        L.append(f"  {e:>4} → {x:>5}: {m * 100:+7.1f} | {md * 100:+7.1f} | {win}/{n} | {zero}")
    # «sell N s before the drain» needs knowing the drain: how regular is it relative to the peak / to the launch?
    if len(dts) >= 3:
        L.append(f"разброс слива: мин {min(dts):.1f}, медиана {st.median(dts):.1f}, макс {max(dts):.1f} мин")
    # winners across coins
    W = collections.defaultdict(lambda: dict(coins=set(), pnl=0.0, entries=[], holds=[], first_block=0, gwei=[], buys=0, sells=0))
    for c in cs:
        per = collections.defaultdict(lambda: [0.0, 0.0, None, None, 0])
        for s in c["swaps"]:
            if not s["from"] or s["from"] == c["creator"]:
                continue
            p = per[s["from"]]
            if s["buy"]:
                p[0] += s["bnb"]
                p[2] = s["t"] if p[2] is None else p[2]
                p[4] += s["blk"] == c["launch_block"]
                W[s["from"]]["gwei"].append(s["gwei"])
                W[s["from"]]["buys"] += 1
            else:
                p[1] += s["bnb"]
                p[3] = s["t"]
                W[s["from"]]["sells"] += 1
        for a, p in per.items():
            w = W[a]
            w["coins"].add(c["token"])
            w["pnl"] += p[1] - p[0]
            if p[2] is not None:
                w["entries"].append(p[2])
            if p[2] is not None and p[3] is not None:
                w["holds"].append(p[3] - p[2])
            w["first_block"] += p[4]
    top = sorted(W.items(), key=lambda kv: -kv[1]["pnl"])[:12]
    L.append("ПОБЕДИТЕЛИ: кошелёк | монет | итог BNB | вход, с (медиана) | держит, с | покупок в блоке запуска | газ gwei | покупок/продаж")
    for a, w in top:
        L.append(f"  {a} | {len(w['coins'])} | {w['pnl']:+.3f} | {st.median(w['entries']) if w['entries'] else '-':} | "
                 f"{round(st.median(w['holds'])) if w['holds'] else '-'} | {w['first_block']} | {round(st.median(w['gwei']), 3) if w['gwei'] else '-'} | {w['buys']}/{w['sells']}")
    tot_out = sum(w["pnl"] for w in W.values())
    L.append(f"все посторонние вместе: {tot_out:+.2f} BNB; кошельков {len(W)}, в плюсе {sum(w['pnl'] > 0 for w in W.values())}")
    vias = collections.Counter(s["via"] for c in cs for s in c["swaps"])
    L.append(f"через что: {vias.most_common(5)}")
    crs = [c["creator"] for c in cs]
    L.append(f"создатели: разных {len(set(crs))} из {len(crs)}")
open(os.path.join(HERE, "family_deep.txt"), "w", encoding="utf-8").write("\n".join(L))
json.dump({f: [{k: v for k, v in c.items() if k not in ("swaps",)} for c in cs] for f, cs in R.items()},
          open(os.path.join(HERE, "family_deep.json"), "w", encoding="utf-8"), ensure_ascii=False, default=str)
print("\n".join(L))
