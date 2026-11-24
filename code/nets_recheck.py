"""18.11 (user): full re-check of nets 1-4 after their return. Read-only, public nodes, no keys.
Two windows of the same length: NOW (the last H hours) and BEFORE (H hours ending at the 16.11 stop, 16.11 07:10 UTC).
For every coin of each net (PairCreated with WBNB, token's slot 6 = the net's guard):
  code (size, sha, function selectors), slots 0-9, pool (start / peak / now BNB, drain minute, method), swaps (buys, sells, BNB in),
  unique buyers, holders (from Transfer logs), the creator (nonce = txs sent, balance now), cash wallets and guards (nonce, balance).
Out: nets_recheck.txt, nets_recheck.csv.  Usage: python nets_recheck.py [hours=12]"""
import collections
import csv
import hashlib
import json
import os
import statistics as st
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
H = float(sys.argv[1]) if len(sys.argv) > 1 else 12
NODES = ["https://rpc-bsc.48.club", "https://rpc.48.club", "https://bsc-dataseed.bnbchain.org"]
WBNB = "bb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
FACTORY = "0xca143ce32fe78f1f7019d7d551a6402fc5350c73"
PAIRCREATED = "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"
SWAP = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
SYNC = "0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1"
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
GUARDS = {"bd418ce6a83995971de9f1cf9f61f345e91aeadd": 1, "dbbf08242c22a634960ceaad44d9f2564fb26452": 2,
          "623e7c23c7c228095f94d9bd567eae55cb87a966": 3, "a70b3f34e3ca7d4665b794ce6bb29168b1d2e40d": 4}
CASH = {1: "0xde8eb937cb5475eee5ac96dce6ba2d18e439c473", 2: "0xF51DD7340C90Bf1683bB32D6397ED3aCEfb6B6A7",
        3: "0x1ac2c5ddd9e7624ca28ea13078416b50a34545b4", 4: "0x238d7170f309a55b87a144a341bd6105897082ca"}
import calendar
STOP_TS = calendar.timegm((2026, 11, 16, 7, 10, 0))  # 16.11.2026 07:10 UTC
P = lambda *a: print(*a, flush=True)


def call(m, p, tries=6):
    err = None
    for k in range(tries):
        u = NODES[k % len(NODES)]
        try:
            r = json.loads(urllib.request.urlopen(urllib.request.Request(u, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": m, "params": p}).encode(),
                                                                         headers={"Content-Type": "application/json"}), timeout=40).read())
            if "error" in r:
                raise RuntimeError(str(r["error"])[:120])
            return r["result"]
        except Exception as e:
            err = e
            time.sleep(0.4 + 0.6 * k)
    raise RuntimeError(f"{m}: {err}")


def logs(addr, topic, a, b, step=400):
    out = []
    x = a
    while x <= b:
        y = min(b, x + step - 1)
        q = {"fromBlock": hex(x), "toBlock": hex(y), "topics": [topic]}
        if addr:
            q["address"] = addr
        try:
            out += call("eth_getLogs", [q])
            x = y + 1
        except Exception:
            if step > 50:
                step //= 2
            else:
                x = y + 1
    return out


HEAD = int(call("eth_blockNumber", []), 16)
BT = {}


def btime(n):
    if n not in BT:
        BT[n] = int(call("eth_getBlockByNumber", [hex(n), False])["timestamp"], 16)
    return BT[n]


def block_at(ts):
    now_ts = btime(HEAD)
    lo, hi = HEAD - int((now_ts - ts) / 0.40) - 2000, HEAD  # ~0.45 s blocks; old blocks far back are pruned on these nodes
    while btime(lo) > ts:
        lo -= 20000
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if btime(mid) < ts:
            lo = mid
        else:
            hi = mid
    return hi


def selectors(code):
    c = bytes.fromhex(code[2:])
    out, i = set(), 0
    while i < len(c):
        op = c[i]
        if op == 0x63 and i + 5 <= len(c):
            out.add(c[i + 1:i + 5].hex())
        i += 1 + (op - 0x5f if 0x60 <= op <= 0x7f else 0)
    return out


def coins_in(a, b):
    found = []
    pcs = logs(FACTORY, PAIRCREATED, a, b)
    P(f"  пар в окне: {len(pcs)}")

    def check(l):
        t0, t1 = l["topics"][1][-40:], l["topics"][2][-40:]
        tok = t1 if t0 == WBNB else t0 if t1 == WBNB else None
        if not tok:
            return None
        s6 = call("eth_getStorageAt", ["0x" + tok, "0x6", "latest"])[-40:]
        if s6 in GUARDS:
            return dict(net=GUARDS[s6], token="0x" + tok, pair="0x" + l["data"][26:66], block=int(l["blockNumber"], 16))
        return None
    with ThreadPoolExecutor(6) as ex:
        for r in ex.map(check, pcs):
            if r:
                found.append(r)
    return found


