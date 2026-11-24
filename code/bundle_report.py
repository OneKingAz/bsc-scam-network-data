"""19.11: the report of the bundle test (run_bundle_test.ps1). Reads bundle_test.csv and the signal_skip*.csv rows since --since.
Usage: python bundle_report.py --since "2026-11-19 18:40:00" [--usd 590] [--short]"""
import argparse, csv, glob, os, sys
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--since", default="2000-01-01 00:00:00")
ap.add_argument("--usd", type=float, default=590.0)
ap.add_argument("--short", action="store_true")
a = ap.parse_args()


def rows(path):
    try:
        return [r for r in csv.DictReader(open(path, encoding="utf-8-sig"), delimiter=";") if r.get("time", "") >= a.since]
    except Exception:
        return []


B = rows(os.path.join(HERE, "bundle_test.csv"))
f = lambda r, k: float(r.get(k) or 0)
block_only = Counter()
for p in glob.glob(os.path.join(HERE, "signal_skip*.csv")):
    n = os.path.basename(p)[len("signal_skip"):-4] or "1"
    for r in rows(p):
        if (r.get("note") or r.get("why") or "").startswith("тест пакетов"):
            block_only[n] += 1

landed = [r for r in B if r["result"] == "попал"]
sold = [r for r in landed if f(r, "got_bnb") > 0]
pnl = sum(f(r, "pnl_bnb") for r in B)
gas = sum(f(r, "gas_bnb") for r in B)
bg = [f(r, "pnl_before_gas_pct") for r in sold if r.get("pnl_before_gas_pct")]
same = sum(1 for r in landed if r.get("pay_block") and r.get("pay_block") == r.get("land_block"))

if a.short:
    print(f"пакетов {len(B)}, попало {len(landed)}" + (f" ({len(landed) / len(B):.0%})" if B else "") + f", в блоке платежа {same}, бот купил {sum(1 for r in landed if r['bot_block'])}, "
          f"продано {len(sold)}, до газа {sum(bg) / len(bg) if bg else 0:+.1f}%, итог {pnl:+.8f} BNB ({pnl * a.usd:+.3f} $); платежей только в блоке: {sum(block_only.values())}")
    sys.exit()

print("=" * 78)
print(f"ИТОГ ТЕСТА ПАКЕТОВ с {a.since}")
print("=" * 78)
print(f"Платежей создателей, видимых только в блоке (пакет невозможен): {sum(block_only.values())}  " + ", ".join(f"сеть {k}: {v}" for k, v in sorted(block_only.items())))
print(f"Пакетов отправлено: {len(B)}   попало в блок: {len(landed)}" + (f" ({len(landed) / len(B):.0%})" if B else ""))
print(f"  из попавших — в том же блоке, что платёж: {same}; бот купил после нас: {sum(1 for r in landed if r['bot_block'])}; продано: {len(sold)}; не продано: {len(landed) - len(sold)}")
if bg:
    print(f"  результат до газа: средний {sum(bg) / len(bg):+.2f}%, в плюсе {sum(1 for x in bg if x > 0)} из {len(bg)}, лучший {max(bg):+.1f}%, худший {min(bg):+.1f}%")
print(f"Газ всего: {gas:.8f} BNB ({gas * a.usd:.3f} $).  ИТОГ с газом: {pnl:+.8f} BNB ({pnl * a.usd:+.3f} $)")
print()
print("По сетям:")
by = defaultdict(list)
for r in B:
    by[r["net"]].append(r)
for n in sorted(by, key=lambda x: int(x) if x.isdigit() else 99):
    rs = by[n]
    ld = [r for r in rs if r["result"] == "попал"]
    sd = [r for r in ld if f(r, "got_bnb") > 0]
    b2 = [f(r, "pnl_before_gas_pct") for r in sd if r.get("pnl_before_gas_pct")]
    print(f"  сеть {n}: пакетов {len(rs)}, попало {len(ld)} ({len(ld) / len(rs):.0%}), бот купил {sum(1 for r in ld if r['bot_block'])}, "
          f"до газа {sum(b2) / len(b2) if b2 else 0:+.1f}%, итог {sum(f(r, 'pnl_bnb') for r in rs):+.8f} BNB; только в блоке: {block_only.get(n, 0)}")
miss = [r for r in B if r["result"] != "попал"]
if miss:
    print()
    print("Почему пакеты не попали (ответы строителей):")
    for k, v in Counter(r.get("builders", "") for r in miss).most_common(6):
        print(f"  {v:4d}  {k}")
    ages = sorted(int(r["seen_age_ms"]) for r in miss if r.get("seen_age_ms"))
    if ages:
        print(f"  от того, как увидели платёж, до отправки пакета: медиана {ages[len(ages) // 2]} мс")
    gap = Counter()
    for r in miss:
        try:
            gap[int(r["pay_block"]) - int(r["send_head"])] += 1
        except Exception:
            gap["?"] += 1
    print("  блок платежа минус голова при отправке:", dict(sorted(gap.items(), key=lambda x: str(x[0]))))
if landed:
    print()
    print("Последние попавшие пакеты:")
    for r in landed[-10:]:
        print(f"  {r['time'][11:]} сеть {r['net']} {r['symbol']:10} платёж {r['share_pct']}% · {r.get('note') or ''} · бот {'да' if r['bot_block'] else 'нет'} · "
              f"{(r['pnl_before_gas_pct'] + '% до газа') if r.get('pnl_before_gas_pct') else 'не продано'}")
