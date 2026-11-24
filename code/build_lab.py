"""Rebuilds bot_lab.py from bot.py: same code, own files (state_lab.json, trades_series_lab.csv), panel config in lab\\,
lock 47655, never live. Run after every bot.py change: python analysis\\build_lab.py"""
import os

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
s = open(os.path.join(ROOT, "bot.py"), "rb").read().decode("utf-8")
o1 = 'TRADES_FILE = os.path.join(HERE, "trades.csv")\r\n'
n1 = o1 + ('# ---- LAB (this file only, 27.10): paper copy of bot.py for testing other cash-desk series next to the demo.\r\n'
           '# Own files, never the live wallet; run with --paper --series ... (run_lab.cmd). Built by analysis\\build_lab.py.\r\n'
           'CFG["mode"] = "paper"\r\n'
           'CFG.update({"lab": True, "telegram_notify_paper": False, "auto_money": False})\r\n'
           'LAB_DIR = os.path.join(HERE, "lab")\r\n'
           'STATE_FILE = os.path.join(HERE, "state_lab.json")\r\n'
           'TRADES_FILE = os.path.join(HERE, "trades_series_lab.csv")\r\n')
o2 = "        web.start(self, sys.modules[__name__])\r\n"
n2 = ("        class LabModule:  # the panel sees this module, but with HERE = lab\\\\ for its config.json and page\r\n"
      "            HERE = LAB_DIR\r\n\r\n"
      "            def __getattr__(self, k):\r\n"
      "                return getattr(sys.modules[__name__], k)\r\n"
      "        web.start(self, LabModule())\r\n")
o3 = 'single_instance("Бот LIVE" if LIVE else "Бот PAPER", 47650 if LIVE else 47651)'
for o in (o1, o2, o3):
    assert s.count(o) == 1, o
s = s.replace(o1, n1).replace(o2, n2).replace(o3, 'single_instance("Бот LAB", 47655)')
open(os.path.join(ROOT, "bot_lab.py"), "wb").write(s.encode("utf-8"))
print("bot_lab.py собран из bot.py")
