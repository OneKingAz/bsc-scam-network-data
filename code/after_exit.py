"""User's question 05.11: do the bots wait until we leave? For every real sold trade: bot buys while we held against the same
age window on coins of the same type we never traded (gas_options_cache.json), and what came right after our sale.
Reads the NodeReal archive. Run: python analysis\\after_exit.py"""
import contextlib, csv, io, json, os, statistics as st, sys, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.argv = sys.argv[:1]
with contextlib.redirect_stdout(io.StringIO()):
    import bot as B
    import journal as J
from web3 import Web3

# 18.11 (user): NodeReal stays the first node; when it fails (429 = monthly limit) the free nodes answer, and NodeReal is tried
# again 10 min later. Log ranges are <= 400 blocks so that every node can serve them.
class _Eth:
    def __init__(self, nodes):
        self.nodes, self.bad = nodes, {}

    def _do(self, fn):
        err = None
        for rnd in range(3):  # all nodes busy (429): wait and try every node again, NodeReal too
            for name, w in self.nodes:
                if rnd == 0 and time.time() < self.bad.get(name, 0):
                    continue
                try:
                    return fn(w)
                except Exception as e:
                    err = e
                    if "429" in str(e) or "Too Many" in str(e):
                        self.bad[name] = time.time() + 600
            time.sleep(3 * (rnd + 1))
        raise RuntimeError(f"ни один узел не ответил: {str(err)[:80]}")

    def get_logs(self, q):
        return self._do(lambda w: w.eth.get_logs(q))

    def get_block(self, n):
        return self._do(lambda w: w.eth.get_block(n))

    @property
    def block_number(self):
        return self._do(lambda w: w.eth.block_number)


class _Multi:
    def __init__(self, nodes):
        self.eth = _Eth(nodes)


_nodes = []
for _name, _url in (("nodereal", "https://bsc-mainnet.nodereal.io/v1/" + (J.NODEREAL_KEY or "")), ("48club", "https://rpc-bsc.48.club"),
                    ("48club-2", "https://rpc.48.club")):
    if _name == "nodereal" and not J.NODEREAL_KEY:
        continue
    _w = Web3(Web3.HTTPProvider(_url, request_kwargs={"timeout": 30}))
    _w.middleware_onion.inject(B.ExtraDataToPOAMiddleware, layer=0)
    _nodes.append((_name, _w))
a = _Multi(_nodes)
SYNC = "0x" + Web3.keccak(text="Sync(uint112,uint112)").hex().replace("0x", "")
SWAP = "0x" + Web3.keccak(text="Swap(address,uint256,uint256,uint256,uint256,address)").hex().replace("0x", "")
WBNB, SPB = int(B.WBNB, 16), 0.45
launch = {l["token"].lower(): l for l in csv.DictReader(open(os.path.join(HERE, "guard_launches.csv"), encoding="utf-8-sig"), delimiter=";")}
for l in csv.DictReader(open(os.path.join(ROOT, "old_data", "analysis", "guard_launches.csv"), encoding="utf-8-sig"), delimiter=";"):
    launch.setdefault(l["token"].lower(), l)
trades = list(csv.DictReader(open(os.path.join(HERE, "hop_trades.csv"), encoding="utf-8-sig"), delimiter=";"))
cache = {k.lower(): v for k, v in json.load(open(os.path.join(HERE, "gas_options_cache.json"), encoding="utf-8")).items()}
traded = {t["token"].lower() for t in trades}
clean = {k: [c for t, c in cache.items() if c.get("kind") == k and c.get("buys") and t not in traded] for k in (1, 10)}


def call(fn, k=5):
    for i in range(k):
        try:
            return fn()
        except Exception:
            if i == k - 1:
                raise
            time.sleep(1 + i)


head = call(lambda: a.eth.block_number)
now = call(lambda: a.eth.get_block(head).timestamp)
# 05.11: the signal test's charts first (the panel's button and the "Итоги" tab read the same file); it needs no panel restart
import signal_charts
CHARTS = os.path.join(HERE, "deal_charts.json")


def write_charts(**k):
    with open(CHARTS + ".tmp", "w", encoding="utf-8") as f:
        json.dump(k, f, ensure_ascii=False)
    os.replace(CHARTS + ".tmp", CHARTS)


try:
    old = json.load(open(CHARTS, encoding="utf-8"))
except (OSError, ValueError):
    old = {}
is_test = lambda t: "сигнал" in t["reason"] or t["reason"].startswith("ловля")  # 18.11: "сеть N, сигнал: …" too (they made the old-deals part re-run)
deals_n = sum(1 for t in trades if float(t["got_bnb"] or 0) and not is_test(t))
try:
    sig, sig_rows = signal_charts.build(a, B, head)
except Exception as e:
    print("графики теста по сигналу:", e)
    sig, sig_rows = old.get("signals", []), old.get("sig_rows", 0)
sig_at = time.strftime("%d.%m %H:%M:%S")
write_charts(**{**old, "signals": sig, "sig_rows": sig_rows, "sig_at": sig_at})
if old.get("deals") and old.get("deals_n") == deals_n:
    sys.exit(0)  # the old deals have not changed
