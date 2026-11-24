"""06.11 (user: find the coins with the same contract and compare them). Read-only. All new PancakeSwap pools of the last N hours,
grouped by contract code; the two known families get the details: guard, desk, pool, life, buys, creator's payments.
Run: python coin_families.py [hours]. Out: coin_families.csv, coin_families.txt"""
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

HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 24
w3 = Web3(Web3.HTTPProvider(nr.URL, request_kwargs={"timeout": 30}))
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
SPB, WBNB = 0.45, "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
FACTORY = Web3.to_checksum_address("0xca143ce32fe78f1f7019d7d551a6402fc5350c73")
ROUTER = "0x10ed43c718714eb63d5aa57b78b54704e256024e"
T = lambda s: "0x" + Web3.keccak(text=s).hex().replace("0x", "")
PAIR, SYNC, SWAP = T("PairCreated(address,address,address,uint256)"), T("Sync(uint112,uint112)"), T("Swap(address,uint256,uint256,uint256,uint256,address)")
MAIN, SECOND = 2995, 1365
out_lines = []


def say(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    out_lines.append(s)


def call(fn, tries=5):
    for k in range(tries):
        try:
            return fn()
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(1 + k)


head = w3.eth.block_number
now = w3.eth.get_block(head).timestamp
lo = head - int(HOURS * 3600 / SPB)
pairs = []
for b in range(lo, head + 1, 2000):
    for lg in call(lambda: w3.eth.get_logs({"address": FACTORY, "fromBlock": b, "toBlock": min(head, b + 1999), "topics": [PAIR]})):
        t0, t1 = "0x" + lg["topics"][1].hex()[-40:], "0x" + lg["topics"][2].hex()[-40:]
        if t0.lower() != WBNB.lower() and t1.lower() != WBNB.lower():
            continue
        pairs.append(dict(token=Web3.to_checksum_address(t1 if t0.lower() == WBNB.lower() else t0), pair=Web3.to_checksum_address("0x" + bytes(lg["data"])[12:32].hex()), block=lg["blockNumber"]))
print(f"пулов с BNB за {HOURS} ч: {len(pairs)}", flush=True)


def code_of(p):
    c = bytes(call(lambda: w3.eth.get_code(p["token"])))
    p["size"], p["hash"] = len(c), Web3.keccak(c).hex().replace("0x", "")[:10]
    return p


with ThreadPoolExecutor(8) as ex:
    list(ex.map(code_of, pairs))
fam = collections.Counter((p["size"], p["hash"]) for p in pairs)
say(f"=== Все пулы с BNB за {HOURS:g} ч: {len(pairs)}, разных контрактов {len(fam)} ===")
for (size, h), n in fam.most_common(25):
    say(f"  код {size} байт, {h}: {n} монет")
ERC = [{"name": n, "inputs": [], "outputs": [{"type": t}], "stateMutability": "view", "type": "function"} for n, t in (("symbol", "string"), ("name", "string"), ("decimals", "uint8"), ("totalSupply", "uint256"))]
FAM_HASH = {}
for (size, h), n in fam.most_common():
    if size in (MAIN, SECOND):
        FAM_HASH[h] = size


def detail(p):
    tok = w3.eth.contract(address=p["token"], abi=ERC)
    for k in ("symbol", "name", "decimals", "totalSupply"):
        try:
            p[k] = call(lambda: getattr(tok.functions, k)().call(), 2)
        except Exception:
            p[k] = ""
    p["slot6"] = "0x" + bytes(call(lambda: w3.eth.get_storage_at(p["token"], 6))).hex()[-40:]
    p["slot3"] = int.from_bytes(bytes(call(lambda: w3.eth.get_storage_at(p["token"], 3))), "big")
    p["slot4"] = int.from_bytes(bytes(call(lambda: w3.eth.get_storage_at(p["token"], 4))), "big")
    wb0 = int(WBNB, 16) < int(p["token"], 16)
    end = min(head, p["block"] + 16000)
    syncs, swaps = [], []
    for b in range(p["block"], end + 1, 2000):
        e = min(end, b + 1999)
        syncs += call(lambda: w3.eth.get_logs({"address": p["pair"], "fromBlock": b, "toBlock": e, "topics": [SYNC]}))
        swaps += call(lambda: w3.eth.get_logs({"address": p["pair"], "fromBlock": b, "toBlock": e, "topics": [SWAP]}))
    ser = [(l["blockNumber"], int.from_bytes(bytes(l["data"])[:32] if wb0 else bytes(l["data"])[32:64], "big") / 1e18) for l in syncs]
    p.update(start="", peak="", life_min="", buys=0, sells=0, buyers=set(), creator="", desk="", desk_bnb="", pays=0, pays3=0)
    if not ser:
        return p
    p["start"], p["peak"] = round(ser[0][1], 3), round(max(v for _, v in ser), 3)
    peak_i = max(range(len(ser)), key=lambda i: ser[i][1])
    drain = next((b for b, v in ser[peak_i:] if v < 0.2 * ser[peak_i][1]), None)
    p["life_min"] = round((drain - p["block"]) * SPB / 60, 2) if drain else ""
    for lg in swaps:
        if drain and lg["blockNumber"] > drain:
            continue
        d = bytes(lg["data"])
        if int.from_bytes(d[0:32] if wb0 else d[32:64], "big"):
            p["buys"] += 1
            p["buyers"].add("0x" + lg["topics"][2].hex()[-40:])
        else:
            p["sells"] += 1
    p["creator"] = call(lambda: w3.eth.get_transaction(syncs[0]["transactionHash"])["from"]).lower()
    r = nr.call("nr_getAssetTransfers", [dict(category=["external"], toAddress=p["creator"], fromBlock=hex(max(0, p["block"] - 4000)), toBlock=hex(p["block"]), maxCount="0x14", excludeZeroValue=True, order="desc")])
    big = [t for t in (r.get("result") or {}).get("transfers", []) if int(t["value"], 16) >= 10**18]
    if big:
        p["desk"], p["desk_bnb"] = big[0]["from"].lower(), round(int(big[0]["value"], 16) / 1e18, 2)
    r = nr.call("nr_getAssetTransfers", [dict(category=["external"], fromAddress=p["creator"], fromBlock=hex(p["block"]), toBlock=hex(min(head, (drain or end))), maxCount="0x64", excludeZeroValue=True, order="asc")])
    pool = p["start"] or 1
    for t in (r.get("result") or {}).get("transfers", []):
        if (t.get("to") or "").lower() != ROUTER and 0.001 <= int(t["value"], 16) / 1e18 <= 3:
            p["pays"] += 1
            p["pays3"] += int(t["value"], 16) / 1e18 / pool >= 0.03
    return p


detailed = [p for p in pairs if p["size"] in (MAIN, SECOND)]
print("разбираю монет:", len(detailed), flush=True)
with ThreadPoolExecutor(6) as ex:
    list(ex.map(detail, detailed))
detailed.sort(key=lambda p: p["block"])
cols = ["time", "family", "symbol", "name", "token", "pair", "code_hash", "guard", "desk", "desk_bnb", "creator", "decimals", "totalSupply", "slot3", "slot4", "start", "peak", "life_min", "buys", "sells", "buyers_n", "pays", "pays3"]
with open(os.path.join(HERE, "coin_families.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=cols, delimiter=";")
    w.writeheader()
    for p in detailed:
        w.writerow(dict(time=time.strftime("%d.%m %H:%M", time.localtime(now - (head - p["block"]) * SPB)), family="первая" if p["size"] == MAIN else "вторая", code_hash=p["hash"], guard=p["slot6"], buyers_n=len(p["buyers"]), **{k: p.get(k, "") for k in cols if k not in ("time", "family", "code_hash", "guard", "buyers_n")}))


def med(x):
    x = [float(v) for v in x if v != ""]
    return round(st.median(x), 2) if x else "—"


say("\n=== Семьи по коду и сторожу ===")
groups = collections.defaultdict(list)
for p in detailed:
    groups[(p["size"], p["hash"], p["slot6"])].append(p)
for (size, h, g), ps in sorted(groups.items(), key=lambda kv: (kv[0][0], -len(kv[1]))):
    desks = collections.Counter(p["desk"][:10] or "нет кассы" for p in ps)
    starts = collections.Counter(p["start"] for p in ps)
    long_ = [p for p in ps if p["pays3"]]
    say(f"\nкод {size} {h}, сторож {g[:10]}: монет {len(ps)}")
    say("  кассы:", desks.most_common(4))
    say("  стартовый пул:", starts.most_common(6))
    say(f"  живёт, мин (медиана) {med(p['life_min'] for p in ps)}; с платежами от 3% пула: {len(long_)} монет, живут {med(p['life_min'] for p in long_)} мин, таких платежей на монету {med(p['pays3'] for p in long_)}")
    say(f"  покупок на монету {med(p['buys'] for p in ps)}, продаж {med(p['sells'] for p in ps)}")
    say("  примеры:", [(p["symbol"], p["start"], p["life_min"], p["pays3"]) for p in ps[-6:]])
say("\n=== Общие покупатели между группами (кошельки на ≥2 монетах группы) ===")
keys = sorted(groups, key=lambda k: -len(groups[k]))[:8]
sets = {}
for k in keys:
    c = collections.Counter(a for p in groups[k] for a in p["buyers"])
    sets[k] = {a for a, n in c.items() if n >= 2 and a.lower() != ROUTER}
    say(f"  {k[0]} {k[2][:10]}: {len(sets[k])}")
for i, x in enumerate(keys):
    for y in keys[i + 1:]:
        o = sets[x] & sets[y]
        if o:
            say(f"  общих {x[0]} {x[2][:10]} и {y[0]} {y[2][:10]}: {len(o)}, напр. {[a[:10] for a in list(o)[:3]]}")
open(os.path.join(HERE, "coin_families.txt"), "w", encoding="utf-8").write("\n".join(out_lines))
print("готово", flush=True)
