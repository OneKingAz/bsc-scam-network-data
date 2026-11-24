"""28.10 (user: "first check"): in what order does the guard 0xbd418ce6 mark holders? For each chain trade from
hop_trades.csv: all Transfer logs of the coin and all guard approvals in the trade window; balances replayed from the
logs. For every mark: among the not-yet-marked addresses the guard marks in this window (its "victims"), the rank of
the target by balance 3 blocks before the mark (1 = biggest) and by first receipt (1 = oldest).
Hypothesis "biggest first" -> balance rank 1; "FIFO" -> receipt rank 1. Run: python mark_order.py"""
import collections, contextlib, csv, io, os, sys, time
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
SPB = 0.45
ours = {"0xfafd8a4e4a494da281ebfdd6f09f9a4345f90cdd": "A"}
for line in open(os.path.join(os.path.dirname(HERE), "hop_wallets.txt"), encoding="utf-8"):
    p = line.strip().split(";")
    if len(p) > 1 and p[1].startswith("0x"):
        ours[p[1].lower()] = "W" + p[0]
name = lambda x: ours.get(x, x[:8])


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
rows = [r for r in csv.DictReader(open(os.path.join(HERE, "hop_trades.csv"), encoding="utf-8-sig"), delimiter=";")
        if r["time"] >= "2026-10-27 15:40"]
tot = collections.Counter()
for r in rows:
    t_end = time.mktime(time.strptime(r["time"], "%Y-%m-%d %H:%M:%S"))
    b_end = head - int((now - t_end) / SPB)
    b_buy = b_end - int(float(r["held_s"]) / SPB)
    lo, hi = b_buy - 150, min(head, b_end + 1300)
    token = Web3.to_checksum_address(r["token"])
    logs = []
    for b in range(lo, hi + 1, 1000):
        logs += call(lambda: a.eth.get_logs({"address": token, "fromBlock": b, "toBlock": min(hi, b + 999),
                                             "topics": [TRANSFER]}))
    tr = [(l["blockNumber"], l["logIndex"], "0x" + l["topics"][1].hex()[-40:], "0x" + l["topics"][2].hex()[-40:],
           int.from_bytes(bytes(l["data"])[:32], "big")) for l in logs]
    tr.sort()
    marks = []
    for blk in nonce_blocks(G, lo, hi):
        bl = call(lambda: a.eth.get_block(blk, full_transactions=True))
        for tx in bl.transactions:
            inp = bytes(tx["input"]).hex()
            if tx["from"] == G and tx["to"] and tx["to"].lower() == token.lower() and inp[:8] == "095ea7b3":
                marks.append((blk, tx["transactionIndex"], "0x" + inp[32:72]))
    marks.sort()
    victims = {m[2] for m in marks}
    # pre-window balances unknown: count only what moved inside the window (our wallets, crumbs and decoys start at 0)
    print(f"\n=== {r['symbol']} {token[:10]} ставка {r['stake_bnb']} итог {r['reason']}  меток {len(marks)}")
    done, first = set(), {}
    for blk, idx, tgt in marks:
        bal = collections.Counter()
        for b, li, f, t, v in tr:
            if b > blk - 3:
                break
            bal[f] -= v
            bal[t] += v
            first.setdefault(t, (b, li))
        pend = [x for x in victims if x not in done and x in first]
        if tgt not in pend:
            done.add(tgt)
            continue
        by_bal = sorted(pend, key=lambda x: -bal[x])
        by_age = sorted(pend, key=lambda x: first[x])
        rb, ra = by_bal.index(tgt) + 1, by_age.index(tgt) + 1
        top = by_bal[0]
        big = bal[top] > 0 and bal[tgt] >= bal[top]  # ties: equal biggest counts as "biggest"
        tot["n"] += 1
        tot["big"] += big
        tot["fifo"] += ra == 1
        tot["holder"] += bal[tgt] > 0
        print(f"  блок {blk}: {name(tgt):<9} баланс-ранг {rb}/{len(pend)}{' (крупнейший)' if big else ''}, "
              f"ранг по времени {ra}, держит {'да' if bal[tgt] > 0 else 'нет'}; крупнейший сейчас {name(top)}")
        done.add(tgt)
print(f"\nИТОГО меток {tot['n']}: крупнейший {tot['big']}, первый по времени {tot['fifo']}, "
      f"держал монету в момент просмотра {tot['holder']}")
