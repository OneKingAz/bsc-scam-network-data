"""05.11: what makes the bots stop buying while we are in a coin? From the NodeReal archive, other wallets' buys in the same age
window on untraded coins of that type against: dust tests (0.00001 BNB, chain_test and decoy tests), trades by stake size,
and stuck trades (the coin parked on a marked wallet, not moving). Read-only. Run: python analysis\\pause_study.py"""
import contextlib, csv, datetime as dt, io, json, os, random, statistics as st, sys, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.argv = sys.argv[:1]
with contextlib.redirect_stdout(io.StringIO()):
    import bot as B
    import journal as J
from web3 import Web3

a = Web3(Web3.HTTPProvider("https://bsc-mainnet.nodereal.io/v1/" + J.NODEREAL_KEY, request_kwargs={"timeout": 30}))
a.middleware_onion.inject(B.ExtraDataToPOAMiddleware, layer=0)
SWAP = "0x" + Web3.keccak(text="Swap(address,uint256,uint256,uint256,uint256,address)").hex().replace("0x", "")
SYNC = "0x" + Web3.keccak(text="Sync(uint112,uint112)").hex().replace("0x", "")
WBNB, SPB, TZ = int(B.WBNB, 16), 0.45, dt.timezone(dt.timedelta(hours=6))
launch = {}
for p in (os.path.join(ROOT, "old_data", "analysis", "guard_launches.csv"), os.path.join(HERE, "guard_launches.csv")):
    for l in csv.DictReader(open(p, encoding="utf-8-sig"), delimiter=";"):
        launch.setdefault(l["token"].lower(), l)
trades = list(csv.DictReader(open(os.path.join(HERE, "hop_trades.csv"), encoding="utf-8-sig"), delimiter=";"))
cache = {k.lower(): v for k, v in json.load(open(os.path.join(HERE, "gas_options_cache.json"), encoding="utf-8")).items()}
touched = {t["token"].lower() for t in trades}
head = a.eth.block_number
now = a.eth.get_block(head).timestamp


def call(fn, k=5):
    for i in range(k):
        try:
            return fn()
        except Exception:
            if i == k - 1:
                raise
            time.sleep(1 + i)


def coin(token):
    l = launch[token.lower()]
    pair, tk = Web3.to_checksum_address(l["pair"]), Web3.to_checksum_address(l["token"])
    is0 = WBNB < int(tk, 16)
    b0 = head - int((now - int(l["ts"])) / SPB) - 60
    sw, sy = [], []
    for b in range(b0, min(head, b0 + 10000), 2000):
        for e in call(lambda: a.eth.get_logs({"address": pair, "fromBlock": b, "toBlock": min(head, b + 1999), "topics": [[SYNC, SWAP]]})):
            d = bytes(e["data"])
            if e["topics"][0].hex().replace("0x", "") == SYNC[2:]:
                r0, r1 = int.from_bytes(d[:32], "big"), int.from_bytes(d[32:64], "big")
                sy.append((e["blockNumber"], (r0 if is0 else r1) / 1e18))
            else:
                x = [int.from_bytes(d[i:i + 32], "big") for i in (0, 32, 64, 96)]
                sw.append((e["blockNumber"], (x[0] if is0 else x[1]) / 1e18, (x[2] if is0 else x[3]) / 1e18))
    if not sy:
        return None
    t0, start = sy[0][0], sy[0][1]
    peak = max(x[1] for x in sy)
    ipk = max(range(len(sy)), key=lambda i: sy[i][1])
    drain = next((x[0] for x in sy[ipk:] if x[1] < 0.2 * peak), None)
    return dict(sym=l["symbol"], ts=int(l["ts"]), t0=t0, kind=25 if start >= 20 else 10 if start >= 5 else 1, sw=sw, drain=drain)


random.seed(5)
s25 = [t for t, c in cache.items() if c.get("kind") == 25 and t not in touched and t in launch]
sample = [c for c in (coin(t) for t in random.sample(s25, min(25, len(s25)))) if c]
norm_src = {25: [[x[0] - c["t0"] for x in c["sw"] if x[1] > 0.0001] for c in sample]}
for k in (1, 10):
    norm_src[k] = [[b - c["t0"] for b in c["buys"]] for t, c in cache.items() if c.get("kind") == k and c.get("buys") and t not in touched]
