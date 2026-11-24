"""18.11 (user): where did an address send its BNB. One-two NodeReal calls (nr_getAssetTransfers), only NODEREAL_KEY of .env read, never printed.
Usage: python trace_out.py <address> [count=20]"""
import json
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
A = sys.argv[1]
N = int(sys.argv[2]) if len(sys.argv) > 2 else 20
K = None
for _l in open(r"D:\bot\.env", encoding="utf-8-sig"):
    if _l.strip().startswith("NODEREAL_KEY="):
        K = _l.split("=", 1)[1].strip().strip('"\'')
U = "https://bsc-mainnet.nodereal.io/v1/" + K
for d in ("from", "to"):
    p = {"category": ["external", "internal", "20"], "maxCount": hex(N), "order": "desc", d + "Address": A}
    try:
        r = json.loads(urllib.request.urlopen(urllib.request.Request(U, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "nr_getAssetTransfers", "params": [p]}).encode(),
                                                                     headers={"Content-Type": "application/json"}), timeout=30).read())
    except Exception as e:
        print(d, "ошибка", str(e).replace(K, "<key>")[:80])
        continue
    if "error" in r:
        print(d, "ошибка", str(r["error"])[:80])
        continue
    print("исходящие" if d == "from" else "входящие")
    for t in r["result"].get("transfers", []):
        v = t.get("value", "0x0")
        v = int(v, 16) / 1e18 if str(v).startswith("0x") else v
        print(" ", time.strftime("%d.%m %H:%M", time.localtime(int(t.get("blockTimeStamp", 0)))), t.get("category"), t.get("asset") or "BNB",
              t.get("from"), "->", t.get("to"), v, t.get("hash"))