print(f"чистых монет для сравнения: 1 BNB {len(clean[1])}, 10 BNB {len(clean[10])}")
print("сделка         тип вход,мин держим,мин | покупок при нас | обычно (мед, 10–90%) | первая покупка после нас | покупок после | слив,мин")
rows, charts = [], []
for t in trades:
    got, stake = float(t["got_bnb"]), float(t["stake_bnb"])
    l = launch.get(t["token"].lower())
    if not got or not l or is_test(t):
        continue
    pair, token = Web3.to_checksum_address(l["pair"]), Web3.to_checksum_address(l["token"])
    is0 = WBNB < int(token, 16)
    b0 = head - int((now - int(l["ts"])) / SPB) - 60
    ev = []
    try:
        for b in range(b0, min(head, b0 + 8000), 400):
            ev += call(lambda: a.eth.get_logs({"address": pair, "fromBlock": b, "toBlock": min(head, b + 399), "topics": [[SYNC, SWAP]]}))
    except Exception as e:  # 18.11: one old deal the nodes cannot serve no longer stops all the others
        print(f"{t['symbol']}: логи не прочитаны ({str(e)[:60]})")
        continue
    sw, sy = [], []
    for e in ev:
        d = bytes(e["data"])
        if e["topics"][0].hex().replace("0x", "") == SYNC[2:]:
            r0, r1 = int.from_bytes(d[:32], "big"), int.from_bytes(d[32:64], "big")
            sy.append((e["blockNumber"], (r0 if is0 else r1) / 1e18))
        else:
            x = [int.from_bytes(d[i:i + 32], "big") for i in (0, 32, 64, 96)]
            sw.append((e["blockNumber"], (x[0] if is0 else x[1]) / 1e18, (x[2] if is0 else x[3]) / 1e18))
    if not sy:
        continue
    t0, start = sy[0][0], sy[0][1]
    kind = 25 if start >= 20 else 10 if start >= 5 else 1
    buy = next((s for s in sw if abs(s[1] - stake) < 1e-9), None)
    sell = next((s for s in sw if abs(s[2] - got) < 1e-7), None)
    if not buy or not sell or kind == 25:
        continue
    peak = max(x[1] for x in sy)
    drain = next((x[0] for x in sy if x[0] > buy[0] and x[1] < 0.2 * peak), None)
    others = [s for s in sw if s[1] > 0 and s != buy]
    during = [s for s in others if buy[0] < s[0] < sell[0]]
    after = [s for s in others if s[0] > sell[0] and (drain is None or s[0] < drain)]
    sells = [s for s in sw if s[2] > 0 and buy[0] < s[0] < sell[0]]
    a0, a1 = buy[0] - t0, sell[0] - t0
    norm = sorted(sum(a0 < x - c["t0"] < a1 for x in c["buys"]) for c in clean[kind])
    q10, q90 = norm[len(norm) // 10], norm[len(norm) * 9 // 10]
    first = (after[0][0] - sell[0]) * SPB if after else None
    rows.append(dict(sym=t["symbol"], kind=kind, n=len(during), med=st.median(norm), q10=q10, q90=q90, hold=(a1 - a0) * SPB / 60, after=len(after), first=first))
    # the panel's "Итоги" charts: price against our buy, every other buy and sell, by minutes from the launch
    tok = {}
    for e in ev:
        if e["topics"][0].hex().replace("0x", "") == SYNC[2:]:
            d = bytes(e["data"])
            r0, r1 = int.from_bytes(d[:32], "big"), int.from_bytes(d[32:64], "big")
            tok[e["blockNumber"]] = ((r0 if is0 else r1), (r1 if is0 else r0))
    pr = lambda v: v[0] / v[1] if v[1] else 0
    ref = next((pr(tok[b]) for b in sorted(tok, reverse=True) if b < buy[0]), pr(tok[min(tok)]))
    end = (drain or head) + 120 / SPB
    mins = lambda b: round((b - t0) * SPB / 60, 2)
    charts.append(dict(sym=t["symbol"], time=t["time"], kind=kind, stake=stake, got=got, pnl_pct=float(t["pnl_pct"]), reason=t["reason"],
                       buy=mins(buy[0]), sell=mins(sell[0]), drain=mins(drain) if drain else None, bots=len(during), usual=st.median(norm),
                       pts=[[mins(b), round((pr(tok[b]) / ref - 1) * 100, 1)] for b in sorted(tok) if b <= end],
                       buys=[mins(s[0]) for s in others if s[0] <= end],
                       sells=[[mins(s[0]), round(s[2], 3)] for s in sw if s[2] > 0 and s != sell and s[0] <= end and (drain is None or s[0] < drain)]))
    print(f"{t['symbol']:<14} {kind:>3} {a0 * SPB / 60:6.1f} {(a1 - a0) * SPB / 60:8.1f}   | {len(during):>6} | {st.median(norm):>5.0f} ({q10}–{q90}) | "
          f"{'нет' if first is None else f'{first:.0f} с':>10} | {len(after):>4} | {'нет' if drain is None else f'{(drain - t0) * SPB / 60:.1f}'}"
          + (f" | продажи при нас: {[round(s[2], 3) for s in sells][:6]}" if sells else ""))
write_charts(at=time.strftime("%d.%m %H:%M"), deals=charts, deals_n=deals_n, signals=sig, sig_rows=sig_rows, sig_at=sig_at)
if rows:
    long = [r for r in rows if r["hold"] >= 2]
    print(f"\nсделок {len(rows)}, держали ≥2 мин: {len(long)}; покупок при нас меньше обычной медианы: {sum(r['n'] < r['med'] for r in long)}, ниже 10%: {sum(r['n'] < r['q10'] for r in long)}")
    print(f"первая покупка после нашей продажи: медиана {st.median(r['first'] for r in rows if r['first'] is not None):.0f} с")
