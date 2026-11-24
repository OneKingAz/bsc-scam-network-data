"""27.10: how fast does the guard (0xbd418ce6) blacklist holders now? For the recent guard launches
(guard_launches.csv) every wallet that received tokens in the first minutes is checked: in which block did
allowance(guard, wallet) become > 0. Read only (eth_call on past blocks through the NodeReal archive node; the key is
read by journal.py and never printed). Out: analysis/guard_speed.csv + a summary on screen.
Run: python analysis\\guard_speed.py [hours=4] [max_holders_per_coin=25]"""
import csv, io, contextlib, os, statistics as st, sys, time
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 4
MAX_H = int(sys.argv[2]) if len(sys.argv) > 2 else 25
sys.argv = sys.argv[:1]
with contextlib.redirect_stdout(io.StringIO()):
    import bot as B
    import journal as J
from web3 import Web3

HERE = os.path.dirname(os.path.abspath(__file__))
arch = Web3(Web3.HTTPProvider("https://bsc-mainnet.nodereal.io/v1/" + J.NODEREAL_KEY, request_kwargs={"timeout": 20}))
GUARD = Web3.to_checksum_address(B.GUARD)
TR = "0x" + Web3.keccak(text="Transfer(address,address,uint256)").hex().removeprefix("0x")
ABI = [{"name": "allowance", "type": "function", "stateMutability": "view", "inputs": [{"type": "address"}] * 2,
        "outputs": [{"type": "uint256"}]}]
ours = {"0xfafd8a4e4a494da281ebfdd6f09f9a4345f90cdd": "A"}
for l in open(os.path.join(os.path.dirname(HERE), "hop_wallets.txt"), encoding="utf-8"):
    p = l.strip().split(";")
    if len(p) == 2 and p[0].isdigit():
        ours[p[1].lower()] = "W" + p[0]
SKIP = {B.ROUTER.lower(), "0x0000000000000000000000000000000000000000", "0x000000000000000000000000000000000000dead",
        GUARD.lower()}


def call(fn, tries=4):
    for k in range(tries):
        try:
            return fn()
        except Exception:
            time.sleep(0.5 * (k + 1))
    return None


def mark_block(c, who, lo, hi):
    """First block in [lo, hi] where allowance(guard, who) > 0, or None if not marked by hi."""
    if not call(lambda: c.functions.allowance(GUARD, who).call(block_identifier=hi)):
        return None
    while lo < hi:
        mid = (lo + hi) // 2
        if call(lambda: c.functions.allowance(GUARD, who).call(block_identifier=mid)):
            hi = mid
        else:
            lo = mid + 1
    return lo


head = arch.eth.block_number
now = time.time()
rows = [r for r in csv.DictReader(open(os.path.join(HERE, "guard_launches.csv"), encoding="utf-8-sig"), delimiter=";")
        if now - float(r["ts"]) < HOURS * 3600]
out = []
for r in rows:
    token, pair = Web3.to_checksum_address(r["token"]), r["pair"].lower()
    b0 = head - int((now - float(r["ts"])) / 0.45) - 300
    logs = []
    for a in range(b0, min(head, b0 + 2400), 500):  # the first ~15 min
        logs += call(lambda: arch.eth.get_logs({"address": token, "topics": [TR], "fromBlock": a,
                                                 "toBlock": min(head, a + 499)})) or []
    first = {}
    for l in logs:
        to = "0x" + l["topics"][2].hex()[-40:]
        if to.lower() in SKIP or to.lower() == pair or to.lower() == token.lower():
            continue
        first.setdefault(Web3.to_checksum_address(to), l["blockNumber"])
    c = arch.eth.contract(token, abi=ABI)
    holders = sorted(first.items(), key=lambda kv: kv[1])[:MAX_H]
    with ThreadPoolExecutor(6) as ex:
        marks = list(ex.map(lambda kv: mark_block(c, kv[0], kv[1], min(head, kv[1] + 2000)), holders))
    for (who, rb), mb in zip(holders, marks):
        out.append(dict(symbol=r["symbol"], token=token, holder=who, ours=ours.get(who.lower(), ""), recv_block=rb,
                        mark_block=mb or "", delay_blocks=(mb - rb) if mb else ""))
    d = [o["delay_blocks"] for o in out if o["symbol"] == r["symbol"] and o["delay_blocks"] != ""]
    print(f"{r['time'][11:16]} {r['symbol']:10} держателей {len(holders):2}, помечено {len(d):2}"
          + (f", через блоков: мин {min(d)}, медиана {st.median(d)}, макс {max(d)}" if d else ""), flush=True)

with open(os.path.join(HERE, "guard_speed.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(out[0].keys()) if out else ["symbol"], delimiter=";")
    w.writeheader()
    w.writerows(out)
d_all = [o["delay_blocks"] for o in out if o["delay_blocks"] != ""]
d_ours = [o["delay_blocks"] for o in out if o["delay_blocks"] != "" and o["ours"]]
d_other = [o["delay_blocks"] for o in out if o["delay_blocks"] != "" and not o["ours"]]
for name, d in (("все", d_all), ("наши", d_ours), ("чужие", d_other)):
    if d:
        q = sorted(d)
        print(f"{name:6}: n={len(d)}, блоков мин {q[0]}, 10% {q[len(q) // 10]}, медиана {st.median(q)}, "
              f"≤6 блоков (≈2.7 с): {sum(x <= 6 for x in q)}, ≤8 (≈3.6 с): {sum(x <= 8 for x in q)}")
print(f"не помечены за ~15 мин: {sum(1 for o in out if o['delay_blocks'] == '')} из {len(out)}")
