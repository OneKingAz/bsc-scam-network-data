"""29.10 (user: "pause, analysis"): would a chain with fast hand-overs for the WHOLE trade pay, and at what stake?
Since 27.10 evening the guard marks the biggest holder by a ~12-block-old snapshot, so a holder must pass the coin on
every ~2 s for the whole trade. For every guard launch in guard_launches.csv since <hours> ago: the pool's Sync events
(NodeReal archive) give the price; entry 30 s after the pool, exit EXIT_BEFORE s before the drain (the bot counter
sells ~2.5-3.5 min before it), at least 60 s after the entry. Gross % = price ratio after the 0.25% swap fee both ways
(token tax unknown); gas = FIXED + PER_MIN * minutes held (1 hand-over + 1 top-up from A every ~2.3 s).
Out: fast_econ.csv and a table by coin type and stake. Run: python fast_econ.py [hours]"""
import collections, contextlib, csv, io, os, statistics as st, sys, time
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 72
sys.argv = sys.argv[:1]
with contextlib.redirect_stdout(io.StringIO()):
    import bot as B
    import journal as J
from web3 import Web3

HERE = os.path.dirname(os.path.abspath(__file__))
a = Web3(Web3.HTTPProvider("https://bsc-mainnet.nodereal.io/v1/" + J.NODEREAL_KEY, request_kwargs={"timeout": 30}))
a.middleware_onion.inject(B.ExtraDataToPOAMiddleware, layer=0)
SYNC = "0x" + Web3.keccak(text="Sync(uint112,uint112)").hex().replace("0x", "")
SPB, ENTRY, EXIT_BEFORE = 0.45, 30, 180
PER_MIN = 26 * (63_500 + 21_000) * 0.05e-9  # BNB a minute: ~26 hand-overs, each with a top-up of the next holder
FIXED = 0.00004  # buy, approve, sell, return to A
STAKES = (0.001, 0.002, 0.003, 0.005, 0.01)
FEE = 0.9975 ** 2


def call(fn, k=5):
    for i in range(k):
        try:
            return fn()
        except Exception:
            if i == k - 1:
                raise
            time.sleep(1 + i)


head = a.eth.block_number
now = a.eth.get_block(head).timestamp
rows = sorted((r for r in csv.DictReader(open(os.path.join(HERE, "guard_launches.csv"), encoding="utf-8-sig"),
                                         delimiter=";") if now - int(r["ts"]) < HOURS * 3600), key=lambda r: int(r["ts"]))
wbnb = int(B.WBNB, 16)
out = []
for r in rows:
    pair, token = Web3.to_checksum_address(r["pair"]), Web3.to_checksum_address(r["token"])
    is0 = wbnb < int(token, 16)
    b0 = head - int((now - int(r["ts"])) / SPB) - 40
    pts = []
    for b in range(b0, min(head, b0 + 9000), 2000):
        for l in call(lambda: a.eth.get_logs({"address": pair, "fromBlock": b, "toBlock": min(head, b + 1999),
                                               "topics": [SYNC]})):
            d = bytes(l["data"])
            r0, r1 = int.from_bytes(d[:32], "big"), int.from_bytes(d[32:64], "big")
            bnb, tok = (r0, r1) if is0 else (r1, r0)
            if tok:
                pts.append((l["blockNumber"], bnb / 1e18, bnb / tok))
        if pts and pts[-1][1] < 0.2 * max(p[1] for p in pts) and pts[-1][1] < pts[0][1]:
            break  # drained
    if len(pts) < 3:
        continue
    t0 = pts[0][0]
    start, peak = pts[0][1], max(p[1] for p in pts)
    ipk = max(range(len(pts)), key=lambda i: pts[i][1])
    drain = next((p[0] for p in pts[ipk:] if p[1] < 0.2 * peak), None)
    if drain is None:
        continue
    kind = 25 if start >= 20 else 10 if start >= 5 else 1
    e_blk = t0 + ENTRY / SPB
    x_blk = max(e_blk + 60 / SPB, drain - EXIT_BEFORE / SPB)
    price_at = lambda blk: next((p[2] for p in reversed(pts) if p[0] <= blk), pts[0][2])
    p_in, p_out = price_at(e_blk), price_at(x_blk)
    gross = (p_out / p_in * FEE - 1) * 100
    mins = (x_blk - e_blk) * SPB / 60
    row = dict(time=time.strftime("%m-%d %H:%M", time.localtime(int(r["ts"]))), sym=r["symbol"], kind=kind,
               start=round(start, 2), peak=round(peak, 2), drain_min=round((drain - t0) * SPB / 60, 1),
               hold_min=round(mins, 1), gross_pct=round(gross, 1))
    for s in STAKES:
        row[f"net_{s}"] = round(gross - (FIXED + PER_MIN * mins) / s * 100, 1)
    out.append(row)
    print(row, flush=True)
with open(os.path.join(HERE, "fast_econ.csv"), "w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(out[0]), delimiter=";")
    w.writeheader()
    w.writerows(out)
print(f"\nгаз быстрых передач: {PER_MIN:.6f} BNB/мин + {FIXED} на сделку; вход {ENTRY} с, выход за {EXIT_BEFORE} с до слива")
for kind in (1, 10, 25):
    g = [x for x in out if x["kind"] == kind]
    if not g:
        continue
    print(f"\nтип {kind} BNB: монет {len(g)}, держать медиана {st.median(x['hold_min'] for x in g):.1f} мин, "
          f"рост до газа: медиана {st.median(x['gross_pct'] for x in g):+.0f}%, среднее {st.mean(x['gross_pct'] for x in g):+.0f}%")
    for s in STAKES:
        v = [x[f"net_{s}"] for x in g]
        print(f"  ставка {s}: после газа среднее {st.mean(v):+.0f}%, медиана {st.median(v):+.0f}%, в плюсе {sum(x > 0 for x in v)}/{len(v)}")
