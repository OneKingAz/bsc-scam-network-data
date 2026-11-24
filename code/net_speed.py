"""Can we buy faster? Sends nothing, spends nothing. For ~90 s: which node shows a new block first, how long a full block takes to
read, and which mempool stream shows a transaction first. Run: python analysis\\net_speed.py [seconds]"""
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

NODES = {"48club": "https://rpc-bsc.48.club", "dataseed": "https://bsc-dataseed.bnbchain.org", "defibit": "https://bsc-dataseed1.defibit.io",
         "ninicoin": "https://bsc-dataseed1.ninicoin.io", "publicnode": "https://bsc-rpc.publicnode.com", "drpc": "https://bsc.drpc.org",
         "nodereal": "https://bsc-mainnet.nodereal.io/v1/" + J.NODEREAL_KEY}
for i, u in enumerate(dict.fromkeys(B.CFG.get("rpc_urls") or [B.CFG["rpc_url"]])):  # the addresses are not printed
    NODES[f"рабочий{i + 1}"] = u
for i, u in enumerate(dict.fromkeys(B.CFG["send_rpc_urls"])):
    NODES[f"отправка{i + 1}"] = u
FEEDS = {"текущий": B.CFG["ws_url"], "nodereal": "wss://bsc-mainnet.nodereal.io/ws/v1/" + J.NODEREAL_KEY, "drpc": "wss://bsc.drpc.org", "publicnode": "wss://bsc-rpc.publicnode.com"}
stop = time.time() + SEC
seen, rtt, errs, full = {}, {k: [] for k in NODES}, {k: 0 for k in NODES}, {k: [] for k in NODES}
pend = {k: {} for k in FEEDS}
lock = threading.Lock()


def rpc(s, u, method, params=None):
    return s.post(u, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}, timeout=5).json()["result"]


def poll(name, url):
    s, last = requests.Session(), 0
    while time.time() < stop:
        t = time.time()
        try:
            n = int(rpc(s, url, "eth_blockNumber"), 16)
            now = time.time()
            rtt[name].append(now - t)
            if n > last:
                with lock:
                    seen.setdefault(n, {})[name] = now
                if last and len(full[name]) < 25:  # how long reading the whole new block takes here
                    t1 = time.time()
                    rpc(s, url, "eth_getBlockByNumber", [hex(n), True])
                    full[name].append(time.time() - t1)
                last = n
        except Exception:
            errs[name] += 1
            time.sleep(0.5)
        time.sleep(max(0, 0.2 - (time.time() - t)))


def feed(name, url):
    try:
        with connect(url, open_timeout=10, max_size=None) as ws:
            ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_subscribe", "params": ["newPendingTransactions", True]}))
            while time.time() < stop:
                try:
                    m = json.loads(ws.recv(timeout=5))
                except TimeoutError:
                    continue
                if m.get("error"):
                    pend[name]["ошибка"] = str(m["error"])[:70]
                    return
                r = (m.get("params") or {}).get("result")
                h = r.get("hash") if isinstance(r, dict) else r
                if h:
                    pend[name].setdefault(h.lower(), time.time())
    except Exception as e:
        pend[name]["ошибка"] = str(e)[:70]


ts = [threading.Thread(target=poll, args=x, daemon=True) for x in NODES.items()] + [threading.Thread(target=feed, args=x, daemon=True) for x in FEEDS.items()]
[t.start() for t in ts]
time.sleep(SEC + 6)
print(f"блоков за {SEC} с: {len(seen)}")
print("узел         первый | отстаёт от самого раннего, мс: медиана  90% | ответ на запрос, мс | чтение всего блока, мс | ошибок")
q = lambda v, p: sorted(v)[int(len(v) * p)] if v else 0
for name in NODES:
    lags = [v[name] - min(v.values()) for v in seen.values() if name in v and len(v) > 2]
    first = sum(min(v, key=v.get) == name for v in seen.values() if name in v)
    print(f"{name:<12} {first:>6} | {q(lags, .5) * 1000:>6.0f} {q(lags, .9) * 1000:>6.0f} | {q(rtt[name], .5) * 1000:>6.0f} | {q(full[name], .5) * 1000:>6.0f} (макс {max(full[name], default=0) * 1000:.0f}) | {errs[name]}")
print("\nпотоки мемпула: транзакций, из них первым, опережает текущий на, мс")
for name in FEEDS:
    if "ошибка" in pend[name]:
        print(f"{name:<12} ошибка: {pend[name]['ошибка']}")
        continue
    p, cur = pend[name], pend["текущий"]
    both = [cur[h] - p[h] for h in p if h in cur]
    others = {k: v for k, v in pend.items() if "ошибка" not in v}
    first = sum(all(p[h] <= o.get(h, 1e18) for o in others.values()) for h in p)
    only = sum(h not in cur for h in p)
    print(f"{name:<12} {len(p):>6}, первым {first:>6}, нет в текущем {only:>6}, раньше текущего: медиана {q(both, .5) * 1000:+.0f} мс")
