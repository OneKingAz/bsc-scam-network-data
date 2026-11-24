"""10.11: family 3553 — the one big buy (~10 % of the pool at 14-18 min) that makes most of the price. Is it paid for just before (a payment we
could see, as on the networks 1-5)? Its slippage (amountOutMin vs the quote)? Read-only, open nodes. Usage: python big_buy3553.py [hours=10]"""
import itertools
import sys
import threading
import time

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

sys.stdout.reconfigure(encoding="utf-8")
H = float(sys.argv[1]) if len(sys.argv) > 1 else 10
URLS = ["https://bsc-rpc.publicnode.com", "https://rpc.48.club", "https://bsc-dataseed.bnbchain.org", "https://bsc-dataseed1.defibit.io"]
W3S = []
for u in URLS:
    w = Web3(Web3.HTTPProvider(u, request_kwargs={"timeout": 30}))
    w.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    W3S.append(w)
RR, LOCK = itertools.count(), threading.Lock()
SPB = 0.45
F = Web3.to_checksum_address("0xca143ce32fe78f1f7019d7d551a6402fc5350c73")
WB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
PC = "0x" + Web3.keccak(text="PairCreated(address,address,address,uint256)").hex().replace("0x", "")
SW = "0x" + Web3.keccak(text="Swap(address,uint256,uint256,uint256,uint256,address)").hex().replace("0x", "")
SYNC = "0x" + Web3.keccak(text="Sync(uint112,uint112)").hex().replace("0x", "")
RABI = [{"name": n, "type": "function", "stateMutability": "payable", "outputs": [],
         "inputs": [{"name": "amountOutMin", "type": "uint256"}, {"name": "path", "type": "address[]"}, {"name": "to", "type": "address"}, {"name": "deadline", "type": "uint256"}]}
        for n in ("swapExactETHForTokens", "swapExactETHForTokensSupportingFeeOnTransferTokens")]
router = Web3().eth.contract(Web3.to_checksum_address("0x10ED43C718714eb63d5aA57B78B54704E256024E"), abi=RABI)


def call(f, logs=False, tries=10):
    for k in range(tries):
        with LOCK:
            i = next(RR) % (2 if logs else len(W3S))
        try:
            return f(W3S[i])
        except Exception:
            time.sleep(0.5 + k * 0.7)
    return None


head = call(lambda w: w.eth.block_number)
lo = head - int(H * 3600 / SPB)
coins = []
for b in range(lo, head + 1, 1000):
    for l in call(lambda w: w.eth.get_logs({"address": F, "fromBlock": b, "toBlock": min(head, b + 999), "topics": [PC]}), logs=True) or []:
        t0, t1 = "0x" + l["topics"][1].hex()[-40:], "0x" + l["topics"][2].hex()[-40:]
        if WB in (t0.lower(), t1.lower()):
            tok = Web3.to_checksum_address(t1 if t0.lower() == WB else t0)
            c = call(lambda w: bytes(w.eth.get_code(tok)))
            if c and Web3.keccak(c).hex().replace("0x", "")[:10] == "1f6f5a665a":
                coins.append(dict(pair=Web3.to_checksum_address("0x" + bytes(l["data"])[12:32].hex()), wb0=t0.lower() == WB, block=l["blockNumber"]))
print(f"монет семьи: {len(coins)}", flush=True)
for c in coins:
    end = min(head, c["block"] + int(35 * 60 / SPB))
    sw = []
    for a in range(c["block"], end + 1, 1000):
        sw += call(lambda w: w.eth.get_logs({"address": c["pair"], "fromBlock": a, "toBlock": min(end, a + 999), "topics": [SW]}), logs=True) or []
    big = None
    for l in sw:
        d = bytes(l["data"])
        v = [int.from_bytes(d[i:i + 32], "big") for i in (0, 32, 64, 96)]
        bin_ = (v[0] if c["wb0"] else v[1]) / 1e18
        if bin_ >= 2 and (big is None or bin_ > big[1]):
            big = (l, bin_)
    if not big:
        continue
    l, bin_ = big
    t = call(lambda w: w.eth.get_transaction(l["transactionHash"]))
    bot, bb = t["from"], l["blockNumber"]
    # balance of the bot before: was it paid just before?
    bal_now = call(lambda w: w.eth.get_balance(bot, bb - 1))
    bal_10 = call(lambda w: w.eth.get_balance(bot, bb - 12))
    paid = None
    for k in range(bb - 12, bb + 1):
        blk = call(lambda w: w.eth.get_block(k, full_transactions=True))
        for x in blk.transactions if blk else []:
            if (x["to"] or "").lower() == bot.lower() and x["value"] >= 0.5 * t["value"]:
                paid = (k, x["from"], x["value"] / 1e18, x["transactionIndex"])
    try:
        fn, args = router.decode_function_input(t["input"])
        mn = args["amountOutMin"]
    except Exception:
        fn, mn = None, None
    sy = call(lambda w: w.eth.get_logs({"address": c["pair"], "fromBlock": bb - 400, "toBlock": bb, "topics": [SYNC]}), logs=True) or []
    pre = [s for s in sy if (s["blockNumber"], s["transactionIndex"]) < (bb, t["transactionIndex"])]
    tol = ""
    if pre and mn is not None:
        d = bytes(pre[-1]["data"])
        r0, r1 = int.from_bytes(d[:32], "big"), int.from_bytes(d[32:64], "big")
        rb, rt = (r0, r1) if c["wb0"] else (r1, r0)
        a = t["value"] * 9975
        out = a * rt // (rb * 10000 + a)
        tol = f"{100 - mn / out * 100:.3f}%" if out else ""
    print(f"{c['pair'][:10]} крупная покупка {bin_:.2f} BNB на {(bb - c['block']) * SPB / 60:.1f} мин, бот {bot[:10]}, газ {t['gasPrice'] / 1e9:.3f}; "
          f"баланс бота за 12 бл. {bal_10 / 1e18 if bal_10 is not None else '?':.3f} → перед покупкой {bal_now / 1e18 if bal_now is not None else '?':.3f}; "
          + (f"ОПЛАЧЕНА за {bb - paid[0]} бл. от {paid[1][:10]} на {paid[2]:.3f} BNB" if paid else "платежа за 12 бл. нет") + (f"; допуск {tol}" if tol else ""), flush=True)
