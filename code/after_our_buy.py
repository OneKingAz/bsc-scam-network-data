"""19.11 (user: is there a big buy right after ours?). For every LIVE Solana trade of tonight: all pool transactions from our buy to 20 s after
our sale (Helius, HELIUS_KEY of D:\\bot\\.env, never printed): signer, its group, SOL into / out of the pool, Withdraw. Prints a timeline per trade.
Out: analysis/after_our_buy.csv"""
import csv, json, os, re, sys, time, urllib.request

sys.stdout.reconfigure(encoding="utf-8")
A = os.path.dirname(os.path.abspath(__file__))
D = r"C:\Users\IT\Desktop\bsc_meme_bot"
WSOL = "So11111111111111111111111111111111111111112"
K = [l.split("=", 1)[1].strip().strip('"\'') for l in open(r"D:\bot\.env", encoding="utf-8-sig") if l.strip().startswith("HELIUS_KEY=")][0]
H = "https://mainnet.helius-rpc.com/?api-key=" + K


def rpc(m, p):
    for k in range(6):
        try:
            r = json.loads(urllib.request.urlopen(urllib.request.Request(H, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": m, "params": p}).encode(),
                                                                         headers={"Content-Type": "application/json"}), timeout=60).read())
            if r.get("result") is not None:
                return r["result"]
        except Exception:
            pass
        time.sleep(1 + k)


rd = lambda p: list(csv.DictReader(open(p, encoding="utf-8-sig"), delimiter=";"))
TEN = {r["wallet"] for r in rd(os.path.join(D, r"investigation_task_04\nine_wallets.csv"))} | {"GH74zHdNZWcF9zRzw3KoNG8GNgUvtzokNwkSFMeWTvi6"}
FLEET = {r["wallet"] for r in rd(os.path.join(D, r"investigation_task_03\park_wallet_flows.csv"))}
FAMILY = {r["who"]: r["role"] for r in rd(os.path.join(D, r"analyst_data_2026-11-18\sol_buyers.csv")) if r["role"] != "посторонний"}
ME = "CqMsFPRFuSqCuxuK79kpw7cFm5ud8MoWBnqT27VfDx6J"
log = open(os.path.join(A, "sol_trader.log"), encoding="utf-8", errors="ignore").read()
trades = [r for r in rd(os.path.join(A, "sol_trades.csv")) if r["mode"] == "LIVE" and r["time"].startswith(sys.argv[1] if len(sys.argv) > 1 else "19.11")]
if len(sys.argv) > 2:  # comma list of mint prefixes
    want = sys.argv[2].split(",")
    trades = [r for r in trades if any(r["mint"].startswith(w) for w in want)]
out = []
for t in trades:
    mint, pool = t["mint"], t["pool"]
    mb = re.search(r"КУПИЛ " + mint[:8] + r".*?tx (\w+)", log)
    ms = re.search(r"ПРОДАЛ " + mint[:8] + r".*?tx (\w+)", log)
    if not mb:
        continue
    buy_sig = mb.group(1)
    sigs, before = [], None  # page back until our buy is in the list (busy pools have > 1000 tx after it)
    for _ in range(30):
        p = {"limit": 1000}
        if before:
            p["before"] = before
        page = rpc("getSignaturesForAddress", [pool, p]) or []
        sigs += page
        if not page or any(x["signature"] == buy_sig for x in page) or len(page) < 1000:
            # one more page to cover the 10 slots before our buy
            if page and len(page) == 1000:
                sigs += rpc("getSignaturesForAddress", [pool, {"limit": 1000, "before": page[-1]["signature"]}]) or []
            break
        before = page[-1]["signature"]
    sigs = sorted(sigs, key=lambda x: x["slot"])
    bslot = next((s["slot"] for s in sigs if s["signature"] == buy_sig), None)
    sslot = next((s["slot"] for s in sigs if ms and s["signature"] == ms.group(1)), None)
    if bslot is None:
        print(mint[:8], "наша покупка не найдена в истории пула")
        continue
    # 19.11 (user: the withdrawal seemed tied to the coin's turnover): turnover of the whole pool from creation up to the withdrawal / our sale
    allok = [s for s in sigs if not s.get("err")]
    # from 10 slots before our buy to ~50 slots (20 s) after our sale
    win = [s for s in sigs if bslot - 10 <= s["slot"] <= (sslot or bslot + 200) + 50 and not s.get("err")]
    print(f"   транзакций пула всего (видимых): {len(allok)}, до нашей продажи: {sum(1 for s in allok if s['slot'] <= (sslot or 10 ** 12))}")
    print(f"\n=== {t['time']} {mint[:8]} ({t['exit_reason']}, итог {t['pnl_pct']}%), наша покупка слот {bslot}, продажа слот {sslot}")
    for s in win:
        tx = rpc("getTransaction", [s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1}])
        if not tx:
            continue
        m = tx["meta"]
        keys = [k["pubkey"] for k in tx["transaction"]["message"]["accountKeys"]]
        signer = keys[0]
        logs = " ".join(m.get("logMessages") or [])
        pre = {(b.get("owner"), b["mint"]): int(b["uiTokenAmount"]["amount"]) for b in m.get("preTokenBalances") or []}
        post = {(b.get("owner"), b["mint"]): int(b["uiTokenAmount"]["amount"]) for b in m.get("postTokenBalances") or []}
        dw = (post.get((pool, WSOL), 0) - pre.get((pool, WSOL), 0)) / 1e9
        pw = post.get((pool, WSOL), 0) / 1e9
        grp = "МЫ" if signer == ME else "десять" if signer in TEN else "парк-109" if signer in FLEET else ("ферма:" + FAMILY[signer]) if signer in FAMILY else \
              ("создатель" if signer == t["creator"] or signer.startswith(t["creator"][:8]) else "посторонний")
        kind = "ВЫВОД" if "Instruction: Withdraw" in logs else "покупка" if dw > 0 else "продажа" if dw < 0 else "прочее"
        rel = s["slot"] - bslot
        out.append(dict(trade=t["time"], mint=mint, slot=s["slot"], slots_after_our_buy=rel, signer=signer, group=grp, kind=kind, sol_into_pool=round(dw, 4),
                        pool_sol_after=round(pw, 3), signature=s["signature"]))
        flag = " <<< КРУПНАЯ" if dw >= 1 else ""
        print(f"  {rel:+5d} сл  {grp:22} {kind:8} {dw:+9.4f} SOL  пул {pw:8.3f}{flag}")
with open(os.path.join(A, "after_our_buy.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(out[0].keys()), delimiter=";")
    w.writeheader()
    w.writerows(out)
