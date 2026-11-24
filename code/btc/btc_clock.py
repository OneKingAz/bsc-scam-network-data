"""24.11: bots on a clock leave marks in time: volume and moves by minute of hour, hour of day (UTC), funding times 00/08/16 UTC.
If a minute is systematically up/down, a scheduled bot is visible (and maybe usable). 30 days of 1-min candles, by thirds."""
import csv, statistics as st, sys, collections, time
sys.stdout.reconfigure(encoding="utf-8")
R = [(int(r["t"]) // 1000, float(r["o"]), float(r["c"]), float(r["v"])) for r in csv.DictReader(open("btc_1m.csv"))]
n = len(R)
def by(key, name, top=6):
    d = collections.defaultdict(lambda: [[], [], [], []])  # ret thirds x3, volume
    for i, (t, o, c, v) in enumerate(R):
        k = key(t); d[k][min(2, i * 3 // n)].append((c / o - 1) * 100); d[k][3].append(v)
    vall = st.mean(x[3] for x in R)
    rows = []
    for k, (a, b, c3, vv) in d.items():
        m = [st.mean(x) for x in (a, b, c3)]
        rows.append((k, st.mean(a + b + c3), m, st.mean(vv) / vall, sum(abs(x) for x in a + b + c3) / len(a + b + c3)))
    print(f"\n{name}: по объёму (×от среднего) самые активные:")
    for r in sorted(rows, key=lambda r: -r[3])[:top]:
        print(f"  {r[0]}: объём ×{r[3]:.2f}, средний ход {r[1]:+.4f}%, по третям {[round(x, 4) for x in r[2]]}, размах {r[4]:.3f}%")
    same = [r for r in rows if all(x > 0.01 for x in r[2]) or all(x < -0.01 for x in r[2])]
    print(f"{name}: с одинаковым знаком во всех третях и ≥0.01%: " + (", ".join(f"{r[0]} {r[1]:+.4f}%" for r in sorted(same, key=lambda r: -abs(r[1]))[:8]) or "нет"))
by(lambda t: time.gmtime(t).tm_min, "МИНУТА ЧАСА")
by(lambda t: time.gmtime(t).tm_hour, "ЧАС СУТОК (UTC)")
by(lambda t: (time.gmtime(t).tm_hour, time.gmtime(t).tm_min) if time.gmtime(t).tm_hour in (0, 8, 16) and time.gmtime(t).tm_min < 3 or time.gmtime(t).tm_hour in (23, 7, 15) and time.gmtime(t).tm_min > 56 else None, "ВОКРУГ FUNDING 00/08/16 UTC", 10)
