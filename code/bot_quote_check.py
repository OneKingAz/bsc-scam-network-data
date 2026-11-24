"""13.11 (outside analyst): did the bot of the networks 3/4 compute its amountOutMin from the pool BEFORE or AFTER our buy?
For each bot buy in sig34_audit.csv: amountOutMin (calldata) / getAmountOut(value) at the reserves just before our buy and just after it.
Exactly 99.99 % on one side = the state the bot read. Pool reserves from Sync logs (48club: publicnode returns old logs empty).
Read-only. Out: bot_quote_check.csv + text."""
import collections
import csv
import os
import sys
import time

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
L = Web3(Web3.HTTPProvider("https://rpc.48.club", request_kwargs={"timeout": 40}))
P = Web3(Web3.HTTPProvider("https://bsc-rpc.publicnode.com", request_kwargs={"timeout": 30}))
P.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
WB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
SY = "0x" + Web3.keccak(text="Sync(uint112,uint112)").hex().replace("0x", "")
F = P.eth.contract("0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73", abi=[{"name": "getPair", "type": "function", "stateMutability": "view",
                   "inputs": [{"type": "address"}, {"type": "address"}], "outputs": [{"type": "address"}]}])


def retry(f, n=6):
    for k in range(n):
        try:
            return f()
        except Exception:
            time.sleep(0.7 + 0.7 * k)
    return None


tokens = {}
for fn in ("signal_test3.csv", "signal_test4.csv"):
    for r in csv.DictReader(open(os.path.join(HERE, fn), encoding="utf-8-sig"), delimiter=";"):
        tokens[(r["time"], r["symbol"])] = r["token"]
rows = [r for r in csv.DictReader(open(os.path.join(HERE, "sig34_audit.csv"), encoding="utf-8-sig"), delimiter=";") if r["bot_tx"]]
by_tok = collections.defaultdict(list)
for r in rows:
    by_tok[tokens[(r["time"], r["symbol"])]].append(r)
SYNCS = {}
out = []
n = 0
for tk, rs in by_tok.items():
    tok = Web3.to_checksum_address(tk)
    pair = retry(lambda: F.functions.getPair(tok, WB).call())
    wb0 = int(WB, 16) < int(tok, 16)
    logs = []
    spans = sorted((int(r["pay_block"]) - 800, int(r["bot_block"])) for r in rs)  # a bot buys every ~80 s: 800 blocks back hold the last Sync
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    for lo, hi in merged:  # 48club times out on 2000-block log ranges (30 s), 400 take ~5 s
        for b in range(lo, hi + 1, 400):
            part = retry(lambda: L.eth.get_logs({"address": pair, "fromBlock": b, "toBlock": min(hi, b + 399), "topics": [SY]}))
            logs += part or []
    logs.sort(key=lambda l: (l["blockNumber"], l["transactionIndex"], l["logIndex"]))
    seq = []
    for l in logs:
        d = bytes(l["data"])
        r0, r1 = int.from_bytes(d[:32], "big"), int.from_bytes(d[32:64], "big")
        seq.append(((l["blockNumber"], l["transactionIndex"]), (r0, r1) if wb0 else (r1, r0)))

    def state(pos):
        st = None
        for p, s in seq:
            if p < pos:
                st = s
            else:
                break
        return st
    for r in rs:
        n += 1
        t = retry(lambda: P.eth.get_transaction(r["bot_tx"]))
        if not t:
            continue
        inp = bytes(t["input"])
        amin, ain = int.from_bytes(inp[4:36], "big"), t["value"]
        our = (int(r["our_block"]), int(r["our_idx"]))
        ratio = lambda st: amin / (ain * 9975 * st[1] // (st[0] * 10000 + ain * 9975)) * 100 if st else None
        before, after = ratio(state(our)), ratio(state((our[0], our[1] + 1)))
        tag = ("до нашей покупки" if before and abs(before - 99.99) < 0.0006 else "после нашей покупки" if after and abs(after - 99.99) < 0.0006
               else "нет данных" if before is None else "другое")
        out.append(dict(net=r["net"], time=r["time"], symbol=r["symbol"], our_vs_pay=r["our_vs_pay"], bot_vs_pay=r["bot_vs_pay"], bot_status=r["bot_status"],
                        ratio_before_us=round(before, 5) if before else "", ratio_after_us=round(after, 5) if after else "", bot_quoted=tag, pnl=r["pnl_pct"]))
    print(f"  {n}/{len(rows)}", flush=True)
cols = list(out[0].keys())
with open(os.path.join(HERE, "bot_quote_check.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=cols, delimiter=";")
    w.writeheader()
    w.writerows(out)
L2 = [f"проверено покупок бота: {len(out)}"]
c = collections.Counter((x["bot_quoted"], "прошла" if x["bot_status"] == "1" else "отклонена") for x in out)
for k, v in sorted(c.items()):
    L2.append(f"  бот считал цену {k[0]}, его покупка {k[1]}: {v}")
for k in sorted({x["our_vs_pay"] for x in out}, key=int):
    g = [x for x in out if x["our_vs_pay"] == k]
    L2.append(f"  мы +{k}: " + ", ".join(f"{a}: {b}" for a, b in collections.Counter(x["bot_quoted"] for x in g).most_common()))
open(os.path.join(HERE, "bot_quote_check.txt"), "w", encoding="utf-8").write("\n".join(L2))
print("\n".join(L2))
