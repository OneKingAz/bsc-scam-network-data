"""10.11 (user: who made money on the coins of the networks 3 and 4?). Read-only, open nodes.
Coins of the last H hours whose token keeps the guard of the network 3 or 4 in slot 6; every swap of their pools; per tx sender:
BNB in (buys), BNB out (sells), coins, net result; the creator of each coin (sender of the pair's first Sync) is marked.
Out: who_won34.csv, who_won34.txt. Usage: python who_won34.py [hours=24]"""
import collections
import csv
import itertools
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from web3 import Web3

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
H = float(sys.argv[1]) if len(sys.argv) > 1 else 24
URLS = ["https://bsc-rpc.publicnode.com", "https://rpc.48.club", "https://bsc-dataseed.bnbchain.org", "https://bsc-dataseed1.defibit.io"]
W3S = [Web3(Web3.HTTPProvider(u, request_kwargs={"timeout": 25})) for u in URLS]
RR, LOCK = itertools.count(), threading.Lock()
SPB = 0.45
F = Web3.to_checksum_address("0xca143ce32fe78f1f7019d7d551a6402fc5350c73")
WB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
GUARDS = {"623e7c23c7c228095f94d9bd567eae55cb87a966": 3, "a70b3f34e3ca7d4665b794ce6bb29168b1d2e40d": 4}
PC = "0x" + Web3.keccak(text="PairCreated(address,address,address,uint256)").hex().replace("0x", "")
SW = "0x" + Web3.keccak(text="Swap(address,uint256,uint256,uint256,uint256,address)").hex().replace("0x", "")
SYNC = "0x" + Web3.keccak(text="Sync(uint112,uint112)").hex().replace("0x", "")
OURS = set()
try:
    OURS = {l.split()[-1].lower() for l in open(os.path.join(HERE, "hop_wallets.txt"), encoding="utf-8") if "0x" in l}
except OSError:
    pass


def call(f, logs=False, tries=8):
    for k in range(tries):
        with LOCK:
            i = next(RR) % (1 if logs else len(W3S))
        try:
            return f(W3S[i])
        except Exception:
            time.sleep(0.5 + k * 0.8)
    return None


head = call(lambda w: w.eth.block_number)
lo = head - int(H * 3600 / SPB)
pairs = []
for b in range(lo, head + 1, 2000):
    for l in call(lambda w: w.eth.get_logs({"address": F, "fromBlock": b, "toBlock": min(head, b + 1999), "topics": [PC]}), logs=True) or []:
        t0, t1 = "0x" + l["topics"][1].hex()[-40:], "0x" + l["topics"][2].hex()[-40:]
        if WB in (t0.lower(), t1.lower()):
            pairs.append(dict(pair=Web3.to_checksum_address("0x" + bytes(l["data"])[12:32].hex()), token=Web3.to_checksum_address(t1 if t0.lower() == WB else t0),
                              wb0=t0.lower() == WB, block=l["blockNumber"]))
print(f"пулов с BNB за {H:g} ч: {len(pairs)}", flush=True)


def net_of(p):
    s = call(lambda w: w.eth.get_storage_at(p["token"], 6))
    return GUARDS.get(s.hex()[-40:].lower()) if s is not None else None


with ThreadPoolExecutor(8) as ex:
    nets = list(ex.map(net_of, pairs))
coins = [dict(p, net=n) for p, n in zip(pairs, nets) if n]
print(f"монет сети 3: {sum(c['net'] == 3 for c in coins)}, сети 4: {sum(c['net'] == 4 for c in coins)}", flush=True)

rows = []


