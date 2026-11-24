"""Waits for an event of the hop trader and exits, so that whoever started it in the background gets woken up. Read-only:
it asks the panel (GET /api/state) and counts the rows of hop_trades.csv, never presses anything, spends nothing.
Events: a new row in hop_trades.csv (a trade ended), the run stopped, the panel silent for 5 minutes.
Run: python analysis\\trade_notify.py   (--once prints the current state and exits)"""
import json, os, sys, time, urllib.request
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
TRADES = os.path.join(HERE, "hop_trades.csv")
STATE = "http://127.0.0.1:8773/api/state"
EVERY, SILENT = 5, 300


def rows():
    try:
        with open(TRADES, encoding="utf-8-sig") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def state():
    with urllib.request.urlopen(STATE, timeout=10) as r:
        return json.load(r)


def report(why, d=None):
    print("СОБЫТИЕ:", why)
    if d:
        s, c = d["state"], d["settings"]
        print(f"фаза={s['phase']} | {s['detail']} | running={s['running']} | сделано={s['done']} | баланс={s['balance']} | "
              f"итог запуска={s['run_pnl']} | застряло подряд={s['stuck_streak']} | монета={s['sym']} | сделок задано={c['trades']} | auto={c['auto']}")
        for line in [x for x in d["log"] if any(w in x for w in ("ИТОГ", "ОСТАНОВКА", "застряли", "ошибка", "куплено", "пропускаю"))][-6:]:
            print(line[:220])
    if os.path.exists(TRADES):
        with open(TRADES, encoding="utf-8-sig") as f:
            print("последняя запись:", f.readlines()[-1].strip())


if "--once" in sys.argv:
    report("разовая проверка", state())
    sys.exit(0)
n0, was_running, t_ok, d = rows(), None, time.time(), None
print("жду событие, строк в hop_trades.csv:", n0, flush=True)
while True:
    time.sleep(EVERY)
    try:
        d = state()
        t_ok = time.time()
    except Exception:
        if time.time() - t_ok > SILENT:
            report("панель не отвечает 5 минут")
            sys.exit(0)
        continue
    if rows() > n0:
        report("сделка закончилась", d)
        sys.exit(0)
    run = d["state"]["running"]
    if was_running and not run and not d["state"].get("manual"):
        report("работа остановилась", d)
        sys.exit(0)
    was_running = run
