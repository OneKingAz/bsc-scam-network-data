"""06.11 (user: "it feels like we are missing something"). Read-only. Every large contract family of the last N hours: does the creator pay a bot
right before its buy, as on our two networks? Paper only: nobody here checks that an outsider can sell these coins.
Run: python family_scan.py [hours] [min coins]. Out: family_scan.csv, family_scan.txt"""
import collections
import csv
import os
import statistics as st
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import nr
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 3
MIN_COINS = int(sys.argv[2]) if len(sys.argv) > 2 else 5
w3 = Web3(Web3.HTTPProvider(nr.URL, request_kwargs={"timeout": 30}))
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
SPB, WBNB = 0.45, "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
FACTORY = Web3.to_checksum_address("0xca143ce32fe78f1f7019d7d551a6402fc5350c73")
ROUTER = "0x10ed43c718714eb63d5aa57b78b54704e256024e"
T = lambda s: "0x" + Web3.keccak(text=s).hex().replace("0x", "")
PAIR, SYNC, SWAP = T("PairCreated(address,address,address,uint256)"), T("Sync(uint112,uint112)"), T("Swap(address,uint256,uint256,uint256,uint256,address)")
lines = []


def say(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    lines.append(s)


def call(fn, tries=4):
    for k in range(tries):
        try:
            return fn()
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(1 + k)


head = w3.eth.block_number
lo = head - int(HOURS * 3600 / SPB)
pairs = []
for b in range(lo, head + 1, 2000):
    for lg in call(lambda: w3.eth.get_logs({"address": FACTORY, "fromBlock": b, "toBlock": min(head, b + 1999), "topics": [PAIR]})):
        t0, t1 = "0x" + lg["topics"][1].hex()[-40:], "0x" + lg["topics"][2].hex()[-40:]
        if WBNB.lower() in (t0.lower(), t1.lower()):
            pairs.append(dict(token=Web3.to_checksum_address(t1 if t0.lower() == WBNB.lower() else t0), pair=Web3.to_checksum_address("0x" + bytes(lg["data"])[12:32].hex()), block=lg["blockNumber"]))
print("пулов", len(pairs), flush=True)


def code_of(p):
    c = bytes(call(lambda: w3.eth.get_code(p["token"])))
    p["size"], p["hash"] = len(c), Web3.keccak(c).hex().replace("0x", "")[:8]


with ThreadPoolExecutor(8) as ex:
    list(ex.map(code_of, pairs))
fam = collections.Counter((p["size"], p["hash"]) for p in pairs)
big = {k for k, n in fam.items() if n >= MIN_COINS and k[0] > 100}  # 45 bytes: a proxy, the launchpad's mass of coins
print("семей", len(big), {k[0]: fam[k] for k in big}, flush=True)


def detail(p):
    p.update(start=None, peak=None, life=None, buys=0, sells=0, creator="", desk="", pays=0, hit=0, rise=[], min_left=None)
    wb0 = int(WBNB, 16) < int(p["token"], 16)
    end = min(head, p["block"] + 8000)
    logs = []
    for b in range(p["block"], end + 1, 2000):
        e = min(end, b + 1999)
        logs += call(lambda: w3.eth.get_logs({"address": p["pair"], "fromBlock": b, "toBlock": e, "topics": [[SYNC, SWAP]]}))
    sy, sw = [], []
    for l in logs:
        d = bytes(l["data"])
        if l["topics"][0].hex().replace("0x", "") == SYNC[2:]:
            sy.append((l["blockNumber"], int.from_bytes(d[:32] if wb0 else d[32:64], "big") / 1e18, l["transactionHash"]))
        else:
            sw.append((l["blockNumber"], int.from_bytes(d[0:32] if wb0 else d[32:64], "big") / 1e18, "0x" + l["topics"][2].hex()[-40:]))
    if not sy:
        return
    p["start"], p["peak"] = round(sy[0][1], 2), round(max(x[1] for x in sy), 2)
    i = max(range(len(sy)), key=lambda k: sy[k][1])
    drain = next((b for b, v, _ in sy[i:] if v < 0.2 * sy[i][1]), None)
    p["life"] = round((drain - p["block"]) * SPB / 60, 2) if drain else None
    p["buys"] = sum(1 for s in sw if s[1] > 0 and (not drain or s[0] <= drain))
    p["sells"] = sum(1 for s in sw if s[1] == 0 and (not drain or s[0] <= drain))
    p["creator"] = call(lambda: w3.eth.get_transaction(sy[0][2])["from"]).lower()
    r = nr.call("nr_getAssetTransfers", [dict(category=["external"], toAddress=p["creator"], fromBlock=hex(max(0, p["block"] - 4000)), toBlock=hex(p["block"]), maxCount="0x14", excludeZeroValue=True, order="desc")])
    big_in = [t for t in (r.get("result") or {}).get("transfers", []) if int(t["value"], 16) >= 10**18]
    if big_in:
        p["desk"] = big_in[0]["from"].lower()
    r = nr.call("nr_getAssetTransfers", [dict(category=["external"], fromAddress=p["creator"], fromBlock=hex(p["block"] + 1), toBlock=hex(drain or end), maxCount="0x64", excludeZeroValue=True, order="asc")])
    for t in (r.get("result") or {}).get("transfers", []):
        v, b, to = int(t["value"], 16) / 1e18, int(t["blockNum"], 16), (t.get("to") or "").lower()
        if to == ROUTER or not 0.001 <= v <= 3:
            continue
        pool = next((x[1] for x in reversed(sy) if x[0] <= b), sy[0][1])
        if not pool or v / pool < 0.03:
            continue
        p["pays"] += 1
        buy = next((s for s in sw if s[2].lower() == to and b <= s[0] <= b + 12 and s[1] > 0), None)
        if buy:
            p["hit"] += 1
            p["rise"].append(2 * v / pool * 100 - 0.5)
            if drain:
                p["min_left"] = min(p["min_left"] if p["min_left"] is not None else 1e9, (drain - b) * SPB)
    return


todo = [p for p in pairs if (p["size"], p["hash"]) in big]
print("разбираю монет", len(todo), flush=True)
with ThreadPoolExecutor(6) as ex:
    list(ex.map(detail, todo))
med = lambda x: round(st.median(x), 2) if x else "—"
say(f"=== {HOURS:g} ч, пулов с BNB {len(pairs)}, семьи от {MIN_COINS} монет ===")
say("(платёж = создатель платит кошельку от 3% пула; покупка = этот кошелёк купил за ≤12 блоков. Рост на бумаге: 2 x доля − 0.5%)")
rows = []
g = collections.defaultdict(list)
for p in todo:
    g[(p["size"], p["hash"])].append(p)
for (size, h), ps in sorted(g.items(), key=lambda kv: -sum(x["hit"] for x in kv[1])):
    rise = [r for p in ps for r in p["rise"]]
    with_hit = [p for p in ps if p["hit"]]
    desks = collections.Counter(p["desk"][:10] or "нет кассы" for p in ps)
    per_h = round(len(rise) / HOURS, 1)
    say(f"\nкод {size} {h}: монет {len(ps)}; старт {collections.Counter(p['start'] for p in ps).most_common(3)}; живут {med([p['life'] for p in ps if p['life'] is not None])} мин; покупок {med([p['buys'] for p in ps])}, продаж {med([p['sells'] for p in ps])}")
    say(f"  кассы {desks.most_common(3)}")
    say(f"  платежей от 3% {sum(p['pays'] for p in ps)}, с покупкой {len(rise)} ({per_h} в час), рост на бумаге в среднем {med(rise)}%, монет с такими {len(with_hit)}; меньше всего до слива {min((p['min_left'] for p in ps if p['min_left'] is not None), default='—')} с")
    rows += [dict(time=p["block"], size=size, hash=h, token=p["token"], pair=p["pair"], desk=p["desk"], creator=p["creator"], start=p["start"], peak=p["peak"], life=p["life"], buys=p["buys"], sells=p["sells"], pays=p["pays"], hit=p["hit"]) for p in ps]
if rows:
    with open(os.path.join(HERE, "family_scan.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter=";")
        w.writeheader()
        w.writerows(rows)
open(os.path.join(HERE, "family_scan.txt"), "w", encoding="utf-8").write("\n".join(lines))
print("готово", flush=True)
