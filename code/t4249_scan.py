"""18.11 night: every coin of the 4249-byte template in the last H hours (factory PairCreated -> code size 4249 -> its pool):
launch, trading start, peak, drain time and how, and every buyer that is NOT one of the template's regular wallets (seen on >= 3 coins),
with the time between such an outsider buy and the drain. The question: does an outsider buy make the creator drain early?
Read-only, public nodes. Out: t4249_scan.csv, t4249_scan.txt.  Usage: python t4249_scan.py [hours=12] [code size=4249]"""
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
H = float(sys.argv[1]) if len(sys.argv) > 1 else 12
SIZE = int(sys.argv[2]) if len(sys.argv) > 2 else 4249
NODES = ["https://rpc-bsc.48.club", "https://rpc.48.club"]
WBNB = "bb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
FACTORY = "0xca143ce32fe78f1f7019d7d551a6402fc5350c73"
PAIRCREATED = "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"
SWAP = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
SYNC = "0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1"
P = lambda *a: print(*a, flush=True)
CNT = [0]


def call(m, p, tries=6):
    err = None
    for k in range(tries):
        CNT[0] += 1
        try:
            r = json.loads(urllib.request.urlopen(urllib.request.Request(NODES[(CNT[0] + k) % 2], data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": m, "params": p}).encode(),
                                                                         headers={"Content-Type": "application/json"}), timeout=40).read())
            if "error" in r:
                raise RuntimeError(str(r["error"])[:100])
            return r["result"]
        except Exception as e:
            err = e
            time.sleep(0.5 + k)
    raise RuntimeError(f"{m}: {str(err)[:80]}")


head = int(call("eth_blockNumber", []), 16)
b_from = head - int(H * 3600 / 0.45)
ranges = [(b, min(b + 399, head)) for b in range(b_from, head + 1, 400)]
with ThreadPoolExecutor(6) as ex:
    lg = [l for part in ex.map(lambda r: call("eth_getLogs", [{"address": FACTORY, "fromBlock": hex(r[0]), "toBlock": hex(r[1]), "topics": [PAIRCREATED]}]), ranges) for l in part]
cand = []
for l in lg:
    t0, t1 = "0x" + l["topics"][1][-40:], "0x" + l["topics"][2][-40:]
    t = t1 if t0[2:] == WBNB else t0 if t1[2:] == WBNB else None
    if t:
        cand.append((t, "0x" + l["data"][26:66], int(l["blockNumber"], 16), t0[2:] == WBNB))
