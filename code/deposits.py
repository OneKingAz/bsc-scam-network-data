"""What came to wallet A from outside (not from our own W wallets), and what it has now. Read-only.
Run: python analysis\\deposits.py"""
import contextlib, io, os, sys, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.argv = sys.argv[:1]
with contextlib.redirect_stdout(io.StringIO()):
    import bot as B
    import journal as J
from web3 import Web3
import requests

URL = "https://bsc-mainnet.nodereal.io/v1/" + J.NODEREAL_KEY
A = "0xb5dC73324B34482F9d3a4610c5c3fb9fB9D5d053".lower()
ours = {A}
for l in open(os.path.join(ROOT, "hop_wallets.txt"), encoding="utf-8-sig"):
    x = l.strip().split(";")
    if len(x) >= 2:
        ours.add(x[1].lower())
a = Web3(Web3.HTTPProvider(URL, request_kwargs={"timeout": 30}))
a.middleware_onion.inject(B.ExtraDataToPOAMiddleware, layer=0)
head = a.eth.block_number
incoming, key = [], None
while True:
    p = dict(category=["external"], toAddress=A, fromBlock=hex(head - 400_000), toBlock=hex(head), maxCount="0x3e8", excludeZeroValue=True)
    if key:
        p["pageKey"] = key
    j = requests.post(URL, json={"jsonrpc": "2.0", "id": 1, "method": "nr_getAssetTransfers", "params": [p]}, timeout=30).json()
    if "result" not in j:
        print("ответ узла:", str(j.get("error"))[:200])
        break
    r = j["result"]
    incoming += r.get("transfers") or []
    key = r.get("pageKey")
    if not key:
        break
ext = [t for t in incoming if t["from"].lower() not in ours]
total = 0
print(f"всего входящих {len(incoming)}, со стороны {len(ext)}")
for t in ext:
    v = int(t["value"], 16) / 1e18
    total += v
    ts = a.eth.get_block(int(t["blockNum"], 16)).timestamp
    print(f"{time.strftime('%d.%m %H:%M:%S', time.localtime(ts))} {v:.6f} BNB, от {t['from'][:10]}…")
print(f"всего со стороны: {total:.6f} BNB")
bal = a.eth.get_balance(Web3.to_checksum_address(A)) / 1e18
print(f"на A сейчас: {bal:.6f} BNB")
