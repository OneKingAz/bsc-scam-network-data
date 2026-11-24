"""After the signal test: were our wallets marked, how close was it, did the bots go on buying? Read-only.
Run: python analysis\\signal_review.py"""
import csv, json, os, statistics as st, sys, time, urllib.request
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
rows = list(csv.DictReader(open(os.path.join(HERE, "signal_test.csv"), encoding="utf-8-sig"), delimiter=";"))
used = json.load(open(os.path.join(HERE, "hop_used.json"), encoding="utf-8"))
addr = {}
for l in open(os.path.join(ROOT, "hop_wallets.txt"), encoding="utf-8-sig"):
    x = l.strip().split(";")
    if len(x) >= 2:
        addr[f"W{x[0]}"] = x[1].lower()
marks = {}
for r in csv.DictReader(open(os.path.join(HERE, "guard_marks.csv"), encoding="utf-8-sig"), delimiter=";"):
    if r["method"] == "095ea7b3":
        marks.setdefault((r["token"].lower(), r["wallet"].lower()), int(r["block"]))
print("попытка, монета: куплено, продажа, блоков держали, метка (блоков после покупки)")
hold, marked = [], 0
per = {}
for r in rows:
    land, sell = int(r["land_block"] or 0), int(r["sell_block"] or 0)
    t = r["token"].lower()
    per.setdefault(t, []).append(r)
    hold.append(sell - land)
for t, rs in per.items():
    ws = used.get(t, [])
    m = sorted(marks[(t, addr[w])] for w in ws if w in addr and (t, addr[w]) in marks)
    lands = sorted(int(r["land_block"]) for r in rs)
    print(f"{rs[0]['symbol']}: попыток {len(rs)}, кошельков {len(ws)}, помечено {len(m)}; покупки {lands}; метки {m}")
    for b in m:
        prev = [x for x in lands if x <= b]
        if prev:
            r = next(x for x in rs if int(x["land_block"]) == prev[-1])
            print(f"   метка через {b - prev[-1]} блоков после покупки в {prev[-1]}, продажа была в {r['sell_block']} ({int(r['sell_block']) - prev[-1]} бл.)" + (" — ПОСЛЕ продажи" if b > int(r["sell_block"]) else " — ДО продажи!"))
print(f"держали монету, блоков: медиана {st.median(hold)}, макс {max(hold)} (с: {st.median(hold) * .45:.1f}, {max(hold) * .45:.1f})")
lag = [int(r["land_block"]) - int(r["send_head"]) for r in rows if r["land_block"]]
print("от отправки до попадания, блоков:", sorted(lag), "медиана", st.median(lag))
pn = [float(r["pnl_pct"]) for r in rows]
print(f"итог: среднее {st.mean(pn):+.1f}%, медиана {st.median(pn):+.1f}%, в плюсе {sum(x > 0 for x in pn)}/{len(pn)}, на монетах 1 BNB среднее {st.mean(float(r['pnl_pct']) for r in rows if r['kind'] == '1'):+.1f}%")
