"""Do the bots' buys fail without us? On coins we did not trade: every payment of the creator to a bot, the bot's router transaction,
its status, and how tight its amountOutMin is. The same tightness for our attempts. Read-only.
Run: python analysis\\idle_base.py [coins] [payments per coin]"""
import contextlib, csv, io, os, sys, time
from concurrent.futures import ThreadPoolExecutor
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
COINS = int(sys.argv[1]) if len(sys.argv) > 1 else 5
PER = int(sys.argv[2]) if len(sys.argv) > 2 else 20
sys.argv = sys.argv[:1]
with contextlib.redirect_stdout(io.StringIO()):
    import bot as B
    import journal as J
from web3 import Web3
import requests

URL = "https://bsc-mainnet.nodereal.io/v1/" + J.NODEREAL_KEY
a = Web3(Web3.HTTPProvider(URL, request_kwargs={"timeout": 30}))
a.middleware_onion.inject(B.ExtraDataToPOAMiddleware, layer=0)
ROUTER, SPB = B.ROUTER.lower(), 0.45
SYNC = "0x" + Web3.keccak(text="Sync(uint112,uint112)").hex().replace("0x", "")
RT = a.eth.contract(Web3.to_checksum_address(B.ROUTER), abi=[{"name": "getAmountsOut", "type": "function", "stateMutability": "view", "inputs": [{"name": "a", "type": "uint256"}, {"name": "p", "type": "address[]"}], "outputs": [{"name": "o", "type": "uint256[]"}]}])
blocks = {}


def call(fn, k=5):
    for i in range(k):
        try:
            return fn()
        except Exception:
            if i == k - 1:
                raise
            time.sleep(1 + i)


def block(n):
    if n not in blocks:
        blocks[n] = call(lambda: a.eth.get_block(n, full_transactions=True))["transactions"]
    return blocks[n]


def tight(tx, token, n):
    """amountOutMin against the quote right before the block it landed in (1 = not a wei of room)."""
    d = bytes(tx["input"])
    mn = int.from_bytes(d[4:36], "big")
    q = call(lambda: RT.functions.getAmountsOut(tx["value"], [Web3.to_checksum_address(B.WBNB), Web3.to_checksum_address(token)]).call(block_identifier=n - 1))[-1]
    return mn / q if q else 0


def bot_tx(frm, value, b0, token):
    for n in range(b0, b0 + 30):
        for tx in block(n):
            if tx["from"].lower() == frm and (tx["to"] or "").lower() == ROUTER and abs(tx["value"] / 1e18 - value) < 0.0002 and token[2:].lower() in bytes(tx["input"]).hex():
                return tx, n
    return None, None


head = call(lambda: a.eth.block_number)
now = call(lambda: a.eth.get_block(head).timestamp)
ours = {r["token"].lower() for r in csv.DictReader(open(os.path.join(HERE, "signal_test.csv"), encoding="utf-8-sig"), delimiter=";")}
ls = [l for l in csv.DictReader(open(os.path.join(HERE, "guard_launches.csv"), encoding="utf-8-sig"), delimiter=";") if l["token"].lower() not in ours and 60 * 60 < now - int(l["ts"]) < 86400 * 2]
print("монета: платежей | купил ок | отклонена | покупки нет | запас amountOutMin у успешных и отклонённых")
tot = dict(ok=0, bad=0, no=0)
tok, tbad = [], []


def coin(l):
    pair, token = Web3.to_checksum_address(l["pair"]), l["token"]
    b0 = head - int((now - int(l["ts"])) / SPB) - 60
    logs = call(lambda: a.eth.get_logs({"address": pair, "fromBlock": b0, "toBlock": b0 + 1999, "topics": [SYNC]}))
    if not logs:
        return None
    creator = call(lambda: a.eth.get_transaction(logs[0]["transactionHash"]))["from"].lower()
    r = call(lambda: requests.post(URL, json={"jsonrpc": "2.0", "id": 1, "method": "nr_getAssetTransfers", "params": [dict(category=["external"], fromAddress=creator, fromBlock=hex(b0), toBlock=hex(b0 + 14000), maxCount="0x3e8", excludeZeroValue=True)]}, timeout=30).json())
    tr = [(int(t["blockNum"], 16), t["to"].lower(), int(t["value"], 16) / 1e18) for t in (r.get("result") or {}).get("transfers") or []]
    tr = [t for t in tr if 0.01 <= t[2] <= 3][:PER]
    res = dict(ok=0, bad=0, no=0, tok=[], tbad=[])
    for b, to, v in tr:
        tx, n = bot_tx(to, v, b, token)
        if tx is None:
            res["no"] += 1
            continue
        st = call(lambda: a.eth.get_transaction_receipt(tx["hash"]))["status"]
        res["ok" if st else "bad"] += 1
        try:
            res["tok" if st else "tbad"].append(tight(tx, token, n))
        except Exception:
            pass
    return l, len(tr), res


with ThreadPoolExecutor(5) as ex:
    out = [x for x in ex.map(coin, ls[-COINS:]) if x]
med = lambda v: sorted(v)[len(v) // 2] if v else None
fm = lambda v: "–" if v is None else f"{v:.4f}"
for l, n, r in out:
    print(f"{l['symbol']:<10} {n:>3} | {r['ok']:>3} | {r['bad']:>3} | {r['no']:>3} | запас: ок {fm(med(r['tok']))}, откл. {fm(med(r['tbad']))}")
    for k in tot:
        tot[k] += r[k]
    tok += r["tok"]
    tbad += r["tbad"]
n = sum(tot.values())
print(f"\nбез нас: платежей {n}, купил {tot['ok']}, отклонена {tot['bad']} ({tot['bad'] * 100 / max(1, n):.0f}%), покупки нет {tot['no']}")
print(f"amountOutMin / цена перед блоком: успешные медиана {fm(med(tok))} (мин {fm(min(tok) if tok else None)}, макс {fm(max(tok) if tok else None)}), отклонённые медиана {fm(med(tbad))} (мин {fm(min(tbad) if tbad else None)}, макс {fm(max(tbad) if tbad else None)})")
