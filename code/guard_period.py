"""27.10 (user): is the guard's check predictable to the block? Finds every transaction of the guard 0xbd418ce6 in the
last HOURS hours (nonce bisection on the NodeReal archive node), with the token and the marked wallet.
Out: analysis/guard_marks.csv (block, time, nonce, token, wallet, gas price). Run: python guard_period.py [hours]"""
import contextlib, csv, io, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
with contextlib.redirect_stdout(io.StringIO()):
    import bot as B
    import journal as J
from web3 import Web3

arch = Web3(Web3.HTTPProvider("https://bsc-mainnet.nodereal.io/v1/" + J.NODEREAL_KEY, request_kwargs={"timeout": 20}))
arch.middleware_onion.inject(B.ExtraDataToPOAMiddleware, layer=0)
G = Web3.to_checksum_address(B.GUARD)
HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 12
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), sys.argv[2] if len(sys.argv) > 2 else "guard_marks.csv")


def nonce(b):
    for k in range(5):
        try:
            return arch.eth.get_transaction_count(G, b)
        except Exception:
            if k == 4:
                raise


def split(lo, hi, nlo, nhi, out):
    """Blocks in (lo, hi] where the nonce grew; nlo/nhi = nonce at lo/hi."""
    if nhi == nlo:
        return
    if hi - lo == 1:
        out.append(hi)
        return
    mid = (lo + hi) // 2
    nm = nonce(mid)
    split(lo, mid, nlo, nm, out)
    split(mid, hi, nm, nhi, out)


head = arch.eth.block_number
lo = head - int(HOURS * 3600 / 0.45)
blocks = []
split(lo, head, nonce(lo), nonce(head), blocks)
rows = []
for b in blocks:
    blk = arch.eth.get_block(b, full_transactions=True)
    for tx in blk.transactions:
        if tx["from"] == G:
            inp = bytes(tx["input"]).hex()
            rows.append([b, blk.timestamp, tx["nonce"], tx["transactionIndex"], tx["to"], "0x" + inp[32:72], inp[:8],
                         tx["gasPrice"] / 1e9])
with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f, delimiter=";")
    w.writerow(["block", "ts", "nonce", "index", "token", "wallet", "method", "gwei"])
    w.writerows(rows)
print(len(rows), "transactions of the guard ->", OUT)
