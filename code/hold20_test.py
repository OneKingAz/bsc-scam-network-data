"""19.11 (user: check holding ~20 s on nets 1/2). For every net 1/2 signal trade on given days: our real buy (Swap in land_block),
then the value of our coins if sold N blocks later, from the pool reserves (Sync) of that block. Our real early sale is ignored (~0.1% of pool).
Prints per-trade and totals. Usage: python hold20_test.py 2026-11-18 2026-11-19"""
import csv, json, sys, time, urllib.request
from collections import defaultdict
sys.stdout.reconfigure(encoding="utf-8")
RPC = "https://rpc-bsc.48.club"
def call(m, p):
    for k in range(5):
        try:
            r = json.loads(urllib.request.urlopen(urllib.request.Request(RPC, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": m, "params": p}).encode(),
                                                                        headers={"Content-Type": "application/json"}), timeout=30).read())
            if "result" in r: return r["result"]
        except Exception: pass
        time.sleep(1 + k)
FACT = "0xca143ce32fe78f1f7019d7d551a6402fc5350c73"; WBNB = "bb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
SWAP = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"; SYNC = "0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1"
HOLDS = [3, 11, 22, 33, 45, 67, 100]  # blocks (~0.45 s each): 1.3/5/10/15/20/30/45 s
days = sys.argv[1:] or ["2026-11-19"]
pairs = {}
res = defaultdict(lambda: defaultdict(list))
for f, net in (("signal_test.csv", 1), ("signal_test2.csv", 2)):
    for x in csv.DictReader(open(f, encoding="utf-8-sig"), delimiter=";"):
        if x["time"][:10] not in days or not x["pnl_pct"] or not x["land_block"]: continue
        tok = x["token"]
        if tok not in pairs:
            pairs[tok] = "0x" + call("eth_call", [{"to": FACT, "data": "0xe6a43905" + tok[2:].lower().rjust(64, "0") + WBNB.rjust(64, "0")}, "latest"])[-40:]
        p = pairs[tok]; t0 = int(tok, 16) < int("0x" + WBNB, 16); lb = int(x["land_block"])
        logs = call("eth_getLogs", [{"address": p, "fromBlock": hex(lb), "toBlock": hex(lb + max(HOLDS))}]) or []
        stake = coins = None; res_by_block = {}; bot_ok = None
        for l in logs:
            b = int(l["blockNumber"], 16); d = bytes.fromhex(l["data"][2:])
            if l["topics"][0] == SYNC:
                r0, r1 = int.from_bytes(d[:32], "big"), int.from_bytes(d[32:64], "big")
                res_by_block[b] = (r1, r0) if t0 else (r0, r1)  # (bnb, token) at end of block (last Sync wins)
            elif l["topics"][0] == SWAP:
                a = [int.from_bytes(d[i * 32:(i + 1) * 32], "big") for i in range(4)]
                bi = (a[1] if t0 else a[0]) / 1e18; to = (a[2] if t0 else a[3])
                if b == lb and stake is None and 0.002 <= bi <= 0.03:
                    stake, coins = bi, to
                elif b > lb and bi >= 0.05 and bot_ok is None:
                    bot_ok = b - lb
        if stake is None: continue
        gas = float(x["gas_bnb"] or 0)
        cur = None; out = {}
        for h in range(0, max(HOLDS) + 1):
            if lb + h in res_by_block: cur = res_by_block[lb + h]
            if h in HOLDS and cur:
                rb, rt = cur; ai = coins * 9975
                got = ai * rb / (rt * 10000 + ai) / 1e18
                out[h] = (got - stake - gas) / stake * 100
        for h, v in out.items(): res[(x["time"][:10], net)][h].append(v)
        print(x["time"], "сеть", net, x["symbol"], "факт", x["pnl_pct"], "| бот купил на +", bot_ok, "|", " ".join(f"{h}бл:{v:+.1f}" for h, v in out.items()))
print("\nСРЕДНЕЕ % на сделку (после газа), в скобках доля в плюсе:")
for k in sorted(res):
    print(k, " ".join(f"{h}бл({h*0.45:.0f}с):{sum(v)/len(v):+.1f}({sum(1 for z in v if z>0)}/{len(v)})" for h, v in sorted(res[k].items())))
