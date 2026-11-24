"""24.11: does a simple short-term BTC rule beat fees? Rules on 1-min closes, out of sample by thirds.
Fees per round trip: spot taker 0.2 %, futures taker 0.1 %, futures maker 0.04 %. Paper only."""
import csv, statistics as st, sys
sys.stdout.reconfigure(encoding="utf-8")
c = [float(r["c"]) for r in csv.DictReader(open("btc_1m.csv"))]
n = len(c)
def mv(k): a = sorted(abs(c[i + k] / c[i] - 1) for i in range(0, n - k, k)); return a[len(a) // 2] * 100
print(f"минут {n}; медианное движение: 1 мин {mv(1):.3f}%, 5 мин {mv(5):.3f}%, 15 мин {mv(15):.3f}%, 60 мин {mv(60):.3f}%")
best = []
for kind in ("откат", "импульс"):
    for N in (5, 15, 60):
        for X in (0.3, 0.6, 1.0, 1.5):
            for H in (5, 15, 60, 240):
                res = [[], [], []]; i = N
                while i < n - H:
                    ch = c[i] / c[i - N] - 1
                    sig = (-1 if ch >= X / 100 else 1 if ch <= -X / 100 else 0) if kind == "откат" else (1 if ch >= X / 100 else -1 if ch <= -X / 100 else 0)
                    if sig:
                        res[min(2, i * 3 // n)].append(sig * (c[i + H] / c[i] - 1) * 100)
                        i += H
                    else:
                        i += 1
                a = res[0] + res[1] + res[2]
                if len(a) >= 30:
                    best.append((st.mean(a), kind, N, X, H, len(a), [round(st.mean(r), 3) if r else 0 for r in res]))
best.sort(reverse=True)
print("\nЛУЧШИЕ 12 правил ДО комиссии (среднее % на сделку, лонг и шорт; по третям месяца):")
for b in best[:12]:
    print(f"  {b[0]:+.3f}%  {b[1]}: движение ≥{b[3]}% за {b[2]} мин → держать {b[4]} мин, сделок {b[5]}, трети {b[6]}")
print("\nкомиссия за круг: спот 0.20%, фьючерсы taker 0.10%, maker 0.04%")
for fee in (0.2, 0.1, 0.04):
    ok = [b for b in best if b[0] > fee and all(x > fee for x in b[6])]
    print(f"  правил в плюсе после комиссии {fee}% во ВСЕХ третях: {len(ok)} из {len(best)}" + (f"; лучшее {ok[0][1]} {ok[0][3]}%/{ok[0][2]}мин/{ok[0][4]}мин {ok[0][0]-fee:+.3f}%" if ok else ""))
