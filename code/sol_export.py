"""17.11 (user, for an outside analyst): full export of one Solana coin. Read-only, Helius (HELIUS_KEY of .env, never printed).
In: sol_insiders_trades.csv (signatures and wallets of the coin's trades).
Out (analysis/):
  sol_export_trades.csv     every trade: signature, blockTime, slot, transactionIndex, wallet, side, tokenAmount, solAmount,
                            solAmount_ex_fee, fee, program, programIds, pool, inner_count, pre/post SOL of the wallet, group
  sol_export_raw.jsonl.gz   the raw transactions (jsonParsed: innerInstructions, pre/postBalances, logs) of the trades and transfers
  sol_export_transfers.csv  every SOL transfer and every non-swap transfer of the coin in the whole history of all its wallets
                            (+ creator, sponsor), before the launch and after
  sol_export_balances.csv   current SOL and coin balance of each wallet
  sol_export_mint.json      mint account: owner program, mintAuthority, freezeAuthority, supply, decimals, extensions
Usage: python sol_export.py <mint> <creator> <sponsor> [history txs per wallet=3000]"""
import collections
import csv
import gzip
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
HCAP = int(sys.argv[4]) if len(sys.argv) > 4 else 3000
WSOL = "So11111111111111111111111111111111111111112"
PROGS = {"6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P": "pump.fun", "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA": "PumpSwap"}
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


RAW = {}  # signature -> small summary (the full tx goes straight to the gz file, not kept in memory)
GZ = gzip.open(os.path.join(HERE, "sol_export_raw.jsonl.gz"), "wt", encoding="utf-8")
GZL = threading.Lock()


def summary(t):
    if not t or not t.get("meta"):
        return None
    sysx = []
    for i in all_ins(t):
        p = i.get("parsed") if isinstance(i, dict) else None
        if isinstance(p, dict) and i.get("program") == "system" and p.get("type") == "transfer":
            sysx.append((p["info"].get("source"), p["info"].get("destination"), p["info"].get("lamports", 0) / 1e9))
    d = collections.defaultdict(float)
    for side, sign in (("preTokenBalances", -1), ("postTokenBalances", 1)):
        for b in t["meta"].get(side) or []:
            if b["mint"] == MINT:
                d[b.get("owner")] += sign * float(b["uiTokenAmount"].get("uiAmount") or 0)
    return dict(blockTime=t.get("blockTime"), slot=t["slot"], err=bool(t["meta"].get("err")), sys=sysx, coin=dict(d))


def tx(s):
    if s in RAW:
        return None
    t = rpc("getTransaction", [s, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1, "commitment": "confirmed"}])
    RAW[s] = summary(t)
    if len(RAW) % 2000 == 0:
        P(f"   скачано транзакций {len(RAW)}, {time.time() - T0:.0f} с")
    if t:
        line = json.dumps({"signature": s, "tx": t}, ensure_ascii=False)
        with GZL:
            GZ.write(line + "\n")
    return t


def keys(t):
    return [k["pubkey"] if isinstance(k, dict) else k for k in t["transaction"]["message"]["accountKeys"]]


def all_ins(t):
    ins = list(t["transaction"]["message"]["instructions"])
    for g in t["meta"].get("innerInstructions") or []:
        ins += g.get("instructions") or []
    return ins


def trade(sig):
    t = tx(sig)
    if not t or not t.get("meta"):
        return None
    m, ks = t["meta"], keys(t)
    payer = ks[0]
    fee = m.get("fee", 0) / 1e9
    native = (m["postBalances"][0] - m["preBalances"][0]) / 1e9
    tok, wsol, other = 0.0, 0.0, {}
    for side, sign in (("preTokenBalances", -1), ("postTokenBalances", 1)):
        for b in m.get(side) or []:
            amt = sign * float(b["uiTokenAmount"].get("uiAmount") or 0)
            if b.get("owner") == payer:
                if b["mint"] == MINT:
                    tok += amt
                elif b["mint"] == WSOL:
                    wsol += amt
            elif b["mint"] == MINT:
                other[b.get("owner")] = other.get(b.get("owner"), 0.0) + amt
    pool = max(other.items(), key=lambda kv: abs(kv[1]))[0] if other else ""
    pids = [i.get("programId") for i in t["transaction"]["message"]["instructions"]]
    allp = {i.get("programId") for i in all_ins(t)}
    sol = native + wsol
    return dict(signature=sig, blockTime=t.get("blockTime"), slot=t["slot"], transactionIndex="", wallet=payer,
                side="buy" if tok > 0 else "sell" if tok < 0 else "other", tokenAmount=tok, solAmount=round(sol, 9),
                solAmount_ex_fee=round(sol + fee, 9), fee=fee, program="+".join(sorted(PROGS[p] for p in allp if p in PROGS)) or "other",
                programIds=" ".join(pids), pool=pool, inner_count=sum(len(g.get("instructions") or []) for g in m.get("innerInstructions") or []),
                pre_sol=m["preBalances"][0] / 1e9, post_sol=m["postBalances"][0] / 1e9, err="" if not m.get("err") else "error")


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


