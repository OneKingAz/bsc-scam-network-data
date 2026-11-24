"""Did the creators' payments change? Morning coins (seen in the mempool) against now: gas price, type, place in the block, who built the block.
Only NodeReal over HTTP, does not touch the running test's mempool stream. Run: python analysis\\pay_compare.py"""
import collections, contextlib, csv, io, os, statistics as st, sys
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
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
rows = list(csv.DictReader(open(os.path.join(HERE, "signal_test.csv"), encoding="utf-8-sig"), delimiter=";"))
launch = {l["token"].lower(): l for l in csv.DictReader(open(os.path.join(HERE, "guard_launches.csv"), encoding="utf-8-sig"), delimiter=";")}
head = a.eth.block_number
now = a.eth.get_block(head).timestamp
print("монета, сигналов мемпул/блок в тесте | платежей | цена газа gwei: медиана (мин–макс) | место в блоке: медиана | тип tx | сборщиков блоков")
for t in dict.fromkeys(r["token"].lower() for r in rows):
    l = launch.get(t)
    if not l:
        continue
    rs = [r for r in rows if r["token"].lower() == t]
    m, k = sum(r["source"] == "мемпул" for r in rs), sum(r["source"] == "блок" for r in rs)
    b0 = head - int((now - int(l["ts"])) / 0.45) - 60
    logs = a.eth.get_logs({"address": Web3.to_checksum_address(l["pair"]), "fromBlock": b0, "toBlock": b0 + 1999, "topics": [SYNC]})
    if not logs:
        continue
    creator = a.eth.get_transaction(logs[0]["transactionHash"])["from"].lower()
    r = requests.post(URL, json={"jsonrpc": "2.0", "id": 1, "method": "nr_getAssetTransfers", "params": [dict(category=["external"], fromAddress=creator, fromBlock=hex(b0), toBlock=hex(min(head, b0 + 14000)), maxCount="0x64", excludeZeroValue=True)]}, timeout=30).json()
    tr = [x for x in (r.get("result") or {}).get("transfers") or [] if 0.001 <= int(x["value"], 16) / 1e18 <= 3][:12]
    gas, idx, types, miners = [], [], collections.Counter(), collections.Counter()
    for x in tr:
        tx = a.eth.get_transaction(x["hash"])
        gas.append(tx["gasPrice"] / 1e9)
        idx.append(tx["transactionIndex"])
        types[tx.get("type", 0)] += 1
        miners[a.eth.get_block(tx["blockNumber"])["miner"][:8]] += 1
    if gas:
        print(f"{rs[0]['time'][11:16]} {l['symbol']:<12} {m:>2}/{k:<2} | {len(tr):>2} | {st.median(gas):.3f} ({min(gas):.3f}–{max(gas):.3f}) | {st.median(idx):>4.0f} (мин {min(idx)}, макс {max(idx)}) | {dict(types)} | {len(miners)}")
