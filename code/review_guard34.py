"""23.11: nets 3/4 signal trades 18-19.11: was our holder marked by the guard, and how many blocks after our buy (Approval guard->W)."""
import csv, requests, sys
sys.stdout.reconfigure(encoding="utf-8")
U = "https://rpc-bsc.48.club"; S = requests.Session()
def rpc(m, p):
    for _ in range(5):
        try:
            r = S.post(U, json={"jsonrpc": "2.0", "id": 1, "method": m, "params": p}, timeout=20).json()
            if "result" in r: return r["result"]
        except Exception: pass
    return None
W = {l.strip().split(";")[-1].lower() for l in open("../hop_wallets.txt", encoding="utf-8") if "0x" in l}
W = {x[x.index("0x"):][:42] for x in W}
TR = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
AP = "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b0ac8c3925b925"
GUARD = {3: "0x623e7c23c7c228095f94d9bd567eae55cb87a966", 4: "0xa70b3f34e3ca7d4665b794ce6bb29168b1d2e40d"}
pad = lambda a: "0x" + "0" * 24 + a[2:]
for n in (3, 4):
    rows = [r for r in csv.DictReader(open(f"signal_test{n}.csv", encoding="utf-8-sig"), delimiter=";") if r["time"] >= "2026-11-18" and r["land_block"] and r["sell_block"]]
    res = []
    for r in rows[-25:]:
        lb, sb, tok = int(r["land_block"]), int(r["sell_block"]), r["token"]
        lg = rpc("eth_getLogs", [{"address": tok, "fromBlock": hex(lb), "toBlock": hex(lb), "topics": [TR]}]) or []
        w = next(("0x" + l["topics"][2][-40:] for l in lg if "0x" + l["topics"][2][-40:] in W), None)
        if not w:
            res.append((r["symbol"], sb - lb, "кошелёк не найден")); continue
        ap = rpc("eth_getLogs", [{"address": tok, "fromBlock": hex(lb), "toBlock": hex(lb + 400), "topics": [AP, pad(GUARD[n]), pad(w)]}]) or []
        mark = int(ap[0]["blockNumber"], 16) - lb if ap else None
        if mark is None:  # no Approval event: the allowance itself
            al = rpc("eth_call", [{"to": tok, "data": "0xdd62ed3e" + pad(GUARD[n])[2:] + pad(w)[2:]}, "latest"])
            mark = "метка без события" if al and int(al, 16) > 0 else "нет метки"
        res.append((r["symbol"], sb - lb, mark))
    print(f"\nСЕТЬ {n} (последние {len(res)} сделок до 19.11 16:3x): монета, держали блоков, метка через блоков после покупки")
    for x in res: print("  ", x)


# part 2: the guard's approve tx itself (no Approval event): scan full blocks after our buy for a tx from the guard to the token naming W
def mark_block(n, tok, w, lb, upto=80):
    for b in range(lb, lb + upto):
        blk = rpc("eth_getBlockByNumber", [hex(b), True])
        if not blk: continue
        for t in blk["transactions"]:
            if (t.get("from") or "").lower() == GUARD[n] and (t.get("to") or "").lower() == tok.lower() and w[2:] in (t.get("input") or "").lower():
                return b
    return None

print("\nКОГДА ПОМЕТИЛИ (блоков после нашей покупки) против того, сколько держали:")
import collections
for n in (3, 4):
    rows = [r for r in csv.DictReader(open(f"signal_test{n}.csv", encoding="utf-8-sig"), delimiter=";") if r["time"] >= "2026-11-18" and r["land_block"] and r["sell_block"]][-15:]
    d = []
    for r in rows:
        lb, sb, tok = int(r["land_block"]), int(r["sell_block"]), r["token"]
        lg = rpc("eth_getLogs", [{"address": tok, "fromBlock": hex(lb), "toBlock": hex(lb), "topics": [TR]}]) or []
        w = next(("0x" + l["topics"][2][-40:] for l in lg if "0x" + l["topics"][2][-40:] in W), None)
        if not w: continue
        m = mark_block(n, tok, w, lb)
        d.append((r["time"][5:16], r["symbol"], sb - lb, None if m is None else m - lb))
        print(f"  сеть {n} {d[-1]}", flush=True)
