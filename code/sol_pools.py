"""18.11 night (user): the Solana farm's pool schedule. For each creator: its txs (Helius, HELIUS_KEY of .env, never printed) -> every pool
it created (CreatePool: SOL put in) and every Withdraw (pool SOL before / after, minutes after the creation), and, for the pools it created,
a sparse price path from the pool's own signatures (sampled) to see when the pool reached each SOL level.
The question: is there a pool SOL level the creator never withdraws below (sell there = safe), and how fast is it reached.
Out: sol_pools.csv, sol_pools.txt.  Usage: python sol_pools.py <creator> [<creator> ...]"""
import collections
import csv
import json
import os
import re
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
WSOL = "So11111111111111111111111111111111111111112"
RPC = None
for _l in open(r"D:\bot\.env", encoding="utf-8-sig"):
    if _l.strip().startswith("HELIUS_KEY="):
        RPC = "https://mainnet.helius-rpc.com/?api-key=" + _l.split("=", 1)[1].strip().strip('"\'')
LAST, LK, CALLS = [0.0], threading.Lock(), [0]
P = lambda *a: print(*a, flush=True)


def rpc(method, params, tries=6):
    for k in range(tries):
        with LK:
            gap = 0.125 - (time.time() - LAST[0])
            if gap > 0:
                time.sleep(gap)
            LAST[0] = time.time()
            CALLS[0] += 1
        try:
            req = urllib.request.Request(RPC, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
                                         headers={"Content-Type": "application/json"})
            r = json.loads(urllib.request.urlopen(req, timeout=60).read())
            if "error" in r:
                raise RuntimeError(str(r["error"])[:100])
            return r["result"]
        except Exception:
            time.sleep(1.0 * (k + 1))
    return None


def sigs_of(addr, cap):
    out, before = [], None
    while len(out) < cap:
        p = {"limit": 1000}
        if before:
            p["before"] = before
        page = rpc("getSignaturesForAddress", [addr, p]) or []
        if not page:
            break
        out += page
        before = page[-1]["signature"]
        if len(page) < 1000:
            break
    return out[:cap]


def tx(s):
    return rpc("getTransaction", [s, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1, "commitment": "confirmed"}])


def pools_in(t, exclude):
    m = t["meta"]
    owners = collections.defaultdict(dict)
    for b in m.get("postTokenBalances") or []:
        owners[b.get("owner")][b["mint"]] = float(b["uiTokenAmount"].get("uiAmount") or 0)
    pre = {(b.get("owner"), b["mint"]): float(b["uiTokenAmount"].get("uiAmount") or 0) for b in (m.get("preTokenBalances") or [])}
    out = []
    for o, mm in owners.items():
        if WSOL in mm and len(mm) == 2 and o != exclude:
            mint = [x for x in mm if x != WSOL][0]
            out.append((o, mint, pre.get((o, WSOL), 0.0), mm[WSOL]))
    return out


rows = []
for cr in sys.argv[1:]:
    hs = [s for s in sigs_of(cr, 6000) if not s.get("err")]
    # only txs that can be a CreatePool / Withdraw: we must read them to know; read all but in parallel
    with ThreadPoolExecutor(8) as ex:
        ts = list(ex.map(lambda s: tx(s["signature"]), hs))
    creates, withdraws = {}, collections.defaultdict(list)
    for t in ts:
        if not t or not t.get("meta") or t["meta"].get("err"):
            continue
        ks = [k["pubkey"] if isinstance(k, dict) else k for k in t["transaction"]["message"]["accountKeys"]]
        if ks[0] != cr:
            continue
        logs = " ".join(t["meta"].get("logMessages") or [])
        if "CreatePool" in logs:
            for o, mint, pre, post in pools_in(t, cr):
                creates[o] = (t["blockTime"], mint, post)
        elif "Withdraw" in logs:
            for o, mint, pre, post in pools_in(t, cr):
                withdraws[o].append((t["blockTime"], pre, post))
    P(f"{cr[:10]}: транзакций {len(hs)}, пулов создано {len(creates)}, выводов {sum(len(v) for v in withdraws.values())}, запросов {CALLS[0]}")
    for o, (ct, mint, sol0) in sorted(creates.items(), key=lambda kv: kv[1][0]):
        ws = sorted(w for w in withdraws.get(o, []) if w[0] - ct > 60 and w[1] > 1)
        # pool path: sample up to 60 of the pool's own txs evenly across its life
        ps = [s for s in sigs_of(o, 20000) if not s.get("err")]
        ps.reverse()
        if ws:
            ps = [s for s in ps if (s.get("blockTime") or 0) <= ws[-1][0]]
        pick = ps[:: max(1, len(ps) // 60)]
        with ThreadPoolExecutor(8) as ex:
            pts = []
            for t in ex.map(lambda s: tx(s["signature"]), pick):
                if t and t.get("meta") and not t["meta"].get("err"):
                    for oo, mm, pre, post in pools_in(t, None):
                        if oo == o:
                            pts.append(((t["blockTime"] - ct) / 60, post))
        pts.sort()
        first_at = {}
        for lvl in (70, 80, 90, 100, 120, 150, 200):
            first_at[lvl] = next((round(m, 1) for m, v in pts if v >= lvl), None)
        rows.append(dict(creator=cr, pool=o, mint=mint, created=time.strftime("%d.%m %H:%M", time.localtime(ct)), sol_in=round(sol0, 2),
                         pool_txs=len(ps), withdraw_min=round((ws[0][0] - ct) / 60, 1) if ws else "", withdraw_pool_sol=round(ws[0][1], 1) if ws else "",
                         withdraws=len(ws), peak_seen=round(max((v for _, v in pts), default=0), 1),
                         **{f"min_to_{k}": v for k, v in first_at.items()}))
        P(f"   пул {o[:10]} {rows[-1]['created']} внесено {sol0:.1f}, вывод " +
          (f"на {rows[-1]['withdraw_min']} мин при {rows[-1]['withdraw_pool_sol']} SOL" if ws else "не было/не видно") +
          f", дошёл до 90 SOL на {first_at[90]} мин, до 100 на {first_at[100]} мин, свапов {len(ps)}")

with open(os.path.join(HERE, "sol_pools.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), delimiter=";")
    w.writeheader()
    w.writerows(rows)
lv = [r["withdraw_pool_sol"] for r in rows if r["withdraw_pool_sol"] != ""]
L = [f"пулов {len(rows)}, с выводом {len(lv)}; внесено: {collections.Counter(r['sol_in'] for r in rows).most_common(5)}",
     f"пул в момент вывода, SOL (по возрастанию): {sorted(lv)}",
     f"минуты до вывода: {sorted(r['withdraw_min'] for r in rows if r['withdraw_min'] != '')}",
     f"запросов Helius {CALLS[0]}"]
P("\n".join(L))
open(os.path.join(HERE, "sol_pools.txt"), "w", encoding="utf-8").write("\n".join(L))
