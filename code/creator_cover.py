"""Does NodeReal's mempool stream see the creators' payments to the bots before their block? Only a NodeReal connection, spends nothing.
Run: python analysis\\creator_cover.py [seconds]"""
import contextlib, csv, io, json, os, statistics as st, sys, threading, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
SEC = int(sys.argv[1]) if len(sys.argv) > 1 else 1200
sys.argv = sys.argv[:1]
with contextlib.redirect_stdout(io.StringIO()):
    import bot as B
    import journal as J
from web3 import Web3
import requests
from websockets.sync.client import connect

URL = "https://bsc-mainnet.nodereal.io/v1/" + J.NODEREAL_KEY
a = Web3(Web3.HTTPProvider(URL, request_kwargs={"timeout": 30}))
a.middleware_onion.inject(B.ExtraDataToPOAMiddleware, layer=0)
SYNC = "0x" + Web3.keccak(text="Sync(uint112,uint112)").hex().replace("0x", "")
stop, seen, drops = time.time() + SEC, {}, 0
start_block = a.eth.block_number


def feed():
    global drops
    while time.time() < stop:
        try:
            with connect("wss://bsc-mainnet.nodereal.io/ws/v1/" + J.NODEREAL_KEY, open_timeout=10, max_size=None) as ws:
                ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_subscribe", "params": ["newPendingTransactions", True]}))
                while time.time() < stop:
                    try:
                        m = json.loads(ws.recv(timeout=5))
                    except TimeoutError:
                        continue
                    r = (m.get("params") or {}).get("result")
                    h = r.get("hash") if isinstance(r, dict) else r
                    if h:
                        seen.setdefault(h.lower(), time.time())
        except Exception:
            drops += 1
            time.sleep(1)


threading.Thread(target=feed, daemon=True).start()
time.sleep(SEC + 3)
head = a.eth.block_number
now = a.eth.get_block(head).timestamp
ls = list(csv.DictReader(open(os.path.join(HERE, "guard_launches.csv"), encoding="utf-8-sig"), delimiter=";"))
ls = [l for l in ls if int(l["ts"]) > now - SEC - 3600]
tot, vis, leads = 0, 0, []
print(f"обрывов потока {drops}, транзакций в потоке {len(seen)}")
for l in ls:
    b0 = max(0, head - int((now - int(l["ts"])) / 0.45) - 60)
    logs = a.eth.get_logs({"address": Web3.to_checksum_address(l["pair"]), "fromBlock": b0, "toBlock": min(head, b0 + 1999), "topics": [SYNC]})
    if not logs:
        continue
    creator = a.eth.get_transaction(logs[0]["transactionHash"])["from"].lower()
    r = requests.post(URL, json={"jsonrpc": "2.0", "id": 1, "method": "nr_getAssetTransfers", "params": [dict(category=["external"], fromAddress=creator, fromBlock=hex(start_block + 30), toBlock=hex(head), maxCount="0x3e8", excludeZeroValue=True)]}, timeout=30).json()
    tr = [t for t in (r.get("result") or {}).get("transfers") or [] if 0.01 <= int(t["value"], 16) / 1e18 <= 3]
    n = 0
    for t in tr:
        h = t["hash"].lower()
        ts = a.eth.get_block(int(t["blockNum"], 16)).timestamp
        tot += 1
        if h in seen and seen[h] < ts + 1:  # a block's time is in whole seconds
            n += 1
            leads.append(ts - seen[h])
    if tr:
        print(f"{l['symbol']:<10} платежей {len(tr):>3}, виден в потоке NodeReal {n:>3}")
    vis += n
print(f"\nвсего платежей {tot}, видно NodeReal {vis} ({vis * 100 / max(1, tot):.0f}%)" + (f", время до метки блока: медиана {st.median(leads):+.2f} с" if leads else ""))
