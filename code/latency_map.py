"""19.11 (user: which BSC nodes and which builders are closest to us?). TCP connect time (≈ one round trip) from this PC to the
listener's BSC peers + known_nodes.txt and to the block builders' endpoints; countries by ip-api.com (public IPs only).
Usage: python latency_map.py   Out: latency_map.txt"""
import json, os, socket, sys, time, urllib.request, concurrent.futures as cf
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
BUILDERS = {"48club (puissant)": "puissant-builder.48.club", "BlockRazor (общий)": "bsc.blockrazor.xyz",
            "BlockRazor Вирджиния": "virginia.builder.blockrazor.io", "BlockRazor Токио": "tokyo.builder.blockrazor.io",
            "BlockRazor Франкфурт": "frankfurt.builder.blockrazor.io", "BlockRazor jp scutum": "jp-bscscutum.blockrazor.xyz",
            "BlockRazor ger scutum": "ger-bscscutum.blockrazor.xyz", "BlockRazor us scutum": "us-bscscutum.blockrazor.xyz",
            "BlockRazor ire scutum": "ire-bscscutum.blockrazor.xyz", "JetBldr": "rpc.bsc.jetbldr.xyz", "bloXroute": "bsc.rpc.blxrbdn.com",
            "48club RPC": "rpc.48.club", "publicnode": "bsc-rpc.publicnode.com"}


def rtt(host, port, n=3):
    best = None
    for _ in range(n):
        try:
            t = time.perf_counter()
            s = socket.create_connection((host, port), timeout=2)
            d = (time.perf_counter() - t) * 1000
            s.close()
            best = d if best is None else min(best, d)
        except Exception:
            pass
    return best


peers = set()
try:
    st = json.loads(urllib.request.urlopen("http://127.0.0.1:8547/status", timeout=5).read())
    peers |= set(st.get("peer_addrs", []))
except Exception:
    pass
try:
    for line in open(r"D:\bot\p2p_listener\known_nodes.txt", encoding="utf-8"):
        if "@" in line:
            peers.add(line.strip().split("@")[1].split("?")[0])
except Exception:
    pass
peers = sorted(peers)
with cf.ThreadPoolExecutor(64) as ex:
    pr = dict(zip(peers, ex.map(lambda a: rtt(a.rsplit(":", 1)[0], int(a.rsplit(":", 1)[1])), peers)))
    br = {}
    for name, h in BUILDERS.items():
        try:
            ip = socket.gethostbyname(h)
        except Exception:
            ip = None
        br[name] = (h, ip, ex.submit(rtt, h, 443).result() if ip else None)
ips = sorted({a.rsplit(":", 1)[0] for a in peers} | {v[1] for v in br.values() if v[1]})
geo = {}
for k in range(0, len(ips), 100):
    try:
        body = json.dumps([{"query": i, "fields": "country,city,org,query"} for i in ips[k:k + 100]]).encode()
        for g in json.loads(urllib.request.urlopen(urllib.request.Request("http://ip-api.com/batch", data=body, headers={"Content-Type": "application/json"}), timeout=20).read()):
            geo[g["query"]] = f"{g.get('country', '?')}, {g.get('city', '?')} ({g.get('org', '')[:28]})"
    except Exception as e:
        print("геолокация:", e)
    time.sleep(1.5)
L = ["СТРОИТЕЛИ И RPC (время установки соединения с этого ПК, мс; меньше — ближе):"]
for name, (h, ip, r) in sorted(br.items(), key=lambda z: z[1][2] if z[1][2] is not None else 1e9):
    L.append(f"  {name:24} {('%6.0f' % r) if r is not None else '   нет'}   {h}  {ip or 'нет DNS'}  {geo.get(ip, '') if ip else ''}")
L += ["", f"УЗЛЫ BSC (подключённые и из known_nodes.txt), ответили {sum(1 for v in pr.values() if v)} из {len(pr)}; 20 ближайших:"]
for a, r in sorted(((a, r) for a, r in pr.items() if r), key=lambda z: z[1])[:20]:
    L.append(f"  {r:6.0f} мс  {a:24} {geo.get(a.rsplit(':', 1)[0], '')}")
by = {}
for a, r in pr.items():
    if r:
        c = geo.get(a.rsplit(":", 1)[0], "?").split(",")[0]
        by.setdefault(c, []).append(r)
L += ["", "по странам (медиана мс, узлов):"]
for c, v in sorted(by.items(), key=lambda z: sorted(z[1])[len(z[1]) // 2]):
    L.append(f"  {c:20} {sorted(v)[len(v) // 2]:6.0f} мс  ({len(v)})")
out = "\n".join(L)
print(out)
open(os.path.join(HERE, "latency_map.txt"), "w", encoding="utf-8").write(out)
