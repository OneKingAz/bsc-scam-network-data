"""08.11 (user: look for new regularities). Read-only, open nodes, no keys.
Pools with BNB created in the last H hours; their buys >= 0.01 BNB; who bought (tx sender), when after the launch, how big.
Looks for: (A) wallets that buy in many coins of one family at a steady moment after the launch (a schedule we could get ahead of);
(B) such buys funded just before by any transfer (the wallet's balance jumps by ~the buy within 10 blocks, also internal transfers).
Out: pattern_scan.csv (all buys), pattern_scan.txt (report). Usage: python pattern_scan.py [hours=3]"""
import collections
import csv
import itertools
import os
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from web3 import Web3

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
H = float(sys.argv[1]) if len(sys.argv) > 1 else 3
URLS = ["https://rpc.48.club", "https://bsc-dataseed.bnbchain.org", "https://bsc-dataseed1.defibit.io", "https://bsc-rpc.publicnode.com",
        "https://bsc-dataseed2.bnbchain.org", "https://bsc-dataseed1.ninicoin.io"]
W3S = [Web3(Web3.HTTPProvider(u, request_kwargs={"timeout": 20})) for u in URLS]
RR = itertools.count()
LOCK = threading.Lock()
SPB = 0.45
FACTORY = Web3.to_checksum_address("0xca143ce32fe78f1f7019d7d551a6402fc5350c73")
WBNB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
PC = "0x" + Web3.keccak(text="PairCreated(address,address,address,uint256)").hex().replace("0x", "")
SWAP = "0x" + Web3.keccak(text="Swap(address,uint256,uint256,uint256,uint256,address)").hex().replace("0x", "")


def call(f, tries=8):
    for k in range(tries):
        with LOCK:
            w = W3S[next(RR) % len(W3S)]
        try:
            return f(w)
        except Exception:
            time.sleep(0.5 + k * 0.7)
    return None


head = call(lambda w: w.eth.block_number)
lo = head - int(H * 3600 / SPB)
pairs = {}
for b in range(lo, head + 1, 2000):
    logs = call(lambda w: w.eth.get_logs({"address": FACTORY, "fromBlock": b, "toBlock": min(head, b + 1999), "topics": [PC]})) or []
    for l in logs:
        t0, t1 = "0x" + l["topics"][1].hex()[-40:], "0x" + l["topics"][2].hex()[-40:]
        if WBNB in (t0.lower(), t1.lower()):
            pair = Web3.to_checksum_address("0x" + bytes(l["data"])[12:32].hex())
            pairs[pair] = dict(token=Web3.to_checksum_address(t1 if t0.lower() == WBNB else t0), wb0=t0.lower() == WBNB, block=l["blockNumber"])
print(f"пулов с BNB за {H:g} ч: {len(pairs)}", flush=True)


def code_of(p):
    c = call(lambda w: bytes(w.eth.get_code(pairs[p]["token"])))
    pairs[p]["code"] = f"{len(c)}:{Web3.keccak(c).hex().replace('0x', '')[:10]}" if c else "?"


with ThreadPoolExecutor(8) as ex:
    list(ex.map(code_of, list(pairs)))
fam = collections.Counter(v["code"] for v in pairs.values())
keep = {p for p, v in pairs.items() if fam[v["code"]] >= 3 and not v["code"].startswith("45:")}  # families of 3+ coins: a pattern needs repeats; 45 bytes = proxies of a launchpad, hundreds of unrelated coins
print(f"семей от 3 монет: {sum(1 for c in fam.values() if c >= 3)}, их пулов {len(keep)}", flush=True)

