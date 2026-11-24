"""17.11 (user): are the creator of a Solana coin and its traders the same owner? Read-only, Helius (HELIUS_KEY of .env, never printed).
1) all trades of the coin (every tx of the mint, buy/sell of the fee payer, SOL incl. wrapped SOL) -> PnL per wallet, entry order;
2) "own" wallets = those the creator or its sponsor sent SOL to (their recent history);
3) cross: how many own wallets traded the coin, when they entered (rank), what they made.
Out: sol_insiders.txt, sol_insiders_trades.csv.
Usage: python sol_insiders.py <mint> <creator> <sponsor> [max mint txs=8000] [max history txs per wallet=400]"""
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
MINT, CREATOR, SPONSOR = sys.argv[1:4]
CAP = int(sys.argv[4]) if len(sys.argv) > 4 else 8000
HCAP = int(sys.argv[5]) if len(sys.argv) > 5 else 400
WSOL = "So11111111111111111111111111111111111111112"
RPC = None
for _l in open(r"D:\bot\.env", encoding="utf-8-sig"):
    if _l.strip().startswith("HELIUS_KEY="):
        RPC = "https://mainnet.helius-rpc.com/?api-key=" + _l.split("=", 1)[1].strip().strip('"\'')
if not RPC:
    sys.exit("нет HELIUS_KEY в .env")
LAST = [0.0]
LK = threading.Lock()
CALLS = [0]


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
            r = json.loads(urllib.request.urlopen(req, timeout=40).read())
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
    return out


def tx(s):
    return rpc("getTransaction", [s, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1, "commitment": "confirmed"}])


def key0(t):
    k = t["transaction"]["message"]["accountKeys"][0]
    return k["pubkey"] if isinstance(k, dict) else k


def trade_of(t, sig):
    if not t or not t.get("meta") or t["meta"].get("err"):
        return None
    payer, m = key0(t), t["meta"]
    sol = (m["postBalances"][0] - m["preBalances"][0]) / 1e9
    tok = collections.defaultdict(float)
    for side, sign in (("preTokenBalances", -1), ("postTokenBalances", 1)):
        for b in m.get(side) or []:
            if b.get("owner") == payer:
                tok[b["mint"]] += sign * float(b["uiTokenAmount"].get("uiAmount") or 0)
    sol += tok.pop(WSOL, 0.0)
    d = tok.get(MINT, 0.0)
    if d > 0 and sol < -0.0005:
        kind = "buy"
    elif d < 0 and sol > 0.0002:
        kind = "sell"
    else:
        return None
    return dict(time=t.get("blockTime"), slot=t["slot"], wallet=payer, kind=kind, sol=round(sol, 6), tokens=d, sig=sig)


def sol_out(t, src):
    """SOL transfers src -> others inside a tx (top-level and inner)."""
    res = []
    if not t or not t.get("meta") or t["meta"].get("err"):
        return res
    ins = list(t["transaction"]["message"]["instructions"])
    for g in t["meta"].get("innerInstructions") or []:
        ins += g.get("instructions") or []
    for i in ins:
        p = i.get("parsed") if isinstance(i, dict) else None
        if isinstance(p, dict) and p.get("type") == "transfer" and i.get("program") == "system":
            inf = p["info"]
            if inf.get("source") == src and inf.get("destination") != src:
                res.append((inf["destination"], inf.get("lamports", 0) / 1e9))
    return res


T0 = time.time()
# 1) the coin's trades
ms = [s for s in sigs_of(MINT, CAP) if not s.get("err")]
ms.reverse()  # oldest first
print(f"монета: транзакций {len(ms)}{' (обрезано)' if len(ms) >= CAP else ''}", flush=True)
with ThreadPoolExecutor(8) as ex:
    rows = [r for r in ex.map(lambda s: trade_of(tx(s["signature"]), s["signature"]), ms) if r]
rows.sort(key=lambda r: (r["slot"], r["sig"]))
print(f"  сделок {len(rows)}, запросов {CALLS[0]}", flush=True)

