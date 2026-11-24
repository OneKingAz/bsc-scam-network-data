"""30.10 (user: "test every option"): how much time does the guard really leave a holder, how regular is its rhythm, and
how many of our wallets stay clean (reusable) - from the fast-hand-over trades (all marks of the coin, NodeReal archive).
For every guard mark M on one of our wallets that got the coin at block R and passed it on at block P:
  the guard saw the wallet holding at its snapshot S = M - L, so L > M - P and L <= M - R.
Max(M - P) .. min(M - R) over all marks = the snapshot lag L; a holder keeping the coin fewer than L blocks is safe.
Also: blocks between consecutive marks of the coin (the rhythm) and the share of our holders never marked.
Out: guard_lag.csv (one row per mark) and a summary. Run: python guard_lag.py [SYM TOKEN "YYYY-mm-dd HH:MM:SS" ...]"""
import collections, contextlib, csv, io, os, statistics as st, sys, time
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARGS = sys.argv[1:]
sys.argv = sys.argv[:1]
with contextlib.redirect_stdout(io.StringIO()):
    import bot as B
    import journal as J
from web3 import Web3

HERE = os.path.dirname(os.path.abspath(__file__))
a = Web3(Web3.HTTPProvider("https://bsc-mainnet.nodereal.io/v1/" + J.NODEREAL_KEY, request_kwargs={"timeout": 30}))
a.middleware_onion.inject(B.ExtraDataToPOAMiddleware, layer=0)
G = Web3.to_checksum_address(B.GUARD)
TRANSFER = "0x" + Web3.keccak(text="Transfer(address,address,uint256)").hex().replace("0x", "")
SPB = 0.45
ours = {"0xfafd8a4e4a494da281ebfdd6f09f9a4345f90cdd": "A"}
for line in open(os.path.join(os.path.dirname(HERE), "hop_wallets.txt"), encoding="utf-8"):
    p = line.strip().split(";")
    if len(p) > 1 and p[1].startswith("0x"):
        ours[p[1].lower()] = "W" + p[0]
TRADES = [("GMEc", "0xe22703f8276eD219D39331c4C381AC1F9c992628", "2026-10-30 00:59:30", 1000),
          ("SKUUB", "0x1125337b97D9b8B63A6Fade866ef4D987F6e815e", "2026-10-29 20:22:25", 400),
          ("ABI", "0x65745B06c9Fca1f3450E597D8F3Da3c59a38Ea31", "2026-10-29 21:27:35", 400),
          ("DINAR", "0x98D3f3139816FF2f228979A0bC002f2e6a87a2aE", "2026-10-29 00:12:40", 400)]
if ARGS:
    TRADES = [(ARGS[i], ARGS[i + 1], ARGS[i + 2], 1000) for i in range(0, len(ARGS) - 2, 3)]


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
rows, summ = [], []
for sym, token, t, span_s in TRADES:
    token = Web3.to_checksum_address(token)
    b_buy = head - int((now - time.mktime(time.strptime(t, "%Y-%m-%d %H:%M:%S"))) / SPB)
    lo, hi = b_buy - 60, min(head, b_buy + int((span_s + 600) / SPB))
    logs = []
    for b in range(lo, hi + 1, 2000):
        logs += call(lambda: a.eth.get_logs({"address": token, "fromBlock": b, "toBlock": min(hi, b + 1999),
                                             "topics": [TRANSFER]}))
    rx, tx_out = collections.defaultdict(list), collections.defaultdict(list)
    for l in logs:
        f, to = "0x" + l["topics"][1].hex()[-40:], "0x" + l["topics"][2].hex()[-40:]
        if to in ours:
            rx[to].append(l["blockNumber"])
        if f in ours:
            tx_out[f].append(l["blockNumber"])
    marks = []
    for blk in nonce_blocks(G, lo, hi):
        bl = call(lambda: a.eth.get_block(blk, full_transactions=True))
        for tx in bl.transactions:
            inp = bytes(tx["input"]).hex()
            if tx["from"] == G and tx["to"] and tx["to"].lower() == token.lower() and inp[:8] == "095ea7b3":
                marks.append((blk, "0x" + inp[32:72]))
    marks.sort()
    lows, highs, holds = [], [], []
    for m, tgt in marks:
        if tgt not in ours or not rx.get(tgt):
            continue
        r = max((b for b in rx[tgt] if b <= m), default=None)
        if r is None:
            continue
        p = min((b for b in tx_out.get(tgt, []) if b >= r), default=None)
        stuck = p is None or p > m
        row = dict(sym=sym, mark=m, target=ours[tgt], got=r, passed=p if p else "", m_minus_r=m - r,
                   m_minus_p=(m - p) if p and p <= m else "", hold=(p - r) if p else "", stuck=int(stuck))
        rows.append(row)
        if not stuck:
            lows.append(m - p)
            highs.append(m - r)
            holds.append(p - r)
    gaps = [b - a_ for (a_, _), (b, _) in zip(marks, marks[1:]) if b - a_ < 40]
    held_ours = {w for w in rx if w != "0xfafd8a4e4a494da281ebfdd6f09f9a4345f90cdd"}
    marked = {t_ for _, t_ in marks}
    clean = sum(1 for w in held_ours if w not in marked)
    s = dict(sym=sym, marks=len(marks), ours_marked=len(highs), holders=len(held_ours), clean=clean,
             lag_low=max(lows) if lows else None, lag_high=min(highs) if highs else None,
             hold_med=st.median(holds) if holds else None,
             gap_med=st.median(gaps) if gaps else None, gap_sd=round(st.pstdev(gaps), 1) if len(gaps) > 1 else None,
             gap_min=min(gaps) if gaps else None, gap_max=max(gaps) if gaps else None)
    summ.append(s)
    print(s, flush=True)
if rows:
    with open(os.path.join(HERE, "guard_lag.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter=";")
        w.writeheader()
        w.writerows(rows)
print("\nмонета  меток  наших_пустых  держателей  чистых  задержка_снимка(блоков)  держали(мед)  между_метками мед/σ/мин/макс")
for s in summ:
    print(f"{s['sym']:<7} {s['marks']:5} {s['ours_marked']:12} {s['holders']:10} {s['clean']:7}   "
          f"{s['lag_low']}..{s['lag_high']:<14} {s['hold_med']}   {s['gap_med']}/{s['gap_sd']}/{s['gap_min']}/{s['gap_max']}")
