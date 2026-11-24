"""18.11 (user: «может, они реагируют на чужие покупки и сразу сбрасывают»). For every pool the Solana bot traded (sol_trades.csv, LIVE):
the pool's creation, our buy and sale (our wallet's own transactions), and the creator's Withdraw (the creator's own transactions,
creator = pool data offset 11). Then the same timing for the pools the watcher recorded without us (sol_watch_paths.jsonl).
Helius (HELIUS_KEY of D:\\bot\\.env, never printed). Out: analysis/sol_react.txt.  Usage: python sol_react.py"""
import base64, csv, json, os, sys, time, urllib.request

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
K = [l.split("=", 1)[1].strip().strip('"\'') for l in open(r"D:\bot\.env", encoding="utf-8-sig") if l.strip().startswith("HELIUS_KEY=")][0]
H = "https://mainnet.helius-rpc.com/?api-key=" + K
ME = "CqMsFPRFuSqCuxuK79kpw7cFm5ud8MoWBnqT27VfDx6J"
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def rpc(m, p):
    for k in range(5):
        try:
            r = json.loads(urllib.request.urlopen(urllib.request.Request(H, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": m, "params": p}).encode(),
                                                                         headers={"Content-Type": "application/json"}), timeout=30).read())
            if r.get("result") is not None:
                return r["result"]
        except Exception:
            pass
        time.sleep(0.4 * (k + 1))


def b58(b):
    n = int.from_bytes(b, "big")
    s = ""
    while n:
        n, r = divmod(n, 58)
        s = B58[r] + s
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + s


def creator_of(pool):
    a = rpc("getAccountInfo", [pool, {"encoding": "base64"}])
    if not a or not a.get("value"):
        return None
    return b58(base64.b64decode(a["value"]["data"][0])[11:43])


def first_time(addr):
    """oldest blockTime of an address (pools have < few thousand txs in their first hour; we page back)"""
    before, last = None, None
    for _ in range(20):
        p = {"limit": 1000}
        if before:
            p["before"] = before
        page = rpc("getSignaturesForAddress", [addr, p]) or []
        if not page:
            break
        last = page[-1]
        before = last["signature"]
        if len(page) < 1000:
            break
    return last.get("blockTime") if last else None


def withdraw_time(creator, pool, t_from, t_to):
    """the creator's Withdraw on this pool between t_from and t_to (blockTime)"""
    sigs = rpc("getSignaturesForAddress", [creator, {"limit": 1000}]) or []
    for s in reversed(sigs):
        bt = s.get("blockTime") or 0
        if bt < t_from or bt > t_to or s.get("err"):
            continue
        t = rpc("getTransaction", [s["signature"], {"encoding": "json", "maxSupportedTransactionVersion": 0}])
        if not t:
            continue
        la = t["meta"].get("loadedAddresses") or {}
        keys = t["transaction"]["message"]["accountKeys"] + la.get("writable", []) + la.get("readonly", [])
        if pool in keys and any("Instruction: Withdraw" in l for l in t["meta"]["logMessages"] or []):
            return bt
    return None


def my_txs(pool, t_from, t_to):
    out = []
    sigs = rpc("getSignaturesForAddress", [ME, {"limit": 200}]) or []
    for s in reversed(sigs):
        bt = s.get("blockTime") or 0
        if bt < t_from or bt > t_to or s.get("err"):
            continue
        t = rpc("getTransaction", [s["signature"], {"encoding": "json", "maxSupportedTransactionVersion": 0}])
        la = (t or {}).get("meta", {}).get("loadedAddresses") or {}
        if t and pool in t["transaction"]["message"]["accountKeys"] + la.get("writable", []) + la.get("readonly", []):
            out.append(bt)
    return out


L = ["ПУЛЫ, ГДЕ МЫ ПОКУПАЛИ (время от создания пула, с):"]
rows = [r for r in csv.DictReader(open(os.path.join(HERE, "sol_trades.csv"), encoding="utf-8-sig"), delimiter=";") if r["mode"] == "LIVE" and r["pnl_sol"]]
ours_gap = []
for r in rows:
    pool = r["pool"]
    cr = creator_of(pool)
    t0 = first_time(pool)
    if not cr or not t0:
        L.append(f"  {r['mint'][:8]}: нет данных")
        continue
    mine = my_txs(pool, t0, t0 + 900)
    wd = withdraw_time(cr, pool, t0, t0 + 3600)
    buy = mine[0] - t0 if mine else None
    sell = mine[-1] - t0 if len(mine) > 1 else None
    w = wd - t0 if wd else None
    gap = (w - buy) if (w is not None and buy is not None) else None
    if gap is not None:
        ours_gap.append((gap, w, buy, sell, r))
    L.append(f"  {r['time'][6:]} {r['mint'][:8]} итог {float(r['pnl_pct']):+7.1f}% | создатель {cr[:8]} | купили на {buy} с, продали на {sell} с | "
             f"вывод ликвидности на {w} с" + (f" (через {gap} с после нашей покупки)" if gap is not None else ""))
    print(L[-1], flush=True)
L.append("\nПУЛЫ ФЕРМЫ БЕЗ НАС (наблюдатель): вывод ликвидности, с от того, как мы узнали о пуле:")
P = [json.loads(l) for l in open(os.path.join(HERE, "sol_watch_paths.jsonl"), encoding="utf-8")]
ws = sorted(round(p["withdraw"][0], 1) for p in P if p.get("withdraw") and p["withdraw"][2] < 1)
L.append(f"  пулов {len(P)}, полный вывод у {len(ws)}: {ws}")
L.append("\nВЫВОД:")
early = [g for g in ours_gap if g[1] < 26]
L.append(f"  с нашей покупкой вывод раньше 26 с (самый ранний без нас — {ws[0] if ws else '-'} с): {len(early)} из {len(ours_gap)} пулов, где вывод найден")
print("\n".join(L[-5:]))
open(os.path.join(HERE, "sol_react.txt"), "w", encoding="utf-8").write("\n".join(L))
