"""What did the signal test do with a coin? Every payment of the creator to a bot from the moment we watched it: bought, written down, or never seen. Read-only.
Run: python analysis\\coin_missed.py SYMBOL"""
import contextlib, csv, io, os, sys, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
SYM = sys.argv[1].upper()
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
rd = lambda n: list(csv.DictReader(open(os.path.join(HERE, n), encoding="utf-8-sig"), delimiter=";")) if os.path.exists(os.path.join(HERE, n)) else []
l = [x for x in rd("guard_launches.csv") if x["symbol"].upper() == SYM]
if not l:
    sys.exit(f"{SYM}: нет в списке монет сторожа")
l = l[-1]
token, pair = l["token"], Web3.to_checksum_address(l["pair"])
is0 = int(B.WBNB, 16) < int(token, 16)
head = a.eth.block_number
now = a.eth.get_block(head).timestamp
b0 = head - int((now - int(l["ts"])) / 0.45) - 60
sy = []
for b in range(b0, head, 2000):
    sy += a.eth.get_logs({"address": pair, "fromBlock": b, "toBlock": min(head, b + 1999), "topics": [SYNC]})
if not sy:
    sys.exit("в пуле нет событий")
bnb = lambda e: int.from_bytes(bytes(e["data"])[:32] if is0 else bytes(e["data"])[32:64], "big") / 1e18
creator = a.eth.get_transaction(sy[0]["transactionHash"])["from"].lower()
t0 = sy[0]["blockNumber"]
pools = [(e["blockNumber"], bnb(e)) for e in sy]
peak = max(p[1] for p in pools)
drain = next((b for b, v in pools[pools.index(max(pools, key=lambda p: p[1])):] if v < .2 * peak), None)
print(f"{SYM} {token}: запуск {l['time'][11:19]}, старт {pools[0][1]:.2f} BNB, пик {peak:.2f}, сейчас {pools[-1][1]:.2f}, возраст {(head - t0) * .45 / 60:.1f} мин, слив {'на %.1f мин' % ((drain - t0) * .45 / 60) if drain else 'нет'}")
r = requests.post(URL, json={"jsonrpc": "2.0", "id": 1, "method": "nr_getAssetTransfers", "params": [dict(category=["external"], fromAddress=creator, fromBlock=hex(t0 - 5), toBlock=hex(head), maxCount="0x3e8", excludeZeroValue=True)]}, timeout=30).json()
pays = [(int(t["blockNum"], 16), int(t["value"], 16) / 1e18) for t in (r.get("result") or {}).get("transfers") or []]
pays = [p for p in pays if 0.001 <= p[1] <= 3]
at = lambda b: next((v for bl, v in reversed(pools) if bl <= b), pools[0][1])
atts = [x for x in rd("signal_test.csv") if x["token"].lower() == token.lower()]
skips = [x for x in rd("signal_skip.csv") if x["token"].lower() == token.lower()]
print(f"платежей боту {len(pays)}; от 3% пула {sum(v / at(b) >= .03 for b, v in pays)}; наших попыток {len(atts)}; записано без покупки {len(skips)}")
big = [(b, v, v / at(b) * 100) for b, v in pays if v / at(b) >= .03]
seen = {int(x["pay_block"]) for x in atts + skips if x.get("pay_block")}
heads = [int(x["send_head"]) for x in atts] + [int(x["head"]) for x in skips if x.get("head")]
print("платежи от 3% пула:")
for b, v, s in big:
    near = [h for h in heads if abs(h - b) <= 4]
    print(f"  {(b - t0) * .45 / 60:5.1f} мин {v:.4f} BNB {s:5.1f}%: {'видели' if b in seen or near else 'НЕ ВИДЕЛИ'}")
for x in atts:
    print(f"попытка {x['time'][11:19]} {x['source']:<6} доля {x['share_pct']:>5}% {x['pnl_pct']:>6}% {x['note']}")
