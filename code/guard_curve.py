"""27.10 (user): how much a guard coin's price grows with time, by pool type (start 25 / 10 BNB). Price of a token in
a constant-product pool ~ (BNB in pool)^2, so the gain from an entry at ENTRY_MIN to minute t is (R_t / R_entry)^2.
Also the chain's gas for that holding time (GAS_PER_MIN, network price, 5 crumbs + hand-over every 15 s).
Run: python guard_curve.py [hours]  (uses the NodeReal archive like guard_drains.py)"""
import contextlib, csv, io, os, statistics as st, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
with contextlib.redirect_stdout(io.StringIO()):
    import bot as B
    import journal as J
from web3 import Web3

HERE = os.path.dirname(os.path.abspath(__file__))
arch = Web3(Web3.HTTPProvider("https://bsc-mainnet.nodereal.io/v1/" + J.NODEREAL_KEY, request_kwargs={"timeout": 30}))
arch.middleware_onion.inject(B.ExtraDataToPOAMiddleware, layer=0)
HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 16
SYNC = "0x" + Web3.keccak(text="Sync(uint112,uint112)").hex().replace("0x", "")
SPB, ENTRY_MIN = 0.45, 0.5
GAS_PER_MIN = 4 * 6 * 63_500 * 0.05e-9  # BNB: 4 cycles a minute, 6 transfers each
MARKS = [1, 2, 3, 5, 8, 10, 12, 15, 20, 30, 40, 45, 48]
head = arch.eth.block_number
now = arch.eth.get_block(head).timestamp
rows = [r for r in csv.DictReader(open(os.path.join(HERE, "guard_launches.csv"), encoding="utf-8-sig"), delimiter=";")
        if now - int(r["ts"]) < HOURS * 3600]
by_type = {}
for r in rows:
    pair = Web3.to_checksum_address(r["pair"])
    start = head - int((now - int(r["ts"])) / SPB) - 400
    wbnb_first = int(Web3.to_checksum_address(B.WBNB), 16) < int(Web3.to_checksum_address(r["token"]), 16)
    series, b = [], start
    while True:
        e = min(b + 4999, head, start + int(3600 / SPB))
        try:
            logs = arch.eth.get_logs({"address": pair, "fromBlock": b, "toBlock": e, "topics": [SYNC]})
        except Exception:
            time.sleep(1)
            continue
        for lg in logs:
            d = bytes(lg["data"])
            r0, r1 = int.from_bytes(d[:32], "big"), int.from_bytes(d[32:64], "big")
            series.append((lg["blockNumber"], (r0 if wbnb_first else r1) / 1e18))
        if e >= start + int(3600 / SPB) or e >= head:
            break
        b = e + 1
    if not series:
        continue
    b0, start_bnb = series[0]
    kind = 25 if start_bnb > 20 else 10 if start_bnb > 5 else 1

    def at(minute):
        blk = b0 + minute * 60 / SPB
        v = [x for bb, x in series if bb <= blk]
        return v[-1] if v else None
    entry = at(ENTRY_MIN)
    g = {}
    for m in MARKS:
        v = at(m)
        if v and v > 0.5 * max(x for _, x in series[:1]):  # not after the drain
            g[m] = (v / entry) ** 2 - 1
    by_type.setdefault(kind, []).append(g)
for kind in sorted(by_type, reverse=True):
    gs = by_type[kind]
    print(f"\nСтарт {kind} BNB, монет {len(gs)}: рост цены от входа на {ENTRY_MIN} мин (медиана, мин…макс), газ цепочки")
    for m in MARKS:
        v = [g[m] for g in gs if m in g]
        if len(v) >= max(2, len(gs) // 2):
            print(f"  {m:>2} мин: {st.median(v) * 100:+6.0f}%  ({min(v) * 100:+.0f}…{max(v) * 100:+.0f}%)  "
                  f"газ ~{GAS_PER_MIN * (m - ENTRY_MIN):.5f} BNB")
