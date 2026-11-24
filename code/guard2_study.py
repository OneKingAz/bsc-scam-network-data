"""27.10 (user): the second guard 0xdbbf0824 (cash desk 0xF51DD734): how long its coins live, how they grow, who buys,
how the guard marks. Creators = the desk's 50 BNB payments in cash_watch.csv; each creator's transactions are found
by nonce bisection on the NodeReal archive, the pool from the PairCreated log in their receipts.
Out: analysis/guard2_coins.csv + report. Run: python guard2_study.py"""
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
SPB = 0.45
DESK = "0xf51dd7340c90bf1683bb32d6397ed3acefb6b6a7"
T = lambda s: "0x" + Web3.keccak(text=s).hex().replace("0x", "")
PAIR_CREATED, SYNC, SWAP = T("PairCreated(address,address,address,uint256)"), T("Sync(uint112,uint112)"), \
    T("Swap(address,uint256,uint256,uint256,uint256,address)")
FACTORY = B.FACTORY.address.lower()


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

    def n(b):
        return call(lambda: a.eth.get_transaction_count(addr, b))

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


def tx_of(addr, blk):
    b = call(lambda: a.eth.get_block(blk, full_transactions=True))
    return [t for t in b.transactions if t["from"].lower() == addr.lower()], b.timestamp


head = a.eth.block_number
now = a.eth.get_block(head).timestamp
pays = [r for r in csv.reader(open(os.path.join(HERE, "cash_watch.csv"), encoding="utf-8-sig"), delimiter=";")
        if len(r) > 4 and r[1] == "pay" and r[2].lower() == DESK]
print(f"платежей кассы 0xF51DD734: {len(pays)}", flush=True)
coins = []
for r in pays:
    creator = Web3.to_checksum_address(r[3])
    t_pay = time.mktime(time.strptime(r[0], "%Y-%m-%d %H:%M:%S"))
    b_pay = head - int((now - t_pay) / SPB)
    pair = token = None
    for blk in nonce_blocks(creator, b_pay - 50, min(head, b_pay + 1500)):
        txs, ts = tx_of(creator, blk)
        for t in txs:
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
        print(f"  {creator[:10]}: пула не нашёл", flush=True)
        continue
    guard = "0x" + call(lambda: a.eth.get_storage_at(token, 6)).hex()[-40:]
    wb0 = int(Web3.to_checksum_address(B.WBNB), 16) < int(token, 16)
    syncs, swaps = [], []
    for b in range(b_launch, min(head, b_launch + 8000), 2000):
        e = min(head, b + 1999)
        syncs += call(lambda: a.eth.get_logs({"address": pair, "fromBlock": b, "toBlock": e, "topics": [SYNC]}))
        swaps += call(lambda: a.eth.get_logs({"address": pair, "fromBlock": b, "toBlock": e, "topics": [SWAP]}))
    ser = [(l["blockNumber"], int.from_bytes(bytes(l["data"])[:32] if wb0 else bytes(l["data"])[32:64], "big") / 1e18)
           for l in syncs]
    if not ser:
        continue
    start = ser[0][1]
    peak_b, peak = max(ser, key=lambda x: x[1])
    drain = next((bb for bb, v in ser if bb > peak_b and v < 0.2 * peak), None)
    buys, sells, buyers = 0, 0, collections.Counter()
    for lg in swaps:
        d = bytes(lg["data"])
        bnb_in = int.from_bytes(d[0:32] if wb0 else d[32:64], "big")
        if drain and lg["blockNumber"] >= drain:
            continue
        frm = call(lambda: a.eth.get_transaction(lg["transactionHash"])["from"]).lower()
        if bnb_in:
            buys += 1
            buyers[frm] += 1
        else:
            sells += 1
    try:
        sym = call(lambda: a.eth.contract(token, abi=B.ERC20_ABI).functions.symbol().call())
    except Exception:
        sym = "?"
    life = (drain - b_launch) * SPB / 60 if drain else None
    c = dict(time=r[0], sym=sym, token=token, pair=pair, creator=creator, guard=guard, start=round(start, 3),
             peak=round(peak, 3), peak_min=round((peak_b - b_launch) * SPB / 60, 1),
             drain_min=round(life, 1) if life else "", growth_x=round((peak / start) ** 2, 2) if start else "",
             bot_buys=buys, sells=sells, buyers=len(buyers), b_launch=b_launch, b_drain=drain or "")
    coins.append(c)
    print(f"  {sym:<9} старт {start:.2f} пик {peak:.2f} на {c['peak_min']} мин, слив {c['drain_min'] or '—'} мин, цена ×{c['growth_x']},"
          f" покупок {buys}, продаж {sells}, покупателей {len(buyers)}, сторож {guard[:10]}", flush=True)
if coins:
    with open(os.path.join(HERE, "guard2_coins.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(coins[0]), delimiter=";")
        w.writeheader()
        w.writerows(coins)

# the guard's marks over the whole window
g = Web3.to_checksum_address(coins[0]["guard"]) if coins else None
if g:
    lo = min(c["b_launch"] for c in coins) - 100
    marks = []
    for blk in nonce_blocks(g, lo, head):
        txs, ts = tx_of(g, blk)
        for t in txs:
            inp = bytes(t["input"]).hex()
            marks.append((blk, t["to"], inp[:8], "0x" + inp[32:72]))
    print(f"\nсторож {g}: транзакций {len(marks)} с блока {lo}; методы {collections.Counter(m[2] for m in marks)}")
    for c in coins:
        mk = [m for m in marks if m[1] and m[1].lower() == c["token"].lower()]
        print(f"  {c['sym']:<9} меток {len(mk)}: " + ", ".join(f"+{(m[0] - c['b_launch']) * SPB:.0f}с {m[3][:8]}" for m in mk[:8]))
