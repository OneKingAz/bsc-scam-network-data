"""19.11: publicnode against our own p2p listener (bsc_p2p_listener.exe, ws://127.0.0.1:8547). Both mempools are heard at once,
every new block is read (hashes only, fast); for every tx of a block: seen by which source, and how many ms before the block's time.
Usage: python mempool_compare.py [seconds=180]   Out: mempool_compare.txt"""
import collections, json, os, sys, threading, time, urllib.request
from websockets.sync.client import connect

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
SEC = int(sys.argv[1]) if len(sys.argv) > 1 else 180
SRC = {"publicnode": "wss://bsc-rpc.publicnode.com", "свой p2p": "ws://127.0.0.1:8547"}
RPC = ["https://bsc-dataseed.bnbchain.org", "https://rpc-bsc.48.club"]
seen = {k: {} for k in SRC}  # source -> hash -> local seconds
info = {}  # hash -> kind
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


def kind(t):
    to = (t.get("to") or "").lower()
    inp = t.get("input") or "0x"
    if to == "0x10ed43c718714eb63d5aa57b78b54704e256024e":
        return "свап Pancake V2"
    if inp == "0x":
        return "простой перевод BNB" if int(t.get("value") or "0x0", 16) else "пустой перевод"
    return {"0x095ea7b3": "approve", "0xa9059cbb": "перевод токена"}.get(inp[:10], "прочий контракт")


def listen(name, url):
    while time.time() < stop:
        try:
            with connect(url, open_timeout=10, max_size=None) as ws:
                ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_subscribe", "params": ["newPendingTransactions", True]}))
                while time.time() < stop:
                    try:
                        m = json.loads(ws.recv(timeout=3))
                    except TimeoutError:
                        continue
                    t = (m.get("params") or {}).get("result")
                    if isinstance(t, dict) and "hash" in t:
                        h = t["hash"].lower()
                        seen[name].setdefault(h, time.time())
                        info.setdefault(h, kind(t))
        except Exception:
            time.sleep(1)


off = []
for _ in range(20):
    b0 = call("eth_getBlockByNumber", ["latest", False]); t0 = time.time()
    x0 = int(b0["mixHash"][-16:], 16) if b0.get("mixHash") else 0
    off.append((int(b0["timestamp"], 16) * 1000 + (x0 if x0 < 1000 else 0)) / 1000 - t0)
    time.sleep(0.2)
CO = max(off)  # chain s = local s + CO
for n_, u in SRC.items():
    threading.Thread(target=listen, args=(n_, u), daemon=True).start()
time.sleep(3)
head = int(call("eth_blockNumber", []), 16)
tot = 0
by = collections.defaultdict(lambda: {"n": 0, **{k: [] for k in SRC}, "both": []})
blocks = 0
while time.time() < stop:
    h = call("eth_blockNumber", [])
    if not h or int(h, 16) <= head:
        time.sleep(0.05)
        continue
    for n in range(head + 1, int(h, 16) + 1):
        b = call("eth_getBlockByNumber", [hex(n), False])
        if not b:
            continue
        x = int(b["mixHash"][-16:], 16) if b.get("mixHash") else 0
        bt = (int(b["timestamp"], 16) * 1000 + (x if x < 1000 else 0)) / 1000
        blocks += 1
        for th in b["transactions"]:
            th = th.lower()
            tot += 1
            k = info.get(th, "не видел никто")
            d = by[k]
            d["n"] += 1
            leads = {}
            for s in SRC:
                if th in seen[s]:
                    leads[s] = (bt - (seen[s][th] + CO)) * 1000
                    d[s].append(leads[s])
            if len(leads) == 2:
                d["both"].append(leads["свой p2p"] - leads["publicnode"])
    head = int(h, 16)
med = lambda xs: sorted(xs)[len(xs) // 2] if xs else 0
lines = [f"СРАВНЕНИЕ МЕМПУЛОВ: {SEC} с, блоков {blocks}, транзакций {tot}. Запас = за сколько мс до времени блока увидели (больше — лучше)", ""]
lines.append(f"{'тип':24} {'в блоках':>8} | {'publicnode: видел, запас':>26} | {'свой p2p: видел, запас':>24} | свой раньше publicnode на (медиана, мс)")
for k, d in sorted(by.items(), key=lambda z: -z[1]["n"]):
    p, o = d["publicnode"], d["свой p2p"]
    lines.append(f"{k:24} {d['n']:8d} | {len(p):6d} ({len(p) / d['n']:4.0%}) {med(p):8.0f} мс | {len(o):6d} ({len(o) / d['n']:4.0%}) {med(o):8.0f} мс | "
                 + (f"{med(d['both']):+.0f} (свой раньше в {sum(1 for x in d['both'] if x > 0)} из {len(d['both'])})" if d["both"] else ""))
out = "\n".join(lines)
print(out)
open(os.path.join(HERE, "mempool_compare.txt"), "w", encoding="utf-8").write(out)
