"""18.11 night: the Solana farm's entry window on every pool of sol_pools.csv: the first N txs of each pool (Helius, HELIUS_KEY of .env,
never printed) -> pool SOL and coin reserves after each tx (price = SOL / coins), time from the CreatePool. Strategy: buy at E s after the
creation (price of the last tx before E + our own impact), sell at the first moment the pool SOL >= T (or at the withdraw = lost),
pool fee ~0.3 % each way. Also: the slots of the first farm buys. Out: sol_window.txt, sol_window_paths.json.
Usage: python sol_window.py [txs per pool=300] [stake SOL=1]"""
import csv
import json
import os
import statistics as st
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
NT = int(sys.argv[1]) if len(sys.argv) > 1 else 300
STAKE = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
WSOL = "So11111111111111111111111111111111111111112"
FEE = 0.003
RPC = None
for _l in open(r"D:\bot\.env", encoding="utf-8-sig"):
    if _l.strip().startswith("HELIUS_KEY="):
        RPC = "https://mainnet.helius-rpc.com/?api-key=" + _l.split("=", 1)[1].strip().strip('"\'')
LAST, LK, CALLS = [0.0], threading.Lock(), [0]


def rpc(method, params, tries=6):
    for k in range(tries):
        with LK:
            gap = 0.125 - (time.time() - LAST[0])
            if gap > 0:
                time.sleep(gap)
            LAST[0] = time.time()
            CALLS[0] += 1
        try:
            r = json.loads(urllib.request.urlopen(urllib.request.Request(RPC, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
                                                                         headers={"Content-Type": "application/json"}), timeout=60).read())
            if "error" in r:
                raise RuntimeError(str(r["error"])[:100])
            return r["result"]
        except Exception:
            time.sleep(1.0 * (k + 1))
    return None


pools = list(csv.DictReader(open(os.path.join(HERE, "sol_pools.csv"), encoding="utf-8-sig"), delimiter=";"))
PATHS = {}


def first_txs(r):
    pool, mint = r["pool"], r["mint"]
    sigs, before = [], None
    while True:
        p = {"limit": 1000}
        if before:
            p["before"] = before
        page = rpc("getSignaturesForAddress", [pool, p]) or []
        if not page:
            break
        sigs += page
        before = page[-1]["signature"]
        if len(page) < 1000:
            break
    sigs.reverse()
    first = [s["signature"] for s in sigs if not s.get("err")][:NT]
    pts = []
    for s in first:
        t = rpc("getTransaction", [s, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1}])
        if not t or not t.get("meta") or t["meta"].get("err"):
            continue
        sol = tok = None
        for b in t["meta"].get("postTokenBalances") or []:
            if b.get("owner") == pool and b["mint"] == WSOL:
                sol = float(b["uiTokenAmount"].get("uiAmount") or 0)
            if b.get("owner") == pool and b["mint"] == mint:
                tok = float(b["uiTokenAmount"].get("uiAmount") or 0)
        if sol is not None and tok:
            pts.append((t["blockTime"], t["slot"], sol, tok))
    return pool, pts, r


with ThreadPoolExecutor(4) as ex:
    for pool, pts, r in ex.map(first_txs, pools):
        if pts:
            t0, s0 = pts[0][0], pts[0][1]
            PATHS[pool] = dict(w_min=r["withdraw_min"], w_sol=r["withdraw_pool_sol"],
                               path=[(p[0] - t0, p[1] - s0, p[2], p[3]) for p in pts])
json.dump(PATHS, open(os.path.join(HERE, "sol_window_paths.json"), "w"), default=str)


def sim(path, e_slot, T, stake):
    """buy after the pool reached slot e_slot (state of the last tx before it), sell at the first state with SOL >= T."""
    before = [p for p in path if p[1] < e_slot]
    if not before:
        return None
    _, _, s, k = before[-1]
    got = k * stake * (1 - FEE) / (s + stake * (1 - FEE))
    for p in path:
        if p[1] < e_slot:
            continue
        sol, tok = p[2] + stake, p[3] - got  # our buy stays in the pool
        if sol - stake >= T:
            return (sol * got * (1 - FEE) / (tok + got * (1 - FEE))) / stake - 1, p[0]
    return None  # never reached T in the first NT txs (sold later or withdrawn: unknown)


L = [f"пулов с данными {len(PATHS)} из {len(pools)}; первые {NT} транзакций каждого; ставка {STAKE} SOL; комиссия пула {FEE * 100:.1f}% в каждую сторону"]
starts = [v["path"][1][2] if len(v["path"]) > 1 else v["path"][0][2] for v in PATHS.values()]
L.append(f"SOL после первых двух транзакций (создание + доплата): медиана {st.median(starts):.2f}")
for T in (88, 89, 90, 91):
    reach = [next((p[0] for p in v["path"] if p[2] >= T), None) for v in PATHS.values()]
    ok = [x for x in reach if x is not None]
    L.append(f"пул дошёл до {T} SOL в первых {NT} транзакциях: {len(ok)} из {len(PATHS)}, через с: медиана {st.median(ok) if ok else '-'}, макс {max(ok) if ok else '-'}")
L.append("\nСТРАТЕГИЯ: вход после слота E от создания (слот ≈0.4 с) → продажа при SOL ≥ T: среднее % | медиана % | худшая % | дошло/всего | время сделки, с (медиана)")
for E in (1, 3, 5, 10, 15, 25):
    for T in (88, 89, 90):
        res = [sim(v["path"], E, T, STAKE) for v in PATHS.values()]
        got = [x for x in res if x]
        if not got:
            continue
        rr = [x[0] for x in got]
        L.append(f"  E={E:>2} слотов, T={T}: {st.mean(rr) * 100:+6.1f} | {st.median(rr) * 100:+6.1f} | {min(rr) * 100:+6.1f} | {len(got)}/{len(PATHS)} | {st.median(x[1] for x in got)}")
# first farm move: the slot where the pool SOL first rises above start + 0.5
fm = []
for v in PATHS.values():
    base = v["path"][1][2] if len(v["path"]) > 1 else v["path"][0][2]
    fm.append(next((p[1] for p in v["path"] if p[2] >= base + 0.5), None))
fm = [x for x in fm if x is not None]
L.append(f"\nпервый заметный рост пула (+0.5 SOL): слот от создания, медиана {st.median(fm)}, мин {min(fm)}, макс {max(fm)}")
L.append(f"запросов Helius {CALLS[0]}")
print("\n".join(L))
open(os.path.join(HERE, "sol_window.txt"), "w", encoding="utf-8").write("\n".join(L))
