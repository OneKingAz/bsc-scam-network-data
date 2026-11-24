"""17.11 (user: look beyond BSC): is there a "payer funds a bot right before its buy" scheme on Solana (pump.fun + PumpSwap)?
Read-only, the public RPC (rate limited: slow on purpose). Recent txs of the pump.fun programs -> the buys (fee payer spent SOL,
log 'Instruction: Buy') -> the buyer's previous txs: a plain SOL transfer TO the buyer a few slots before, of about the spent sum = paid.
Payers that fund many different buyers this way = a scheme like our networks. Out: sol_scan.txt
Usage: python sol_scan.py [txs per program=150]"""
import collections
import json
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
N = int(sys.argv[1]) if len(sys.argv) > 1 else 150
RPC = "https://api.mainnet-beta.solana.com"
try:  # 17.11: the user's Helius key (only this line of .env is read; it is never printed)
    for _l in open(r"D:\bot\.env", encoding="utf-8-sig"):
        if _l.strip().startswith("HELIUS_KEY="):
            RPC = "https://mainnet.helius-rpc.com/?api-key=" + _l.split("=", 1)[1].strip().strip('"\'')
except OSError:
    pass
PROGS = {"6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P": "pump.fun", "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA": "PumpSwap"}
SYS = "11111111111111111111111111111111"
LAST = [0.0]


def rpc(method, params, tries=6):
    for k in range(tries):
        gap = (0.13 if "helius" in RPC else 0.25) - (time.time() - LAST[0])
        if gap > 0:
            time.sleep(gap)
        LAST[0] = time.time()
        try:
            req = urllib.request.Request(RPC, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
                                         headers={"Content-Type": "application/json"})
            r = json.loads(urllib.request.urlopen(req, timeout=30).read())
            if "error" in r:
                raise RuntimeError(r["error"])
            return r["result"]
        except Exception as e:
            time.sleep(1.5 * (k + 1))
            err = e
    print("  узел не ответил:", str(err)[:80].replace(RPC, "<rpc>"))
    return None


def tx(sig):
    return rpc("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1, "commitment": "confirmed"}])


buys = []
for prog, name in PROGS.items():
    sigs = rpc("getSignaturesForAddress", [prog, {"limit": N}]) or []
    for s in sigs:
        if s.get("err"):
            continue
        t = tx(s["signature"])
        if not t or not t.get("meta"):
            continue
        logs = " ".join(t["meta"].get("logMessages") or [])
        if "Instruction: Buy" not in logs:
            continue
        keys = t["transaction"]["message"]["accountKeys"]
        payer = keys[0]["pubkey"] if isinstance(keys[0], dict) else keys[0]
        spent = (t["meta"]["preBalances"][0] - t["meta"]["postBalances"][0]) / 1e9
        if spent >= 0.05:
            buys.append(dict(venue=name, buyer=payer, sol=spent, slot=t["slot"], sig=s["signature"]))
    print(f"{name}: транзакций {len(sigs)}, покупок от 0.05 SOL: {sum(1 for b in buys if b['venue'] == name)}")


def funder(b):
    """The buyer's txs just before this buy: a SystemProgram transfer to it within 25 slots (~10 s), >= half of the spent SOL."""
    prev = rpc("getSignaturesForAddress", [b["buyer"], {"limit": 6, "before": b["sig"]}]) or []
    for s in prev:
        if b["slot"] - s["slot"] > 25:
            break
        t = tx(s["signature"])
        if not t:
            continue
        for ins in t["transaction"]["message"]["instructions"]:
            p = ins.get("parsed") if isinstance(ins, dict) else None
            if ins.get("programId") == SYS and isinstance(p, dict) and p.get("type") == "transfer":
                info = p["info"]
                if info.get("destination") == b["buyer"] and info.get("lamports", 0) / 1e9 >= b["sol"] * 0.5:
                    return info["source"], b["slot"] - s["slot"], info["lamports"] / 1e9
    return None, None, None


paid = collections.defaultdict(list)
for i, b in enumerate(buys):
    f, lag, amt = funder(b)
    if f:
        paid[f].append((b, lag, amt))
    if i % 20 == 19:
        print(f"  проверено покупателей {i + 1}/{len(buys)}", flush=True)
L = [f"покупок проверено {len(buys)}, оплачены переводом SOL за ≤25 слотов: {sum(len(v) for v in paid.values())}"]
for f, lst in sorted(paid.items(), key=lambda kv: -len(kv[1]))[:12]:
    L.append(f"  плательщик {f}: {len(lst)} покупок у {len({b['buyer'] for b, _, _ in lst})} покупателей; задержка (слоты) {sorted(l for _, l, _ in lst)[:8]}; "
             f"сумма/покупка {[round(a / b['sol'], 2) for b, _, a in lst][:6]}")
print("\n".join(L))
open(r"D:\bot\analysis\sol_scan.txt", "w", encoding="utf-8").write("\n".join(L))
