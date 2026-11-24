"""Why no signal on a coin? Its creator, the creator's payments in the last minutes against the pool, and buys. Read-only.
Run: python analysis\\sig_diag.py <token> [minutes]"""
import contextlib, csv, io, os, sys, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
T = sys.argv[1]
MIN = float(sys.argv[2]) if len(sys.argv) > 2 else 30
sys.argv = sys.argv[:1]
with contextlib.redirect_stdout(io.StringIO()):
    import bot as B
    import journal as J
from web3 import Web3
import requests

URL = "https://bsc-mainnet.nodereal.io/v1/" + J.NODEREAL_KEY
a = Web3(Web3.HTTPProvider(URL, request_kwargs={"timeout": 30}))
a.middleware_onion.inject(B.ExtraDataToPOAMiddleware, layer=0)
l = next(r for r in csv.DictReader(open(os.path.join(HERE, "guard_launches.csv"), encoding="utf-8-sig"), delimiter=";") if r["token"].lower() == T.lower())
pair, token = Web3.to_checksum_address(l["pair"]), Web3.to_checksum_address(l["token"])
is0 = int(B.WBNB, 16) < int(token, 16)
SYNC = "0x" + Web3.keccak(text="Sync(uint112,uint112)").hex().replace("0x", "")
head = a.eth.block_number
now = a.eth.get_block(head).timestamp
b0 = head - int((now - int(l["ts"])) / 0.45) - 60
first = None
for b in range(b0, head, 2000):
    logs = a.eth.get_logs({"address": pair, "fromBlock": b, "toBlock": min(head, b + 1999), "topics": [SYNC]})
    if logs:
        first = logs[0]
        break
creator = a.eth.get_transaction(first["transactionHash"])["from"].lower()
d = bytes(first["data"])
start = int.from_bytes(d[:32] if is0 else d[32:64], "big") / 1e18
r0, r1, _ = a.eth.contract(pair, abi=B.PAIR_ABI).functions.getReserves().call()
pool = (r0 if is0 else r1) / 1e18
print(f"{l['symbol']}: возраст {(now - int(l['ts'])) / 60:.1f} мин, старт {start:.2f} BNB, сейчас в пуле {pool:.2f} BNB, создатель {creator}")
b1 = head - int(MIN * 60 / 0.45)
r = requests.post(URL, json={"jsonrpc": "2.0", "id": 1, "method": "nr_getAssetTransfers", "params": [dict(category=["external"], fromAddress=creator, fromBlock=hex(b1), toBlock=hex(head), maxCount="0x3e8", excludeZeroValue=True)]}, timeout=30).json()
tr = (r.get("result") or {}).get("transfers") or []
v = [int(t["value"], 16) / 1e18 for t in tr]
print(f"платежей создателя за {MIN:g} мин: {len(v)}")
if v:
    s = sorted(v)
    print(f"сумма: медиана {s[len(s) // 2]:.3f}, макс {s[-1]:.3f}; порог теста 3% пула = {pool * 0.03:.3f} BNB; выше порога {sum(x >= pool * 0.03 for x in v)}")
    print("последние:", [round(x, 3) for x in v[-10:]])
