"""28.10: why did NOKc (27.10 23:59) and CRML (28.10 07:09) get stuck on W1? For each coin: our buy, crumbs and the
hand-over to W1 (Transfer logs from our wallets), and every transaction of the guard 0xbd418ce6 in the same minutes
(who it marked, in which block, gas). Run: python stuck_study.py"""
import contextlib, io, os, sys, time
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
with contextlib.redirect_stdout(io.StringIO()):
    import bot as B
    import journal as J
from web3 import Web3

HERE = os.path.dirname(os.path.abspath(__file__))
a = Web3(Web3.HTTPProvider("https://bsc-mainnet.nodereal.io/v1/" + J.NODEREAL_KEY, request_kwargs={"timeout": 30}))
a.middleware_onion.inject(B.ExtraDataToPOAMiddleware, layer=0)
G = Web3.to_checksum_address(B.GUARD)
TRANSFER = "0x" + Web3.keccak(text="Transfer(address,address,uint256)").hex().replace("0x", "")
ours = {}
for line in open(os.path.join(os.path.dirname(HERE), "hop_wallets.txt"), encoding="utf-8"):
    p = line.strip().split(";")
    if len(p) > 1 and p[1].startswith("0x"):
        ours[p[1].lower()] = "W" + p[0]
ours["0xfafd8a4e4a494da281ebfdd6f09f9a4345f90cdd"] = "A"


def call(fn, k=5):
    for i in range(k):
        try:
            return fn()
        except Exception:
            if i == k - 1:
                raise
            time.sleep(1 + i)


def nonce_blocks(addr, lo, hi):
    out = []
    n = lambda b: call(lambda: a.eth.get_transaction_count(addr, b))

    def split(l, h, nl, nh):
        if nl == nh:
            return
        if h - l == 1:
            out.append(h)
            return
        m = (l + h) // 2
        nm = n(m)
        split(l, m, nl, nm)
        split(m, h, nm, nh)
    split(lo, hi, n(lo), n(hi))
    return out


head = a.eth.block_number
now = a.eth.get_block(head).timestamp
CASES = [("NOKc", "0x59c6B20973340403D9F5f062F6251D5134Ce14Ed", "2026-10-27 23:59:09"),
         ("CRML", "0x08Cc412E4829366B1A5c7d3624A8a0B81200a913", "2026-10-28 07:09:16")]
if len(sys.argv) > 3:  # python stuck_study.py SYM TOKEN "YYYY-MM-DD HH:MM:SS"  (another trade to compare)
    CASES = [(sys.argv[1], sys.argv[2], sys.argv[3])]
for sym, token, t_buy in CASES:
    token = Web3.to_checksum_address(token)
    b_buy = head - int((now - time.mktime(time.strptime(t_buy, "%Y-%m-%d %H:%M:%S"))) / 0.45)
    lo, hi = b_buy - 200, min(head, b_buy + 400)
    print(f"\n=== {sym} {token}  (покупка ~блок {b_buy})")
    logs = call(lambda: a.eth.get_logs({"address": token, "fromBlock": lo, "toBlock": hi, "topics": [TRANSFER]}))
    for lg in logs:
        f = "0x" + lg["topics"][1].hex()[-40:]
        t = "0x" + lg["topics"][2].hex()[-40:]
        if f.lower() in ours or t.lower() in ours:
            print(f"  перевод блок {lg['blockNumber']} idx {lg['logIndex']}: {ours.get(f.lower(), f[:10])} → {ours.get(t.lower(), t[:10])}")
    for blk in nonce_blocks(G, lo, hi):
        b = call(lambda: a.eth.get_block(blk, full_transactions=True))
        for tx in b.transactions:
            if tx["from"] == G:
                inp = bytes(tx["input"]).hex()
                tgt = "0x" + inp[32:72]
                print(f"  СТОРОЖ блок {blk} idx {tx['transactionIndex']}: {inp[:8]} на {tx['to'][:10]} → {ours.get(tgt.lower(), tgt[:12])}"
                      f"  ({'эта монета' if tx['to'] and tx['to'].lower() == token.lower() else 'другая'}) газ {tx['gasPrice'] / 1e9}")
