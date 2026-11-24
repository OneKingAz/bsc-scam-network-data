"""30.10 (user: "test every option, show a table"): the cost options of the always-fast chain, on every 1- and 10-BNB
guard coin since <hours> ago (guard_launches.csv; NodeReal archive: Sync for the price, Swap for the bots' buys).
Base: buy 30 s after the pool, sell when the bots are done (as bot_exit: >= BOT_MIN buys and 50 s silence, else the
backstop), gas per minute GAS[option]. Early exit: if by CHECK s after the buy the bots bought fewer than N times,
sell then. Gross = price ratio after the 0.25% swap fee both ways (token tax unknown: paper).
Options (gas a minute, BNB): now = 1 s hand-overs with a top-up each (GMEc: 0.000145); reuse = clean wallets reused,
no top-up per hand-over (-25%); slow = hand-over every SLOW_F of now's rate (guard_lag.py says how far it can go).
Out: gas_options.csv + a table. Run: python gas_options.py [hours] [slow_factor]"""
import collections, contextlib, csv, io, os, statistics as st, sys, time
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 96
SLOW_F = float(sys.argv[2]) if len(sys.argv) > 2 else 0.5
sys.argv = sys.argv[:1]
with contextlib.redirect_stdout(io.StringIO()):
    import bot as B
    import journal as J
from web3 import Web3

HERE = os.path.dirname(os.path.abspath(__file__))
a = Web3(Web3.HTTPProvider("https://bsc-mainnet.nodereal.io/v1/" + J.NODEREAL_KEY, request_kwargs={"timeout": 30}))
a.middleware_onion.inject(B.ExtraDataToPOAMiddleware, layer=0)
SYNC = "0x" + Web3.keccak(text="Sync(uint112,uint112)").hex().replace("0x", "")
SWAP = "0x" + Web3.keccak(text="Swap(address,uint256,uint256,uint256,uint256,address)").hex().replace("0x", "")
SPB, ENTRY, FEE = 0.45, 30, 0.9975 ** 2
BOT_MIN, SILENCE, BACKSTOP = {1: 7, 10: 24}, 50, {1: 25 * 60, 10: 30 * 60}  # as hop_trader since 04.11
G_NOW = 0.000145
GAS = {"сейчас (1 с + пополнение)": G_NOW, "повтор кошельков": G_NOW * 0.75,
       f"реже ×{SLOW_F}": G_NOW * SLOW_F, f"реже ×{SLOW_F} + повтор": G_NOW * SLOW_F * 0.75}
FIXED = 0.00006
STAKES = (0.001, 0.002, 0.003, 0.005)
RULES = [None, (120, 2), (180, 2), (180, 3), (240, 3)]  # (s after the buy, fewer bot buys than this -> sell)
ours = {"0xfafd8a4e4a494da281ebfdd6f09f9a4345f90cdd"}
for line in open(os.path.join(os.path.dirname(HERE), "hop_wallets.txt"), encoding="utf-8"):
    p = line.strip().split(";")
    if len(p) > 1 and p[1].startswith("0x"):
        ours.add(p[1].lower())


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
launches = sorted((r for r in csv.DictReader(open(os.path.join(HERE, "guard_launches.csv"), encoding="utf-8-sig"),
                                             delimiter=";") if now - int(r["ts"]) < HOURS * 3600), key=lambda r: int(r["ts"]))
