"""Read-only study of the second desk (0xF51DD734): every coin from its payments since SINCE.
Per coin: pool start/peak, life, buys, creator payments to bots and whether the paid buy follows, how the guard marks.
Writes guard2_today.csv and guard2_pays.csv next to this script."""
import collections
import contextlib
import csv
import io
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"D:\bot")
os.chdir(r"D:\bot")
with contextlib.redirect_stdout(io.StringIO()):
    import bot as B
    import journal as J
from web3 import Web3

OUT = os.path.dirname(os.path.abspath(__file__))
SINCE = sys.argv[1] if len(sys.argv) > 1 else "2026-11-06 14:00:00"
a = Web3(Web3.HTTPProvider("https://bsc-mainnet.nodereal.io/v1/" + J.NODEREAL_KEY, request_kwargs={"timeout": 30}))
a.middleware_onion.inject(B.ExtraDataToPOAMiddleware, layer=0)
SPB = 0.45
DESK = "0xf51dd7340c90bf1683bb32d6397ed3acefb6b6a7"
T = lambda s: "0x" + Web3.keccak(text=s).hex().replace("0x", "")
PAIR_CREATED, SYNC, SWAP = T("PairCreated(address,address,address,uint256)"), T("Sync(uint112,uint112)"), T("Swap(address,uint256,uint256,uint256,uint256,address)")
FACTORY, ROUTER = B.FACTORY.address.lower(), B.ROUTER.lower()


def call(fn, tries=5):
    for k in range(tries):
        try:
            return fn()
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(1 + k)


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


def txs_of(addr, blk):
    b = call(lambda: a.eth.get_block(blk, full_transactions=True))
    return [t for t in b.transactions if t["from"].lower() == addr.lower()]


head = a.eth.block_number
now = a.eth.get_block(head).timestamp
rows = [r for r in csv.reader(open(r"D:\bot\analysis\cash_watch.csv", encoding="utf-8-sig"), delimiter=";") if len(r) > 4 and r[1] == "pay" and r[2].lower() == DESK and r[0] >= SINCE]
seen, pays = set(), []
for r in rows:
    if r[3].lower() not in seen:
        seen.add(r[3].lower())
        pays.append(r)
