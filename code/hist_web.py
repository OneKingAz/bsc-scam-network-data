# Web UI for the launch history (hist_launches.jsonl): backtest any entry/exit rules by series and day.
# Run: python analysis\hist_web.py  ->  http://127.0.0.1:8772  (read-only, no keys, no trading)
import json, os, statistics as st, datetime as dt
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "hist_launches.jsonl")
PORT = 8772
DATA = {"rows": [], "mtime": 0}


def load():
    m = os.path.getmtime(SRC)
    if m != DATA["mtime"]:
        rows = []
        with open(SRC, encoding="utf-8") as fh:
            for l in fh:
                try:
                    r = json.loads(l)
                except ValueError:
                    continue  # last line may be half-written by hist.py
                d = dt.datetime.fromtimestamp(r["time"])
                r["day"], r["hour"] = d.strftime("%m-%d"), d.hour
                rows.append(r)
        DATA.update(rows=rows, mtime=m)
    return DATA["rows"]


def sim(path, delay=30, tp=200, sl=50, hold=300, cap=300, maxdrop=-10):
    e = next(((t, p) for t, p in path if t >= delay and p > -90), None)
    if e is None or e[1] < maxdrop: return None
    base = 1 + e[1] / 100
    q = [(t - e[0], ((1 + p / 100) / base - 1) * 100) for t, p in path if t >= e[0]]
    for i, (t, pct) in enumerate(q):
        nxt = q[min(i + 1, len(q) - 1)][1]
        if pct >= tp or pct <= -sl or t >= hold: return min(nxt, cap)
    return min(q[-1][1], cap)


def stats(res):
    if not res: return None
    return dict(n=len(res), mean=round(st.mean(res), 1), median=round(st.median(res), 1),
                win=round(100 * sum(y > 0 for y in res) / len(res)), zero=sum(y <= -80 for y in res))


def pick(q):
    rows = load()
    ser = [s for s in q.get("series", [""])[0].split(",") if s]
    days = [s for s in q.get("days", [""])[0].split(",") if s]
    h0, h1 = int(q.get("h0", ["0"])[0]), int(q.get("h1", ["23"])[0])
    b0, b1 = float(q.get("bmin", ["0"])[0]), float(q.get("bmax", ["1e9"])[0])
    return [r for r in rows if (not ser or r["series"] in ser) and (not days or r["day"] in days)
            and h0 <= r["hour"] <= h1 and b0 <= r["bnb0"] <= b1]


def params(q):
    g = lambda k, d: float(q.get(k, [d])[0])
    return dict(delay=g("delay", 30), tp=g("tp", 200), sl=g("sl", 50), hold=g("hold", 300), cap=g("cap", 300))


def api_meta(q):
    rows = load()
    c = defaultdict(int)
    for r in rows: c[r["series"]] += 1
    return dict(n=len(rows), series=sorted(c.items(), key=lambda kv: -kv[1]),
                days=sorted({r["day"] for r in rows}),
                updated=dt.datetime.fromtimestamp(DATA["mtime"]).strftime("%d.%m %H:%M"))


def api_run(q):
    rows, p = pick(q), params(q)
    days = sorted({r["day"] for r in rows})
    by = defaultdict(list)
    for r in rows: by[r["series"]].append(r)
    by = dict(sorted(by.items(), key=lambda kv: -len(kv[1])))
    by["ВСЕ выбранные"] = rows
    out, hist = [], [0] * 9
    edges = [-80, -50, -20, 0, 20, 50, 100, 200]
    for k, v in by.items():
        res, per = [], {}
        for d in days:
            rd = [y for y in (sim(r["path"], **p) for r in v if r["day"] == d) if y is not None]
            res += rd
            per[d] = stats(rd)
        rugs = [r["rug_s"] for r in v if r["rug_s"] is not None]
        out.append(dict(series=k, launches=len(v), rugs=len(rugs),
                        rug_med=round(st.median(rugs), 1) if rugs else None, all=stats(res), days=per))
        if k == "ВСЕ выбранные":
            for y in res: hist[sum(y > e for e in edges)] += 1
    return dict(days=days, rows=out, hist=hist, edges=edges)


def api_grid(q):
    rows = pick(q)
    p = params(q)
    delays = [5, 15, 30, 45, 60, 90, 120, 150]
    tps = [30, 50, 100, 150, 200, 300]
    days = sorted({r["day"] for r in rows})
    cells = []
    for d in delays:
        line = []
        for t in tps:
            pp = dict(p, delay=d, tp=t)
            byday = defaultdict(list)
            for r in rows:
                y = sim(r["path"], **pp)
                if y is not None: byday[r["day"]].append(y)
            res = [y for v in byday.values() for y in v]
            daymeans = [st.mean(byday[dd]) for dd in days if byday[dd]]
            s = stats(res)
            if s:
                s["plus_days"], s["ndays"] = sum(m > 0 for m in daymeans), len(daymeans)
                s["worst_day"] = round(min(daymeans), 1) if daymeans else None
            line.append(s)
        cells.append(line)
    return dict(delays=delays, tps=tps, cells=cells)


def api_path(q):
    # median price path of the selection, 0..300 s, for the chart
    rows = pick(q)
    buckets = defaultdict(list)
    for r in rows:
        last = None
        for t, p in r["path"]:
            b = int(t // 5) * 5
            if b != last: buckets[b].append(max(min(p, 500), -100)); last = b
    pts = []
    for b, v in sorted(buckets.items()):
        if b > 300: continue
        s = sorted(v)
        pts.append([b, round(st.median(s), 1), round(s[len(s) // 4], 1), round(s[3 * len(s) // 4], 1), len(s)])
    rug = defaultdict(int)
    for r in rows:
        if r["rug_s"] is not None and r["rug_s"] <= 300: rug[int(r["rug_s"] // 10) * 10] += 1
    return dict(pts=pts, rug=sorted(rug.items()), n=len(rows))


API = {"/api/meta": api_meta, "/api/run": api_run, "/api/grid": api_grid, "/api/path": api_path}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        pages = {"/": "hist_simple.html", "/expert": "hist_web.html"}
        if u.path in pages:
            with open(os.path.join(HERE, pages[u.path]), "rb") as fh:
                return self.send(200, fh.read(), "text/html; charset=utf-8")
        f = API.get(u.path)
        if not f: return self.send(404, b"not found", "text/plain")
        try:
            body = json.dumps(f(parse_qs(u.query)), ensure_ascii=False).encode()
            self.send(200, body, "application/json; charset=utf-8")
        except Exception as e:
            self.send(500, json.dumps({"error": str(e)}, ensure_ascii=False).encode(), "application/json")


if __name__ == "__main__":
    load()
    print(f"История: {len(DATA['rows'])} запусков. Открыть http://127.0.0.1:{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
