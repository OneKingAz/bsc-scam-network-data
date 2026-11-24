"""10.11 (user: who pays the bots of the family 3553 and when). Read-only, open nodes (recent state only).
For every buy of the family's bots in the coins of the last H hours: when the bot's balance last rose before the buy (binary search on
get_balance), by how much, from whom (the tx in that block), how many blocks before the buy. Then per payer: how many buys it paid, lead time.
Out: fund3553.csv, fund3553.txt. Usage: python fund3553.py [hours=4] [min_bnb=0.05]"""
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
from web3.middleware import ExtraDataToPOAMiddleware

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
H = float(sys.argv[1]) if len(sys.argv) > 1 else 4
MINB = float(sys.argv[2]) if len(sys.argv) > 2 else 0.05
URLS = ["https://bsc-rpc.publicnode.com", "https://rpc.48.club", "https://bsc-dataseed.bnbchain.org", "https://bsc-dataseed1.defibit.io", "https://bsc-dataseed2.bnbchain.org"]
W3S = []
for u in URLS:
    w = Web3(Web3.HTTPProvider(u, request_kwargs={"timeout": 30}))
    w.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    W3S.append(w)
RR, LOCK = itertools.count(), threading.Lock()
SPB = 0.45
F = Web3.to_checksum_address("0xca143ce32fe78f1f7019d7d551a6402fc5350c73")
WB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
PC = "0x" + Web3.keccak(text="PairCreated(address,address,address,uint256)").hex().replace("0x", "")
SW = "0x" + Web3.keccak(text="Swap(address,uint256,uint256,uint256,uint256,address)").hex().replace("0x", "")


def call(f, logs=False, tries=10):
    for k in range(tries):
        with LOCK:
            i = next(RR) % (2 if logs else len(W3S))
        try:
            return f(W3S[i])
        except Exception:
            time.sleep(0.4 + k * 0.6)
    return None


head = call(lambda w: w.eth.block_number)
lo = head - int(H * 3600 / SPB)
cand = []
for b in range(lo, head + 1, 1000):
    for l in call(lambda w: w.eth.get_logs({"address": F, "fromBlock": b, "toBlock": min(head, b + 999), "topics": [PC]}), logs=True) or []:
        t0, t1 = "0x" + l["topics"][1].hex()[-40:], "0x" + l["topics"][2].hex()[-40:]
        if WB in (t0.lower(), t1.lower()):
            cand.append(dict(pair=Web3.to_checksum_address("0x" + bytes(l["data"])[12:32].hex()), token=Web3.to_checksum_address(t1 if t0.lower() == WB else t0),
                             wb0=t0.lower() == WB, block=l["blockNumber"]))


def fam(p):
    c = call(lambda w: bytes(w.eth.get_code(p["token"])))
    return bool(c) and Web3.keccak(c).hex().replace("0x", "")[:10] == "1f6f5a665a"


with ThreadPoolExecutor(6) as ex:
    coins = [p for p, ok in zip(cand, ex.map(fam, cand)) if ok]
print(f"монет семьи за {H:g} ч: {len(coins)}", flush=True)
buys = []
for c in coins:
    end = min(head, c["block"] + int(35 * 60 / SPB))
    for a in range(c["block"], end + 1, 1000):
        got = None
        for _try in range(6):  # a lost chunk would hide the big buy: try harder, and say so
            got = call(lambda w: w.eth.get_logs({"address": c["pair"], "fromBlock": a, "toBlock": min(end, a + 999), "topics": [SW]}), logs=True)
            if got is not None:
                break
            time.sleep(3)
        if got is None:
            print(f"  ! не прочитал свапы {c['pair'][:10]} блоки {a}…", flush=True)
        for l in got or []:
            d = bytes(l["data"])
            v = [int.from_bytes(d[i:i + 32], "big") for i in (0, 32, 64, 96)]
            bnb = (v[0] if c["wb0"] else v[1]) / 1e18
            if bnb >= MINB:
                buys.append(dict(pair=c["pair"], start=c["block"], block=l["blockNumber"], tx=l["transactionHash"], bnb=bnb))
print(f"покупок от {MINB} BNB: {len(buys)}", flush=True)


def study(r):
    t = call(lambda w: w.eth.get_transaction(r["tx"]))
    if not t:
        return r
    who, b = t["from"], r["block"]
    r["bot"], r["age_s"] = who.lower(), round((b - r["start"]) * SPB)
    need = t["value"]
    # the last block before the buy where the balance went up by >= half of the buy
    hi_ = b - 1
    lo_ = b - 3000
    bh = call(lambda w: w.eth.get_balance(who, hi_))
    bl = call(lambda w: w.eth.get_balance(who, lo_))
    if bh is None or bl is None:
        return r
    if bh - bl < need * 0.5:
        r["fund"] = f"не пополнялся за 3000 бл. (баланс {bl / 1e18:.3f} → {bh / 1e18:.3f})"
        return r
    while hi_ - lo_ > 1:
        mid = (lo_ + hi_) // 2
        bm = call(lambda w: w.eth.get_balance(who, mid))
        if bm is None:
            return r
        if bh - bm >= need * 0.5:
            lo_ = mid
        else:
            hi_ = mid
    blk = call(lambda w: w.eth.get_block(hi_, full_transactions=True))
    for x in blk.transactions if blk else []:
        if (x["to"] or "").lower() == who.lower() and x["value"]:
            r["payer"], r["paid"], r["lead"], r["pay_idx"] = x["from"].lower(), x["value"] / 1e18, b - hi_, x["transactionIndex"]
            break
    else:
        r["payer"], r["lead"] = "внутренний перевод (контракт)", b - hi_
    return r


with ThreadPoolExecutor(8) as ex:
    buys = list(ex.map(study, buys))
with open(os.path.join(HERE, "fund3553.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=["pair", "start", "block", "age_s", "bnb", "bot", "payer", "paid", "lead", "pay_idx", "fund", "tx"], delimiter=";", extrasaction="ignore")
    w.writeheader()
    w.writerows(buys)
pay = collections.defaultdict(list)
for r in buys:
    pay[r.get("payer") or (r.get("fund") and "не пополнялся") or "?"].append(r)
lines = [f"семья 3553 за {H:g} ч: монет {len(coins)}, покупок от {MINB} BNB {len(buys)}"]
for p, rs in sorted(pay.items(), key=lambda kv: -len(kv[1])):
    leads = [r["lead"] for r in rs if r.get("lead") is not None]
    ratio = [r["paid"] / r["bnb"] for r in rs if r.get("paid")]
    lines.append(f"{p}: покупок {len(rs)}, ботов {len({r.get('bot') for r in rs})}, BNB {sum(r['bnb'] for r in rs):.2f}"
                 + (f", платёж за {statistics.median(leads)} бл. до покупки (мин {min(leads)}, макс {max(leads)})" if leads else "")
                 + (f", платёж/покупка медиана {statistics.median(ratio):.4f}" if ratio else ""))
big = [r for r in buys if r["bnb"] >= 2]
lines.append("\nкрупные покупки (от 2 BNB):")
for r in big:
    lines.append(f"  {r['pair'][:10]} на {r.get('age_s', 0) / 60:.1f} мин {r['bnb']:.2f} BNB бот {str(r.get('bot'))[:10]}: " +
                 (f"оплачено {str(r.get('payer'))[:12]} {r.get('paid', 0):.3f} BNB за {r.get('lead')} бл." if r.get("lead") is not None else str(r.get("fund"))))
open(os.path.join(HERE, "fund3553.txt"), "w", encoding="utf-8").write("\n".join(lines))
print("\n".join(lines))
