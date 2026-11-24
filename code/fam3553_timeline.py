"""10.11 (user: study the behaviour of the family 3553, something was missed, maybe the bots). Read-only, open nodes.
For each coin of the family in the last H hours: every swap and liquidity change with its second after the launch, BNB, sender and its
role (creator / family bot = buys in 3+ coins / outsider / us), the drain (method, sender, second) and what happened just before it.
Out: fam3553_timeline.txt. Usage: python fam3553_timeline.py [hours=8]"""
import collections
import itertools
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from web3 import Web3

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
H = float(sys.argv[1]) if len(sys.argv) > 1 else 8
URLS = ["https://bsc-rpc.publicnode.com", "https://rpc.48.club", "https://bsc-dataseed.bnbchain.org", "https://bsc-dataseed1.defibit.io", "https://bsc-dataseed2.bnbchain.org"]
W3S = [Web3(Web3.HTTPProvider(u, request_kwargs={"timeout": 30})) for u in URLS]
RR, LOCK = itertools.count(), threading.Lock()
SPB = 0.45
F = Web3.to_checksum_address("0xca143ce32fe78f1f7019d7d551a6402fc5350c73")
WB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
HASH = "1f6f5a665a"
PC = "0x" + Web3.keccak(text="PairCreated(address,address,address,uint256)").hex().replace("0x", "")
SW = "0x" + Web3.keccak(text="Swap(address,uint256,uint256,uint256,uint256,address)").hex().replace("0x", "")
SYNC = "0x" + Web3.keccak(text="Sync(uint112,uint112)").hex().replace("0x", "")
OURS = set()
try:
    for l in open(os.path.join(HERE, "hop_wallets.txt"), encoding="utf-8"):
        for p in l.replace(";", " ").replace(",", " ").split():
            if p.lower().startswith("0x") and len(p) == 42:
                OURS.add(p.lower())
except OSError:
    pass
METHODS = {"02751cec": "removeLiquidityETH", "af2979eb": "removeLiquidityETHSupportingFee", "ded9382a": "removeLiquidityETHWithPermit", "baa2abde": "removeLiquidity",
           "f305d719": "addLiquidityETH", "7ff36ab5": "swapExactETHForTokens", "b6f9de95": "swapExactETHForTokensFee", "fb3bdb41": "swapETHForExactTokens",
           "791ac947": "swapExactTokensForETHFee", "18cbafe5": "swapExactTokensForETH"}


def call(f, logs=False, tries=10):
    for k in range(tries):
        with LOCK:
            i = next(RR) % (2 if logs else len(W3S))  # logs: publicnode / 48club only
        try:
            return f(W3S[i])
        except Exception:
            time.sleep(0.5 + k * 0.7)
    return None


head = call(lambda w: w.eth.block_number)
lo = head - int(H * 3600 / SPB)
pairs = []
for b in range(lo, head + 1, 1000):
    for l in call(lambda w: w.eth.get_logs({"address": F, "fromBlock": b, "toBlock": min(head, b + 999), "topics": [PC]}), logs=True) or []:
        t0, t1 = "0x" + l["topics"][1].hex()[-40:], "0x" + l["topics"][2].hex()[-40:]
        if WB in (t0.lower(), t1.lower()):
            pairs.append(dict(pair=Web3.to_checksum_address("0x" + bytes(l["data"])[12:32].hex()), token=Web3.to_checksum_address(t1 if t0.lower() == WB else t0),
                              wb0=t0.lower() == WB, block=l["blockNumber"]))


def fam(p):
    c = call(lambda w: bytes(w.eth.get_code(p["token"])))
    return bool(c) and Web3.keccak(c).hex().replace("0x", "")[:10] == HASH


with ThreadPoolExecutor(6) as ex:
    coins = [p for p, ok in zip(pairs, ex.map(fam, pairs)) if ok]
print(f"монет семьи за {H:g} ч: {len(coins)}", flush=True)


def events(c):
    end = min(head, c["block"] + int(40 * 60 / SPB))
    ev = []
    for a in range(c["block"], end + 1, 1000):
        for l in call(lambda w: w.eth.get_logs({"address": c["pair"], "fromBlock": a, "toBlock": min(end, a + 999), "topics": [SYNC]}), logs=True) or []:
            d = bytes(l["data"])
            r0, r1 = int.from_bytes(d[:32], "big"), int.from_bytes(d[32:64], "big")
            ev.append(dict(block=l["blockNumber"], idx=l["logIndex"], tx=l["transactionHash"], bnb=(r0 if c["wb0"] else r1) / 1e18))
    for e in ev:
        t = call(lambda w: w.eth.get_transaction(e["tx"]))
        e["from"] = t["from"].lower() if t else "?"
        e["method"] = METHODS.get(bytes(t["input"])[:4].hex(), bytes(t["input"])[:4].hex()) if t else "?"
        e["gwei"] = round(t["gasPrice"] / 1e9, 3) if t else 0
    c["ev"] = ev
    return c


with ThreadPoolExecutor(4) as ex:
    coins = list(ex.map(events, coins))
buyers = collections.Counter()
for c in coins:
    for a in {e["from"] for e in c["ev"]}:
        buyers[a] += 1
creators = {c["ev"][0]["from"] for c in coins if c["ev"]}
bots = {a for a, n in buyers.items() if n >= 3 and a not in creators}


def role(a):
    return "МЫ" if a in OURS else "создатель" if a in creators else "бот семьи" if a in bots else "ПОСТОРОННИЙ"


lines = [f"семья 3553 за {H:g} ч: монет {len(coins)}; ботов семьи (в 3+ монетах) {len(bots)}; создателей {len(creators)}"]
early_after_outsider = 0
for c in coins:
    ev = c["ev"]
    if not ev:
        continue
    t = lambda e: round((e["block"] - c["block"]) * SPB)
    peak = max(e["bnb"] for e in ev)
    drain = next((e for e in ev if e["bnb"] < 0.2 * peak and e["block"] > ev[0]["block"]), None)
    when = time.strftime("%d.%m %H:%M", time.localtime(time.time() - (head - c["block"]) * SPB))
    lines.append(f"\n{when} {c['token'][:10]} старт {ev[0]['bnb']:.1f} BNB, пик {peak:.1f}, слив " + (f"на {t(drain) / 60:.1f} мин: {drain['method']} от {drain['from'][:10]} ({role(drain['from'])})" if drain else "нет"))
    prev = ev[0]["bnb"]
    shown = [e for e in ev[1:] if not drain or e["block"] <= drain["block"]]
    for e in shown[-8:] if len(shown) > 8 else shown:
        dv = e["bnb"] - prev
        prev = e["bnb"]
        lines.append(f"   {t(e):>5} с  {dv:+8.4f} BNB  пул {e['bnb']:7.2f}  {e['method']:<26} {e['from'][:10]} {role(e['from'])}  {e['gwei']} gwei")
    if drain:
        before = [e for e in ev[1:] if e["block"] < drain["block"]]
        if before and role(before[-1]["from"]) in ("ПОСТОРОННИЙ", "МЫ"):
            early_after_outsider += 1
            lines.append(f"   ! перед сливом последней была покупка постороннего/нашей, за {drain['block'] - before[-1]['block']} бл.")
lines.insert(1, f"сливов сразу после покупки постороннего или нашей: {early_after_outsider}")
open(os.path.join(HERE, "fam3553_timeline.txt"), "w", encoding="utf-8").write("\n".join(lines))
print("\n".join(lines[:4]))