wbnb = int(B.WBNB, 16)
CACHE = os.path.join(HERE, "gas_options_cache.json")
import json
cache = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {}
coins = []
for r in launches:
    if r["token"] in cache:
        c = dict(cache[r["token"]])  # a copy: price_at below must not reach the cache file
        pts = [tuple(p) for p in c["pts"]]
        c["price_at"] = (lambda pts: lambda blk: next((p[2] for p in reversed(pts) if p[0] <= blk), pts[0][2]))(pts)
        c["pts"] = pts
        if c["kind"] != 25:
            coins.append(c)
        continue
    pair, token = Web3.to_checksum_address(r["pair"]), Web3.to_checksum_address(r["token"])
    is0 = wbnb < int(token, 16)
    b0 = head - int((now - int(r["ts"])) / SPB) - 40
    pts, buys = [], []
    for b in range(b0, min(head, b0 + 5000), 2000):
        for l in call(lambda: a.eth.get_logs({"address": pair, "fromBlock": b, "toBlock": min(head, b + 1999),
                                               "topics": [[SYNC, SWAP]]})):
            d = bytes(l["data"])
            if l["topics"][0].hex().replace("0x", "") == SYNC[2:]:
                r0, r1 = int.from_bytes(d[:32], "big"), int.from_bytes(d[32:64], "big")
                bnb, tok = (r0, r1) if is0 else (r1, r0)
                if tok:
                    pts.append((l["blockNumber"], bnb / 1e18, bnb / tok))
            else:
                bnb_in = int.from_bytes(d[0:32] if is0 else d[32:64], "big")
                to = "0x" + l["topics"][2].hex()[-40:]
                if bnb_in and to not in ours and not to.startswith("0xf71d73a4"):
                    buys.append(l["blockNumber"])
        if pts and pts[-1][1] < 0.2 * max(p[1] for p in pts) and pts[-1][1] < pts[0][1]:
            break
    if len(pts) < 3:
        continue
    t0, start = pts[0][0], pts[0][1]
    kind = 25 if start >= 20 else 10 if start >= 5 else 1
    if kind == 25:
        cache[r["token"]] = dict(kind=25, pts=[], buys=[])
        continue
    peak = max(p[1] for p in pts)
    ipk = max(range(len(pts)), key=lambda i: pts[i][1])
    drain = next((p[0] for p in pts[ipk:] if p[1] < 0.2 * peak), None)
    if drain is None:
        continue
    buys = sorted(b for b in buys if b < drain - 2)
    e_blk = t0 + ENTRY / SPB
    price_at = (lambda pts: lambda blk: next((p[2] for p in reversed(pts) if p[0] <= blk), pts[0][2]))(pts)  # 04.11: bound per coin
    # bot_exit: first block where >= BOT_MIN buys happened and SILENCE s passed since the last one
    exit_bot = None
    for k in range(BOT_MIN[kind] - 1, len(buys)):
        quiet = (buys[k + 1] if k + 1 < len(buys) else drain) - buys[k]
        if quiet * SPB >= SILENCE and buys[k] + SILENCE / SPB > e_blk + 60 / SPB:
            exit_bot = buys[k] + SILENCE / SPB
            break
    exit_bot = exit_bot or t0 + BACKSTOP[kind] / SPB
    if exit_bot >= drain:  # the dump came first: the sale would get nothing
        exit_bot = None
    coins.append(dict(time=time.strftime("%m-%d %H:%M", time.localtime(int(r["ts"]))), sym=r["symbol"], kind=kind,
                      t0=t0, e=e_blk, drain=drain, exit=exit_bot, buys=buys, price_at=price_at, pts=pts))
    cache[r["token"]] = {k: v for k, v in coins[-1].items() if k != "price_at"}
    with open(CACHE + ".tmp", "w", encoding="utf-8") as f:
        json.dump(cache, f)
    os.replace(CACHE + ".tmp", CACHE)  # a failed write must not damage the file
    print(f"{coins[-1]['time']} {r['symbol']:<12} тип {kind:2}  ботов {len(buys):3}  слив {(drain - t0) * SPB / 60:5.1f} мин  "
          f"продажа по ботам {'—' if exit_bot is None else f'{(exit_bot - t0) * SPB / 60:.1f} мин'}", flush=True)


def bot_exit(c, silence):
    """The sale block of the bot counter with this silence, None when the dump comes first."""
    kind, buys = c["kind"], c["buys"]
    for k in range(BOT_MIN[kind] - 1, len(buys)):
        quiet = (buys[k + 1] if k + 1 < len(buys) else c["drain"]) - buys[k]
        if quiet * SPB >= silence and buys[k] + silence / SPB > c["e"] + 60 / SPB:
            x = buys[k] + silence / SPB
            return x if x < c["drain"] else None
    x = c["t0"] + BACKSTOP[kind] / SPB
    return x if x < c["drain"] else None


