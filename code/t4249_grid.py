"""18.11 night: entry/exit grid for the 4249 template on every coin of t4249_scan.csv (Sync reserves, constant product, 0.25 % fee each
way, our own buy stays in the pool until we sell). Entry = the first Sync at or after E s from the launch (trading opens ~22.5 s);
exit at X s or into the drained pool. Read-only. Out: t4249_grid.txt.  Usage: python t4249_grid.py"""
import csv
import json
import os
import statistics as st
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
NODES = ["https://rpc-bsc.48.club", "https://rpc.48.club"]
WBNB = "bb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
SYNC = "0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1"
CNT = [0]


def call(m, p, tries=6):
    err = None
    for k in range(tries):
        CNT[0] += 1
        try:
            r = json.loads(urllib.request.urlopen(urllib.request.Request(NODES[(CNT[0] + k) % 2], data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": m, "params": p}).encode(),
                                                                         headers={"Content-Type": "application/json"}), timeout=40).read())
            if "error" in r:
                raise RuntimeError(str(r["error"])[:100])
            return r["result"]
        except Exception as e:
            err = e
            time.sleep(0.5 + k)
    raise RuntimeError(str(err)[:80])


rows = list(csv.DictReader(open(os.path.join(HERE, "t4249_scan.csv"), encoding="utf-8-sig"), delimiter=";"))


def path(r):
    pair = call("eth_call", [{"to": "0x" + WBNB, "data": "0x"}, "latest"]) if False else None
    tok = r["token"]
    fac = call("eth_call", [{"to": "0xca143ce32fe78f1f7019d7d551a6402fc5350c73", "data": "0xe6a43905" + tok[2:].rjust(64, "0") + WBNB.rjust(64, "0")}, "latest"])
    pair = "0x" + fac[-40:]
    t0 = call("eth_call", [{"to": pair, "data": "0x0dfe1681"}, "latest"])
    wbnb0 = t0[-40:] == WBNB
    L0 = int(r["launch_block"])
    out = []
    for b in range(L0, L0 + 2400, 400):
        out += call("eth_getLogs", [{"address": pair, "fromBlock": hex(b), "toBlock": hex(b + 399), "topics": [SYNC]}])
    pts = []
    for l in out:
        d = l["data"][2:]
        a, b = int(d[:64], 16), int(d[64:128], 16)
        rb, rt = (a, b) if wbnb0 else (b, a)
        pts.append(((int(l["blockNumber"], 16) - L0) * 0.45, rb / 1e18, rt))
    return pts


with ThreadPoolExecutor(6) as ex:
    paths = list(ex.map(path, rows))


def at(p, t, after=False):
    if after:
        return next((x for x in p if x[0] >= t), None)
    r = None
    for x in p:
        if x[0] <= t:
            r = x
        else:
            break
    return r


def trade(p, e, x, stake):
    a = at(p, e, after=True)
    if not a or a[1] < 0.5:
        return None
    rb, rt = a[1], a[2]
    got = rt * stake * 0.9975 / (rb + stake * 0.9975)
    b = at(p, x)
    if not b or b[1] < 0.5:
        return -1.0
    rb2, rt2 = b[1] + stake, b[2] - got
    return (rb2 * got * 0.9975 / (rt2 + got * 0.9975)) / stake - 1


L = [f"шаблон 4249: монет {len(paths)}; вход = первый блок не раньше E с (торговля с ~22.5 с), выход в X с"]
for stake in (0.01, 0.05, 0.1, 0.3):
    L.append(f"\nСТАВКА {stake} BNB: E → X: среднее % | медиана % | худшая % | в плюсе/всего")
    best = []
    for e in (23, 30, 45, 60, 90, 120, 180):
        for x in (240, 300, 360, 420, 480, 540, 570, 600, 630, 660):
            res = [trade(p, e, x, stake) for p in paths]
            res = [r for r in res if r is not None]
            if len(res) < 0.8 * len(paths):
                continue
            best.append((st.mean(res), e, x, st.median(res), min(res), sum(r > 0 for r in res), len(res)))
    for m, e, x, md, mn, w, n in sorted(best, reverse=True)[:8]:
        L.append(f"  {e:>4} → {x:>4}: {m * 100:+7.1f} | {md * 100:+7.1f} | {mn * 100:+7.1f} | {w}/{n}")
# price curve
cur = []
for m in (0.5, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11):
    v = []
    for p in paths:
        a, b = at(p, 23, after=True), at(p, m * 60)
        if a and b and b[1] > 0.5:
            v.append((b[1] / b[2]) / (a[1] / a[2]))
        elif a:
            v.append(0)
    cur.append(f"{m}м x{st.median(v):.2f} (мин x{min(v):.2f})")
L.append("\nцена к цене открытия (медиана, минимум): " + ", ".join(cur))
L.append(f"запросов {CNT[0]}")
print("\n".join(L))
open(os.path.join(HERE, "t4249_grid.txt"), "w", encoding="utf-8").write("\n".join(L))
