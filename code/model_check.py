"""TZ stage 2: the exit table against the facts: every real sold trade whose coin is in gas_options_cache.json — what the
pool model gives for the same buy and sell moments against what was really received. Also: which launches the cache
leaves out. Offline. Run: python analysis\\model_check.py"""
import csv, datetime as dt, json, os, statistics as st, sys
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SPB, FEE = 0.45, 0.9975
TZ = dt.timezone(dt.timedelta(hours=6))
cache = json.load(open(os.path.join(HERE, "gas_options_cache.json"), encoding="utf-8"))
launches = list(csv.DictReader(open(os.path.join(HERE, "guard_launches.csv"), encoding="utf-8-sig"), delimiter=";"))
by_token = {l["token"].lower(): l for l in launches}
cache_l = {k.lower(): v for k, v in cache.items()}
print(f"запусков в журнале {len(launches)}, в кэше {len(cache)}: 25 BNB {sum(v.get('kind') == 25 for v in cache.values())}, "
      f"1 BNB {sum(v.get('kind') == 1 for v in cache.values())}, 10 BNB {sum(v.get('kind') == 10 for v in cache.values())}")
first = min(int(l["ts"]) for l in launches if l["token"] in cache)
later = [l for l in launches if int(l["ts"]) >= first]
miss = [l for l in later if l["token"] not in cache]
print(f"с первого запуска кэша {len(later)}, не попали в кэш (нет слива за ~37 мин или мало точек): {len(miss)}")
print("  ", [(l["symbol"], dt.datetime.fromtimestamp(int(l["ts"]), TZ).strftime("%m-%d %H:%M")) for l in miss[:30]])
print("\nсделка           тип  вход с  держал мин  модель%   факт%  разница")
diffs = []
for t in csv.DictReader(open(os.path.join(ROOT, "old_data", "analysis", "hop_trades.csv"), encoding="utf-8-sig"), delimiter=";"):
    got, stake = float(t["got_bnb"]), float(t["stake_bnb"])
    c, l = cache_l.get(t["token"].lower()), by_token.get(t["token"].lower())
    if not got or not c or not c.get("pts") or not l:
        continue
    end = dt.datetime.strptime(t["time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ).timestamp()
    t_sell, t_buy = end - 1, end - float(t["held_s"]) - 1  # the row is written right after the sale
    blk = lambda x: c["t0"] + (x - int(l["ts"])) / SPB
    at = lambda b: (lambda p: (p[1], p[1] / p[2]))(next((p for p in reversed(c["pts"]) if p[0] <= b), c["pts"][0]))
    b0, t0 = at(blk(t_buy))
    tok = stake * FEE * t0 / (b0 + stake * FEE)
    b1, t1 = at(blk(t_sell))
    model = (tok * FEE * b1 / (t1 + tok * FEE) / stake - 1) * 100
    real = (got / stake - 1) * 100
    diffs.append(real - model)
    print(f"{t['symbol']:<16} {c['kind']:>3}  {t_buy - int(l['ts']):6.0f}  {float(t['held_s']) / 60:9.1f}  {model:+7.1f}  {real:+7.1f}  {real - model:+7.1f}")
if diffs:
    print(f"разница факт − модель: средняя {st.mean(diffs):+.1f}, медиана {st.median(diffs):+.1f}, сделок {len(diffs)}")
