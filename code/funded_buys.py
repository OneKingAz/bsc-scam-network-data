"""07.11 (user: look for analogues of our two networks). Read-only, public node only, live for N minutes.
For every buy in the pools of the big contract families: did the buyer get the BNB just before the buy (<= 10 blocks), from whom,
and was the payment about the size of the buy? A family whose buys are announced by such payments can be traded like ours.
Run: python funded_buys.py [minutes]. Out: funded_buys.csv, funded_buys.txt"""
import collections
import csv
import os
import statistics as st
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

MIN = float(sys.argv[1]) if len(sys.argv) > 1 else 20
w3 = Web3(Web3.HTTPProvider("https://rpc.48.club", request_kwargs={"timeout": 15}))
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
SPB, WBNB = 0.45, "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
FACTORY = Web3.to_checksum_address("0xca143ce32fe78f1f7019d7d551a6402fc5350c73")
ROUTERS = {"0x10ed43c718714eb63d5aa57b78b54704e256024e", "0x13f4ea83d0bd40e75c8222255bc855a974568dd4"}
T = lambda s: "0x" + Web3.keccak(text=s).hex().replace("0x", "")
PAIR, SWAP, SYNC = T("PairCreated(address,address,address,uint256)"), T("Swap(address,uint256,uint256,uint256,uint256,address)"), T("Sync(uint112,uint112)")
BACK = 60  # ~27 s; the public node keeps ~128 blocks of state
lines = []


