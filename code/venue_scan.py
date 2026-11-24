"""17.11 (user: why so few networks): a one-off look beyond PancakeSwap V2. Read-only, open node.
Last N blocks (default 1500, ~11 min): every buy with BNB sent to four.meme, PancakeSwap V3 / smart routers or other routers.
For each buyer: did its balance grow by >= half of the buy within 10 blocks before, and from whom (the 'payer').
A payer that funds many different buyers right before their buys = the same scheme as our networks. Out: venue_scan.txt"""
import collections
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

sys.stdout.reconfigure(encoding="utf-8")
N = int(sys.argv[1]) if len(sys.argv) > 1 else 1500
W = Web3(Web3.HTTPProvider("https://bsc-rpc.publicnode.com", request_kwargs={"timeout": 25}))
W.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
VENUES = {"0x5c952063c7fc8610ffdb798152d69f0b9550762b": "four.meme", "0x13f4ea83d0bd40e75c8222255bc855a974568dd4": "Pancake V3 роутер",
          "0x1b81d678ffb9c0263b24a1847a2e5f11f2b6c5b8": "Pancake смарт-роутер", "0x10ed43c718714eb63d5aa57b78b54704e256024e": "Pancake V2 роутер",
          "0x1de460f363af910f51726def188f9004276bf4bc": "OKX роутер", "0xba56663c1de9423ec886b24ac752d86ff0d9f7c0": "агрегатор 0xba56663c"}


def call(f):
    for k in range(6):
        try:
            return f()
        except Exception:
            time.sleep(0.5 + 0.5 * k)
    return None


head = W.eth.block_number - 2
with ThreadPoolExecutor(10) as ex:
    blocks = list(ex.map(lambda b: (b, call(lambda: W.eth.get_block(b, full_transactions=True))), range(head - N, head)))
buys = []
for b, blk in blocks:
    for t in blk.transactions if blk else []:
        to = (t["to"] or "").lower()
        if to in VENUES and t["value"] >= 10**16:
            buys.append((b, to, t["from"].lower(), t["value"] / 1e18))
print(f"блоков {N}, покупок за BNB от 0.01: {len(buys)}; по площадкам: {dict(collections.Counter(VENUES[x[1]] for x in buys))}")
BLK = dict(blocks)


def funder(x):
    b, to, a, v = x
    A = Web3.to_checksum_address(a)
    b0, b1 = call(lambda: W.eth.get_balance(A, b - 11)), call(lambda: W.eth.get_balance(A, b - 1))
    if b0 is None or b1 is None or (b1 - b0) / 1e18 < v * 0.5:
        return x, None, None
    for k in range(b - 10, b):
        if (call(lambda: W.eth.get_balance(A, k)) or 0) - b0 >= v * 0.5 * 1e18:
            blk = BLK.get(k) or call(lambda: W.eth.get_block(k, full_transactions=True))
            for t in blk.transactions if blk else []:
                if (t["to"] or "").lower() == a:
                    return x, t["from"].lower(), b - k
            return x, "внутренний перевод", b - k
    return x, None, None


with ThreadPoolExecutor(10) as ex:
    res = list(ex.map(funder, buys))
paid = [r for r in res if r[1]]
print(f"оплачены за ≤10 блоков до покупки: {len(paid)} из {len(res)}")
by = collections.defaultdict(list)
for x, p, lag in paid:
    by[p].append((x, lag))
L = []
for p, lst in sorted(by.items(), key=lambda kv: -len(kv[1]))[:12]:
    ven = collections.Counter(VENUES[x[1]] for x, _ in lst)
    buyers = {x[2] for x, _ in lst}
    line = f"  плательщик {p}: оплатил {len(lst)} покупок {len(buyers)} разным покупателям; площадки {dict(ven)}; задержка {dict(collections.Counter(l for _, l in lst).most_common(3))}"
    print(line)
    L.append(line)
open(r"D:\bot\analysis\venue_scan.txt", "w", encoding="utf-8").write("\n".join(L))
