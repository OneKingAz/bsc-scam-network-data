"""27.10 (user): in what order do the network's bots buy and sell? For every guard launch since <hours> ago: every
swap (minute from the pool, buy/sell, BNB, the wallet that sent it), then across the coins: which wallets come
back, when each buys and sells, how long it holds, what it nets, and how the buys/sells sit against the drain.
Out: analysis/bot_swaps.csv (one row per swap) + a report on screen. Run: python bot_sequence.py [hours]"""
import collections, contextlib, csv, io, os, statistics as st, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
with contextlib.redirect_stdout(io.StringIO()):
    import bot as B
    import journal as J
from web3 import Web3

HERE = os.path.dirname(os.path.abspath(__file__))
w3 = Web3(Web3.HTTPProvider("https://bsc-mainnet.nodereal.io/v1/" + J.NODEREAL_KEY, request_kwargs={"timeout": 30}))
w3.middleware_onion.inject(B.ExtraDataToPOAMiddleware, layer=0)
HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 6
SPB = 0.45
SWAP = "0x" + Web3.keccak(text="Swap(address,uint256,uint256,uint256,uint256,address)").hex().replace("0x", "")
SYNC = "0x" + Web3.keccak(text="Sync(uint112,uint112)").hex().replace("0x", "")
OURS = {"0xfafd8a4e4a494da281ebfdd6f09f9a4345f90cdd"}
for line in open(os.path.join(os.path.dirname(HERE), "hop_wallets.txt"), encoding="utf-8"):
    p = line.strip().split(";")
    if len(p) > 1 and p[1].startswith("0x"):
        OURS.add(p[1].lower())
head = w3.eth.block_number
now = w3.eth.get_block(head).timestamp
launches = [r for r in csv.DictReader(open(os.path.join(HERE, "guard_launches.csv"), encoding="utf-8-sig"), delimiter=";")
            if now - int(r["ts"]) < HOURS * 3600]
rows = []
for r in launches:
    pair, token = Web3.to_checksum_address(r["pair"]), Web3.to_checksum_address(r["token"])
    wb0 = int(Web3.to_checksum_address(B.WBNB), 16) < int(token, 16)
    start = head - int((now - int(r["ts"])) / SPB) - 300
    end = min(head, start + int(3900 / SPB))
    logs, syncs = [], []
    for b in range(start, end + 1, 5000):
        e = min(b + 4999, end)
        for _ in range(3):
            try:
                logs += w3.eth.get_logs({"address": pair, "fromBlock": b, "toBlock": e, "topics": [SWAP]})
                syncs += w3.eth.get_logs({"address": pair, "fromBlock": b, "toBlock": e, "topics": [SYNC]})
                break
            except Exception:
                time.sleep(1)
    if not syncs:
        continue
    b0 = syncs[0]["blockNumber"]
    pool = [(s["blockNumber"], int.from_bytes(bytes(s["data"])[:32] if wb0 else bytes(s["data"])[32:64], "big") / 1e18)
            for s in syncs]
    peak = max(v for _, v in pool)
    drain_b = next((bb for bb, v in pool if v < 0.2 * peak and bb > b0), None)
    start_bnb = pool[0][1]
    kind = 25 if start_bnb > 20 else 10 if start_bnb > 5 else 1
    for lg in logs:
        d = bytes(lg["data"])
        a0i, a1i, a0o, a1o = (int.from_bytes(d[i:i + 32], "big") for i in (0, 32, 64, 96))
        bnb_in, bnb_out = (a0i, a0o) if wb0 else (a1i, a1o)
        try:
            frm = w3.eth.get_transaction(lg["transactionHash"])["from"].lower()
        except Exception:
            frm = "?"
        rows.append(dict(coin=r["symbol"], kind=kind, minute=round((lg["blockNumber"] - b0) * SPB / 60, 2),
                         to_drain=round((drain_b - lg["blockNumber"]) * SPB / 60, 2) if drain_b else "",
                         side="buy" if bnb_in else "sell", bnb=round((bnb_in or bnb_out) / 1e18, 4), wallet=frm,
                         ours=int(frm in OURS)))
    print(f"{r['symbol']}: {sum(1 for x in rows if x['coin'] == r['symbol'])} свапов, тип {kind}, "
          f"опустошён {'на %.1f мин' % ((drain_b - b0) * SPB / 60) if drain_b else 'ещё нет'}", flush=True)
if not rows:  # 27.10: an empty run must not wipe the file of the previous one
    sys.exit("свапов нет — файл не тронут")
with open(os.path.join(HERE, "bot_swaps.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter=";")
    w.writeheader()
    w.writerows(rows)

bots = [x for x in rows if not x["ours"]]
coins = sorted({x["coin"] for x in bots})
per = collections.defaultdict(lambda: collections.defaultdict(list))
for x in bots:
    per[x["wallet"]][x["coin"]].append(x)
print(f"\nкошельков-ботов: {len(per)}, монет: {len(coins)}")
print("Кто на скольких монетах, когда первая покупка (мин от запуска, медиана), сколько держит, итог:")
lines = []
for wlt, cs in per.items():
    firsts = [min(t["minute"] for t in tr if t["side"] == "buy") for tr in cs.values() if any(t["side"] == "buy" for t in tr)]
    sells = [t for tr in cs.values() for t in tr if t["side"] == "sell"]
    holds = []
    for tr in cs.values():
        b = [t["minute"] for t in tr if t["side"] == "buy"]
        s_ = [t["minute"] for t in tr if t["side"] == "sell"]
        if b and s_:
            holds.append(max(s_) - min(b))
    net = sum(t["bnb"] if t["side"] == "sell" else -t["bnb"] for tr in cs.values() for t in tr)
    lines.append((len(cs), wlt, st.median(firsts) if firsts else None, st.median(holds) if holds else None,
                  len(sells), net))
lines.sort(key=lambda l: (-l[0], l[2] if l[2] is not None else 99))
for n, wlt, fm, hm, ns, net in lines[:30]:
    print(f"  {wlt[:12]} монет {n:2d} | 1-я покупка ~{fm if fm is not None else '-'} мин | держит ~{hm if hm is not None else '-'}"
          f" мин | продаж {ns:3d} | итог {net:+.3f} BNB")
print("\nПо минутам от запуска (все монеты): покупок / продаж, BNB")
bucket = collections.defaultdict(lambda: [0, 0, 0.0, 0.0])
for x in bots:
    k = int(x["minute"] // 2 * 2)
    i = 0 if x["side"] == "buy" else 1
    bucket[k][i] += 1
    bucket[k][i + 2] += x["bnb"]
for k in sorted(bucket):
    b = bucket[k]
    print(f"  {k:2d}-{k + 2:<2d} мин: покупок {b[0]:3d} ({b[2]:6.2f} BNB) | продаж {b[1]:3d} ({b[3]:6.2f} BNB)")
print("\nПеред опустошением (мин до него): покупок / продаж")
pre = collections.defaultdict(lambda: [0, 0])
for x in bots:
    if x["to_drain"] != "" and -1 <= x["to_drain"] <= 10:
        pre[int(x["to_drain"])][0 if x["side"] == "buy" else 1] += 1
for k in sorted(pre, reverse=True):
    print(f"  за {k:2d} мин: покупок {pre[k][0]:3d} | продаж {pre[k][1]:3d}")
