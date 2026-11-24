"""TZ stage 2: why GMEc gave +51% against +155% by the pool. Reads the pair's Sync/Swap from the NodeReal archive and
compares, at our real buy and sell blocks: the pool price, what the AMM formula promises and what we really got.
Run: python analysis\\gmec_check.py SYM   (coin taken from old_data\\analysis\\hop_trades.csv and guard_launches.csv)"""
import contextlib, csv, io, os, sys, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
SYMS = sys.argv[1:] or ["GMEc"]
sys.argv = sys.argv[:1]
with contextlib.redirect_stdout(io.StringIO()):
    import bot as B
    import journal as J
from web3 import Web3

a = Web3(Web3.HTTPProvider("https://bsc-mainnet.nodereal.io/v1/" + J.NODEREAL_KEY, request_kwargs={"timeout": 30}))
a.middleware_onion.inject(B.ExtraDataToPOAMiddleware, layer=0)
SYNC = "0x" + Web3.keccak(text="Sync(uint112,uint112)").hex().replace("0x", "")
SWAP = "0x" + Web3.keccak(text="Swap(address,uint256,uint256,uint256,uint256,address)").hex().replace("0x", "")
WBNB = int(B.WBNB, 16)
SPB = 0.45
trades = list(csv.DictReader(open(os.path.join(ROOT, "old_data", "analysis", "hop_trades.csv"), encoding="utf-8-sig"), delimiter=";"))
launches = list(csv.DictReader(open(os.path.join(HERE, "guard_launches.csv"), encoding="utf-8-sig"), delimiter=";"))
ours = set()
for line in open(os.path.join(ROOT, "old_data", "analysis", "old_addrs.txt"), encoding="utf-8") if os.path.exists(os.path.join(ROOT, "old_data", "analysis", "old_addrs.txt")) else []:
    ours.add(line.strip().lower())


def call(fn, k=5):
    for i in range(k):
        try:
            return fn()
        except Exception:
            if i == k - 1:
                raise
            time.sleep(1 + i)


for sym in SYMS:
    tr = [t for t in trades if t["symbol"] == sym][-1]
    lc = [l for l in launches if l["token"].lower() == tr["token"].lower()][0]
    pair, token = Web3.to_checksum_address(lc["pair"]), Web3.to_checksum_address(lc["token"])
    is0 = WBNB < int(token, 16)
    head = call(lambda: a.eth.block_number)
    now = call(lambda: a.eth.get_block(head).timestamp)
    b0 = head - int((now - int(lc["ts"])) / SPB) - 60
    pts, swaps = [], []
    for b in range(b0, b0 + 6000, 2000):
        for l in call(lambda: a.eth.get_logs({"address": pair, "fromBlock": b, "toBlock": b + 1999, "topics": [[SYNC, SWAP]]})):
            d = bytes(l["data"])
            if l["topics"][0].hex().replace("0x", "") == SYNC[2:]:
                r0, r1 = int.from_bytes(d[:32], "big"), int.from_bytes(d[32:64], "big")
                bnb, tok = (r0, r1) if is0 else (r1, r0)
                pts.append((l["blockNumber"], l["logIndex"], bnb, tok))
            else:
                x = [int.from_bytes(d[i:i + 32], "big") for i in (0, 32, 64, 96)]
                bi, ti, bo, to = (x[0], x[1], x[2], x[3]) if is0 else (x[1], x[0], x[3], x[2])
                swaps.append(dict(b=l["blockNumber"], i=l["logIndex"], bnb_in=bi, tok_in=ti, bnb_out=bo, tok_out=to,
                                  tx=l["transactionHash"].hex()))
    stake, got = float(tr["stake_bnb"]), float(tr["got_bnb"])
    # our buy: BNB in == the stake exactly; our sell: BNB out == got
    buy = next((s for s in swaps if abs(s["bnb_in"] / 1e18 - stake) < 1e-9), None)
    sell = next((s for s in swaps if abs(s["bnb_out"] / 1e18 - got) < 1e-7), None)
    print(f"=== {sym} {token} pair {pair}, ставка {stake}, получено {got}, держали {tr['held_s']} с, причина {tr['reason']}")
    print(f"свапов {len(swaps)}, точек {len(pts)}, запуск в блоке {pts[0][0]}")
    if not buy or not sell:
        print("наша покупка/продажа не найдены", bool(buy), bool(sell))
        continue
    before = lambda s: [p for p in pts if (p[0], p[1]) < (s["b"], s["i"])][-1]
    amm = lambda dx, rx, ry: dx * 9975 * ry // (rx * 10000 + dx * 9975)
    pb, ps = before(buy), before(sell)
    t0 = pts[0][0]
    price = lambda p: p[2] / p[3]
    e = next((p for p in reversed(pts) if p[0] <= t0 + 30 / SPB), pts[0])
    print(f"покупка: блок {buy['b']} (+{(buy['b'] - t0) * SPB:.0f} с), пул {pb[2] / 1e18:.3f} BNB; из пула вышло {buy['tok_out'] / 1e18:.4f} токенов, "
          f"по формуле {amm(buy['bnb_in'], pb[2], pb[3]) / 1e18:.4f}")
    print(f"продажа: блок {sell['b']} (+{(sell['b'] - t0) * SPB:.0f} с), пул {ps[2] / 1e18:.3f} BNB; в пул вошло токенов {sell['tok_in'] / 1e18:.4f}; "
          f"из пула {sell['bnb_out'] / 1e18:.6f} BNB, по формуле {amm(sell['tok_in'], ps[3], ps[2]) / 1e18:.6f}")
    print(f"токенов купили {buy['tok_out'] / 1e18:.4f}, дошло до пула при продаже {sell['tok_in'] / 1e18:.4f}: потеря {(1 - sell['tok_in'] / buy['tok_out']) * 100:.2f}%")
    print(f"цена: вход по расчёту (+30 с) {price(e):.3e}, перед нашей покупкой {price(pb):.3e}, перед продажей {price(ps):.3e}")
    print(f"рост по пулу: от +30 с до продажи {(price(ps) / price(e) - 1) * 100:+.1f}%, от нашей покупки до продажи {(price(ps) / price(pb) - 1) * 100:+.1f}%, факт {(got / stake - 1) * 100:+.1f}%")
    print("разбор: токены × цена продажи без комиссии =", f"{sell['tok_in'] * price(ps) / 1e18:.6f} BNB; нужна цена для ставки {stake}: {stake * 1e18 / buy['tok_out']:.3e}")
    print("цена по минутам от запуска (BNB в пуле, цена к нашей покупке):")
    for m in range(0, 26):
        c = [p for p in pts if p[0] <= t0 + m * 60 / SPB]
        if c:
            print(f"  {m:2} мин: {c[-1][2] / 1e18:7.3f} BNB  {(price(c[-1]) / price(pb) - 1) * 100:+8.1f}%")
    big = [s for s in swaps if s["b"] <= sell["b"] + 5 and (s["bnb_in"] > 0.02e18 or s["bnb_out"] > 0.02e18)]
    print("крупные свапы рядом с продажей:", [(s["b"] - sell["b"], round(s["bnb_in"] / 1e18, 3), round(s["bnb_out"] / 1e18, 3)) for s in big[-12:]])
    tx = call(lambda: a.eth.get_transaction("0x" + sell["tx"].replace("0x", "")))
    print("продавец:", tx["from"], "газ", tx["gasPrice"] / 1e9, "gwei")
