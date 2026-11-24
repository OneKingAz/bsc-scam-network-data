"""24.11 (user allowed): Binance public archive, futures metrics BTCUSDT daily zips for the last N days -> metrics/ (csv only, read as data)."""
import requests, zipfile, io, os, sys, time, datetime
days = int(sys.argv[1]) if len(sys.argv) > 1 else 180
D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "metrics"); ok = miss = 0
for k in range(days, 0, -1):
    d = (datetime.date.today() - datetime.timedelta(days=k)).isoformat()
    out = os.path.join(D, f"BTCUSDT-metrics-{d}.csv")
    if os.path.exists(out): ok += 1; continue
    u = f"https://data.binance.vision/data/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-{d}.zip"
    try:
        r = requests.get(u, timeout=30)
        if r.status_code != 200: miss += 1; continue
        z = zipfile.ZipFile(io.BytesIO(r.content))
        names = [n for n in z.namelist() if n.endswith(".csv") and "/" not in n and ".." not in n]
        if not names: miss += 1; continue
        open(out, "wb").write(z.read(names[0])); ok += 1
    except Exception as e:
        miss += 1
    time.sleep(0.05)
print("файлов", ok, "нет", miss)
