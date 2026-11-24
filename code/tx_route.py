"""10.11 (user: how to land sooner; how do the scam bots send). Read-only, open nodes.
For our trades on the networks 3/4 (signal_test3/4.csv, with the payment's block): the creator's payment tx, the paid bot's buy tx and
our buy tx: block, index in the block, gas price, the block's validator (coinbase), the block size. Then the guards of the networks 3/4:
their transactions over the last day (approve = a mark) and whether any of our wallets used there is marked.
Out: tx_route.csv, tx_route.txt. Usage: python tx_route.py ["2026-11-08 19:49"]"""
import collections
import csv
import os
import statistics
import sys

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
SINCE = sys.argv[1] if len(sys.argv) > 1 else "2026-11-08 19:49"
w3 = Web3(Web3.HTTPProvider("https://bsc-dataseed.bnbchain.org", request_kwargs={"timeout": 25}))
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
ROUTER = "0x10ed43c718714eb63d5aa57b78b54704e256024e"
BLK = {}


def block(n):
    if n not in BLK:
        BLK[n] = w3.eth.get_block(n, full_transactions=True)
    return BLK[n]


rows = []
for n in (3, 4):
    for r in csv.DictReader(open(os.path.join(HERE, f"signal_test{n}.csv"), encoding="utf-8-sig"), delimiter=";"):
        if r["time"] >= SINCE and r["pay_block"] and r["land_block"]:
            rows.append((n, r))
out = []
for n, r in rows:
    pay_b, land_b, paid = int(r["pay_block"]), int(r["land_block"]), float(r["bnb_paid"])
    pb = block(pay_b)
    pay = next((t for t in pb.transactions if t["value"] and abs(t["value"] / 1e18 - paid) < 0.000006), None)
    bot = None
    if pay:
        for k in range(pay_b, pay_b + 6):
            bot = next((t for t in block(k).transactions if t["from"].lower() == pay["to"].lower() and (t["to"] or "").lower() == ROUTER), None)
            if bot:
                break
    lb = block(land_b)
    stake = float(r["got_bnb"] or 0) - float(r["pnl_bnb"] or 0) - float(r["gas_bnb"] or 0)
    ours = next((t for t in lb.transactions if (t["to"] or "").lower() == ROUTER and t["value"] and abs(t["value"] / 1e18 - stake) < 0.02 * stake), None)
    rc = w3.eth.get_transaction_receipt(bot["hash"]) if bot else None
    d = dict(net=n, time=r["time"], symbol=r["symbol"], pnl_pct=r["pnl_pct"], source=r["source"],
             pay_block=pay_b, pay_idx=pay["transactionIndex"] if pay else "", pay_gwei=round(pay["gasPrice"] / 1e9, 3) if pay else "", pay_val=pb["miner"][:10],
             bot_block=bot["blockNumber"] if bot else "", bot_idx=bot["transactionIndex"] if bot else "", bot_gwei=round(bot["gasPrice"] / 1e9, 3) if bot else "",
             bot_status=rc["status"] if rc else "", bot_val=block(bot["blockNumber"])["miner"][:10] if bot else "",
             send_head=r["send_head"], our_block=land_b, our_idx=ours["transactionIndex"] if ours else "", our_gwei=round(ours["gasPrice"] / 1e9, 3) if ours else "",
             our_val=lb["miner"][:10], our_block_txs=len(lb.transactions), our_from=ours["from"] if ours else "", token=r["token"])
    out.append(d)
    print(f"{n} {r['time'][11:16]} {r['symbol']:<10} платёж бл+0 idx {d['pay_idx']} {d['pay_gwei']} gwei | бот +{(d['bot_block'] - pay_b) if bot else '-'} idx {d['bot_idx']} {d['bot_gwei']} gwei ст {d['bot_status']} | "
          f"мы отправили на +{int(r['send_head']) - pay_b}, попали +{land_b - pay_b} idx {d['our_idx']}/{d['our_block_txs']} {d['our_gwei']} gwei, валидатор {d['our_val']}", flush=True)
if out:
    with open(os.path.join(HERE, "tx_route.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]), delimiter=";")
        w.writeheader()
        w.writerows(out)
lines = []
bots = [d for d in out if d["bot_block"] != ""]
if bots:
    lines.append(f"боты: попадают через {statistics.median(d['bot_block'] - d['pay_block'] for d in bots)} бл. (медиана) после платежа, газ {statistics.median(d['bot_gwei'] for d in bots)} gwei, "
                 f"место в блоке медиана {statistics.median(d['bot_idx'] for d in bots)}")
pays = [d for d in out if d["pay_idx"] != ""]
if pays:
    lines.append(f"платежи создателя: газ {statistics.median(d['pay_gwei'] for d in pays)} gwei, место в блоке медиана {statistics.median(d['pay_idx'] for d in pays)}")
ours = [d for d in out if d["our_idx"] != ""]
if ours:
    lines.append(f"мы: отправляем на +{statistics.median(int(d['send_head']) - d['pay_block'] for d in ours)}, попадаем на +{statistics.median(d['our_block'] - d['pay_block'] for d in ours)}, "
                 f"газ {statistics.median(d['our_gwei'] for d in ours)} gwei, место медиана {statistics.median(d['our_idx'] for d in ours)}")
    vc = collections.Counter(d["our_val"] for d in ours)
    lines.append("валидаторы наших блоков: " + ", ".join(f"{v} {c}" for v, c in vc.most_common(6)))
    late = collections.Counter((d["our_val"], d["our_block"] - d["pay_block"]) for d in ours)
    lines.append("валидатор → на какой блок попали: " + ", ".join(f"{v}:+{k} ×{c}" for (v, k), c in sorted(late.items())))

# marks of the guards on OUR wallets that held these coins (allowance(guard, wallet) on the token)
ALW = [{"name": "allowance", "inputs": [{"type": "address"}, {"type": "address"}], "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"}]
GOF = {3: "0x623e7c23C7C228095f94d9BD567Eae55cB87A966", 4: "0xA70B3f34E3ca7D4665b794CE6bb29168b1D2e40d"}
marked = checked = 0
for d in out:
    if d.get("our_from"):
        checked += 1
        a = w3.eth.contract(Web3.to_checksum_address(d["token"]), abi=ALW).functions.allowance(Web3.to_checksum_address(GOF[d["net"]]), d["our_from"]).call()
        marked += a > 0
lines.append(f"метки сторожа на наших кошельках сетей 3/4: помечено {marked} из {checked}")
import time as _t
n0 = {g: w3.eth.get_transaction_count(Web3.to_checksum_address(g)) for g in GOF.values()}
_t.sleep(60)
for k, g in GOF.items():
    lines.append(f"сторож сети {k}: за 60 с транзакций {w3.eth.get_transaction_count(Web3.to_checksum_address(g)) - n0[g]}")
# the guards of the networks 3/4 over the last day
head = w3.eth.block_number
for g, n in (("0x623e7c23C7C228095f94d9BD567Eae55cB87A966", 3), ("0xA70B3f34E3ca7D4665b794CE6bb29168b1D2e40d", 4)):
    G = Web3.to_checksum_address(g)
    lines.append(f"сторож сети {n} {g[:10]}: транзакций всего {w3.eth.get_transaction_count(G)}, баланс {w3.eth.get_balance(G) / 1e18:.4f} BNB")
open(os.path.join(HERE, "tx_route.txt"), "w", encoding="utf-8").write("\n".join(lines))
print("\n".join(lines))