def swaps(c):
    end = min(head, c["block"] + int(45 * 60 / SPB))
    out = []
    for a in range(c["block"], end + 1, 2000):
        for l in call(lambda w: w.eth.get_logs({"address": c["pair"], "fromBlock": a, "toBlock": min(end, a + 1999), "topics": [SW]}), logs=True) or []:
            d = bytes(l["data"])
            v = [int.from_bytes(d[i:i + 32], "big") for i in (0, 32, 64, 96)]
            bin_ = (v[0] if c["wb0"] else v[1]) / 1e18
            bout = (v[2] if c["wb0"] else v[3]) / 1e18
            out.append(dict(net=c["net"], pair=c["pair"], block=l["blockNumber"], age=round((l["blockNumber"] - c["block"]) * SPB), tx=l["transactionHash"], bin=bin_, bout=bout))
    sy = call(lambda w: w.eth.get_logs({"address": c["pair"], "fromBlock": c["block"], "toBlock": c["block"] + 5, "topics": [SYNC]}), logs=True) or []
    if sy:
        t = call(lambda w: w.eth.get_transaction(sy[0]["transactionHash"]))
        c["creator"] = t["from"].lower() if t else ""
    return out


with ThreadPoolExecutor(4) as ex:
    for o in ex.map(swaps, coins):
        rows += o
print(f"свапов: {len(rows)}", flush=True)


def sender(r):
    t = call(lambda w: w.eth.get_transaction(r["tx"]))
    r["from"] = t["from"].lower() if t else "?"


with ThreadPoolExecutor(10) as ex:
    list(ex.map(sender, rows))
creators = {c.get("creator") for c in coins if c.get("creator")}
by = collections.defaultdict(lambda: dict(bin=0.0, bout=0.0, coins=set(), buys=0, sells=0, nets=set(), ages=[]))
for r in rows:
    x = by[r["from"]]
    x["bin"] += r["bin"]
    x["bout"] += r["bout"]
    x["coins"].add(r["pair"])
    x["nets"].add(r["net"])
    if r["bin"]:
        x["buys"] += 1
        x["ages"].append(r["age"])
    else:
        x["sells"] += 1
res = []
for a, x in by.items():
    kind = "мы" if a in OURS else "создатель" if a in creators else ""
    res.append(dict(wallet=a, kind=kind, nets="/".join(map(str, sorted(x["nets"]))), coins=len(x["coins"]), buys=x["buys"], sells=x["sells"],
                    bnb_in=round(x["bin"], 4), bnb_out=round(x["bout"], 4), net_bnb=round(x["bout"] - x["bin"], 4),
                    first_buy_s=min(x["ages"]) if x["ages"] else "", med_buy_s=sorted(x["ages"])[len(x["ages"]) // 2] if x["ages"] else ""))
res.sort(key=lambda r: -r["net_bnb"])
with open(os.path.join(HERE, "who_won34.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(res[0]), delimiter=";")
    w.writeheader()
    w.writerows(res)
lines = [f"сети 3/4 за {H:g} ч: монет {len(coins)}, свапов {len(rows)}, кошельков {len(res)}"]
grp = collections.defaultdict(lambda: [0, 0.0])
for r in res:
    k = r["kind"] or ("продаёт" if r["sells"] else "только покупает")
    grp[k][0] += 1
    grp[k][1] += r["net_bnb"]
lines.append("по группам: " + "; ".join(f"{k}: {n} кош., итог {v:+.3f} BNB" for k, (n, v) in grp.items()))
lines.append("\nв плюсе (без создателей):")
for r in [r for r in res if r["net_bnb"] > 0 and r["kind"] != "создатель"][:25]:
    lines.append(f"  {r['wallet']} {r['kind'] or ''} сети {r['nets']}, монет {r['coins']}, покупок {r['buys']}, продаж {r['sells']}, вложил {r['bnb_in']}, вывел {r['bnb_out']}, итог {r['net_bnb']:+} BNB, первая покупка на {r['first_buy_s']} с")
lines.append("\nв минусе сильнее всех:")
for r in sorted(res, key=lambda r: r["net_bnb"])[:10]:
    lines.append(f"  {r['wallet']} {r['kind']} сети {r['nets']}, монет {r['coins']}, покупок {r['buys']}, продаж {r['sells']}, итог {r['net_bnb']:+} BNB")
open(os.path.join(HERE, "who_won34.txt"), "w", encoding="utf-8").write("\n".join(lines))
print("\n".join(lines))
