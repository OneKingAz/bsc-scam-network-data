"""The signals that were not bought (signal_skip.csv): what would they have brought? The price from about the block we would have reached (head + 2) to 4 blocks
later, the swap's fees 0.5 %, gas as in the test. No slippage of our own and no broken bot buys: an estimate. Read-only. Run: python analysis\\sig_skip.py [stake]"""
import contextlib, csv, io, json, os, statistics as st, sys
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
STAKE = float(sys.argv[1]) if len(sys.argv) > 1 else 0.002
sys.argv = sys.argv[:1]
with contextlib.redirect_stdout(io.StringIO()):
    import bot as B
    import journal as J
from web3 import Web3

a = Web3(Web3.HTTPProvider("https://bsc-mainnet.nodereal.io/v1/" + J.NODEREAL_KEY, request_kwargs={"timeout": 30}))
a.middleware_onion.inject(B.ExtraDataToPOAMiddleware, layer=0)
SYNC = "0x" + Web3.keccak(text="Sync(uint112,uint112)").hex().replace("0x", "")
pairs = {l["token"].lower(): l["pair"] for l in csv.DictReader(open(os.path.join(HERE, "guard_launches.csv"), encoding="utf-8-sig"), delimiter=";")}
rows = list(csv.DictReader(open(os.path.join(HERE, "signal_skip.csv"), encoding="utf-8-sig"), delimiter=";"))
out = []
for r in rows:
    p = pairs.get(r["token"].lower())
    if not p or not r["head"]:
        continue
    is0 = int(B.WBNB, 16) < int(r["token"], 16)
    land = int(r["head"]) + 2
    try:
        logs = a.eth.get_logs({"address": Web3.to_checksum_address(p), "fromBlock": land - 1500, "toBlock": land + 4, "topics": [SYNC]})
    except Exception:
        continue
    if not logs:
        continue
    price = lambda e: (lambda d: (int.from_bytes(d[:32], "big") / int.from_bytes(d[32:64], "big")) if is0 else (int.from_bytes(d[32:64], "big") / int.from_bytes(d[:32], "big")))(bytes(e["data"]))
    before = [e for e in logs if e["blockNumber"] < land]
    if not before:
        continue
    gross = (price(logs[-1]) / price(before[-1]) - 1) * 100 - 0.5
    out.append((r, gross, gross - 0.00002 / STAKE * 100))
print(f"записано {len(rows)}, посчитано {len(out)}; ставка {STAKE}")
for name, cond in (("мелкий платёж", lambda r: r["why"] == "мелкий платёж"), ("шла другая попытка", lambda r: r["why"] == "шла другая попытка"), ("шла подготовка", lambda r: r["why"].startswith("шла подг")), ("мемпул", lambda r: r["source"] == "мемпул"), ("блок", lambda r: r["source"] == "блок")):
    v = [x for x in out if cond(x[0])]
    if v:
        print(f"{name:<20} n={len(v):>4} до газа ср {st.mean(x[1] for x in v):+6.1f}% | после газа ср {st.mean(x[2] for x in v):+6.1f}% в плюсе {sum(x[2] > 0 for x in v)}")
for a0, b0 in ((0, 1), (1, 2), (2, 3), (3, 5), (5, 10), (10, 1000)):
    v = [x for x in out if a0 <= float(x[0]["share_pct"] or 0) < b0]
    if v:
        print(f"доля {a0}–{b0}%: n={len(v):>4} до газа ср {st.mean(x[1] for x in v):+6.1f}% | после газа {st.mean(x[2] for x in v):+6.1f}% в плюсе {sum(x[2] > 0 for x in v)}")
