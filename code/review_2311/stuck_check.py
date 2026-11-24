"""23.11: can our stuck coins be sold now? balance, guard mark, pool value, transfer-to-pair simulation (eth_call, no money)."""
import requests, sys
sys.stdout.reconfigure(encoding="utf-8")
U = "https://bsc-rpc.publicnode.com"; S = requests.Session()
def rpc(m, p):
    for _ in range(4):
        try:
            r = S.post(U, json={"jsonrpc": "2.0", "id": 1, "method": m, "params": p}, timeout=20).json()
            if "result" in r or "error" in r: return r
        except Exception: pass
    return {}
pad = lambda a: a[2:].lower().rjust(64, "0")
W = {l.strip().split(";")[1].lower(): l.split(";")[0] for l in open("../hop_wallets.txt", encoding="utf-8") if ";0x" in l}
TR = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
WBNB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"; FACT = "0xca143ce32fe78f1f7019d7d551a6402fc5350c73"
cases = [("KIMJONGUN", "0x0054f000Df18598Bd0774Be71f0c832b28e74552", 135257394, "0xbd418ce6")]
for sym, tok, lb, guard_pref in cases:
    lg = rpc("eth_getLogs", [{"address": tok, "fromBlock": hex(lb), "toBlock": hex(lb), "topics": [TR]}]).get("result") or []
    w = next(("0x" + l["topics"][2][-40:] for l in lg if "0x" + l["topics"][2][-40:] in W), None)
    bal = int(rpc("eth_call", [{"to": tok, "data": "0x70a08231" + pad(w)}, "latest"])["result"], 16)
    pair = "0x" + rpc("eth_call", [{"to": FACT, "data": "0xe6a43905" + pad(tok) + pad(WBNB)}, "latest"])["result"][-40:]
    rs = rpc("eth_call", [{"to": pair, "data": "0x0902f1ac"}, "latest"])["result"][2:]
    t0 = "0x" + rpc("eth_call", [{"to": pair, "data": "0x0dfe1681"}, "latest"])["result"][-40:]
    r0, r1 = int(rs[:64], 16), int(rs[64:128], 16)
    rt, rb = (r0, r1) if t0.lower() == tok.lower() else (r1, r0)
    val = bal * 9975 * rb / (rt * 10000 + bal * 9975) / 1e18 if rt else 0
    slot6 = rpc("eth_getStorageAt", [tok, "0x6", "latest"])["result"]
    guard = "0x" + slot6[-40:]
    mark = int(rpc("eth_call", [{"to": tok, "data": "0xdd62ed3e" + pad(guard) + pad(w)}, "latest"])["result"], 16)
    sim = rpc("eth_call", [{"from": w, "to": tok, "data": "0xa9059cbb" + pad(pair) + hex(bal)[2:].rjust(64, "0"), "gasPrice": hex(50_000_000)}, "latest"])
    print(f"{sym}: кошелёк W{W[w]} {w}, монет {bal / 1e18:.2f}, пул {rb / 1e18:.3f} BNB, стоят ≈ {val:.6f} BNB; сторож {guard}, метка {'ЕСТЬ' if mark else 'нет'}; "
          f"перевод в пул (симуляция): {'проходит' if 'result' in sim else 'ОТКАЗ ' + str(sim.get('error', {}).get('message', ''))[:60]}")