# 2) own wallets: who got SOL from the creator / sponsor
own = {}
for src, name in ((CREATOR, "создатель"), (SPONSOR, "спонсор")):
    hs = [s for s in sigs_of(src, HCAP) if not s.get("err")][:HCAP]
    with ThreadPoolExecutor(8) as ex:
        ts = list(ex.map(lambda s: tx(s["signature"]), hs))
    n = 0
    for t in ts:
        for dst, v in sol_out(t, src):
            if v >= 0.01:
                o = own.setdefault(dst, {"from": set(), "sol": 0.0, "first": t.get("blockTime")})
                o["from"].add(name); o["sol"] += v
                o["first"] = min(o["first"] or 9e18, t.get("blockTime") or 9e18)
                n += 1
    print(f"{name}: транзакций {len(hs)}, переводов SOL от 0.01: {n}", flush=True)
own.pop(CREATOR, None)
own.pop(SPONSOR, None)

# 3) cross
pos = collections.defaultdict(lambda: dict(b=0.0, s=0.0, bt=0.0, st=0.0, t0=None, t1=None, n=0))
order = []
for r in rows:
    p = pos[r["wallet"]]
    p["n"] += 1
    if r["kind"] == "buy":
        p["b"] += -r["sol"]; p["bt"] += r["tokens"]
        if p["t0"] is None:
            p["t0"] = r["time"]; order.append(r["wallet"])
    else:
        p["s"] += r["sol"]; p["st"] += -r["tokens"]; p["t1"] = r["time"]
rank = {w: i + 1 for i, w in enumerate(order)}
t_first = rows[0]["time"] if rows else 0


def pnl(p):
    return p["s"] - p["b"]


tag = lambda w: ("СОЗДАТЕЛЬ" if w == CREATOR else "СПОНСОР" if w == SPONSOR else
                 "свой(" + "+".join(sorted(own[w]["from"])) + ")" if w in own else "")
L = [f"Монета {MINT}", f"создатель {CREATOR}, спонсор {SPONSOR}",
     f"сделок {len(rows)}, кошельков {len(pos)}, первая сделка {time.strftime('%d.%m %H:%M:%S', time.localtime(t_first)) if t_first else '-'}",
     f"«своих» кошельков (получали SOL от создателя/спонсора): {len(own)}"]
ins = [w for w in pos if w in own or w in (CREATOR, SPONSOR)]
L.append(f"из них торговали монетой: {len(ins)}")
L.append("\nСВОИ, торговавшие монетой: кошелёк | кто платил (SOL) | № входа | вход, с от 1-й сделки | купил SOL | продал SOL | итог SOL | сделок")
for w in sorted(ins, key=lambda w: rank.get(w, 9e9)):
    p = pos[w]
    src = f"{'+'.join(sorted(own[w]['from']))} {own[w]['sol']:.2f}" if w in own else tag(w)
    L.append(f"{w} | {src} | {rank.get(w, '-')} | {(p['t0'] - t_first) if p['t0'] else '-'} | {p['b']:.3f} | {p['s']:.3f} | {pnl(p):+.3f} | {p['n']}")
tot_in = sum(pnl(pos[w]) for w in ins)
tot_all = sum(pnl(p) for p in pos.values())
L.append(f"\nитог своих {tot_in:+.3f} SOL; итог всех кошельков {tot_all:+.3f} SOL (минус = деньги ушли в пул/создателю/комиссии)")
first20 = order[:20]
L.append(f"среди первых 20 покупателей своих: {sum(w in own or w in (CREATOR, SPONSOR) for w in first20)}")
L.append("\nТОП-15 по прибыли на монете:")
for w, p in sorted(pos.items(), key=lambda kv: -pnl(kv[1]))[:15]:
    L.append(f"{w} | {pnl(p):+.3f} | вход №{rank.get(w, '-')} | {tag(w)}")
L.append(f"\nзапросов к Helius: {CALLS[0]}, время {time.time() - T0:.0f} с")
print("\n".join(L))
open(os.path.join(HERE, "sol_insiders.txt"), "w", encoding="utf-8").write("\n".join(L))
with open(os.path.join(HERE, "sol_insiders_trades.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=["time", "slot", "wallet", "kind", "sol", "tokens", "sig", "own"], delimiter=";")
    w.writeheader()
    for r in rows:
        w.writerow({**r, "own": tag(r["wallet"])})
