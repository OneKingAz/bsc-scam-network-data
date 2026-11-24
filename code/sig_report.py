"""Signal test report from a moment on: by coin type, payment size, source, age of the coin. Read-only.
Run: python analysis\\sig_report.py "2026-11-05 14:10:00" """
import csv, json, os, statistics as st, sys
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
since = sys.argv[1] if len(sys.argv) > 1 else "2026-11-05 14:10:00"
rows = [r for r in csv.DictReader(open(os.path.join(HERE, "signal_test.csv"), encoding="utf-8-sig"), delimiter=";") if r["time"] >= since and r["pnl_bnb"]]
launch = {l["token"].lower(): int(l["ts"]) for l in csv.DictReader(open(os.path.join(HERE, "guard_launches.csv"), encoding="utf-8-sig"), delimiter=";")}
import datetime as dt
for r in rows:
    r["got"], r["gas"], r["pnl"] = float(r["got_bnb"] or 0), float(r["gas_bnb"]), float(r["pnl_bnb"])
    r["stake"] = r["got"] - r["gas"] - r["pnl"]
    r["gross"] = (r["got"] / r["stake"] - 1) * 100
    r["net"] = float(r["pnl_pct"])
    r["share"] = float(r["share_pct"])
    r["age"] = (dt.datetime.strptime(r["time"], "%Y-%m-%d %H:%M:%S").timestamp() - launch.get(r["token"].lower(), 0)) / 60


def line(name, v):
    if v:
        print(f"{name:<24} n={len(v):>3} до газа ср {st.mean(x['gross'] for x in v):+6.1f}% мед {st.median(x['gross'] for x in v):+6.1f}% | итог ср {st.mean(x['net'] for x in v):+6.1f}% в плюсе {sum(x['pnl'] > 0 for x in v):>3} | {sum(x['pnl'] for x in v):+.6f} BNB")


print(f"попыток {len(rows)}, итог {sum(r['pnl'] for r in rows):+.6f} BNB, заблокировано {sum(not r['got'] for r in rows)}")
print("\nмонеты:")
for t in dict.fromkeys(r["token"] for r in rows):
    v = [r for r in rows if r["token"] == t]
    line(f"{v[0]['symbol']} ({v[0]['kind']} BNB)", v)
print("\nставка, BNB:")
for g in ((0, .0021), (.0021, .0031), (.0031, .0041), (.0041, 1)):
    line(f"{g[0]}–{g[1]}", [x for x in rows if g[0] <= x["stake"] < g[1]])
print("\nтип:")
for k in ("1", "10", "25"):
    line(f"{k} BNB", [r for r in rows if r["kind"] == k])
print("\nразмер платежа, % пула:")
for a, b in ((0, 1), (1, 2), (2, 3), (3, 5), (5, 10), (10, 20), (20, 1000)):
    line(f"{a}–{b}%", [r for r in rows if a <= r["share"] < b])
print("\nисточник:")
for s in ("мемпул", "блок"):
    line(s, [r for r in rows if r["source"] == s])
print("\nот отправки до блока, блоков:")
for g in sorted({int(r["land_block"]) - int(r["send_head"]) for r in rows if r["land_block"]}):
    line(str(g), [r for r in rows if r["land_block"] and int(r["land_block"]) - int(r["send_head"]) == g])
print("\nвозраст монеты, мин:")
for a, b in ((0, 3), (3, 8), (8, 15), (15, 30), (30, 60), (60, 1000)):
    line(f"{a}–{b}", [r for r in rows if a <= r["age"] < b])
worst = sorted(rows, key=lambda r: r["net"])[:5]
print("\nхудшие:", [(r["symbol"], r["net"], round(r["age"], 1)) for r in worst])
print("лучшие:", [(r["symbol"], r["net"], r["share"], r["source"]) for r in sorted(rows, key=lambda r: -r["net"])[:5]])
