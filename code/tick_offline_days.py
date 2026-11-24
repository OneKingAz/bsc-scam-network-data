"""23.11: offline check of «buy before the payment» for nets 1/2: does the creator's next payment land in the block we would predict?
Payments: signal_test{,2}.csv + signal_skip{,2}.csv (token, pay_block, share). Grid 5.00 s = 11.111 blocks (0.45 s).
For each large payment P (share>=3%) and tick k: predicted block = P + round(k*11.111); our buy would land <= pred-1.
hit = the next payment lands in [pred, pred+W] (after our buy, within W blocks); early = it lands in (P, pred-1] (before or with our buy -> bot broken, we lose fees).
Profit if hit: bot buys `paid` into the pool -> price x((pool+paid)/pool)^2; minus 0.6% fees and gas. Miss: -0.6% - gas."""
import csv, collections, sys
sys.stdout.reconfigure(encoding="utf-8")
GRID = 5.0 / 0.45
pays = {1: collections.defaultdict(dict), 2: collections.defaultdict(dict)}
for net, files in ((1, ("signal_test.csv", "signal_skip.csv")), (2, ("signal_test2.csv", "signal_skip2.csv"))):
    for fn in files:
        for r in csv.DictReader(open(fn, encoding="utf-8-sig"), delimiter=";"):
            try:
                b = int(r["pay_block"]); paid = float(r["bnb_paid"]); pool = float(r["pool_bnb"]); day = r["time"][:10]
            except Exception:
                continue
            if pool <= 0 or paid <= 0.001:
                continue
            pays[net][r["token"]][b] = (paid, pool, day)
FEE, GAS_PCT = 0.006, 0.002  # gas at a $5 stake ~0.2%
for net in (1,):
    print(f"\n=== СЕТЬ {net}: монет {len(pays[net])}, платежей {sum(len(v) for v in pays[net].values())}")
    for W in (1,):
        for ks in ((6,), (10,), (6, 10), (5, 6, 10)):
            stat = collections.defaultdict(lambda: [0, 0, 0, 0.0])  # tries, hits, early, pnl
            for tok, d in pays[net].items():
                blocks = sorted(d)
                for i, P in enumerate(blocks):
                    paid, pool, day = d[P]
                    if paid / pool < 0.03:
                        continue
                    nxt = blocks[i + 1:]
                    for k in ks:  # one attempt per tick; stop after the next payment arrives
                        pred = P + round(k * GRID)
                        s = stat[day]
                        first = next((b for b in nxt), None)
                        if first is not None and first < pred:  # already came before our buy for this tick
                            if first == pred - 1:
                                s[0] += 1; s[2] += 1; s[3] -= FEE + GAS_PCT
                            break
                        s[0] += 1
                        if first is not None and pred <= first <= pred + W:
                            p2, pl2, _ = d[first]
                            s[1] += 1; s[3] += ((pl2 + p2) / pl2) ** 2 - 1 - FEE - GAS_PCT
                            break
                        s[3] -= FEE + GAS_PCT
            parts = []
            for day in sorted(stat):
                t, h, e, p = stat[day]
                parts.append(f"{day}: попыток {t}, попал {h} ({h*100//max(t,1)}%), раньше {e}, итог {p*100/max(t,1):+.2f}%/попытку")
            print(f"  тики {ks} окно +{W}: " + " | ".join(parts))
