"""27.10 (user): when are the guard coins' pools emptied, at what size, and does the next launch wait for that?
For every launch in guard_launches.csv since <hours> ago: the pool's BNB over time from its Sync events (NodeReal
archive), the peak, the moment it fell below 20% of the peak (the drain) and the next launch's time.
Out: analysis/guard_drains.csv. Run: python guard_drains.py [hours]"""
import contextlib, csv, io, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
with contextlib.redirect_stdout(io.StringIO()):
    import bot as B
    import journal as J
from web3 import Web3

HERE = os.path.dirname(os.path.abspath(__file__))
arch = Web3(Web3.HTTPProvider("https://bsc-mainnet.nodereal.io/v1/" + J.NODEREAL_KEY, request_kwargs={"timeout": 30}))
arch.middleware_onion.inject(B.ExtraDataToPOAMiddleware, layer=0)
HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 24
SYNC = "0x" + Web3.keccak(text="Sync(uint112,uint112)").hex().replace("0x", "")
SPB = 0.45
head = arch.eth.block_number
now = arch.eth.get_block(head).timestamp
rows = [r for r in csv.DictReader(open(os.path.join(HERE, "guard_launches.csv"), encoding="utf-8-sig"), delimiter=";")
        if now - int(r["ts"]) < HOURS * 3600]
rows.sort(key=lambda r: int(r["ts"]))
out = []
for k, r in enumerate(rows):
    pair = Web3.to_checksum_address(r["pair"])
    start = head - int((now - int(r["ts"])) / SPB) - 400
    wbnb_first = int(Web3.to_checksum_address(B.WBNB), 16) < int(Web3.to_checksum_address(r["token"]), 16)
    series, b = [], start
    while b <= head:  # 2 h at most after the launch
        e = min(b + 4999, head, start + int(7200 / SPB))
        try:
            logs = arch.eth.get_logs({"address": pair, "fromBlock": b, "toBlock": e, "topics": [SYNC]})
        except Exception:
            time.sleep(1)
            continue
        for lg in logs:
            d = bytes(lg["data"])
            r0, r1 = int.from_bytes(d[:32], "big"), int.from_bytes(d[32:64], "big")
            series.append((lg["blockNumber"], (r0 if wbnb_first else r1) / 1e18))
        if e >= start + int(7200 / SPB) or e >= head:
            break
        b = e + 1
    if not series:
        continue
    b0 = series[0][0]
    peak_bnb, peak_b = max((v, bb) for bb, v in series)
    drain = next(((bb, v) for bb, v in series if bb > peak_b and v < 0.2 * peak_bnb), None)
    nxt = rows[k + 1] if k + 1 < len(rows) else None
    out.append(dict(time=r["time"], symbol=r["symbol"], start_bnb=round(series[0][1], 2), peak_bnb=round(peak_bnb, 2),
                    peak_min=round((peak_b - b0) * SPB / 60, 1),
                    drain_min=round((drain[0] - b0) * SPB / 60, 1) if drain else "",
                    left_bnb=round(drain[1], 2) if drain else "",
                    next_launch_min=round((int(nxt["ts"]) - int(r["ts"])) / 60, 1) if nxt else "",
                    swaps=len(series)))
    print(out[-1], flush=True)
with open(os.path.join(HERE, "guard_drains.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(out[0]), delimiter=";")
    w.writeheader()
    w.writerows(out)