EXITS = {"идеал: за 3 мин до слива": lambda c: c["drain"] - 180 / SPB,
         "боты, тишина 50 с (сейчас)": lambda c: bot_exit(c, 50),
         "боты, тишина 120 с": lambda c: bot_exit(c, 120),
         "боты, тишина 180 с": lambda c: bot_exit(c, 180),
         "фикс. 5 мин после входа": lambda c: c["e"] + 300 / SPB if c["e"] + 300 / SPB < c["drain"] else None}


def result(c, rule, gas_min, stake, ex=None):
    """% after gas for one coin; the dump before the sale = -100% of the stake plus the gas."""
    x = bot_exit(c, SILENCE) if ex is None else ex(c)
    if rule:
        chk, n = rule
        t_chk = c["e"] + chk / SPB
        if sum(1 for b in c["buys"] if b <= t_chk) < n and (x is None or t_chk < x) and t_chk < c["drain"]:
            x = t_chk
    if x is None:
        mins = (c["drain"] - c["e"]) * SPB / 60
        return -100 - (FIXED + gas_min * mins) / stake * 100
    mins = (x - c["e"]) * SPB / 60
    gross = (c["price_at"](x) / c["price_at"](c["e"]) * FEE - 1) * 100
    return gross - (FIXED + gas_min * mins) / stake * 100


for kind in (1, 10):
    cs = [c for c in coins if c["kind"] == kind]
    if not cs:
        continue
    g = GAS[f"реже ×{SLOW_F} + повтор"]
    for gname, gv in (("сейчас", G_NOW), (f"реже ×{SLOW_F} + повтор", g)):
        print(f"\n=== правила выхода, тип {kind} BNB, {len(cs)} монет, газ «{gname}»")
        print("правило".ljust(30) + "".join(f"{s:>18}" for s in STAKES) + "   до газа (медиана)  слив раньше продажи")
        for en, ex in EXITS.items():
            vals = {s: [result(c, None, gv, s, ex) for c in cs] for s in STAKES}
            gross = [result(c, None, 0, 1, ex) for c in cs]
            print(en.ljust(30) + "".join(f"{st.mean(v):+9.0f}% ({sum(x > 0 for x in v):2}/{len(v)})" for v in vals.values())
                  + f"   {st.median(gross):+8.0f}%   {sum(ex(c) is None for c in cs)}")
out = []
print(f"\nмонет: {len(coins)} (1 BNB: {sum(c['kind'] == 1 for c in coins)}, 10 BNB: {sum(c['kind'] == 10 for c in coins)})")
for kind in (1, 10):
    cs = [c for c in coins if c["kind"] == kind]
    if not cs:
        continue
    print(f"\n=== тип {kind} BNB, монет {len(cs)} — среднее после газа, % ставки (в плюсе / всего)")
    head_row = "вариант газа / ранний выход".ljust(44) + "".join(f"{s:>16}" for s in STAKES)
    print(head_row)
    for gname, g in GAS.items():
        for rule in RULES:
            vals = {s: [result(c, rule, g, s) for c in cs] for s in STAKES}
            label = f"{gname} / " + ("нет" if rule is None else f"<{rule[1]} ботов за {rule[0]} с")
            print(label.ljust(44) + "".join(f"{st.mean(v):+8.0f}% ({sum(x > 0 for x in v):2}/{len(v)})" for v in vals.values()))
            out.append(dict(kind=kind, gas=gname, rule="нет" if rule is None else f"{rule[1]}@{rule[0]}",
                            **{f"mean_{s}": round(st.mean(v), 1) for s, v in vals.items()},
                            **{f"plus_{s}": sum(x > 0 for x in v) for s, v in vals.items()}, n=len(cs)))
with open(os.path.join(HERE, "gas_options.csv"), "w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(out[0]), delimiter=";")
    w.writeheader()
    w.writerows(out)