P(f"пар с WBNB за {H:g} ч: {len(cand)}")
with ThreadPoolExecutor(8) as ex:
    sizes = list(ex.map(lambda c: (len(call("eth_getCode", [c[0], "latest"])) - 2) // 2, cand))
coins = [c for c, s in zip(cand, sizes) if s == SIZE]
P(f"монет шаблона {SIZE}: {len(coins)}")


def study(c):
    tok, pair, blk, wbnb0 = c
    b1 = min(head, blk + 2400)
    lg = []
    for b in range(blk, b1 + 1, 400):
        lg += call("eth_getLogs", [{"address": pair, "fromBlock": hex(b), "toBlock": hex(min(b + 399, b1)), "topics": [[SWAP, SYNC]]}])
    syncs, swaps = [], []
    for l in lg:
        d = l["data"][2:]
        w = [int(d[i:i + 64], 16) for i in range(0, len(d), 64)]
        bn = int(l["blockNumber"], 16)
        if l["topics"][0] == SYNC:
            syncs.append((bn, (w[0] if wbnb0 else w[1]) / 1e18, l["transactionHash"]))
        else:
            bi = (w[0] if wbnb0 else w[1]) / 1e18
            bo = (w[2] if wbnb0 else w[3]) / 1e18
            swaps.append((bn, bi > 0, bi or bo, l["transactionHash"]))
    syncs = [s for s in syncs if s[1] > 0 or syncs.index(s) > 0]
    if not syncs:
        return None
    L0 = syncs[0][0]
    drain = next((b for a, b in zip(syncs, syncs[1:]) if a[1] >= 0.5 and b[1] < 0.25 * a[1]), None)
    hs = list({s[3] for s in swaps} | ({drain[2]} if drain else set()) | {syncs[0][2]})
    txs = {}
    for h in hs:
        try:
            txs[h] = call("eth_getTransactionByHash", [h])
        except Exception:
            pass
    creator = (txs.get(syncs[0][2]) or {}).get("from", "").lower()
    return dict(token=tok, pair=pair, launch=L0, start=syncs[0][1], peak=max(s[1] for s in syncs), creator=creator,
                drain_t=(drain[0] - L0) * 0.45 if drain else None, drain_sel=((txs.get(drain[2]) or {}).get("input") or "")[2:10] if drain else "",
                drain_by_creator=((txs.get(drain[2]) or {}).get("from", "").lower() == creator) if drain else None,
                swaps=[((s[0] - L0) * 0.45, s[1], s[2], (txs.get(s[3]) or {}).get("from", "").lower()) for s in swaps])


with ThreadPoolExecutor(6) as ex:
    res = [r for r in ex.map(study, coins) if r]
seen = collections.Counter()
for r in res:
    for a in {s[3] for s in r["swaps"]}:
        seen[a] += 1
regular = {a for a, n in seen.items() if n >= 3}
rows, L = [], []
for r in sorted(res, key=lambda r: r["launch"]):
    outs = [s for s in r["swaps"] if s[1] and s[3] not in regular and s[3] != r["creator"]]
    first_trade = min((s[0] for s in r["swaps"] if s[3] != r["creator"]), default=None)
    before = [s for s in outs if r["drain_t"] is None or s[0] <= r["drain_t"]]
    gap = (r["drain_t"] - before[-1][0]) if before and r["drain_t"] is not None else None
    rows.append(dict(token=r["token"], launch_block=r["launch"], start=round(r["start"], 3), peak=round(r["peak"], 3),
                     first_trade_s=first_trade, drain_min=round(r["drain_t"] / 60, 2) if r["drain_t"] is not None else "",
                     drain_sel=r["drain_sel"], by_creator=r["drain_by_creator"], outsider_buys=len(before),
                     outsider_bnb=round(sum(s[2] for s in before), 4), last_outsider_to_drain_s=round(gap, 1) if gap is not None else ""))
with open(os.path.join(HERE, f"t{SIZE}_scan.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), delimiter=";")
    w.writeheader()
    w.writerows(rows)
dr = [x["drain_min"] for x in rows if x["drain_min"] != ""]
touched = [x for x in rows if x["outsider_buys"]]
L.append(f"шаблон {SIZE}, {H:g} ч: монет {len(rows)}, регулярных кошельков (на 3+ монетах) {len(regular)}")
L.append(f"старт BNB: {collections.Counter(x['start'] for x in rows).most_common(4)}; начало торговли, с: {sorted(x['first_trade_s'] for x in rows if x['first_trade_s'] is not None)[:5]}…")
L.append(f"слив, мин: мин {min(dr) if dr else '-'}, медиана {st.median(dr) if dr else '-'}, макс {max(dr) if dr else '-'}; без слива {len(rows) - len(dr)}; "
         f"способ {collections.Counter(x['drain_sel'] for x in rows).most_common(3)}")
L.append(f"монет с посторонними покупками до слива: {len(touched)}")
for x in touched:
    L.append(f"   {x['token']} посторонних покупок {x['outsider_buys']} ({x['outsider_bnb']} BNB), слив на {x['drain_min']} мин, последняя чужая покупка за {x['last_outsider_to_drain_s']} с до слива")
early = [x for x in rows if x["drain_min"] != "" and x["drain_min"] < 10]
L.append(f"ранние сливы (<10 мин): {len(early)}: " + ", ".join(f"{x['drain_min']} мин (чужих {x['outsider_buys']})" for x in early))
L.append(f"запросов {CNT[0]}")
P("\n".join(L))
open(os.path.join(HERE, f"t{SIZE}_scan.txt"), "w", encoding="utf-8").write("\n".join(L))
