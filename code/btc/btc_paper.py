"""24.11: FORWARD paper test (no money, no keys) of the only BTC candidate from fut_bt.py: when the top traders' long/short
POSITION ratio is an extreme (rolling-24h z-score >= Z), take its side; hold H minutes. Every 5 min: public Binance futures data.
Rules: (Z 2.0, 60 min), (Z 2.5, 60 min), (Z 2.0, 240 min). One position per rule. Fee 0.04 % per round trip (maker).
Out: btc_paper.csv (each closed paper trade), btc_paper.log. Lock port 47672."""
import requests, time, csv, os, socket, statistics as st, sys
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__)); OUT = os.path.join(HERE, "btc_paper.csv"); LOG = os.path.join(HERE, "btc_paper.log")
B = "https://fapi.binance.com"; S = requests.Session(); FEE = 0.04
RULES = [(2.0, 60), (2.5, 60), (2.0, 240)]
def say(m):
    with open(LOG, "a", encoding="utf-8") as f: f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + m + "\n")
def get(path, **p):
    for _ in range(5):
        try:
            r = S.get(B + path, params=dict(symbol="BTCUSDT", **p), timeout=20).json()
            if r: return r
        except Exception: pass
        time.sleep(5)
def price(): return float(get("/fapi/v1/ticker/price")["price"])
lock = socket.socket()
try: lock.bind(("127.0.0.1", 47672)); lock.listen(1)
except OSError: sys.exit("уже работает")
say("=== СТАРТ бумажного теста BTC (лонг/шорт топ-трейдеров) ===")
pos = {r: None for r in RULES}
seen = set()
while True:
    try:
        hist = get("/futures/data/topLongShortPositionRatio", period="5m", limit=300)
        t, v = int(hist[-1]["timestamp"]), float(hist[-1]["longShortRatio"])
        win = [float(x["longShortRatio"]) for x in hist[-289:-1]]
        z = (v - st.mean(win)) / (st.pstdev(win) or 1e-12)
        now, p = time.time(), price()
        for r in RULES:
            Z, H = r; ps = pos[r]
            if ps and now >= ps["t_out"]:
                pnl = ps["side"] * (p / ps["p_in"] - 1) * 100 - FEE
                new = not os.path.exists(OUT)
                with open(OUT, "a", newline="", encoding="utf-8") as f:
                    w = csv.writer(f, delimiter=";")
                    if new: w.writerow(["rule", "opened", "closed", "side", "z", "p_in", "p_out", "pnl_pct_after_fee"])
                    w.writerow([f"z{Z}/{H}m", time.strftime("%Y-%m-%d %H:%M", time.localtime(ps["t_in"])), time.strftime("%Y-%m-%d %H:%M"),
                                "лонг" if ps["side"] > 0 else "шорт", round(ps["z"], 2), ps["p_in"], p, round(pnl, 4)])
                say(f"закрыл z{Z}/{H}м: {pnl:+.3f}%"); pos[r] = None
            if pos[r] is None and t not in seen and abs(z) >= Z:
                pos[r] = dict(t_in=now, t_out=now + H * 60, side=1 if z > 0 else -1, p_in=p, z=z)
                say(f"открыл z{Z}/{H}м: {'лонг' if z > 0 else 'шорт'} по {p}, z={z:+.2f}")
        seen.add(t)
    except Exception as e:
        say("ошибка: " + str(e)[:100])
    time.sleep(60)
