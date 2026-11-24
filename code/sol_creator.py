"""17.11 (user): who created the Solana coins the top wallets made money on. Read-only, Helius (HELIUS_KEY of .env, never printed).
For each mint: its oldest tx (paging getSignaturesForAddress to the end) -> the fee payer = the creator. For the creator: its recent txs,
how many of them create coins (log 'Instruction: Create'), and who sent it SOL first in what we see.
Usage: python sol_creator.py <mint prefix or full mint> ...   (prefixes are looked up in sol_pnl_trades.csv)"""
import csv
import json
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
RPC = None
for _l in open(r"D:\bot\.env", encoding="utf-8-sig"):
    if _l.strip().startswith("HELIUS_KEY="):
        RPC = "https://mainnet.helius-rpc.com/?api-key=" + _l.split("=", 1)[1].strip().strip('"\'')
LAST = [0.0]


def rpc(m, p):
    for k in range(6):
        gap = 0.13 - (time.time() - LAST[0])
        if gap > 0:
            time.sleep(gap)
        LAST[0] = time.time()
        try:
            r = json.loads(urllib.request.urlopen(urllib.request.Request(RPC, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": m, "params": p}).encode(),
                                                                         headers={"Content-Type": "application/json"}), timeout=40).read())
            if "error" in r:
                raise RuntimeError(str(r["error"])[:80])
            return r["result"]
        except Exception:
            time.sleep(1 + k)
    return None


def tx(s):
    return rpc("getTransaction", [s, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1}])


def payer(t):
    k = t["transaction"]["message"]["accountKeys"][0]
    return k["pubkey"] if isinstance(k, dict) else k


mints = set()
rows = list(csv.DictReader(open(r"D:\bot\analysis\sol_pnl_trades.csv", encoding="utf-8-sig"), delimiter=";"))
for a in sys.argv[1:]:
    mints |= {r["mint"] for r in rows if r["mint"].startswith(a)} or {a}
for mint in sorted(mints):
    before, oldest, n = None, None, 0
    while True:
        p = {"limit": 1000}
        if before:
            p["before"] = before
        page = rpc("getSignaturesForAddress", [mint, p]) or []
        if not page:
            break
        n += len(page)
        oldest, before = page[-1], page[-1]["signature"]
        if len(page) < 1000 or n > 400000:
            break
    t = tx(oldest["signature"]) if oldest else None
    if not t:
        print(f"{mint}: создание не найдено")
        continue
    cr = payer(t)
    logs = " ".join(t["meta"].get("logMessages") or [])
    print(f"\nМОНЕТА {mint}: транзакций всего ~{n}, создана {time.strftime('%d.%m %H:%M:%S', time.localtime(t['blockTime']))}"
          f"{' (pump.fun Create)' if 'Instruction: Create' in logs else ''}")
    print(f"  создатель {cr}")
    hist = rpc("getSignaturesForAddress", [cr, {"limit": 100}]) or []
    creates, funders = 0, {}
    for s in hist[:60]:
        h = tx(s["signature"])
        if not h:
            continue
        lg = " ".join(h["meta"].get("logMessages") or [])
        if "Instruction: Create" in lg and payer(h) == cr:
            creates += 1
        for ins in h["transaction"]["message"]["instructions"]:
            pp = ins.get("parsed") if isinstance(ins, dict) else None
            if isinstance(pp, dict) and pp.get("type") == "transfer" and pp["info"].get("destination") == cr and pp["info"].get("source") != cr:
                funders[pp["info"]["source"]] = funders.get(pp["info"]["source"], 0) + pp["info"].get("lamports", 0) / 1e9
    span = (hist[0]["blockTime"] - hist[-1]["blockTime"]) / 3600 if len(hist) > 1 and hist[-1].get("blockTime") else 0
    print(f"  у создателя последних транзакций {len(hist)} (за {span:.1f} ч), из 60 проверенных — создание монет: {creates}")
    print(f"  кто присылал ему SOL: " + (", ".join(f"{f} ({v:.2f} SOL)" for f, v in sorted(funders.items(), key=lambda kv: -kv[1])[:4]) or "не видно в последних 60"))