CODEINFO = {}


def study(c, end_block):
    tok, pair, b0 = c["token"], c["pair"], c["block"]
    code = call("eth_getCode", [tok, "latest"])
    c["size"] = (len(code) - 2) // 2
    c["sha"] = hashlib.sha256(bytes.fromhex(code[2:])).hexdigest()[:12]
    CODEINFO.setdefault(c["sha"], selectors(code))
    c["slots"] = [call("eth_getStorageAt", [tok, hex(i), "latest"])[-40:] for i in range(10)]
    last = min(end_block, b0 + int(3 * 3600 / 0.45))  # follow a coin at most 3 h
    sy = logs(pair, SYNC, b0, last)
    t0 = call("eth_call", [{"to": pair, "data": "0x0dfe1681"}, "latest"])[-40:]
    wi = 0 if t0 == WBNB else 1
    bnb = [(int(l["blockNumber"], 16), int(l["data"][2 + 64 * wi:66 + 64 * wi], 16) / 1e18, l["transactionHash"]) for l in sy]
    ts0 = btime(b0)
    c["start_bnb"] = round(bnb[0][1], 2) if bnb else 0
    c["peak_bnb"] = round(max((x[1] for x in bnb), default=0), 2)
    c["end_bnb"] = round(bnb[-1][1], 2) if bnb else 0
    c["drain_min"] = c["drain_how"] = ""
    for i in range(1, len(bnb)):
        if bnb[i - 1][1] > 1 and bnb[i][1] < 0.15 * bnb[i - 1][1]:
            c["drain_min"] = round((btime(bnb[i][0]) - ts0) / 60, 1)
            tx = call("eth_getTransactionByHash", [bnb[i][2]])
            c["drain_how"] = tx["input"][:10]
            c["drain_from"] = tx["from"]
            break
    if bnb:
        c["creator"] = call("eth_getTransactionByHash", [bnb[0][2]])["from"]
        c["creator_nonce"] = int(call("eth_getTransactionCount", [c["creator"], "latest"]), 16)
        c["creator_bal"] = round(int(call("eth_getBalance", [c["creator"], "latest"]), 16) / 1e18, 3)
    sw = logs(pair, SWAP, b0, last)
    buys = sells = 0
    bnb_in = 0.0
    buyers = set()
    first_buy = []
    for l in sw:
        d = l["data"][2:]
        a0i, a1i, a0o, a1o = (int(d[i * 64:(i + 1) * 64], 16) for i in range(4))
        win = a0i if wi == 0 else a1i
        if win > 0:
            buys += 1
            bnb_in += win / 1e18
            buyers.add(l["topics"][2][-40:])
            if len(first_buy) < 1:
                first_buy.append(round((btime(int(l["blockNumber"], 16)) - ts0) / 60, 2))
        else:
            sells += 1
    c.update(buys=buys, sells=sells, bnb_in=round(bnb_in, 3), buyers=len(buyers), first_buy_min=first_buy[0] if first_buy else "")
    tr = logs(tok, TRANSFER, b0, end_block)
    bal = collections.Counter()
    for l in tr:
        v = int(l["data"], 16)
        bal["0x" + l["topics"][1][-40:]] -= v
        bal["0x" + l["topics"][2][-40:]] += v
    c["holders"] = sum(1 for a, v in bal.items() if v > 0 and a != "0x" + "0" * 40)
    c["transfers"] = len(tr)
    c["age_min"] = round((btime(end_block) - ts0) / 60, 1)
    return c


def window(name, ts_a, ts_b):
    a, b = block_at(ts_a), block_at(ts_b)
    P(f"\n=== окно {name}: блоки {a}–{b} ({time.strftime('%d.%m %H:%M', time.localtime(ts_a))} – {time.strftime('%d.%m %H:%M', time.localtime(ts_b))})")
    cs = coins_in(a, b)
    P(f"  монет сетей 1–4: {collections.Counter(c['net'] for c in cs)}")
    with ThreadPoolExecutor(4) as ex:
        cs = list(ex.map(lambda c: study(c, b), cs))
    for c in cs:
        c["window"] = name
    return cs


