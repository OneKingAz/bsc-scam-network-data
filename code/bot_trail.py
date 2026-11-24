"""17.11 (user: look for other networks): where do the known bots of the networks 1-4 trade now? Read-only, open node (state ~70 min back).
Bots = wallets of bot_watch.csv seen buying in 10+ coins. For each: nonce now vs ~70 min ago; for the active ones, every own tx in that
window (binary search of the nonce) -> its 'to', method and value. Out: bot_trail.txt"""
import collections
import csv
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
W = Web3(Web3.HTTPProvider("https://bsc-rpc.publicnode.com", request_kwargs={"timeout": 25}))
W.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
NAMES = {"0x10ed43c718714eb63d5aa57b78b54704e256024e": "PancakeSwap V2 роутер", "0x13f4ea83d0bd40e75c8222255bc855a974568dd4": "PancakeSwap V3 роутер",
         "0x1b81d678ffb9c0263b24a1847a2e5f11f2b6c5b8": "PancakeSwap смарт-роутер", "0x5c952063c7fc8610ffdb798152d69f0b9550762b": "four.meme",
         "0x1de460f363af910f51726def188f9004276bf4bc": "OKX роутер"}


def call(f):
    for k in range(6):
        try:
            return f()
        except Exception:
            time.sleep(0.5 + 0.5 * k)
    return None


cnt = collections.defaultdict(set)
fam = {}
for fn in ("bot_watch_v1.csv", "bot_watch.csv"):
    p = os.path.join(HERE, fn)
    if os.path.exists(p):
        for r in csv.DictReader(open(p, encoding="utf-8-sig"), delimiter=";"):
            if r["kind"] == "покупка" and r["role"] == "бот семьи":
                cnt[r["sender"]].add(r["pair"])
                fam[r["sender"]] = r["family"]
bots = [a for a, s in cnt.items() if len(s) >= 10]
head = W.eth.block_number
lo = head - 9000
print(f"ботов (10+ монет): {len(bots)}; окно блоков {lo}..{head} (~68 мин)")


def nonce(a, b):
    return call(lambda: W.eth.get_transaction_count(Web3.to_checksum_address(a), b))


def active(a):
    n0, n1 = nonce(a, lo), nonce(a, head)
    return a, n0, n1


with ThreadPoolExecutor(10) as ex:
    st = list(ex.map(active, bots))
act = [(a, n0, n1) for a, n0, n1 in st if n0 is not None and n1 is not None and n1 > n0]
print(f"активны за окно: {len(act)} из {len(bots)}")
by_fam = collections.Counter(fam[a] for a, _, _ in act)
print("по семьям:", dict(by_fam))


def blocks(a, l, h, nl, nh):
    if nl == nh:
        return []
    if h - l == 1:
        return [h]
    m = (l + h) // 2
    nm = nonce(a, m)
    if nm is None:
        return []
    return blocks(a, l, m, nl, nm) + blocks(a, m, h, nm, nh)


TO = collections.Counter()
EX = collections.defaultdict(list)


def trail(x):
    a, n0, n1 = x
    out = []
    for b in blocks(a, lo, head, n0, n1)[:6]:
        blk = call(lambda: W.eth.get_block(b, full_transactions=True))
        for t in blk.transactions if blk else []:
            if t["from"].lower() == a:
                out.append((b, (t["to"] or "").lower(), bytes(t["input"])[:4].hex(), t["value"] / 1e18, Web3.to_hex(t["hash"])))
    return a, out


with ThreadPoolExecutor(6) as ex:
    res = list(ex.map(trail, act[:25]))
for a, out in res:
    for b, to, m, v, h in out:
        TO[to] += 1
        EX[to].append((a, b, m, v, h))
L = []
for to, n in TO.most_common(12):
    a, b, m, v, h = EX[to][0]
    line = f"  {to} ({NAMES.get(to, '?')}): {n} tx; пример: бот {a[:10]} блок {b} метод {m} {v:.4f} BNB {h}"
    print(line)
    L.append(line)
open(os.path.join(HERE, "bot_trail.txt"), "w", encoding="utf-8").write("\n".join(L))