T0 = time.time()
STAGE2 = os.environ.get("SOL_STAGE2") == "1"  # trades csv and mint already done: only raw, history in a window, balances
BEFORE_H = float(os.environ.get("SOL_BEFORE_H", "6"))  # history window: from N hours before the first trade to now
src = list(csv.DictReader(open(os.path.join(HERE, "sol_insiders_trades.csv"), encoding="utf-8-sig"), delimiter=";"))
group = {r["wallet"]: r["own"] for r in src}
trade_sigs = list(dict.fromkeys(r["sig"] for r in src))

# A) trades
COUNT_ONLY = os.environ.get("SOL_COUNT_ONLY") == "1"  # only count the history window, fetch nothing else
P(f"A) сделки: {len(trade_sigs)}{' (пропуск: только подсчёт окна)' if COUNT_ONLY else ''}")
with ThreadPoolExecutor(8) as ex:
    rows = [r for r in ex.map(trade, [] if COUNT_ONLY else trade_sigs) if r]
if COUNT_ONLY:
    rows = [dict(blockTime=min(int(r["time"]) for r in src if r["time"]), slot=0, signature="")]
    STAGE2 = True
rows.sort(key=lambda r: (r["slot"], r["signature"]))
P(f"   готово, запросов {CALLS[0]}, {time.time() - T0:.0f} с")

T_FIRST = min(r["blockTime"] for r in rows if r.get("blockTime"))
if STAGE2:
    P("   этапы B и C уже сделаны раньше (sol_export_trades.csv, sol_export_mint.json)")
# B) position inside the block
slots = [] if STAGE2 else sorted({r["slot"] for r in rows})
P(f"B) порядок в блоке: блоков {len(slots)}")
want = collections.defaultdict(set)
for r in rows:
    want[r["slot"]].add(r["signature"])
idx = {}


def block(s):
    b = rpc("getBlock", [s, {"encoding": "json", "transactionDetails": "signatures", "rewards": False, "maxSupportedTransactionVersion": 1}])
    if b:
        for i, sg in enumerate(b.get("signatures") or []):
            if sg in want[s]:
                idx[sg] = i


with ThreadPoolExecutor(8) as ex:
    list(ex.map(block, slots))
