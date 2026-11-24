"""19.11 (user: don't give up on nets 1/2). Nets 1/2 bots now quote at the payment block (0.001 %), so a buy between the payment and
the bot kills the bot's buy. Idea: buy right AFTER a successful bot buy (our buy is then older than the next payment and is inside the
next bot's quote), hold until the next successful bot buy, sell in the block after it. Paper on the real pool path (Sync), all coins of
nets 1/2 that we traded on the given days. Drain (pool BNB < 50 % of peak) before the sale = -100 %.
Usage: python ride_test.py 2026-11-18 2026-11-19 [maxwait_s]"""
import csv, json, sys, time, urllib.request
sys.stdout.reconfigure(encoding="utf-8")
def call(m, p):
    for k in range(6):
        try:
            r = json.loads(urllib.request.urlopen(urllib.request.Request("https://rpc-bsc.48.club", data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": m, "params": p}).encode(),
                                                                        headers={"Content-Type": "application/json"}), timeout=40).read())
            if "result" in r: return r["result"]
        except Exception: pass
        time.sleep(2 + k)
FACT = "0xca143ce32fe78f1f7019d7d551a6402fc5350c73"; WBNB = "bb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
SWAP = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"; SYNC = "0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1"
days = [a for a in sys.argv[1:] if a.startswith("20")] or ["2026-11-19"]
MAXWAIT = int(next((a for a in sys.argv[1:] if a.isdigit()), "150")) / 0.45  # blocks
STAKE, GAS = 0.0085, 0.000054  # $5 and gas of buy+sell
coins = {}
for f, net in (("signal_test.csv", 1), ("signal_test2.csv", 2)):
    for x in csv.DictReader(open(f, encoding="utf-8-sig"), delimiter=";"):
        if x["time"][:10] in days and x["pay_block"]:
            c = coins.setdefault(x["token"], dict(net=net, sym=x["symbol"], lo=10 ** 12, hi=0))
            c["lo"] = min(c["lo"], int(x["pay_block"])); c["hi"] = max(c["hi"], int(x["pay_block"]))
tot = {1: [], 2: []}
for tok, c in coins.items():
    pair = "0x" + call("eth_call", [{"to": FACT, "data": "0xe6a43905" + tok[2:].lower().rjust(64, "0") + WBNB.rjust(64, "0")}, "latest"])[-40:]
    t0 = int(tok, 16) < int("0x" + WBNB, 16)
    lo, hi = c["lo"] - 3000, c["hi"] + 3000
    ev = []
    for s in range(lo, hi, 400):
        for l in call("eth_getLogs", [{"address": pair, "fromBlock": hex(s), "toBlock": hex(min(s + 399, hi))}]) or []:
            d = bytes.fromhex(l["data"][2:]); b = int(l["blockNumber"], 16); i = int(l["logIndex"], 16)
            if l["topics"][0] == SYNC:
                r0, r1 = int.from_bytes(d[:32], "big"), int.from_bytes(d[32:64], "big")
                ev.append((b, i, "S", (r1, r0) if t0 else (r0, r1)))
            elif l["topics"][0] == SWAP:
                a = [int.from_bytes(d[k * 32:(k + 1) * 32], "big") for k in range(4)]
                bi = (a[1] if t0 else a[0]) / 1e18
                if bi >= 0.05: ev.append((b, i, "B", bi))
    ev.sort()
    if not ev: continue
    res_end = {}; peak = 0; cur = None
    for b, i, k, v in ev:
        if k == "S": res_end[b] = v
    blocks = sorted(res_end)
    def reserves(b):  # pool at the end of block b
        import bisect
        j = bisect.bisect_right(blocks, b) - 1
        return res_end[blocks[j]] if j >= 0 else None
    buys = [b for b, i, k, v in ev if k == "B"]
    out = []
    for n in range(len(buys) - 1):
        eb = buys[n] + 1
        r = reserves(eb)
        if not r: continue
        ai = int(STAKE * 1e18) * 9975; coins_got = ai * r[1] // (r[0] * 10000 + ai)
        nxt = next((b for b in buys[n + 1:] if b > eb), None)
        sb = (nxt + 1) if nxt and nxt + 1 - eb <= MAXWAIT else int(eb + MAXWAIT)
        pk = max(reserves(b)[0] for b in blocks if eb <= b <= sb) if any(eb <= b <= sb for b in blocks) else r[0]
        rs = reserves(sb)
        if rs[0] < 0.5 * max(pk, r[0]):
            out.append(-100.0); continue
        ci = coins_got * 9975; got = ci * rs[0] / (rs[1] * 10000 + ci) / 1e18
        out.append((got - STAKE - GAS) / STAKE * 100)
    tot[c["net"]] += out
    if out:
        print(f"сеть {c['net']} {c['sym']:12} покупок ботов {len(buys)}  сделок {len(out)}  ср {sum(out)/len(out):+.1f}%  в плюсе {sum(1 for z in out if z > 0)}  обнулений {sum(1 for z in out if z <= -99)}")
for n, v in tot.items():
    if v: print(f"ИТОГО сеть {n}: {len(v)} сделок, ср {sum(v)/len(v):+.2f}%, в плюсе {sum(1 for z in v if z > 0)}, обнулений {sum(1 for z in v if z <= -99)}, медиана {sorted(v)[len(v)//2]:+.1f}%")
