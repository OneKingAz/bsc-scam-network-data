"""Is the wallet paying the bots one per coin, and the same from coin to coin? Last coins: creator (first Sync's sender), who paid each
buyer right before its buy. Run: python analysis\\creator_same.py [coins]"""
import collections, contextlib, csv, io, os, sys, time
from concurrent.futures import ThreadPoolExecutor
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
N = int(sys.argv[1]) if len(sys.argv) > 1 else 10
sys.argv = sys.argv[:1]
with contextlib.redirect_stdout(io.StringIO()):
    import bot as B
    import journal as J
from web3 import Web3
import requests

URL = "https://bsc-mainnet.nodereal.io/v1/" + J.NODEREAL_KEY
a = Web3(Web3.HTTPProvider(URL, request_kwargs={"timeout": 30}))
a.middleware_onion.inject(B.ExtraDataToPOAMiddleware, layer=0)
SYNC = "0x" + Web3.keccak(text="Sync(uint112,uint112)").hex().replace("0x", "")
SWAP = "0x" + Web3.keccak(text="Swap(address,uint256,uint256,uint256,uint256,address)").hex().replace("0x", "")
head = a.eth.block_number
now = a.eth.get_block(head).timestamp
rows = list(csv.DictReader(open(os.path.join(HERE, "guard_launches.csv"), encoding="utf-8-sig"), delimiter=";"))[-N - 1:-1]


def call(fn, k=5):
    for i in range(k):
        try:
            return fn()
        except Exception:
            if i == k - 1:
                raise
            time.sleep(1 + i)


def payers(to, b):
    r = call(lambda: requests.post(URL, json={"jsonrpc": "2.0", "id": 1, "method": "nr_getAssetTransfers", "params": [dict(category=["external"], toAddress=to, fromBlock=hex(b - 60), toBlock=hex(b), maxCount="0x14", excludeZeroValue=True)]}, timeout=30).json())
    tr = [t for t in (r.get("result") or {}).get("transfers") or [] if int(t["blockNum"], 16) <= b]
    return max(tr, key=lambda t: int(t["blockNum"], 16))["from"].lower() if tr else "(нет платежа)"


def look(l):
    pair, token = Web3.to_checksum_address(l["pair"]), Web3.to_checksum_address(l["token"])
    is0 = int(B.WBNB, 16) < int(token, 16)
    b0 = head - int((now - int(l["ts"])) / 0.45) - 60
    sy, sw = [], []
    for b in range(b0, min(head, b0 + 8000), 2000):
        for e in call(lambda: a.eth.get_logs({"address": pair, "fromBlock": b, "toBlock": min(head, b + 1999), "topics": [[SYNC, SWAP]]})):
            d = bytes(e["data"])
            if e["topics"][0].hex().replace("0x", "") == SYNC[2:]:
                sy.append(e)
            elif int.from_bytes(d[0:32] if is0 else d[32:64], "big") > 10**14:
                sw.append(e)
    if not sy:
        return None
    creator = call(lambda: a.eth.get_transaction(sy[0]["transactionHash"]))["from"].lower()
    d = bytes(sy[0]["data"])
    start = int.from_bytes(d[:32] if is0 else d[32:64], "big") / 1e18
    c = collections.Counter()
    for e in sw[:40]:
        frm = call(lambda: a.eth.get_transaction(e["transactionHash"]))["from"].lower()
        c[payers(frm, e["blockNumber"])] += 1
    return l["symbol"], round(start), creator, c


with ThreadPoolExecutor(5) as ex:
    res = [r for r in ex.map(look, rows) if r]
creators = collections.Counter(r[2] for r in res)
for sym, start, creator, c in res:
    top = c.most_common(1)[0] if c else ("", 0)
    print(f"{sym:<12} {start:>3} BNB создатель {creator[:12]} | покупок {sum(c.values()):3} | платит создатель {c.get(creator, 0):3} | остальные: {[(k[:10], n) for k, n in c.most_common(4) if k != creator]}")
print(f"\nмонет {len(res)}, разных создателей {len(creators)}, повторы: {[(k[:10], n) for k, n in creators.items() if n > 1]}")