if not STAGE2:
    for r in rows:
        r["transactionIndex"] = idx.get(r["signature"], "")
        r["group"] = group.get(r["wallet"], "")
    with open(os.path.join(HERE, "sol_export_trades.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), delimiter=";")
        w.writeheader()
        w.writerows(rows)
    P(f"   индекс найден у {len(idx)} из {len(rows)}, запросов {CALLS[0]}, {time.time() - T0:.0f} с")
    # C) mint
    mi = rpc("getAccountInfo", [MINT, {"encoding": "jsonParsed"}])
    json.dump(mi, open(os.path.join(HERE, "sol_export_mint.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    P("C) mint записан")

# D) whole history of every wallet: SOL transfers + non-swap moves of the coin
wallets = sorted(set(group) | {CREATOR, SPONSOR})
role = lambda a: "создатель" if a == CREATOR else "спонсор" if a == SPONSOR else (group.get(a) or ("торговал" if a in group else ""))
P(f"D) история кошельков: {len(wallets)}")
tsig = set(trade_sigs)
hist, total = {}, {}
T_FROM = T_FIRST - BEFORE_H * 3600


AFTER_H = float(os.environ.get("SOL_AFTER_H", "999"))  # window end: N hours after the first trade
HUB_CAP = int(os.environ.get("SOL_HUB_CAP", "6000"))  # creator and sponsor: whole history up to this many txs


def window(a):
    if a in (CREATOR, SPONSOR):
        h = sigs_of(a, HUB_CAP)
        return h, len(h)
    out, before, n = [], None, 0
    while True:
        p = {"limit": 1000}
        if before:
            p["before"] = before
        page = rpc("getSignaturesForAddress", [a, p]) or []
        if not page:
            break
        n += len(page)
        out += [s for s in page if T_FROM <= (s.get("blockTime") or 0) <= T_FIRST + AFTER_H * 3600]
        before = page[-1]["signature"]
        if len(page) < 1000 or (page[-1].get("blockTime") or 0) < T_FROM:
            break
    return out, n


with ThreadPoolExecutor(8) as ex:
    for a, (h, n) in zip(wallets, ex.map(window, wallets)):
        hist[a] = [s["signature"] for s in h if not s.get("err")]
        total[a] = n
extra = sorted({s for h in hist.values() for s in h} - tsig)
P(f"   окно с {time.strftime('%d.%m %H:%M', time.localtime(T_FROM))} (за {BEFORE_H:g} ч до первой сделки) по сейчас: "
  f"подписей {sum(len(h) for h in hist.values())}, новых (не сделки) {len(extra)}")
if COUNT_ONLY:
    for a in sorted(wallets, key=lambda a: -len(hist[a]))[:5]:
        P(f"   {a[:8]} {role(a)}: в окне {len(hist[a])}, просмотрено {total[a]}")
    sys.exit(0)
if len(extra) > int(os.environ.get("SOL_MAX_EXTRA", "40000")):
    sys.exit("слишком много транзакций в окне — уменьшить SOL_BEFORE_H")
with ThreadPoolExecutor(8) as ex:
    list(ex.map(tx, extra))
W = set(wallets)
tr = []
for s in sorted(set(extra) | tsig):
    t = RAW.get(s)
    if not t or t["err"]:
        continue
    for a, b, v in t["sys"]:
        if a in W or b in W:
            tr.append(dict(signature=s, blockTime=t["blockTime"], slot=t["slot"], asset="SOL", source=a, source_role=role(a),
                           destination=b, destination_role=role(b), amount=v, in_trade=s in tsig))
    if s in tsig:
        continue  # coin moves inside swaps are already in the trades file
    d = t["coin"]
    outs = [(o, v) for o, v in d.items() if v < 0]
    ins_ = [(o, v) for o, v in d.items() if v > 0]
    for o, v in outs:
        for o2, v2 in ins_ or [("", -v)]:
            tr.append(dict(signature=s, blockTime=t.get("blockTime"), slot=t["slot"], asset="COIN", source=o, source_role=role(o),
                           destination=o2, destination_role=role(o2), amount=min(-v, v2), in_trade=False))
    if not outs:
        for o2, v2 in ins_:
            tr.append(dict(signature=s, blockTime=t.get("blockTime"), slot=t["slot"], asset="COIN", source="", source_role="",
                           destination=o2, destination_role=role(o2), amount=v2, in_trade=False))
tr.sort(key=lambda x: (x["slot"], x["signature"]))
with open(os.path.join(HERE, "sol_export_transfers.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=["signature", "blockTime", "slot", "asset", "source", "source_role", "destination", "destination_role",
                                      "amount", "in_trade"], delimiter=";")
    w.writeheader()
    w.writerows(tr)
P(f"   переводов {len(tr)}, запросов {CALLS[0]}, {time.time() - T0:.0f} с")

# E) current balances
bal = []


def balance(a):
    s = rpc("getBalance", [a]) or {}
    ta = rpc("getTokenAccountsByOwner", [a, {"mint": MINT}, {"encoding": "jsonParsed"}]) or {}
    coin = sum(float(x["account"]["data"]["parsed"]["info"]["tokenAmount"].get("uiAmount") or 0) for x in ta.get("value") or [])
    return dict(wallet=a, role=role(a), sol=(s.get("value") or 0) / 1e9, coin=coin, txs_in_window=len(hist.get(a, [])), txs_seen=total.get(a, 0))


with ThreadPoolExecutor(8) as ex:
    bal = list(ex.map(balance, wallets))
with open(os.path.join(HERE, "sol_export_balances.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(bal[0].keys()), delimiter=";")
    w.writeheader()
    w.writerows(bal)

GZ.close()
P(f"ГОТОВО: запросов {CALLS[0]}, {time.time() - T0:.0f} с")
