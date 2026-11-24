"""10.11 (user's checklist): profile of the wallets in analysis/bot_watch.csv per family. Read-only.
1 wallets repeating across pools; 2 intervals between a wallet's operations; 3 buys/sells in the same or adjacent blocks (bursts);
4 repeating buy sizes; 5 sell sizes and periodicity; 6 funding: who sent BNB to the most active wallets (the last top-up before their
first buy here, by a binary search of the balance; open nodes keep recent state only); 7 gas price and method per wallet.
Out: bot_profile.txt (+ bot_funding.csv). Usage: python bot_profile.py [family substring, e.g. 2941] [top wallets for funding=40]"""
import collections
import csv
import itertools
import os
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
FAM = sys.argv[1] if len(sys.argv) > 1 else "2941"
TOPN = int(sys.argv[2]) if len(sys.argv) > 2 else 40
SPB = 0.45
rows = [r for r in csv.DictReader(open(os.path.join(HERE, "bot_watch.csv"), encoding="utf-8-sig"), delimiter=";") if FAM in r["family"]]
for r in rows:
    r["block"], r["bnb"], r["age_s"], r["idx"] = int(r["block"]), float(r["bnb"]), int(r["age_s"]), int(r["idx"])
pairs = sorted({r["pair"] for r in rows})
L = [f"семья {FAM}: событий {len(rows)}, пулов {len(pairs)} ({time.strftime('%d.%m %H:%M')})"]

# 1 repeating wallets
by_w = collections.defaultdict(list)
for r in rows:
    by_w[r["sender"]].append(r)
