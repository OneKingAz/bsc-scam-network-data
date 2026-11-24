"""18.11 night: the first seconds of the Solana farm's pools, tx by tx (Helius, HELIUS_KEY of .env, never printed): pool SOL after every
tx, its time from the CreatePool, its slot, who did it (creator / farm wallet funded by the creator / other). Shows how early an outsider
could buy and at what pool size, and whether outsiders were there. Usage: python sol_first_secs.py [pools=6] [txs per pool=200]"""
import collections
import csv
import json
import os
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
NP = int(sys.argv[1]) if len(sys.argv) > 1 else 6
NT = int(sys.argv[2]) if len(sys.argv) > 2 else 200
WSOL = "So11111111111111111111111111111111111111112"
RPC = None
for _l in open(r"D:\bot\.env", encoding="utf-8-sig"):
    if _l.strip().startswith("HELIUS_KEY="):
        RPC = "https://mainnet.helius-rpc.com/?api-key=" + _l.split("=", 1)[1].strip().strip('"\'')
LAST, LK = [0.0], threading.Lock()


def rpc(method, params, tries=6):
    for k in range(tries):
        with LK:
            gap = 0.125 - (time.time() - LAST[0])
            if gap > 0:
                time.sleep(gap)
            LAST[0] = time.time()
        try:
            r = json.loads(urllib.request.urlopen(urllib.request.Request(RPC, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
                                                                         headers={"Content-Type": "application/json"}), timeout=60).read())
            if "error" in r:
                raise RuntimeError(str(r["error"])[:100])
            return r["result"]
        except Exception:
            time.sleep(1.0 * (k + 1))
    return None


pools = [r for r in csv.DictReader(open(os.path.join(HERE, "sol_pools.csv"), encoding="utf-8-sig"), delimiter=";") if r["withdraw_pool_sol"]]
pools = pools[-NP:]
for r in pools:
    pool = r["pool"]
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
    with ThreadPoolExecutor(8) as ex:
        ts = list(ex.map(lambda s: rpc("getTransaction", [s, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1}]), first))
    t0 = s0 = None
    print(f"\n=== пул {pool[:10]} (создатель {r['creator'][:8]}), вывод на {r['withdraw_min']} мин при {r['withdraw_pool_sol']} SOL; первые {len(ts)} транзакций:")
    payers = collections.Counter()
    shown = 0
    for t in ts:
        if not t or not t.get("meta"):
            continue
        m = t["meta"]
        ks = [k["pubkey"] if isinstance(k, dict) else k for k in t["transaction"]["message"]["accountKeys"]]
        sol = next((float(b["uiTokenAmount"].get("uiAmount") or 0) for b in (m.get("postTokenBalances") or []) if b.get("owner") == pool and b["mint"] == WSOL), None)
        if t0 is None:
            t0, s0 = t["blockTime"], t["slot"]
        payers[ks[0]] += 1
        if shown < 40 or (sol and sol >= 85 and shown < 60):
            print(f"  +{t['blockTime'] - t0:>4} с слот +{t['slot'] - s0:>4} | пул {sol if sol is not None else '-':>8} SOL | {ks[0][:8]}{' СОЗДАТЕЛЬ' if ks[0] == r['creator'] else ''}{' ОШИБКА' if m.get('err') else ''}")
            shown += 1
    print(f"  разных отправителей в первых {len(ts)}: {len(payers)}")
