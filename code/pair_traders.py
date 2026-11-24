"""27.10 (user): who buys and who sells in a pair. Every Swap since the pool appeared: buy/sell, BNB, and the wallet
that sent the transaction (tx.from); per wallet: buys, sells, net BNB, first/last minute, guard-marked or not, ours.
Run: python pair_traders.py <pair> [blocks back, default from the pool's first Sync]"""
import collections, contextlib, csv, io, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
with contextlib.redirect_stdout(io.StringIO()):
    import bot as B
from web3 import Web3

HERE = os.path.dirname(os.path.abspath(__file__))
w3 = B.w3
SPB = 0.45
pair = Web3.to_checksum_address(sys.argv[1])
PAIR_ABI = [{"name": n, "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "address"}]}
            for n in ("token0", "token1")]
pc = w3.eth.contract(pair, abi=PAIR_ABI)
t0, t1 = pc.functions.token0().call(), pc.functions.token1().call()
token = t1 if t0.lower() == B.WBNB.lower() else t0
wbnb_is0 = t0.lower() == B.WBNB.lower()
SWAP = "0x" + Web3.keccak(text="Swap(address,uint256,uint256,uint256,uint256,address)").hex().replace("0x", "")
head = w3.eth.block_number
back = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
logs = []
for b in range(head - back, head + 1, 2000):
    logs += w3.eth.get_logs({"address": pair, "fromBlock": b, "toBlock": min(b + 1999, head), "topics": [SWAP]})
GUARD = Web3.to_checksum_address(B.GUARD)
tok = w3.eth.contract(token, abi=B.TRANSFER_ABI + [{"name": "allowance", "type": "function", "stateMutability": "view",
                                                    "inputs": [{"type": "address"}] * 2, "outputs": [{"type": "uint256"}]},
                                                   {"name": "symbol", "type": "function", "stateMutability": "view",
                                                    "inputs": [], "outputs": [{"type": "string"}]}])
sym = tok.functions.symbol().call()
ours = set()
for line in open(os.path.join(os.path.dirname(HERE), "hop_wallets.txt"), encoding="utf-8"):
    p = line.strip().split(";")
    if len(p) > 1 and p[1].startswith("0x"):
        ours.add(p[1].lower())
W = collections.defaultdict(lambda: dict(buys=0, sells=0, bnb_in=0.0, bnb_out=0.0, first=None, last=None))
b_first = logs[0]["blockNumber"] if logs else head
for lg in logs:
    d = bytes(lg["data"])
    a0i, a1i, a0o, a1o = (int.from_bytes(d[i:i + 32], "big") for i in (0, 32, 64, 96))
    bnb_in, bnb_out = (a0i, a0o) if wbnb_is0 else (a1i, a1o)
    frm = w3.eth.get_transaction(lg["transactionHash"])["from"].lower()
    w = W[frm]
    m = round((lg["blockNumber"] - b_first) * SPB / 60, 1)
    w["first"] = w["first"] if w["first"] is not None else m
    w["last"] = m
    if bnb_in:
        w["buys"] += 1
        w["bnb_in"] += bnb_in / 1e18
    else:
        w["sells"] += 1
        w["bnb_out"] += bnb_out / 1e18
print(f"{sym} пара {pair}: свапов {len(logs)} за {round((head - b_first) * SPB / 60)} мин, кошельков {len(W)}")
rows = sorted(W.items(), key=lambda kv: -(kv[1]["bnb_in"] + kv[1]["bnb_out"]))
for a, w in rows[:25]:
    marked = tok.functions.allowance(GUARD, Web3.to_checksum_address(a)).call() > 0
    tag = "НАШ" if a in ours or a == "0xfafd8a4e4a494da281ebfdd6f09f9a4345f90cdd" else ("помечен" if marked else "")
    print(f"{a[:12]} покупок {w['buys']:3d} на {w['bnb_in']:8.3f} BNB | продаж {w['sells']:3d} на {w['bnb_out']:8.3f} BNB "
          f"| чистыми {w['bnb_out'] - w['bnb_in']:+8.3f} | мин {w['first']}–{w['last']} {tag}")
tot_in = sum(w["bnb_in"] for w in W.values())
tot_out = sum(w["bnb_out"] for w in W.values())
print(f"всего куплено на {tot_in:.2f} BNB, продано на {tot_out:.2f} BNB; продавцов {sum(1 for w in W.values() if w['sells'])}")
