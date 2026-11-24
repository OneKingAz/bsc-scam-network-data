"""Is a signal seen in the mempool really better than one seen in a block? Every attempt of the signal test. Read-only.
Run: python analysis\\src_proof.py"""
import csv, os, random, statistics as st, sys
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
rows = [r for r in csv.DictReader(open(os.path.join(HERE, "signal_test.csv"), encoding="utf-8-sig"), delimiter=";") if r["pnl_bnb"] and r["source"] in ("мемпул", "блок")]
for r in rows:
    r["p"] = float(r["pnl_pct"])
    r["net"] = float(r["pnl_bnb"])
M, K = [r for r in rows if r["source"] == "мемпул"], [r for r in rows if r["source"] == "блок"]


def line(name, a, b):
    f = lambda v: f"n={len(v):>2} ср {st.mean(v):+6.1f}% мед {st.median(v):+6.1f}% в плюсе {sum(x > 0 for x in v):>2}" if v else "n= 0"
    print(f"{name:<26} мемпул {f([r['p'] for r in a])} | блок {f([r['p'] for r in b])}")


line("все", M, K)
for name, cond in (("до 09:00 (первый тест)", lambda r: r["time"][11:] < "09:00"), ("после 09:00", lambda r: r["time"][11:] >= "09:00")):
    line(name, [r for r in M if cond(r)], [r for r in K if cond(r)])
for k in ("1", "10", "25"):
    line(f"монеты {k} BNB", [r for r in M if r["kind"] == k], [r for r in K if r["kind"] == k])
print("\nвнутри одной монеты (где были оба вида):")
wins, both = 0, 0
for t in dict.fromkeys(r["token"] for r in rows):
    a, b = [r["p"] for r in M if r["token"] == t], [r["p"] for r in K if r["token"] == t]
    sym = next(r["symbol"] for r in rows if r["token"] == t)
    if a and b:
        both += 1
        wins += st.mean(a) > st.mean(b)
        print(f"  {sym:<10} мемпул {a} ср {st.mean(a):+.1f} | блок {b} ср {st.mean(b):+.1f}")
print(f"мемпул лучше на {wins} монетах из {both}")
# could the gap be chance? shuffle the labels; and without the three best attempts
all_p = [r["p"] for r in rows]
gap = st.mean(r["p"] for r in M) - st.mean(r["p"] for r in K)
random.seed(1)
hits = 0
for _ in range(20000):
    random.shuffle(all_p)
    hits += st.mean(all_p[:len(M)]) - st.mean(all_p[len(M):]) >= gap
print(f"\nразница средних {gap:+.1f} п.п.; случайно такая или больше: {hits / 20000 * 100:.1f}%")
top = sorted(rows, key=lambda r: -r["p"])[3:]
line("без 3 лучших попыток", [r for r in top if r["source"] == "мемпул"], [r for r in top if r["source"] == "блок"])
print(f"\nв деньгах, BNB: мемпул {sum(r['net'] for r in M):+.6f}, блок {sum(r['net'] for r in K):+.6f}")
