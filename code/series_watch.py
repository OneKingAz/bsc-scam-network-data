"""Watches the live (8765) and paper (8766) panels for series 50.0 / 17.02 / 55.02 launches: prints every log line about such a
token (passed / why skipped / bought / sold) and every buy or sell, once. Also appended to series_watch.log."""
import json, os, re, sys, time, urllib.request

sys.stdout.reconfigure(encoding="utf-8")
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "series_watch.log")
seen, syms, fails, diag = set(), set(), {8765: 0, 8766: 0}, {"HELL", "KRON"}
first = True


def out(msg):
    print(msg, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%m-%d ") + msg + "\n")


while True:
    for port, name in ((8765, "LIVE"), (8766, "ДЕМО")):
        try:
            s = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=10))
            fails[port] = 0
        except Exception:
            fails[port] += 1
            if fails[port] == 6:
                out(f"{name}: панель не отвечает ~2 мин")
            continue
        for line in s.get("log", []):
            line = str(line)
            if line in seen:
                continue
            seen.add(line)
            m = re.search(r"ручной запуск (.+?): серия кассы (?:50\.0|17\.02|55\.02)", line)
            if m:
                syms.add(m.group(1))
            hit = any(f" {x}" in line for x in syms) or (("ПОКУПКА" in line or "ПРОДАЖА" in line or "СЕРИЯ " in line or "ТЕСТ B" in line or "сторож" in line) and "KRON" not in line)
            if "Работаю:" in line or "пробная продажа не проходит" in line:
                continue
            d = re.search(r"РАЗБОР ПРОДАЖИ (.+?):", line)
            if d:
                if d.group(1) in diag:
                    continue
                diag.add(d.group(1))
            if hit and not first:
                out(f"{name} {line}")
    first = False
    time.sleep(20)