now = btime(HEAD)
rows = window("СЕЙЧАС", now - H * 3600, now) + window("ДО ОСТАНОВКИ", STOP_TS - H * 3600, STOP_TS)
keys = ["window", "net", "token", "pair", "block", "age_min", "size", "sha", "start_bnb", "peak_bnb", "end_bnb", "drain_min", "drain_how",
        "creator", "creator_nonce", "creator_bal", "buys", "sells", "bnb_in", "buyers", "first_buy_min", "holders", "transfers"]
with open(os.path.join(HERE, "nets_recheck.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=keys, delimiter=";", extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)


def med(xs):
    xs = [x for x in xs if x != "" and x is not None]
    return round(st.median(xs), 2) if xs else "-"


L = [f"Сети 1–4: последние {H:g} ч против {H:g} ч до остановки 16.11 (голова {HEAD})"]
for net in (1, 2, 3, 4):
    L.append(f"\n=== СЕТЬ {net} ===")
    for wn in ("ДО ОСТАНОВКИ", "СЕЙЧАС"):
        cs = [c for c in rows if c["net"] == net and c["window"] == wn]
        if not cs:
            L.append(f"  {wn}: монет нет")
            continue
        dr = [c for c in cs if c["drain_min"] != ""]
        L.append(f"  {wn}: монет {len(cs)}; код {collections.Counter((c['size'], c['sha']) for c in cs).most_common(3)}")
        L.append(f"     старт BNB {collections.Counter(c['start_bnb'] for c in cs).most_common(4)}; пик медиана {med([c['peak_bnb'] for c in cs])}")
        L.append(f"     покупок на монету: медиана {med([c['buys'] for c in cs])}, макс {max(c['buys'] for c in cs)}; продаж медиана {med([c['sells'] for c in cs])}; "
                 f"покупателей медиана {med([c['buyers'] for c in cs])}; BNB внесено медиана {med([c['bnb_in'] for c in cs])}")
        L.append(f"     держателей медиана {med([c['holders'] for c in cs])}; переводов медиана {med([c['transfers'] for c in cs])}; первая покупка, мин: медиана {med([c['first_buy_min'] for c in cs])}")
        L.append(f"     слито {len(dr)} из {len(cs)}; минута слива: медиана {med([c['drain_min'] for c in dr])}, разброс {min((c['drain_min'] for c in dr), default='-')}–{max((c['drain_min'] for c in dr), default='-')}; способ {collections.Counter(c['drain_how'] for c in dr).most_common(3)}")
        L.append(f"     создатель: транзакций (nonce) медиана {med([c.get('creator_nonce') for c in cs])}, баланс сейчас медиана {med([c.get('creator_bal') for c in cs])}")
        for c in sorted(cs, key=lambda c: c["block"])[-6:]:
            L.append(f"       {c['token']} возраст {c['age_min']} мин, старт {c['start_bnb']} пик {c['peak_bnb']} сейчас {c['end_bnb']}, покупок {c['buys']} "
                     f"({c['bnb_in']} BNB), держателей {c['holders']}, слив {c['drain_min'] or 'нет'}, создатель nonce {c.get('creator_nonce')} баланс {c.get('creator_bal')}")
    cs = [c for c in rows if c["net"] == net]
    if cs:
        slots = collections.defaultdict(collections.Counter)
        for c in cs:
            for i, s in enumerate(c["slots"]):
                slots[i][(c["window"], "одинаков" if s == cs[0]["slots"][i] else "разный")] += 1
        same = [i for i in range(10) if all(c["slots"][i] == cs[0]["slots"][i] for c in cs)]
        L.append(f"  слоты, одинаковые у всех монет обоих окон: {same}")
L.append("\n=== функции кода (селекторы) ===")
for sha, sel in CODEINFO.items():
    L.append(f"  {sha}: {len(sel)} селекторов: {' '.join(sorted(sel))}")
L.append("\n=== кассы и сторожа сейчас ===")
for net, a in CASH.items():
    L.append(f"  касса сети {net} {a}: nonce {int(call('eth_getTransactionCount', [a, 'latest']), 16)}, баланс {int(call('eth_getBalance', [a, 'latest']), 16) / 1e18:.3f} BNB")
for g, net in GUARDS.items():
    L.append(f"  сторож сети {net} 0x{g}: nonce {int(call('eth_getTransactionCount', ['0x' + g, 'latest']), 16)}, баланс {int(call('eth_getBalance', ['0x' + g, 'latest']), 16) / 1e18:.4f} BNB")
P("\n".join(L))
open(os.path.join(HERE, "nets_recheck.txt"), "w", encoding="utf-8").write("\n".join(L))