rep = collections.Counter(len({r['pair'] for r in v}) for v in by_w.values())
L.append("\n1. Повтор адресов: кошельков " + str(len(by_w)) + "; в скольких пулах: " + ", ".join(f"{k}: {n}" for k, n in sorted(rep.items())))
multi = [w for w, v in by_w.items() if len({r['pair'] for r in v}) >= max(2, len(pairs) // 2)]
L.append(f"   в половине пулов и больше: {len(multi)} кошельков")

# 2 intervals
gaps = []
for w, v in by_w.items():
    for p in {r["pair"] for r in v}:
        bl = sorted(r["block"] for r in v if r["pair"] == p)
        gaps += [(b - a) * SPB for a, b in zip(bl, bl[1:])]
if gaps:
    q = sorted(gaps)
    L.append(f"\n2. Интервалы между операциями одного кошелька в одном пуле: медиана {statistics.median(q):.0f} с, 10% {q[len(q) // 10]:.0f} с, 90% {q[len(q) * 9 // 10]:.0f} с")

# 3 bursts: several sells in one block
for kind in ("продажа", "покупка"):
    blk = collections.Counter((r["pair"], r["block"]) for r in rows if r["kind"] == kind)
    burst = [n for n in blk.values() if n >= 3]
    L.append(f"\n3. {kind}: в одном блоке 3+ разных кошельков — {len(burst)} раз (по {statistics.median(burst) if burst else 0} шт. медиана); "
             f"доля {kind} в таких блоках {sum(burst) / max(1, sum(blk.values())) * 100:.0f}%")

# 4 buy sizes
buys = [r for r in rows if r["kind"] == "покупка" and r["role"] != "мы"]
sz = collections.Counter(round(r["bnb"], 3) for r in buys)
L.append(f"\n4. Покупки: {len(buys)}, разных размеров (до 0.001) {len(sz)}; частые: " + ", ".join(f"{k}×{n}" for k, n in sz.most_common(8)))
if buys:
    q = sorted(r["bnb"] for r in buys)
    L.append(f"   размер: медиана {statistics.median(q):.3f}, 10% {q[len(q) // 10]:.3f}, 90% {q[len(q) * 9 // 10]:.3f} BNB")

# 5 sells: size and periodicity of the bursts
for p in pairs[:12]:
    ev = sorted((r for r in rows if r["pair"] == p), key=lambda r: (r["block"], r["idx"]))
    sb = sorted({r["block"] for r in ev if r["kind"] == "продажа" and r["bnb"] < -0.2})
    groups = []
    for b in sb:
        if groups and b - groups[-1][-1] <= 5:
            groups[-1].append(b)
        else:
            groups.append([b])
    starts = [g[0] for g in groups]
    gp = [(b - a) * SPB for a, b in zip(starts, starts[1:])]
    drain = next((r for r in ev if r["kind"] == "слив"), None)
    rise = []
    for g in groups:
        before = [r for r in ev if r["block"] < g[0]]
        prev_burst = max((x for x in starts if x < g[0]), default=None)
        base = [r for r in ev if prev_burst is not None and prev_burst + 6 >= r["block"] >= prev_burst]
        if before and base:
            rise.append(before[-1]["pool_after"] and float(before[-1]["pool_after"]) - min(float(r["pool_after"]) for r in base))
    L.append(f"\n5. {p[:10]}: залпов продаж {len(groups)}, на секундах {[round((g[0] - ev[0]['block']) * SPB) for g in groups]}, интервалы {[round(x) for x in gp]}; "
             f"рост пула перед залпом (от дна прошлого) {[round(x, 1) for x in rise]} BNB; слив " + (f"на {drain['age_s']} с, {drain['bnb']:.1f} BNB, от {drain['sender'][:10]}" if drain else "нет"))

# 7 gas / method
gas = collections.Counter((r["gwei"], r["method"]) for r in rows if r["role"] != "мы")
L.append("\n7. Газ и метод (самые частые): " + "; ".join(f"{g} gwei {m} ×{n}" for (g, m), n in gas.most_common(8)))

# 6 funding of the most active wallets
URLS = ["https://bsc-rpc.publicnode.com", "https://rpc.48.club", "https://bsc-dataseed.bnbchain.org"]
W3S = []
for u in URLS:
    w = Web3(Web3.HTTPProvider(u, request_kwargs={"timeout": 25}))
    w.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    W3S.append(w)
RR, LOCK = itertools.count(), threading.Lock()


def call(f, tries=8):
    for k in range(tries):
        with LOCK:
            w = W3S[next(RR) % len(W3S)]
        try:
            return f(w)
        except Exception:
            time.sleep(0.4 + k * 0.5)
    return None


top = sorted(by_w, key=lambda w: -len(by_w[w]))[:TOPN]


def funder(a):
    first = min(r["block"] for r in by_w[a])
    who = Web3.to_checksum_address(a)
    hi_, lo_ = first - 1, first - 6000
    bh, bl = call(lambda w: w.eth.get_balance(who, hi_)), call(lambda w: w.eth.get_balance(who, lo_))
    if bh is None or bl is None or bh <= bl:
        return a, None, None, None
    while hi_ - lo_ > 1:
        mid = (lo_ + hi_) // 2
        bm = call(lambda w: w.eth.get_balance(who, mid))
        if bm is None:
            return a, None, None, None
        if bm < bh - (bh - bl) * 0.5:
            lo_ = mid
        else:
            hi_ = mid
    blk = call(lambda w: w.eth.get_block(hi_, full_transactions=True))
    for x in blk.transactions if blk else []:
        if (x["to"] or "").lower() == a:
            return a, x["from"].lower(), x["value"] / 1e18, first - hi_
    return a, "внутренний перевод", None, first - hi_


with ThreadPoolExecutor(6) as ex:
    fund = list(ex.map(funder, top))
src = collections.defaultdict(list)
for a, f, v, lead in fund:
    src[f or "?"].append((a, v, lead))
L.append(f"\n6. Источники денег у {len(top)} самых активных кошельков:")
for f, lst in sorted(src.items(), key=lambda kv: -len(kv[1])):
    leads = [x[2] for x in lst if x[2] is not None]
    L.append(f"   {f}: кошельков {len(lst)}" + (f", пополнение за {statistics.median(leads)} бл. до первой покупки здесь" if leads else "")
             + (f", суммы {sorted(round(x[1], 3) for x in lst if x[1])[:6]}" if any(x[1] for x in lst) else ""))
with open(os.path.join(HERE, "bot_funding.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f, delimiter=";")
    w.writerow(["wallet", "funder", "bnb", "lead_blocks", "ops", "pairs"])
    for a, fu, v, lead in fund:
        w.writerow([a, fu, v, lead, len(by_w[a]), len({r['pair'] for r in by_w[a]})])
open(os.path.join(HERE, "bot_profile.txt"), "w", encoding="utf-8").write("\n".join(L))
print("\n".join(L))
