"""19.11 (user: where should a server stand?). Which country's BSC nodes give us the creators' payments FIRST.
Reads D:\\bot\\p2p_listener\\p2p_firsts.csv (every plain BNB transfer the listener got: first peer, time) and the creators' payments the
bot saw (signal_test*.csv, signal_skip*.csv: time, value); matches them by value (5 decimals) and time (+-60 s); geolocates the peers
(ip-api.com, only the peers' public IPs are sent). Compares with all plain transfers and with how many peers each country has.
Usage: python p2p_country.py [since "YYYY-mm-dd HH:MM:SS"]   Out: p2p_country.txt"""
import collections, csv, glob, json, os, sys, time, urllib.request
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
FIRSTS = r"D:\bot\p2p_listener\p2p_firsts.csv"
since = sys.argv[1] if len(sys.argv) > 1 else "2000-01-01 00:00:00"
rows = [r for r in csv.DictReader(open(FIRSTS, encoding="utf-8"), delimiter=";")]
by_val = collections.defaultdict(list)
for r in rows:
    try:
        v = round(int(r["value_wei"]) / 1e18, 5)
        by_val[v].append(r)
    except Exception:
        pass
pays = []
for p in glob.glob(os.path.join(HERE, "signal_test*.csv")) + glob.glob(os.path.join(HERE, "signal_skip*.csv")):
    for r in csv.DictReader(open(p, encoding="utf-8-sig"), delimiter=";"):
        if r.get("time", "") >= since and r.get("bnb_paid"):
            try:
                t = datetime.strptime(r["time"], "%Y-%m-%d %H:%M:%S").timestamp() * 1000
                pays.append((t, round(float(r["bnb_paid"]), 5), r.get("source", "")))
            except Exception:
                pass
matched = []
for t, v, src in pays:
    c = [r for r in by_val.get(v, []) if abs(int(r["first_ms"]) - t) < 60000]
    if c:
        matched.append((min(c, key=lambda r: abs(int(r["first_ms"]) - t)), src))
ips = sorted({r["first_peer"].rsplit(":", 1)[0] for r in rows if r.get("first_peer")})
geo = {}
cache = os.path.join(HERE, "p2p_geo_cache.json")
try:
    geo = json.load(open(cache, encoding="utf-8"))
except Exception:
    pass
todo = [i for i in ips if i not in geo]
for k in range(0, len(todo), 100):
    body = json.dumps([{"query": i, "fields": "country,city,query"} for i in todo[k:k + 100]]).encode()
    try:
        for g in json.loads(urllib.request.urlopen(urllib.request.Request("http://ip-api.com/batch", data=body, headers={"Content-Type": "application/json"}), timeout=20).read()):
            geo[g["query"]] = f"{g.get('country', '?')}"
    except Exception as e:
        print("геолокация:", e)
    time.sleep(1.5)
json.dump(geo, open(cache, "w", encoding="utf-8"))
country = lambda r: geo.get(r["first_peer"].rsplit(":", 1)[0], "?") if r.get("first_peer") else "?"
try:
    st = json.loads(urllib.request.urlopen("http://127.0.0.1:8547/status", timeout=5).read())
    peers_now = collections.Counter(geo.get(a.rsplit(":", 1)[0], "?") for a in st.get("peer_addrs", []))
except Exception:
    peers_now = collections.Counter()
allc = collections.Counter(country(r) for r in rows)
payc = collections.Counter(country(r) for r, _ in matched)
lines = [f"ОТКУДА ПЕРВЫМИ ПРИХОДЯТ ПЛАТЕЖИ (с {since}): простых переводов BNB {len(rows)}, платежей создателей у бота {len(pays)}, сопоставлено {len(matched)}", "",
         f"{'страна узла':22} {'платежи создателей':>20} {'все переводы BNB':>18} {'узлов сейчас':>13}"]
for c in sorted(set(allc) | set(payc), key=lambda c: -payc.get(c, 0) * 1000 - allc.get(c, 0)):
    lines.append(f"{c:22} {payc.get(c, 0):7d} ({payc.get(c, 0) / max(1, len(matched)):4.0%})     {allc.get(c, 0):7d} ({allc.get(c, 0) / max(1, len(rows)):4.0%})   {peers_now.get(c, 0):8d}")
out = "\n".join(lines)
print(out)
open(os.path.join(HERE, "p2p_country.txt"), "w", encoding="utf-8").write(out)
