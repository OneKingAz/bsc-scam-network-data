"""09.11: a wallet that buys every coin of a family at a steady second after the launch (pattern_scan.txt). For each of its coins:
pool at launch, its first buy (second, BNB, share of the pool), what a buy of ours at second ENTRY would make if sold right after its buy
(by Sync reserves, 0.25% fee each way, no tax), and whether the pool was drained later. Read-only, open nodes.
Usage: python sched_buyer.py <wallet> [entry_s=10]"""
import csv
import sys

from web3 import Web3

sys.stdout.reconfigure(encoding="utf-8")
WHO = sys.argv[1].lower()
ENTRY = float(sys.argv[2]) if len(sys.argv) > 2 else 10
SPB = 0.45
w3 = Web3(Web3.HTTPProvider("https://bsc-rpc.publicnode.com", request_kwargs={"timeout": 20}))
SYNC = "0x" + Web3.keccak(text="Sync(uint112,uint112)").hex().replace("0x", "")
PC = "0x" + Web3.keccak(text="PairCreated(address,address,address,uint256)").hex().replace("0x", "")
WB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
T0 = [{"name": "token0", "inputs": [], "outputs": [{"type": "address"}], "stateMutability": "view", "type": "function"}]
rows = [r for r in csv.DictReader(open(r"D:\bot\analysis\pattern_scan.csv", encoding="utf-8-sig"), delimiter=";") if r["from"].lower() == WHO]
pairs = {}
for r in sorted(rows, key=lambda r: int(r["block"])):
    pairs.setdefault(r["pair"], []).append(r)
head = w3.eth.block_number
tot = []
for pair, rs in pairs.items():
    P = Web3.to_checksum_address(pair)
    wb0 = w3.eth.contract(P, abi=T0).functions.token0().call().lower() == WB
    first = rs[0]
    b_buy = int(first["block"])
    b0 = b_buy - int(float(first["age_s"]) / SPB)  # the pool's block
    syncs = []
    for a in range(b0 - 2, head + 1, 5000):
        syncs += w3.eth.get_logs({"address": P, "fromBlock": a, "toBlock": min(head, a + 4999), "topics": [SYNC]})
        if a > b_buy + 20000:
            break

    def res(l):
        d = bytes(l["data"])
        r0, r1 = int.from_bytes(d[:32], "big"), int.from_bytes(d[32:64], "big")
        return (r0, r1) if wb0 else (r1, r0)

    rb_start = res(syncs[0])[0] / 1e18
    enter = [l for l in syncs if l["blockNumber"] <= b0 + ENTRY / SPB]
    after = [l for l in syncs if l["blockNumber"] == b_buy]
    if not enter or not after:
        print(f"{pair[:10]}: нет данных")
        continue
    rbE, rtE = res(enter[-1])
    rbA, rtA = res(after[-1])
    stake = 0.01e18
    tok = stake * 0.9975 * rtE / (rbE + stake * 0.9975)
    back = tok * 0.9975 * rbA / (rtA + tok * 0.9975) / 1e18
    pnl = (back / 0.01 - 1) * 100
    peak = max(res(l)[0] for l in syncs) / 1e18
    last = res(syncs[-1])[0] / 1e18
    life = next(((l["blockNumber"] - b0) * SPB for l in syncs if res(l)[0] / 1e18 < 0.2 * peak), None)
    between = sum(1 for l in syncs if b0 + ENTRY / SPB < l["blockNumber"] < b_buy)
    tot.append(pnl)
    print(f"{pair[:10]}: пул {rb_start:.2f} BNB, его покупка на {float(first['age_s']):.1f} с {float(first['bnb']):.3f} BNB ({float(first['bnb']) / (rbE / 1e18) * 100:.1f}% пула), "
          f"свапов между нашим входом и ним {between}, вход на {ENTRY:g} с → выход после него {pnl:+.2f}%, пик {peak:.1f}, сейчас {last:.1f}, "
          f"{'слит на ' + str(round(life)) + ' с' if life else 'не слит'}")
if tot:
    print(f"\nв среднем {sum(tot) / len(tot):+.2f}% на {len(tot)} монетах (бумага: без налога токена и газа)")
