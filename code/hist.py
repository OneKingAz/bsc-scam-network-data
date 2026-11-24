"""History of manual launches from the chain (NodeReal): launches >= MIN_BNB in WBNB pools, price path 300 s from Sync,
pull time, creator's cash desk and series. Resumable. Usage: python hist.py <days> <end_block> ; then report."""
import json, os, sys, time, statistics as st
from collections import defaultdict
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
KEY = ""
for l in open(os.path.join(HERE, "..", ".env"), encoding="utf-8-sig"):
    if l.strip().startswith("NODEREAL_KEY="):
        KEY = l.split("=", 1)[1].strip().strip("\"'")
URL = "https://bsc-mainnet.nodereal.io/v1/" + KEY
FACTORY = "0xca143ce32fe78f1f7019d7d551a6402fc5350c73"
WBNB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
T_PAIR = "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"
T_SYNC = "0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1"
MIN_BNB = 8
CHUNK = 2000
TRACK = 900  # blocks after creation to look at (~400 s)
OUT = os.path.join(HERE, "hist_launches.jsonl")
DONE = os.path.join(HERE, "hist_done.txt")
SERIES = [17.02, 40.02, 55.02, 12.29, 58.01, 17.84, 50.0, 9.58]


def rpc(method, params, pause=0.3):
    for i in range(8):
        try:
            r = requests.post(URL, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=60).json()
            time.sleep(pause)
            if "error" in r:
                msg = str(r["error"])
                if "limit" in msg.lower() or "rate" in msg.lower() or "429" in msg:
                    time.sleep(5 * (i + 1)); continue
                raise RuntimeError(msg[:200])
            return r["result"]
        except requests.RequestException:
            time.sleep(5 * (i + 1))
    raise RuntimeError("rpc failed " + method)


def logs(addr, topic, a, b):
    """getLogs, splitting the range or the address list when the node refuses a big answer."""
    try:
        return rpc("eth_getLogs", [{"address": addr, "topics": [topic], "fromBlock": hex(a), "toBlock": hex(b)}])
    except RuntimeError:
        if isinstance(addr, list) and len(addr) > 1:
            h = len(addr) // 2
            return logs(addr[:h], topic, a, b) + logs(addr[h:], topic, a, b)
        if b - a > 50:
            m = (a + b) // 2
            return logs(addr, topic, a, m) + logs(addr, topic, m + 1, b)
        raise


def ts(n):
    return int(rpc("eth_getBlockByNumber", [hex(n), False])["timestamp"], 16)


def funder(creator, b0):
    r = rpc("nr_getAssetTransfers", [{"category": ["external"], "toAddress": creator, "fromBlock": hex(b0 - 3000),
                                      "toBlock": hex(b0), "maxCount": "0x32", "excludeZeroValue": True}], pause=1.8)
    tr = [t for t in (r or {}).get("transfers", []) if int(t["value"], 16) >= 10**18]
    if not tr:
        return "", 0.0
    t = max(tr, key=lambda t: int(t["blockNum"], 16))
    return t["from"].lower(), int(t["value"], 16) / 1e18


def series(bnb, pool):
    for s in SERIES:
        if abs(bnb - s) < 0.03:
            return str(s)
    if not bnb:
        return "нет кассы" if pool >= 9 else "<9 BNB"
    return "копии17.6" if 17.4 <= pool <= 17.8 else "прочие"


