"""17.11 (user): who makes the most on Solana memecoins (pump.fun + PumpSwap) and how. Read-only, the user's Helius key
(only HELIUS_KEY of .env is read, never printed). Recent txs of both programs for HOURS hours -> each successful buy/sell of the fee payer:
SOL change (native + wrapped SOL) and the token change. Per wallet and coin: realized PnL on coins it bought AND sold (>= 90 % of what it
bought), the hold time, the entry moment (seconds after the coin's first trade in our data), sizes. Top wallets by realized PnL.
Out: sol_pnl_trades.csv (every trade), sol_pnl.txt (the report). Usage: python sol_pnl.py [hours=2] [max txs per program=15000]"""
import collections
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
HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 2.0
CAP = int(sys.argv[2]) if len(sys.argv) > 2 else 15000
RPC = None
for _l in open(r"D:\bot\.env", encoding="utf-8-sig"):
    if _l.strip().startswith("HELIUS_KEY="):
        RPC = "https://mainnet.helius-rpc.com/?api-key=" + _l.split("=", 1)[1].strip().strip('"\'')
if not RPC:
    sys.exit("нет HELIUS_KEY в .env")
PROGS = {"6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P": "pump.fun", "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA": "PumpSwap"}
WSOL = "So11111111111111111111111111111111111111112"
RATE = threading.Semaphore(8)
LAST = [0.0]
LK = threading.Lock()


def rpc(method, params, tries=6):
    err = None
    for k in range(tries):
        with LK:  # <= ~8 requests a second (the free plan allows 10)
            gap = 0.125 - (time.time() - LAST[0])
            if gap > 0:
                time.sleep(gap)
            LAST[0] = time.time()
        try:
            req = urllib.request.Request(RPC, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
                                         headers={"Content-Type": "application/json"})
            r = json.loads(urllib.request.urlopen(req, timeout=40).read())
            if "error" in r:
                raise RuntimeError(str(r["error"])[:100])
            return r["result"]
        except Exception as e:
            err = e
            time.sleep(1.0 * (k + 1))
    print("  запрос не прошёл:", str(err).replace(RPC, "<rpc>")[:90], flush=True)
    return None


now = time.time()
sigs = []
for prog, name in PROGS.items():
    before, got = None, 0
    while got < CAP:
        p = {"limit": 1000}
        if before:
            p["before"] = before
        page = rpc("getSignaturesForAddress", [prog, p]) or []
        if not page:
            break
        for s in page:
            if not s.get("err") and s.get("blockTime") and now - s["blockTime"] <= HOURS * 3600:
                sigs.append((s["signature"], name))
        got += len(page)
        before = page[-1]["signature"]
        if page[-1].get("blockTime") and now - page[-1]["blockTime"] > HOURS * 3600:
            break
    print(f"{name}: подписей {got}, успешных за {HOURS:g} ч {sum(1 for _, n in sigs if n == name)}", flush=True)
sigs = sigs[: 2 * CAP]
print(f"транзакций к загрузке: {len(sigs)}", flush=True)
rows = []
done = [0]


def load(x):
    sig, venue = x
    t = rpc("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1, "commitment": "confirmed"}])
    done[0] += 1
    if done[0] % 1000 == 0:
        print(f"  загружено {done[0]}/{len(sigs)}", flush=True)
    if not t or not t.get("meta") or t["meta"].get("err"):
        return None
    keys = t["transaction"]["message"]["accountKeys"]
    payer = keys[0]["pubkey"] if isinstance(keys[0], dict) else keys[0]
    m = t["meta"]
    sol = (m["postBalances"][0] - m["preBalances"][0]) / 1e9
    tok = collections.defaultdict(float)
    for side, sign in (("preTokenBalances", -1), ("postTokenBalances", 1)):
        for b in m.get(side) or []:
            if b.get("owner") == payer:
                tok[b["mint"]] += sign * float(b["uiTokenAmount"].get("uiAmount") or 0)
    sol += tok.pop(WSOL, 0.0)
    moved = [(mint, d) for mint, d in tok.items() if abs(d) > 0]
    if len(moved) != 1:
        return None
    mint, d = moved[0]
    if d > 0 and sol < -0.001:
        kind = "buy"
    elif d < 0 and sol > 0.0005:
        kind = "sell"
    else:
        return None
    return dict(time=t.get("blockTime"), slot=t["slot"], venue=venue, wallet=payer, mint=mint, kind=kind, sol=round(sol, 6), tokens=d, sig=sig)


with ThreadPoolExecutor(8) as ex:
    for r in ex.map(load, sigs):
        if r:
            rows.append(r)
rows.sort(key=lambda r: (r["slot"], r["sig"]))
with open(os.path.join(HERE, "sol_pnl_trades.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["time"], delimiter=";")
    w.writeheader()
    w.writerows(rows)
first_seen = {}
for r in rows:
    first_seen.setdefault(r["mint"], r["time"])
pos = collections.defaultdict(lambda: dict(bt=0.0, st=0.0, bs=0.0, ss=0.0, t0=None, t1=None, n=0))
for r in rows:
    p = pos[(r["wallet"], r["mint"])]
    p["n"] += 1
    if r["kind"] == "buy":
        p["bt"] += r["tokens"]; p["bs"] += -r["sol"]; p["t0"] = p["t0"] or r["time"]
    else:
        p["st"] += -r["tokens"]; p["ss"] += r["sol"]; p["t1"] = r["time"]
W = collections.defaultdict(lambda: dict(pnl=0.0, closed=0, win=0, spent=0.0, holds=[], entries=[], open=0, trades=0))
for (wal, mint), p in pos.items():
    a = W[wal]
    a["trades"] += p["n"]
    if p["bt"] > 0 and p["st"] >= 0.9 * p["bt"] and p["t0"] and p["t1"]:
        pnl = p["ss"] - p["bs"]
        a["pnl"] += pnl; a["closed"] += 1; a["win"] += pnl > 0; a["spent"] += p["bs"]
        a["holds"].append(p["t1"] - p["t0"])
        a["entries"].append(p["t0"] - first_seen[mint])
    elif p["bt"] > 0:
        a["open"] += 1
L = [f"Solana, pump.fun + PumpSwap, {HOURS:g} ч до {time.strftime('%d.%m %H:%M')}: сделок {len(rows)}, кошельков {len(W)}, монет {len(first_seen)}",
     "топ-20 кошельков по реализованной прибыли (только монеты, которые и купил, и продал):",
     "кошелёк | прибыль SOL | закрыто монет (в плюсе) | потратил SOL | держит, с (медиана) | вход, с после первой сделки монеты (медиана) | открытых | сделок"]
for wal, a in sorted(W.items(), key=lambda kv: -kv[1]["pnl"])[:20]:
    L.append(f"{wal} | {a['pnl']:+.3f} | {a['closed']} ({a['win']}) | {a['spent']:.2f} | {st.median(a['holds']):.0f} | "
             f"{st.median(a['entries']):.0f} | {a['open']} | {a['trades']}")
closed = [a for a in W.values() if a["closed"]]
L.append(f"\nвсего кошельков с закрытыми позициями {len(closed)}: в плюсе {sum(a['pnl'] > 0 for a in closed)}, сумма прибыли {sum(a['pnl'] for a in closed):+.2f} SOL")
print("\n".join(L))
open(os.path.join(HERE, "sol_pnl.txt"), "w", encoding="utf-8").write("\n".join(L))
