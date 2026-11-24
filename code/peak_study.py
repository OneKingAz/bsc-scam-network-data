"""User's idea (05.11): the network drains a coin when the pool reaches a certain sum. Offline, gas_options_cache.json
(1- and 10-BNB coins): how tight is the pool's peak before the drain, how does it split into levels, is it stable by day,
and what does an exit "pool >= X BNB" give against the current rules; X is chosen on the first half of the days and
tested on the second. Run: python analysis\\peak_study.py"""
import json, os, statistics as st, sys
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
SPB, FEE, LAG, SILENCE, ENTRY, STAKE = 0.45, 0.9975, 7, 50, 36, 0.005
BOT_MIN, BACKSTOP = {1: 7, 10: 24}, {1: 25 * 60, 10: 30 * 60}
G_MIN, G_FIX = 0.000145, 0.00006
coins = [c for c in json.load(open(os.path.join(HERE, "gas_options_cache.json"), encoding="utf-8")).values()
         if c.get("kind") in (1, 10) and c.get("pts")]


def at(c, blk):
    p = next((p for p in reversed(c["pts"]) if p[0] <= blk), c["pts"][0])
    return p[1], p[1] / p[2]


def peak_before(c):
    return max(p[1] for p in c["pts"] if p[0] < c["drain"])


def sim(c, tp=None, level=None):
    """% after gas: the current exits (tp, bots + silence, pool -25%, backstop) plus 'pool >= level'."""
    e = c["t0"] + ENTRY / SPB
    if e >= c["drain"]:
        return None
    b0, t0 = at(c, e)
    tok = STAKE * FEE * t0 / (b0 + STAKE * FEE)
    value = lambda blk: (lambda b, t: tok * FEE * b / (t + tok * FEE))(*at(c, blk))
    bot, buys, kind = None, c["buys"], c["kind"]
    for k in range(BOT_MIN[kind] - 1, len(buys)):
        q = (buys[k + 1] if k + 1 < len(buys) else c["drain"]) - buys[k]
        if q * SPB >= SILENCE and buys[k] + SILENCE / SPB > e + 60 / SPB:
            bot = buys[k] + SILENCE / SPB
            break
    limit = c["t0"] + BACKSTOP[kind] / SPB
    x, why = (bot, "боты") if bot and bot < limit else (limit, "предел")
    peak = b0
    for p in c["pts"]:
        if p[0] <= e:
            continue
        if p[0] >= x:
            break
        peak = max(peak, p[1])
        if level is not None and p[1] >= level:
            x, why = p[0], "уровень"
            break
        if tp is not None and value(p[0]) >= STAKE * (1 + tp / 100):
            x, why = p[0], "цель"
            break
        if p[1] < peak * 0.75:
            x, why = p[0], "падение"
            break
    mins = (min(x, c["drain"]) - e) * SPB / 60
    gross = -100.0 if x + LAG >= c["drain"] else (value(x + LAG) / STAKE - 1) * 100
    return gross - (G_FIX + G_MIN * mins) / STAKE * 100, mins, "слив раньше" if gross == -100 else why


def line(name, rs):
    rs = [r for r in rs if r]
    ends = {}
    for r in rs:
        ends[r[2]] = ends.get(r[2], 0) + 1
    print(f"    {name:<22} среднее {st.mean(r[0] for r in rs):+6.0f}%  медиана {st.median(r[0] for r in rs):+6.0f}%  "
          f"в плюсе {sum(r[0] > 0 for r in rs)}/{len(rs)}  мин {st.mean(r[1] for r in rs):5.1f}  {ends}")


for kind in (1, 10):
    cs = sorted((c for c in coins if c["kind"] == kind), key=lambda c: c["t0"])
    peaks = [peak_before(c) for c in cs]
    q = st.quantiles(peaks, n=20)
    print(f"\n=== {kind} BNB, монет {len(cs)}: пул перед сливом")
    print(f"  мин {min(peaks):.2f}, 5% {q[0]:.2f}, 25% {q[4]:.2f}, медиана {st.median(peaks):.2f}, 75% {q[14]:.2f}, 95% {q[18]:.2f}, макс {max(peaks):.2f}; "
          f"разброс (откл/среднее) {st.pstdev(peaks) / st.mean(peaks) * 100:.0f}%")
    w = 0.25 if kind == 1 else 1.0
    bins = {}
    for p in peaks:
        bins[int(p / w)] = bins.get(int(p / w), 0) + 1
    print("  по уровням: " + "  ".join(f"{b * w:g}–{(b + 1) * w:g}:{n}" for b, n in sorted(bins.items())))
    print("  по дням (медиана пика, мин–макс, покупок ботов, минута слива):")
    days = {}
    for c, p in zip(cs, peaks):
        days.setdefault(c["time"][:5], []).append((p, c))
    for d, v in sorted(days.items()):
        print(f"    {d}: {len(v):2} монет  {st.median(x[0] for x in v):6.2f}  {min(x[0] for x in v):6.2f}–{max(x[0] for x in v):6.2f}  "
              f"покупок {st.median(len(x[1]['buys']) for x in v):4.0f}  слив {st.median((x[1]['drain'] - x[1]['t0']) * SPB / 60 for x in v):5.1f} мин")
    n = [len(c["buys"]) for c in cs]
    mins = [(c["drain"] - c["t0"]) * SPB / 60 for c in cs]
    added = [p - c["pts"][0][1] for p, c in zip(peaks, cs)]
    print(f"  занесено ботами до пика: медиана {st.median(added):.2f} BNB; покупок медиана {st.median(n):.0f} ({min(n)}–{max(n)}); слив медиана {st.median(mins):.1f} мин ({min(mins):.1f}–{max(mins):.1f})")
    # the level the pool stood at for the last minute before the drain: how long before the drain is the peak reached?
    lead = []
    for c, p in zip(cs, peaks):
        hit = next(x[0] for x in c["pts"] if x[1] >= p * 0.98)
        lead.append((c["drain"] - hit) * SPB / 60)
    print(f"  пик (98%) достигается за {st.median(lead):.1f} мин до слива (5% {st.quantiles(lead, n=20)[0]:.1f}, 95% {st.quantiles(lead, n=20)[18]:.1f})")
    print("  выход «пул дошёл до уровня», ставка 0.005, после газа, все монеты:")
    line("сейчас (+100%)", [sim(c, 100) for c in cs])
    line("без цели", [sim(c, None) for c in cs])
    grid = sorted({round(q[i] / w) * w for i in (0, 2, 4, 7, 9, 12, 14, 16, 18)})
    for lv in grid:
        line(f"уровень {lv:g} BNB", [sim(c, None, lv) for c in cs])
    half = len(cs) // 2
    train, test = cs[:half], cs[half:]
    best = max(grid, key=lambda lv: st.mean(r[0] for r in (sim(c, None, lv) for c in train) if r))
    print(f"  проверка вне выборки: лучший уровень на 1-й половине = {best:g} BNB; на 2-й половине:")
    line(f"уровень {best:g}", [sim(c, None, best) for c in test])
    line("+100%", [sim(c, 100) for c in test])
    line("без цели", [sim(c, None) for c in test])
