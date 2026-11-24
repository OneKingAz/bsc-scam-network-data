"""Who bought? A buy on a guard coin by the end of the buyer's address: its transaction, who paid the buyer right before, how many buys it has. Read-only.
Run: python analysis\\who_bought.py SYMBOL 8F0974"""
import contextlib, csv, io, os, sys
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
SYM, END = sys.argv[1].upper(), sys.argv[2].lower()
sys.argv = sys.argv[:1]
with contextlib.redirect_stdout(io.StringIO()):
    import bot as B
    import journal as J
from web3 import Web3
import requests

URL = "https://bsc-mainnet.nodereal.io/v1/" + J.NODEREAL_KEY
a = Web3(Web3.HTTPProvider(URL, request_kwargs={"timeout": 30}))
a.middleware_onion.inject(B.ExtraDataToPOAMiddleware, layer=0)
SWAP = "0x" + Web3.keccak(text="Swap(address,uint256,uint256,uint256,uint256,address)").hex().replace("0x", "")
SYNC = "0x" + Web3.keccak(text="Sync(uint112,uint112)").hex().replace("0x", "")
ls = list(csv.DictReader(open(os.path.join(HERE, "guard_launches.csv"), encoding="utf-8-sig"), delimiter=";"))
l = [x for x in ls if x["symbol"].upper() == SYM][-1]
pair, token = Web3.to_checksum_address(l["pair"]), l["token"]
is0 = int(B.WBNB, 16) < int(token, 16)
head = a.eth.block_number
now = a.eth.get_block(head).timestamp
b0 = head - int((now - int(l["ts"])) / 0.45) - 60
logs = []
for b in range(b0, head, 2000):
    logs += a.eth.get_logs({"address": pair, "fromBlock": b, "toBlock": min(head, b + 1999), "topics": [[SWAP, SYNC]]})
sy = next(e for e in logs if e["topics"][0].hex().replace("0x", "") == SYNC[2:])
creator = a.eth.get_transaction(sy["transactionHash"])["from"].lower()
buys = []
for e in logs:
    if e["topics"][0].hex().replace("0x", "") != SWAP[2:]:
        continue
    d = bytes(e["data"])
    v = int.from_bytes(d[:32] if is0 else d[32:64], "big") / 1e18
    if v >= 0.3:
        buys.append((e["blockNumber"], v, e["transactionHash"]))
print(f"монета {SYM} {token}, создатель {creator}")
found = 0
for b, v, h in reversed(buys[-40:]):
    tx = a.eth.get_transaction(h)
    frm = tx["from"].lower()
    if not frm.endswith(END):
        continue
    found += 1
    r = requests.post(URL, json={"jsonrpc": "2.0", "id": 1, "method": "nr_getAssetTransfers", "params": [dict(category=["external"], toAddress=frm, fromBlock=hex(b - 60), toBlock=hex(b), maxCount="0x14", excludeZeroValue=True)]}, timeout=30).json()
    tr = (r.get("result") or {}).get("transfers") or []
    pay = max(tr, key=lambda t: int(t["blockNum"], 16)) if tr else None
    print(f"покупка: кошелёк {frm}, {v:.4f} BNB, блок {b} ({(b - b0 - 60) * .45 / 60:.1f} мин), статус {a.eth.get_transaction_receipt(h)['status']}")
    if pay:
        print(f"  перед ней заплатил {pay['from'].lower()} {int(pay['value'], 16) / 1e18:.5f} BNB за {b - int(pay['blockNum'], 16)} блоков; это создатель: {pay['from'].lower() == creator}")
    print(f"  транзакций у кошелька всего: {a.eth.get_transaction_count(Web3.to_checksum_address(frm))}")
    break
if not found:
    print(f"покупки ≥0.3 BNB с кошелька …{END} среди последних 40 нет; такие покупки: " + ", ".join(sorted({a.eth.get_transaction(h)['from'][-6:] for _, _, h in buys[-6:]})))
