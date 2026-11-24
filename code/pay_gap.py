"""19.11 (user): how much time is there between the moment our p2p listener sees a creator's payment and the block that includes it?
Creators' payments = the bot's signal rows (signal_test*/signal_skip*) matched to the listener's p2p_firsts.csv by value and time.
For each: block number by the tx hash, the block's ms time; gap = block ms - first seen ms (corrected by the chain clock offset).
Usage: python pay_gap.py [since]   Out: pay_gap.txt"""
import csv, glob, json, os, sys, time, urllib.request
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
since = sys.argv[1] if len(sys.argv) > 1 else "2000-01-01 00:00:00"
RPC = ["https://bsc-dataseed.bnbchain.org", "https://rpc-bsc.48.club"]


def call(m, p):
    for u in RPC:
        try:
            r = json.loads(urllib.request.urlopen(urllib.request.Request(u, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": m, "params": p}).encode(),
                                                                         headers={"Content-Type": "application/json"}), timeout=10).read())
            if r.get("result") is not None:
                return r["result"]
        except Exception:
            pass


def bms(b):
    x = int(b["mixHash"][-16:], 16) if b.get("mixHash") else 0
    return int(b["timestamp"], 16) * 1000 + (x if x < 1000 else 0)


off = []  # chain ms - local ms, least delayed read
for _ in range(15):
    b = call("eth_getBlockByNumber", ["latest", False]); t = time.time() * 1000
    off.append(bms(b) - t)
    time.sleep(0.2)
CO = max(off)
rows = list(csv.DictReader(open(r"D:\bot\p2p_listener\p2p_firsts.csv", encoding="utf-8"), delimiter=";"))
by_val = {}
for r in rows:
    try:
        by_val.setdefault(round(int(r["value_wei"]) / 1e18, 5), []).append(r)
    except Exception:
        pass
pays = []
for p in glob.glob(os.path.join(HERE, "signal_test*.csv")) + glob.glob(os.path.join(HERE, "signal_skip*.csv")):
    net = os.path.basename(p).replace("signal_test", "").replace("signal_skip", "").replace(".csv", "") or "1"
    for r in csv.DictReader(open(p, encoding="utf-8-sig"), delimiter=";"):
        if r.get("time", "") >= since and r.get("bnb_paid"):
            try:
                pays.append((datetime.strptime(r["time"], "%Y-%m-%d %H:%M:%S").timestamp() * 1000, round(float(r["bnb_paid"]), 5), net, r.get("symbol", "")))
            except Exception:
                pass
gaps, seen_h = [], set()
for t, v, net, sym in pays:
    c = [r for r in by_val.get(v, []) if abs(int(r["first_ms"]) - t) < 60000 and r["hash"] not in seen_h]
    if not c:
        continue
    r = min(c, key=lambda r: abs(int(r["first_ms"]) - t))
    seen_h.add(r["hash"])
    tx = call("eth_getTransactionByHash", [r["hash"]])
    if not tx or not tx.get("blockNumber"):
        continue
    b = call("eth_getBlockByNumber", [tx["blockNumber"], False])
    gap = bms(b) - (int(r["first_ms"]) + CO)
    gaps.append((gap, net, sym, int(tx["blockNumber"], 16), int(tx["transactionIndex"], 16), len(b["transactions"]), r["first_peer"]))
lines = [f"ЗАПАС ДО БЛОКА ПО ПЛАТЕЖАМ СОЗДАТЕЛЕЙ (с {since}): сопоставлено {len(gaps)}; сдвиг часов цепочки {CO:+.0f} мс", ""]
if gaps:
    g = sorted(x[0] for x in gaps)
    q = lambda p: g[min(len(g) - 1, int(p * len(g)))]
    lines.append(f"запас, мс: минимум {g[0]:.0f}, 10% {q(0.1):.0f}, медиана {q(0.5):.0f}, 90% {q(0.9):.0f}, максимум {g[-1]:.0f}")
    for lim in (0, 200, 300, 450, 700, 1000):
        lines.append(f"  запас больше {lim} мс: {sum(1 for x in g if x > lim)} из {len(g)}")
    lines.append("")
    lines.append("место платежа в блоке (номер транзакции / всего): " + ", ".join(f"{x[4]}/{x[5]}" for x in gaps[:30]))
    lines.append("")
    for x in sorted(gaps)[-10:]:
        lines.append(f"  {x[0]:6.0f} мс  сеть {x[1]} {x[2]} блок {x[3]} поз. {x[4]}/{x[5]} первый узел {x[6]}")
out = "\n".join(lines)
print(out)
open(os.path.join(HERE, "pay_gap.txt"), "w", encoding="utf-8").write(out)