def say(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    lines.append(s)


def call(fn, tries=3):
    for k in range(tries):
        try:
            return fn()
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(0.5 + k)


pairs = {}  # pair -> (family, token, wbnb is token0)
code_cache = {}


def add_pairs(lo, hi):
    new = []
    for b in range(lo, hi + 1, 1000):
        for lg in call(lambda: w3.eth.get_logs({"address": FACTORY, "fromBlock": b, "toBlock": min(hi, b + 999), "topics": [PAIR]})):
            t0, t1 = "0x" + lg["topics"][1].hex()[-40:], "0x" + lg["topics"][2].hex()[-40:]
            if WBNB not in (t0.lower(), t1.lower()):
                continue
            tok = Web3.to_checksum_address(t1 if t0.lower() == WBNB else t0)
            new.append((Web3.to_checksum_address("0x" + bytes(lg["data"])[12:32].hex()), tok, t0.lower() == WBNB))

    def fam(x):
        pair, tok, wb0 = x
        try:
            c = bytes(call(lambda: w3.eth.get_code(tok)))
            g = bytes(call(lambda: w3.eth.get_storage_at(tok, 6))).hex()[-40:][:8] if len(c) == 1365 else ""
            return pair, (f"{len(c)}" + (f"/{g}" if g else ""), tok, wb0)
        except Exception:
            return pair, None
    with ThreadPoolExecutor(6) as ex:
        for pair, v in ex.map(fam, new):
            if v and 100 < int(v[0].split("/")[0]) < 20000:
                pairs[pair] = v


head = w3.eth.block_number
add_pairs(head - int(2 * 3600 / SPB), head)
say(f"пулов за 2 ч: {len(pairs)}, семей {collections.Counter(v[0] for v in pairs.values()).most_common(12)}")
rows, done_blocks = [], set()
end = time.time() + MIN * 60
last = head
blk_cache = {}


def block_txs(n):
    if n not in blk_cache:
        blk_cache[n] = call(lambda: w3.eth.get_block(n, full_transactions=True))["transactions"]
        if len(blk_cache) > 400:
            blk_cache.pop(min(blk_cache))
    return blk_cache[n]


def check(ev):
    """ev = (block, pair, buyer, bnb_in, tx hash): when did the buyer get its BNB?"""
    b, pair, buyer, bnb, txh = ev
    fam_, tok, _ = pairs[pair]
    B = Web3.to_checksum_address(buyer)
    out = dict(block=b, family=fam_, token=tok, pair=pair, buyer=buyer, bnb=round(bnb, 5), funded="", lag="", payer="", paid="", ratio="", sender="")
    try:
        tx = call(lambda: w3.eth.get_transaction(txh))
        out["sender"] = tx["from"].lower()
        S = tx["from"]
        before = call(lambda: w3.eth.get_balance(S, b - 1)) / 1e18
        old = call(lambda: w3.eth.get_balance(S, b - BACK)) / 1e18
        need = bnb * 0.8
        if old >= need or before < need:
            out["funded"] = 0
            return out
        lo_, hi_ = b - BACK, b - 1  # first block where the balance reached `need`
        while hi_ - lo_ > 1:
            m = (lo_ + hi_) // 2
            if call(lambda: w3.eth.get_balance(S, m)) / 1e18 >= need:
                hi_ = m
            else:
                lo_ = m
        pay = next((t for t in block_txs(hi_) if (t["to"] or "").lower() == S.lower() and t["value"] > 0), None)
        out.update(funded=1, lag=b - hi_)
        if pay:
            out.update(payer=pay["from"].lower(), paid=round(pay["value"] / 1e18, 5), ratio=round(pay["value"] / 1e18 / bnb, 4))
    except Exception as e:
        out["funded"] = "?"
    return out


pool = ThreadPoolExecutor(8)
futs = []
say(f"слежу {MIN:g} мин")
while time.time() < end:
    try:
        h = w3.eth.block_number
        if h <= last:
            time.sleep(0.3)
            continue
        if h - last > 40:
            last = h - 40
        if (h // 200) != (last // 200):
            add_pairs(last + 1, h)
        evs = []
        for lg in call(lambda: w3.eth.get_logs({"fromBlock": last + 1, "toBlock": h, "topics": [SWAP]})):
            p = Web3.to_checksum_address(lg["address"])
            if p not in pairs:
                continue
            d = bytes(lg["data"])
            wb0 = pairs[p][2]
            bnb = int.from_bytes(d[0:32] if wb0 else d[32:64], "big") / 1e18
            if bnb >= 0.01:
                evs.append((lg["blockNumber"], p, "0x" + lg["topics"][2].hex()[-40:], bnb, lg["transactionHash"]))
        futs += [pool.submit(check, e) for e in evs]
        last = h
    except Exception as e:
        time.sleep(2)
rows = [f.result() for f in futs]
with open(os.path.join(HERE, "funded_buys.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["block"], delimiter=";")
    w.writeheader()
    w.writerows(rows)
say(f"\nпокупок от 0.01 BNB: {len(rows)}")
g = collections.defaultdict(list)
for r in rows:
    g[r["family"]].append(r)
for fam_, rs in sorted(g.items(), key=lambda kv: -len(kv[1])):
    f1 = [r for r in rs if r["funded"] == 1]
    exact = [r for r in f1 if r["ratio"] != "" and 0.98 <= r["ratio"] <= 1.05]
    payers = collections.Counter(r["payer"][:10] for r in f1 if r["payer"])
    coins = len({r["token"] for r in rs})
    say(f"\n{fam_}: монет {coins}, покупок {len(rs)}, деньги пришли за ≤{BACK} блоков до покупки: {len(f1)} ({len(f1) * 100 // max(1, len(rs))}%), из них платёж ≈ сумме покупки: {len(exact)}")
    if f1:
        say(f"  за сколько блоков: {collections.Counter(r['lag'] for r in f1).most_common(6)}; кто платил: {payers.most_common(5)}")
        say(f"  покупки медиана {st.median(r['bnb'] for r in rs):.3f} BNB; оплаченные медиана {st.median(r['bnb'] for r in f1):.3f}")
open(os.path.join(HERE, "funded_buys.txt"), "w", encoding="utf-8").write("\n".join(lines))
print("готово")