buys = []
addrs = list(keep)
for i in range(0, len(addrs), 40):
    part = addrs[i:i + 40]
    print(f"  свапы: пулы {i}…{i + len(part)} из {len(addrs)}", flush=True)
    for b in range(lo, head + 1, 1000):
        logs = call(lambda w: w.eth.get_logs({"address": part, "fromBlock": b, "toBlock": min(head, b + 999), "topics": [SWAP]})) or []
        for l in logs:
            p = pairs[Web3.to_checksum_address(l["address"])]
            d = bytes(l["data"])
            a0i, a1i = int.from_bytes(d[0:32], "big"), int.from_bytes(d[32:64], "big")
            bnb = (a0i if p["wb0"] else a1i) / 1e18
            if bnb >= 0.01:
                buys.append(dict(pair=l["address"], block=l["blockNumber"], tx=l["transactionHash"].hex(), bnb=bnb, age_s=round((l["blockNumber"] - p["block"]) * SPB, 1), code=p["code"]))
print(f"покупок от 0.01 BNB: {len(buys)}", flush=True)


def sender(r):
    t = call(lambda w: w.eth.get_transaction(r["tx"]))
    r["from"] = t["from"] if t else "?"
    r["to_c"] = t["to"] if t else "?"


with ThreadPoolExecutor(10) as ex:
    list(ex.map(sender, buys))
print("отправители найдены", flush=True)

by = collections.defaultdict(list)
for r in buys:
    by[(r["code"], r["from"])].append(r)
cands = []
for (code, who), rs in by.items():
    coins = {r["pair"] for r in rs}
    if who == "?" or len(coins) < 3:
        continue
    first = {}
    for r in sorted(rs, key=lambda r: r["block"]):
        first.setdefault(r["pair"], r)
    ages = [r["age_s"] for r in first.values()]
    share = len(coins) / fam[code]
    cands.append(dict(code=code, who=who, coins=len(coins), fam=fam[code], share=round(share, 2), buys=len(rs), bnb_med=round(statistics.median(r["bnb"] for r in rs), 4),
                      age_med=round(statistics.median(ages), 1), age_iqr=round((statistics.quantiles(ages, n=4)[2] - statistics.quantiles(ages, n=4)[0]) if len(ages) >= 4 else 0, 1), rs=rs))
cands.sort(key=lambda c: (-c["share"], c["age_iqr"]))
print(f"кошельков, покупающих в 3+ монетах одной семьи: {len(cands)}", flush=True)


def funded(r):
    """The sender's balance just before the buy minus 10 blocks earlier: a jump of ~the buy = paid for it (any way, internal too)."""
    b = r["block"]
    before = call(lambda w: w.eth.get_balance(r["from"], b - 10))
    just = call(lambda w: w.eth.get_balance(r["from"], b - 1))
    r["jump"] = round(((just or 0) - (before or 0)) / 1e18, 5) if before is not None and just is not None else None


top = cands[:40]
todo = [r for c in top for r in c["rs"][:6]]
with ThreadPoolExecutor(8) as ex:
    list(ex.map(funded, todo))

with open(os.path.join(HERE, "pattern_scan.csv"), "w", newline="", encoding="utf-8-sig") as f:
    wr = csv.writer(f, delimiter=";")
    wr.writerow(["code", "pair", "block", "age_s", "bnb", "from", "to", "jump", "tx"])
    for r in buys:
        wr.writerow([r["code"], r["pair"], r["block"], r["age_s"], round(r["bnb"], 5), r["from"], r.get("to_c"), r.get("jump", ""), r["tx"]])
out = [f"pattern_scan {time.strftime('%d.%m %H:%M')}, {H:g} ч, пулов {len(pairs)}, покупок {len(buys)}", ""]
for c in top:
    js = [r["jump"] for r in c["rs"][:6] if r.get("jump") is not None]
    paid = sum(1 for r, j in ((r, r.get("jump")) for r in c["rs"][:6]) if j is not None and j >= 0.8 * r["bnb"])
    out.append(f"{c['code']:<16} {c['who']} монет {c['coins']}/{c['fam']} ({c['share']:.0%}), покупок {c['buys']}, медиана {c['bnb_med']} BNB, "
               f"первая покупка на {c['age_med']} с (разброс {c['age_iqr']} с), пополнен перед покупкой {paid}/{len(js)}")
open(os.path.join(HERE, "pattern_scan.txt"), "w", encoding="utf-8").write("\n".join(out))
print("\n".join(out))