print(f"сравнение: 1 BNB {len(norm_src[1])}, 10 BNB {len(norm_src[10])}, 25 BNB {len(norm_src[25])} чистых монет")


def norm(kind, a0, a1):
    v = sorted(sum(a0 < x < a1 for x in n) for n in norm_src[kind])
    return st.median(v), v[len(v) // 10], v[len(v) * 9 // 10]


def line(group, name, c, b0, b1, note=""):
    a0, a1 = b0 - c["t0"], b1 - c["t0"]
    n = sum(1 for x in c["sw"] if b0 < x[0] < b1 and x[1] > 0.0001)
    m, q10, q90 = norm(c["kind"], a0, a1)
    after = sorted(x[0] for x in c["sw"] if x[0] >= b1 and x[1] > 0.0001 and (c["drain"] is None or x[0] < c["drain"]))
    first = f"{(after[0] - b1) * SPB:.0f} с" if after else "нет"
    print(f"{group:<8} {name:<13} {c['kind']:>3} BNB  с {a0 * SPB / 60:5.1f} мин, {(a1 - a0) * SPB / 60:5.1f} мин | покупок {n:>3} | обычно {m:>4.0f} ({q10}–{q90}) | следующая {first:>7} {note}")
    return dict(g=group, n=n, m=m, q10=q10, q90=q90, a=a0, dur=a1 - a0)


by_time = lambda c, ts: c["t0"] + (ts - c["ts"]) / SPB
stamp = lambda s: dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ).timestamp()
out = []
print("\n--- тесты на пыли (0.00001 BNB)")
seen = {}
for r in csv.DictReader(open(os.path.join(HERE, "chain_test.csv"), encoding="utf-8-sig"), delimiter=";"):
    if r["kind"] == "держатель" and r["got_block"]:
        seen.setdefault(r["token"].lower(), []).append(int(r["got_block"]))
for tk, bl in seen.items():
    c = coin(tk) if tk in launch else None
    if c and max(bl) - min(bl) > 60:
        out.append(line("пыль", c["sym"] + " цепочка", c, min(bl), max(bl)))
for l in launch.values():
    if l["symbol"] == "MAX":
        c = coin(l["token"])
        if c and c["t0"]:
            for name, t1, t2 in (("MAX приманки", "2026-10-27 14:15:40", "2026-10-27 14:18:10"), ("MAX 8 приман", "2026-10-27 14:40:30", "2026-10-27 14:43:30")):
                if c["ts"] < stamp(t1) < c["ts"] + 3600:
                    out.append(line("пыль", name, c, by_time(c, stamp(t1)), by_time(c, stamp(t2))))
print("\n--- сделки: чем больше ставка?")
for t in trades:
    got, stake = float(t["got_bnb"]), float(t["stake_bnb"])
    if t["token"].lower() not in launch or (got and float(t["held_s"]) < 120):
        continue
    c = coin(t["token"])
    if not c:
        continue
    buy = next((s for s in c["sw"] if abs(s[1] - stake) < 1e-9), None)
    if not buy:
        continue
    if got:
        sell = next((s for s in c["sw"] if abs(s[2] - got) < 1e-7), None)
        if sell:
            out.append(line(f"{stake:g}", t["symbol"], c, buy[0], sell[0]))
    else:  # stuck: the coin sits on a marked wallet and stops moving
        end = c["drain"] or buy[0] + 1200 / SPB
        out.append(line("застрял", t["symbol"], c, buy[0] + float(t["held_s"]) / SPB, end, f"ставка {stake:g}, держали до метки {float(t['held_s']):.0f} с"))
print("\nитог по группам (покупок / обычно):")
groups = {}
for r in out:
    g = r["g"] if r["g"] in ("пыль", "застрял") else ("ставка <0.002" if float(r["g"]) < 0.002 else "ставка ≥0.002")
    groups.setdefault(g, []).append(r)
for g, v in groups.items():
    print(f"  {g:<15} случаев {len(v):2}: покупок {sum(r['n'] for r in v):4}, обычно {sum(r['m'] for r in v):5.0f}, доля {sum(r['n'] for r in v) / max(1, sum(r['m'] for r in v)) * 100:4.0f}%; ниже 10%-й границы: {sum(r['n'] < r['q10'] for r in v)}/{len(v)}")