print(f"создателей {len(pays)} с {SINCE}", flush=True)
coins, sig_rows, marks_by_guard = [], [], collections.defaultdict(list)
for r in pays:
    creator = Web3.to_checksum_address(r[3])
    b_pay = head - int((now - time.mktime(time.strptime(r[0], "%Y-%m-%d %H:%M:%S"))) / SPB)
    hi = min(head, b_pay + 1500)
    pair = token = b_launch = None
    blocks = nonce_blocks(creator, b_pay - 60, hi)
    for blk in blocks:
        for t in txs_of(creator, blk):
            rc = call(lambda: a.eth.get_transaction_receipt(t["hash"]))
            for lg in rc["logs"]:
                if lg["address"].lower() == FACTORY and lg["topics"][0].hex().replace("0x", "") == PAIR_CREATED[2:]:
                    t0 = Web3.to_checksum_address("0x" + lg["topics"][1].hex()[-40:])
                    t1 = Web3.to_checksum_address("0x" + lg["topics"][2].hex()[-40:])
                    token = t1 if t0.lower() == B.WBNB.lower() else t0
                    pair = Web3.to_checksum_address("0x" + bytes(lg["data"])[12:32].hex())
                    b_launch = blk
        if pair:
            break
    if not pair:
        print(f"{r[0][11:19]} {creator[:10]}: транзакций {len(blocks)}, пула нет", flush=True)
        coins.append(dict(time=r[0], sym="", creator=creator, note="нет пула", ntx=len(blocks)))
        continue
    guard = "0x" + call(lambda: a.eth.get_storage_at(token, 6)).hex()[-40:]
    size = len(call(lambda: a.eth.get_code(token)))
    wb0 = int(Web3.to_checksum_address(B.WBNB), 16) < int(token, 16)
    end = min(head, b_launch + 4000)
    syncs, swaps = [], []
    for b in range(b_launch, end + 1, 2000):
        e = min(end, b + 1999)
        syncs += call(lambda: a.eth.get_logs({"address": pair, "fromBlock": b, "toBlock": e, "topics": [SYNC]}))
        swaps += call(lambda: a.eth.get_logs({"address": pair, "fromBlock": b, "toBlock": e, "topics": [SWAP]}))
    ser = [(l["blockNumber"], l["logIndex"], int.from_bytes(bytes(l["data"])[:32] if wb0 else bytes(l["data"])[32:64], "big") / 1e18) for l in syncs]
    if not ser:
        continue
    start = ser[0][2]
    peak_b, _, peak = max(ser, key=lambda x: x[2])
    drain = next((bb for bb, _, v in ser if bb >= peak_b and v < 0.2 * peak), None)

    def pool_before(block):
        v = [x[2] for x in ser if x[0] < block]
        return v[-1] if v else start

    def pool_after(block):
        v = [x[2] for x in ser if x[0] <= block]
        return v[-1] if v else start
    buys, sells = [], 0
    for lg in swaps:
        d = bytes(lg["data"])
        bnb_in = int.from_bytes(d[0:32] if wb0 else d[32:64], "big") / 1e18
        tx = call(lambda: a.eth.get_transaction(lg["transactionHash"]))
        if bnb_in:
            buys.append(dict(block=lg["blockNumber"], frm=tx["from"].lower(), bnb=bnb_in, value=tx["value"] / 1e18))
        elif not drain or lg["blockNumber"] < drain:
            sells += 1
    last = drain if drain else end
    payments = []
    for blk in nonce_blocks(creator, b_launch, min(head, last + 40)):
        for t in txs_of(creator, blk):
            if t["value"] and t["to"] and t["to"].lower() != ROUTER:
                payments.append((blk, t["to"].lower(), t["value"] / 1e18))
    n_match = 0
    for blk, to, val in payments:
        m = next((x for x in buys if x["frm"] == to and blk <= x["block"] <= blk + 12), None)
        rise = lag = entry_pool = None
        if m:
            n_match += 1
            lag = m["block"] - blk
            entry_pool = pool_after(blk + 2)
            rise = ((pool_after(m["block"]) / entry_pool) ** 2 - 1) * 100 if entry_pool else None
        sig_rows.append(dict(sym=r[0][11:16], token=token, b_pay=blk, age_s=round((blk - b_launch) * SPB, 1), bnb=round(val, 5), pool=round(pool_before(blk), 3), share=round(val / pool_before(blk) * 100, 2) if pool_before(blk) else "", buy=int(bool(m)), lag=lag if lag is not None else "", rise=round(rise, 1) if rise is not None else "", to_drain_s=round((drain - blk) * SPB, 1) if drain else ""))
    try:
        sym = call(lambda: a.eth.contract(token, abi=B.ERC20_ABI).functions.symbol().call())
    except Exception:
        sym = "?"
    for s in sig_rows:
        if s["token"] == token:
            s["sym"] = sym
    life = round((drain - b_launch) * SPB / 60, 2) if drain else ""
    c = dict(time=r[0], sym=sym, token=token, pair=pair, creator=creator, guard=guard, size=size, start=round(start, 3), peak=round(peak, 3), peak_s=round((peak_b - b_launch) * SPB), life_min=life, x=round((peak / start) ** 2, 2) if start else "", buys=len(buys), sells=sells, buyers=len({x["frm"] for x in buys}), pays=len(payments), matched=n_match, b_launch=b_launch, b_drain=drain or "", note="")
    coins.append(c)
    marks_by_guard[guard].append(c)
    print(f"{r[0][11:19]} {sym:<10} код {size} сторож {guard[:10]} старт {start:.2f} пик {peak:.2f} на {c['peak_s']}с, живёт {life or '—'} мин, ×{c['x']}, покупок {len(buys)}, покупателей {c['buyers']}, платежей {len(payments)}, совпало {n_match}", flush=True)

cols = sorted({k for c in coins for k in c})
with open(os.path.join(OUT, "guard2_today.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=cols, delimiter=";")
    w.writeheader()
    w.writerows(coins)
if sig_rows:
    with open(os.path.join(OUT, "guard2_pays.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(sig_rows[0]), delimiter=";")
        w.writeheader()
        w.writerows(sig_rows)
print("\n--- сторожа ---")
for g, cs in marks_by_guard.items():
    if int(g, 16) == 0:
        continue
    G = Web3.to_checksum_address(g)
    lo = min(c["b_launch"] for c in cs) - 100
    marks = []
    blks = nonce_blocks(G, lo, head)
    for blk in blks:
        for t in txs_of(G, blk):
            inp = bytes(t["input"]).hex()
            marks.append((blk, (t["to"] or "").lower(), inp[:8], "0x" + inp[32:72]))
    print(f"{g[:10]} монет {len(cs)}, транзакций {len(marks)}, методы {collections.Counter(m[2] for m in marks)}, баланс {a.eth.get_balance(G) / 1e18:.4f}")
    print("  кого метит:", collections.Counter(m[3][:10] for m in marks).most_common(8))
    for c in cs:
        mk = [m for m in marks if m[1] == c["token"].lower()]
        print(f"  {c['sym']:<10} меток {len(mk)}: " + ", ".join(f"+{(m[0] - c['b_launch']) * SPB:.0f}с {m[3][:8]}" for m in mk[:6]))
print("\n--- итог платежей ---")
for s in sig_rows:
    print(s["sym"], "возраст", s["age_s"], "BNB", s["bnb"], "пул", s["pool"], "доля%", s["share"], "купил", s["buy"], "лаг", s["lag"], "рост%", s["rise"], "до слива с", s["to_drain_s"])
print("готово")