def download(days, end):
    start = end - int(days * 86400 / 0.45)
    done = set(open(DONE).read().split()) if os.path.exists(DONE) else set()
    for a in range(start, end, CHUNK):
        if str(a) in done:
            continue
        b = min(a + CHUNK - 1, end)
        pairs = {}
        for l in logs(FACTORY, T_PAIR, a, b):
            t0, t1 = "0x" + l["topics"][1][-40:], "0x" + l["topics"][2][-40:]
            if WBNB in (t0, t1):
                pairs["0x" + l["data"][26:66]] = (t1 if t0 == WBNB else t0, t0 == WBNB)
        ta, tb = ts(a), ts(b)
        spb = (tb - ta) / max(1, b - a)
        syncs = defaultdict(list)
        if pairs:
            for l in logs(list(pairs), T_SYNC, a, b + TRACK):
                syncs[l["address"].lower()].append(l)
        rows = []
        for pair, (token, bnb_is0) in pairs.items():
            pts = []
            for l in sorted(syncs.get(pair, []), key=lambda l: (int(l["blockNumber"], 16), int(l["logIndex"], 16))):
                r0, r1 = int(l["data"][2:66], 16), int(l["data"][66:130], 16)
                rb, rt = (r0, r1) if bnb_is0 else (r1, r0)
                pts.append((int(l["blockNumber"], 16), rb, rt, l["transactionHash"]))
            k = next((i for i, p in enumerate(pts) if p[1] >= MIN_BNB * 10**18 and p[2] > 0), None)
            if k is None:
                continue
            b0, rb0, rt0, h0 = pts[k]
            price0 = rb0 / rt0
            path, rug, mx = [], None, rb0
            for blk, rb, rt, _ in pts[k:]:
                t = round((blk - b0) * spb, 1)
                if t > 300:
                    break
                mx = max(mx, rb)
                if rug is None and rb < 0.2 * mx:
                    rug = t
                path.append([t, round(((rb / rt) / price0 - 1) * 100 - 1.0, 2) if rt else -100])
            creator, f, fb = "", "", 0.0
            if rb0 >= 9 * 10**18:  # pools under 9 BNB almost never had a cash desk; saves the slow 250-CU call
                try:
                    creator = rpc("eth_getTransactionByHash", [h0])["from"].lower()
                    f, fb = funder(creator, b0)
                except RuntimeError:
                    pass
            rows.append(dict(pair=pair, token=token, block=b0, time=int(ta + (b0 - a) * spb), bnb0=round(rb0 / 1e18, 2),
                             creator=creator, funder=f, funder_bnb=round(fb, 3), series=series(fb, rb0 / 1e18),
                             rug_s=rug, path=path))
        with open(OUT, "a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        with open(DONE, "a") as fh:
            fh.write(f"{a}\n")
        print(time.strftime("%H:%M:%S"), f"блоки {a}: пар {len(pairs)}, запусков {len(rows)}", flush=True)


def sim(path, delay=30, tp=200, sl=50, hold=300):
    e = next(((t, p) for t, p in path if t >= delay and p > -90), None)
    if e is None or e[1] < -10: return None
    base = 1 + e[1] / 100
    q = [(t - e[0], ((1 + p / 100) / base - 1) * 100) for t, p in path if t >= e[0]]
    for i, (t, pct) in enumerate(q):
        nxt = q[min(i + 1, len(q) - 1)][1]
        if pct >= tp or pct <= -sl or t >= hold: return nxt
    return q[-1][1]


STRATS = {"30с/TP200": dict(delay=30, tp=200, sl=50, hold=300), "5→55с": dict(delay=5, tp=200, sl=50, hold=50),
          "20→60с": dict(delay=20, tp=200, sl=50, hold=40), "75с/TP100": dict(delay=75, tp=100, sl=50, hold=120),
          "150с/90с": dict(delay=150, tp=100, sl=30, hold=90), "90с/TP200": dict(delay=90, tp=200, sl=50, hold=300)}


def report():
    import datetime as dt
    rows = [json.loads(l) for l in open(OUT, encoding="utf-8")]
    out = [f"Запусков {len(rows)}, с кассой {sum(1 for r in rows if r['funder'])}. "
           "Каждая ячейка: среднее % на сделку (обнулений/сделок); по дням — среднее % за день."]
    G = defaultdict(list)
    for r in rows:
        G[r["series"]].append(r)
    G["ВСЕ"] = rows
    for k, v in sorted(G.items(), key=lambda kv: -len(kv[1])):
        if not v:
            continue
        rugs = [r["rug_s"] for r in v if r["rug_s"] is not None]
        out.append(f"\n== {k}: n={len(v)}, изъято {len(rugs)}, когда медиана "
                   f"{(st.median(rugs) if rugs else '-')} с, пул медиана {st.median(r['bnb0'] for r in v)} BNB")
        days = sorted({dt.datetime.fromtimestamp(r["time"]).strftime("%m-%d") for r in v})
        for nm, p in STRATS.items():
            res = [y for y in (sim(r["path"], **p) for r in v) if y is not None]
            if not res:
                continue
            per = []
            for d in days:
                rd = [y for y in (sim(r["path"], **p) for r in v
                                  if dt.datetime.fromtimestamp(r["time"]).strftime("%m-%d") == d) if y is not None]
                per.append(f"{d}:{st.mean(rd):+.0f}" if rd else f"{d}:-")
            plus_days = sum(1 for x in per if x.split(":")[1] not in "-" and float(x.split(":")[1]) > 0)
            out.append(f"  {nm:10} {st.mean(res):+.1f}% ({sum(y <= -80 for y in res)}/{len(res)}) "
                       f"дней в плюсе {plus_days}/{len(days)} | " + " ".join(per))
    txt = "\n".join(out)
    open(os.path.join(HERE, "hist_report.txt"), "w", encoding="utf-8").write(txt)
    print(txt)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if sys.argv[1] == "report":
        report()
    else:
        download(float(sys.argv[1]), int(sys.argv[2]))
        report()
