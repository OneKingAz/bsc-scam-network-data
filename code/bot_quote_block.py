"""13.11 (outside analyst): at which block's pool state did the bot of the networks 3/4 compute its price?
For every bot buy in sig34_audit.csv: amountOutMin from the calldata, the BNB in, and the pool reserves after each block from the payment-1
to the bot's block (from Sync logs, no archive state needed). getAmountOut(amountIn) at each block's reserves; the block whose quote
gives amountOutMin / quote = 0.9999 is the block the bot read. Out: bot_quote_block.csv + text."""
import collections
import csv
import os
import sys
import time

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
WS = []
for u in ("https://bsc-rpc.publicnode.com", "https://bsc-dataseed.bnbchain.org"):
    w = Web3(Web3.HTTPProvider(u, request_kwargs={"timeout": 30}))
    w.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    WS.append(w)
LOGW = Web3(Web3.HTTPProvider("https://rpc.48.club", request_kwargs={"timeout": 30}))
WB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
SY = "0x" + Web3.keccak(text="Sync(uint112,uint112)").hex().replace("0x", "")
fac = WS[0].eth.contract("0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73", abi=[{"name": "getPair", "type": "function", "stateMutability": "view",
                         "inputs": [{"type": "address"}, {"type": "address"}], "outputs": [{"type": "address"}]}])


def call(f):
    for k in range(8):
        try:
            return f(WS[k % 2])
        except Exception:
            time.sleep(0.5 + 0.4 * k)
    return None


tokens = {}
for fn in ("signal_test3.csv", "signal_test4.csv"):
    for r in csv.DictReader(open(os.path.join(HERE, fn), encoding="utf-8-sig"), delimiter=";"):
        tokens[(r["time"], r["symbol"])] = r["token"]
rows = [r for r in csv.DictReader(open(os.path.join(HERE, "sig34_audit.csv"), encoding="utf-8-sig"), delimiter=";") if r["bot_tx"]]
PAIR = {}
out = []
for r in rows:
    tok = Web3.to_checksum_address(tokens[(r["time"], r["symbol"])])
    if tok not in PAIR:
        PAIR[tok] = (call(lambda w: fac.functions.getPair(tok, WB).call()), int(WB, 16) < int(tok, 16))
    pair, wb0 = PAIR[tok]
    pay, bot, our = int(r["pay_block"]), int(r["bot_block"]), int(r["our_block"])
    t = call(lambda w: w.eth.get_transaction(r["bot_tx"]))
    inp = bytes(t["input"])
    amin, ain = int.from_bytes(inp[4:36], "big"), t["value"]
    logs = None  # 13.11: publicnode answers old get_logs with an EMPTY list (no error); 48club keeps them
    for k in range(6):
        try:
            logs = LOGW.eth.get_logs({"address": pair, "fromBlock": pay - 40, "toBlock": bot, "topics": [SY]})
            break
        except Exception:
            time.sleep(1 + k)
    logs = logs or []
    logs.sort(key=lambda l: (l["blockNumber"], l["logIndex"]))
    state, res = None, {}
    li = 0
    for b in range(pay - 40, bot + 1):
        while li < len(logs) and logs[li]["blockNumber"] <= b:
            d = bytes(logs[li]["data"])
            r0, r1 = int.from_bytes(d[:32], "big"), int.from_bytes(d[32:64], "big")
            state = (r0, r1) if wb0 else (r1, r0)  # (bnb, token)
            li += 1
        if state and b >= pay - 1:
            rb, rt = state
            q = ain * 9975 * rt // (rb * 10000 + ain * 9975)
            res[b] = amin / q * 100 if q else 0
    match = [b for b, v in res.items() if abs(v - 99.99) < 0.0005]
    # the latest block whose state the minimum still matches = the block the bot read (state unchanged after it gives the same quote)
    read = max(match) if match else None
    out.append(dict(net=r["net"], time=r["time"], symbol=r["symbol"], pay=pay, our=our, bot=bot, bot_status=r["bot_status"],
                    our_vs_pay=int(r["our_vs_pay"]), read_vs_pay=(read - pay) if read is not None else "", read_before_our=int(read < our) if read is not None else "",
                    ratio_at_bot_block=round(res.get(bot - 1, 0), 4), pnl=r["pnl_pct"]))
cols = list(out[0].keys())
with open(os.path.join(HERE, "bot_quote_block.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=cols, delimiter=";")
    w.writeheader()
    w.writerows(out)
ok = [x for x in out if x["read_vs_pay"] != ""]
print(f"транзакций бота {len(out)}, блок расчёта найден у {len(ok)} (amountOutMin = 99.99% котировки по резервам этого блока)")
print("бот считал цену по блоку (от платежа):", dict(sorted(collections.Counter(x["read_vs_pay"] for x in ok).items())))
print("наша покупка попала ДО блока, по которому бот считал (расчёт уже с нами) → бот прошёл:",
      collections.Counter(x["bot_status"] for x in ok if x["read_before_our"] == 0 and x["our"] <= x["pay"] + x["read_vs_pay"]))
a = [x for x in ok if x["our"] <= x["pay"] + x["read_vs_pay"]]
b = [x for x in ok if x["our"] > x["pay"] + x["read_vs_pay"]]
print(f"наша покупка в блоке расчёта или раньше: {len(a)}, бот прошёл {sum(x['bot_status'] == '1' for x in a)}")
print(f"наша покупка ПОСЛЕ блока расчёта: {len(b)}, бот прошёл {sum(x['bot_status'] == '1' for x in b)}")
