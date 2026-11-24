"""Which mempool stream sees plain BNB payments (like the creator's) before their block, and how early? Spends nothing.
Run: python analysis\\mempool_cover.py [seconds]"""
import contextlib, io, json, os, statistics as st, sys, threading, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
SEC = int(sys.argv[1]) if len(sys.argv) > 1 else 90
sys.argv = sys.argv[:1]
with contextlib.redirect_stdout(io.StringIO()):
    import bot as B
    import journal as J
import requests
from websockets.sync.client import connect

FEEDS = {"текущий": B.CFG["ws_url"], "nodereal": "wss://bsc-mainnet.nodereal.io/ws/v1/" + J.NODEREAL_KEY}
stop = time.time() + SEC
pend = {k: {} for k in FEEDS}
drops = {k: [] for k in FEEDS}
blocks = {}


def feed(name, url):
    while time.time() < stop:
        try:
            with connect(url, open_timeout=10, max_size=None) as ws:
                ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_subscribe", "params": ["newPendingTransactions", True]}))
                while time.time() < stop:
                    try:
                        m = json.loads(ws.recv(timeout=5))
                    except TimeoutError:
                        continue
                    if m.get("error"):
                        raise RuntimeError(str(m["error"])[:60])
                    r = (m.get("params") or {}).get("result")
                    h = r.get("hash") if isinstance(r, dict) else r
                    if h:
                        pend[name].setdefault(h.lower(), time.time())
        except Exception as e:
            drops[name].append(str(e)[:60])
            time.sleep(1)


def chain():
    s, last = requests.Session(), 0
    u = "https://bsc-rpc.publicnode.com"
    while time.time() < stop + 3:
        try:
            n = int(s.post(u, json={"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}, timeout=5).json()["result"], 16)
            if n > last:
                t = time.time()
                b = s.post(u, json={"jsonrpc": "2.0", "id": 1, "method": "eth_getBlockByNumber", "params": [hex(n), True]}, timeout=5).json()["result"]
                if b:
                    blocks[n] = (t, b["transactions"])
                    last = n
        except Exception:
            time.sleep(0.2)
        time.sleep(0.1)


for x in FEEDS.items():
    threading.Thread(target=feed, args=x, daemon=True).start()
threading.Thread(target=chain, daemon=True).start()
time.sleep(SEC + 6)
print(f"блоков {len(blocks)}; обрывы: " + ", ".join(f"{k} {len(v)}" for k, v in drops.items()) + " " + str({k: v[:1] for k, v in drops.items() if v}))
t0 = time.time() - SEC  # the streams need a few seconds to warm up
rows = {"все транзакции": lambda tx: True, "простые платежи ≥0.01 BNB": lambda tx: tx["input"] in ("0x", "") and int(tx["value"], 16) >= 10**16}
for title, cond in rows.items():
    txs = [(tx["hash"].lower(), t) for n, (t, l) in blocks.items() if t > t0 + 8 for tx in l if cond(tx)]
    print(f"\n{title}: {len(txs)}")
    for name, p in pend.items():
        leads = [t - p[h] for h, t in txs if h in p and p[h] < t]
        print(f"  {name:<10} видит до блока {len(leads) * 100 / max(1, len(txs)):5.1f}%, опережение блока: медиана {st.median(leads) * 1000 if leads else 0:6.0f} мс")
    a, b = pend["текущий"], pend["nodereal"]
    only = sum(1 for h, t in txs if (h in b and b[h] < t) and not (h in a and a[h] < t))
    any_ = sum(1 for h, t in txs if (h in b and b[h] < t) or (h in a and a[h] < t))
    both = [a[h] - b[h] for h, t in txs if h in a and h in b]
    print(f"  оба вместе {any_ * 100 / max(1, len(txs)):5.1f}%; nodereal добавляет {only * 100 / max(1, len(txs)):.1f}%; nodereal раньше текущего: медиана {st.median(both) * 1000 if both else 0:+.0f} мс")
