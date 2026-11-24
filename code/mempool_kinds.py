"""19.11 (user: what does publicnode show in advance?). Listens to the publicnode mempool (full txs) for N seconds and reads every new
block; for every tx of a block: was it seen pending before, and how many ms before the block's own time. Grouped by kind and gas price.
Usage: python mempool_kinds.py [seconds=180]   Out: mempool_kinds.txt (+ printed)"""
import collections, json, os, sys, threading, time, urllib.request
from websockets.sync.client import connect

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
SEC = int(sys.argv[1]) if len(sys.argv) > 1 else 180
WS = "wss://bsc-rpc.publicnode.com"
RPC = ["https://bsc-dataseed.bnbchain.org", "https://rpc-bsc.48.club", "https://bsc-rpc.publicnode.com"]
ROUTERS = {"0x10ed43c718714eb63d5aa57b78b54704e256024e": "Pancake V2", "0x13f4ea83d0bd40e75c8222255bc855a974568dd4": "Pancake V3",
           "0x1b81d678ffb9c0263b24a97847620c99d213eb14": "Pancake V3", "0xba56663c1de9423ec886b24ac752d86ff0d9f7c0": "агрегатор"}
seen = {}  # hash -> local time seen pending
stop = time.time() + SEC


def call(m, p):
    for u in RPC:
        try:
            r = json.loads(urllib.request.urlopen(urllib.request.Request(u, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": m, "params": p}).encode(),
                                                                         headers={"Content-Type": "application/json"}), timeout=10).read())
            if r.get("result") is not None:
                return r["result"]
        except Exception:
            pass


def listen():
    while time.time() < stop:
        try:
            with connect(WS, open_timeout=10, max_size=None) as ws:
                ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_subscribe", "params": ["newPendingTransactions", True]}))
                while time.time() < stop:
                    try:
                        m = json.loads(ws.recv(timeout=3))
                    except TimeoutError:
                        continue
                    tx = (m.get("params") or {}).get("result")
                    if isinstance(tx, dict) and "hash" in tx:
                        seen.setdefault(tx["hash"].lower(), time.time())
        except Exception:
            time.sleep(1)


def kind(t):
    to = (t.get("to") or "").lower()
    inp = t.get("input") or "0x"
    val = int(t.get("value") or "0x0", 16)
    if to in ROUTERS:
        return "свап " + ROUTERS[to] + (" (за BNB)" if val else "")
    if inp == "0x":
        return "простой перевод BNB" if val else "пустой перевод"
    return {"0x095ea7b3": "approve", "0xa9059cbb": "перевод токена"}.get(inp[:10], "прочий контракт")


# the chain clock against ours, measured before the run from fresh heads (the least delayed read wins)
off = []
for _ in range(20):
    b0 = call("eth_getBlockByNumber", ["latest", False]); t0 = time.time()
    x0 = int(b0["mixHash"][-16:], 16) if b0.get("mixHash") else 0
    off.append((int(b0["timestamp"], 16) * 1000 + (x0 if x0 < 1000 else 0)) / 1000 - t0)
    time.sleep(0.2)
CO = max(off) * 1000
threading.Thread(target=listen, daemon=True).start()
time.sleep(3)
head = int(call("eth_blockNumber", []), 16)
stats = collections.defaultdict(lambda: [0, 0, []])
gas = collections.defaultdict(lambda: [0, 0])
blocks = 0
while time.time() < stop:
    h = call("eth_blockNumber", [])
    if not h or int(h, 16) <= head:
        time.sleep(0.05)
        continue
    for n in range(head + 1, int(h, 16) + 1):
        b = call("eth_getBlockByNumber", [hex(n), True])
        if not b:
            continue
        now = time.time()
        x = int(b["mixHash"][-16:], 16) if b.get("mixHash") else 0
        bms = int(b["timestamp"], 16) * 1000 + (x if x < 1000 else 0)
        blocks += 1
        for t in b["transactions"]:
            k = kind(t)
            s = seen.get(t["hash"].lower())
            st = stats[k]
            st[0] += 1
            g = int(t.get("gasPrice") or "0x0", 16) / 1e9
            gk = "<0.1 gwei" if g < 0.1 else "0.1–1 gwei" if g < 1 else "≥1 gwei"
            gas[gk][0] += 1
            if s:
                st[1] += 1
                gas[gk][1] += 1
                st[2].append(bms - s * 1000)
    head = int(h, 16)
co = CO  # chain ms = local ms + co (measured before the run)
lines = [f"ЗАМЕР МЕМПУЛА publicnode: {SEC} с, блоков {blocks}, транзакций в них {sum(v[0] for v in stats.values())}; часы цепочки {co:+.0f} мс к нашим; запас > 0 = видели ДО времени блока", ""]
lines.append(f"{'тип транзакции':30} {'в блоках':>9} {'видели заранее':>16}   запас до блока, мс: медиана / 10% худших / 10% лучших")
for k, (n, sn, leads) in sorted(stats.items(), key=lambda z: -z[1][0]):
    ls = sorted(l - co for l in leads)
    q = lambda p: ls[min(len(ls) - 1, int(p * len(ls)))] if ls else 0
    lines.append(f"{k:30} {n:9d} {sn:8d} ({sn / n:4.0%})   " + (f"{q(0.5):6.0f} / {q(0.1):6.0f} / {q(0.9):6.0f}" if ls else ""))
lines.append("")
lines.append("по цене газа:")
for k, (n, sn) in sorted(gas.items()):
    lines.append(f"  {k:12} в блоках {n:6d}, видели заранее {sn:6d} ({sn / n:.0%})")
out = "\n".join(lines)
print(out)
open(os.path.join(HERE, "mempool_kinds.txt"), "w", encoding="utf-8").write(out)
