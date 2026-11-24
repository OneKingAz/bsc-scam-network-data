"""05.11: "the coin's creator pays a bot, the bot buys ~2 s later". On recent coins of every type (NodeReal archive): how many buys
follow a creator's payment, how early, how many payments lead to no buy, and what buying on that signal gives on paper. We buy LAND
blocks after the payment (a signal with less lead lands after the bot, at the higher price: counted as it is), we sell SELL blocks
after the bot's buy; own impact and 0.25% fee both ways; gas 0.00003 BNB per attempt. No reaction of the bots assumed.
Run: python analysis\\creator_signal.py [coins per type]"""
import collections, contextlib, csv, io, json, os, statistics as st, sys, time
from concurrent.futures import ThreadPoolExecutor
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
PER = int(sys.argv[1]) if len(sys.argv) > 1 else 12
sys.argv = sys.argv[:1]
with contextlib.redirect_stdout(io.StringIO()):
    import bot as B
    import journal as J
from web3 import Web3
import requests

URL = "https://bsc-mainnet.nodereal.io/v1/" + J.NODEREAL_KEY
a = Web3(Web3.HTTPProvider(URL, request_kwargs={"timeout": 30}))
a.middleware_onion.inject(B.ExtraDataToPOAMiddleware, layer=0)
SYNC = "0x" + Web3.keccak(text="Sync(uint112,uint112)").hex().replace("0x", "")
SWAP = "0x" + Web3.keccak(text="Swap(address,uint256,uint256,uint256,uint256,address)").hex().replace("0x", "")
WBNB, SPB, FEE, GAS = int(B.WBNB, 16), 0.45, 0.9975, 0.00003
head = a.eth.block_number
now = a.eth.get_block(head).timestamp


def call(fn, k=5):
    for i in range(k):
        try:
            return fn()
        except Exception:
            if i == k - 1:
                raise
            time.sleep(1 + i)


def outgoing(addr, b0, b1):
    out, key = [], None
    for _ in range(8):
        p = dict(category=["external"], fromAddress=addr, fromBlock=hex(b0), toBlock=hex(b1), maxCount="0x3e8", excludeZeroValue=True)
        if key:
            p["pageKey"] = key
        r = call(lambda: requests.post(URL, json={"jsonrpc": "2.0", "id": 1, "method": "nr_getAssetTransfers", "params": [p]}, timeout=30).json())
        res = r.get("result") or {}
        out += res.get("transfers") or []
        key = res.get("pageKey")
        if not key:
            break
    return out


def load(l):
    pair, token = Web3.to_checksum_address(l["pair"]), Web3.to_checksum_address(l["token"])
    is0 = WBNB < int(token, 16)
    b0 = head - int((now - int(l["ts"])) / SPB) - 60
    sy, sw = [], []
    for b in range(b0, min(head, b0 + 10000), 2000):
        for e in call(lambda: a.eth.get_logs({"address": pair, "fromBlock": b, "toBlock": min(head, b + 1999), "topics": [[SYNC, SWAP]]})):
            d = bytes(e["data"])
            if e["topics"][0].hex().replace("0x", "") == SYNC[2:]:
                x, y = int.from_bytes(d[:32], "big"), int.from_bytes(d[32:64], "big")
                bnb, tok = (x, y) if is0 else (y, x)
                sy.append((e["blockNumber"], e["logIndex"], bnb / 1e18, tok / 1e18, e["transactionHash"]))
            else:
                x = [int.from_bytes(d[i:i + 32], "big") for i in (0, 32, 64, 96)]
                bi = (x[0] if is0 else x[1]) / 1e18
                if bi > 0.0001:
                    sw.append((e["blockNumber"], e["logIndex"], bi, e["transactionHash"]))
    if len(sy) < 3 or not sw:
        return None
    t0, start = sy[0][0], sy[0][2]
    peak = max(s[2] for s in sy)
    ipk = max(range(len(sy)), key=lambda i: sy[i][2])
    drain = next((s[0] for s in sy[ipk:] if s[2] < 0.2 * peak), None)
    if drain is None:
        return None
    creator = call(lambda: a.eth.get_transaction(sy[0][4]))["from"].lower()
    froms = {}
    for s in sw:
        froms[s[3]] = call(lambda: a.eth.get_transaction(s[3]))["from"].lower()
    pays = [(int(t["blockNum"], 16), t["to"].lower(), int(t["value"], 16) / 1e18) for t in outgoing(creator, t0 - 300, drain) if t.get("to")]
    return dict(sym=l["symbol"], t0=t0, kind=25 if start >= 20 else 10 if start >= 5 else 1, sy=[s[:4] for s in sy], drain=drain, creator=creator,
                buys=[(s[0], s[2], froms[s[3]]) for s in sw], pays=pays)


rows = list(csv.DictReader(open(os.path.join(HERE, "guard_launches.csv"), encoding="utf-8-sig"), delimiter=";"))
cache = {k.lower(): v.get("kind") for k, v in json.load(open(os.path.join(HERE, "gas_options_cache.json"), encoding="utf-8")).items()}
pick = collections.defaultdict(list)
for l in reversed(rows):
    if now - int(l["ts"]) < 60 * 3600 and now - int(l["ts"]) > 3600:
        k = cache.get(l["token"].lower())
        if k and len(pick[k]) < PER:
            pick[k].append(l)
print("выборка:", {k: len(v) for k, v in pick.items()}, flush=True)
coins = []
with ThreadPoolExecutor(6) as ex:
    for c in ex.map(load, [l for v in pick.values() for l in v]):
        if c:
            coins.append(c)
print("загружено монет:", len(coins), flush=True)


def at(c, b):
    p = next((s for s in reversed(c["sy"]) if s[0] <= b), c["sy"][0])
    return p[2], p[3]


def gain(c, e, x, stake):
    b0, t0 = at(c, e - 0.5)
    tok = stake * FEE * t0 / (b0 + stake * FEE)
    b1, t1 = at(c, x)
    return (tok * FEE * b1 / (t1 + tok * FEE) / stake - 1) * 100 - GAS / stake * 100


for kind in (1, 10, 25):
    cs = [c for c in coins if c["kind"] == kind]
    if not cs:
        continue
    total, signed, leads, ratios = 0, 0, [], []
    unused, allpays = 0, 0
    for c in cs:
        for b, v, who in c["buys"]:
            total += 1
            p = [x for x in c["pays"] if x[1] == who and 0 <= b - x[0] <= 60]
            if p:
                signed += 1
                x = max(p, key=lambda x: x[0])
                leads.append(b - x[0])
                ratios.append(v / x[2] if x[2] else 0)
        for pb, to, v in c["pays"]:
            if pb < c["t0"] or pb >= c["drain"]:
                continue
            allpays += 1
            if not any(w == to and 0 <= b - pb <= 20 for b, _, w in c["buys"]):
                unused += 1
    s = sorted(leads) or [0]
    print(f"\n=== {kind} BNB, монет {len(cs)}: покупок {total}, перед ними платёж создателя {signed} ({signed * 100 // max(1, total)}%)")
    print(f"  запас, блоков: мин {s[0]}, 25% {s[len(s) // 4]}, медиана {s[len(s) // 2]}, 75% {s[len(s) * 3 // 4]}, макс {s[-1]}; ≥3: {sum(x >= 3 for x in s) * 100 // len(s)}%, ≥4: {sum(x >= 4 for x in s) * 100 // len(s)}%")
    print(f"  покупка/платёж: медиана {st.median(ratios) if ratios else 0:.3f}; платежей создателя при жизни монеты {allpays}, без покупки за 20 блоков {unused}")
    print(f"  бумага: вход по платежу, ставка 0.005 и 0.001; выход после покупки бота")
    for LAND, SELL in ((2, 3), (3, 4), (3, 6)):
        for minshare in (0.0, 0.03, 0.08):
            v5, v1, hold, n = [], [], [], 0
            for c in cs:
                for pb, to, val in c["pays"]:
                    if pb < c["t0"] or pb + LAND >= c["drain"]:
                        continue
                    pool = at(c, pb)[0]
                    if val < minshare * pool:
                        continue
                    n += 1
                    nb = next((b for b, _, w in c["buys"] if w == to and 0 <= b - pb <= 20), None)
                    e = pb + LAND
                    x = (nb + SELL) if nb is not None else e + 6
                    if x >= c["drain"]:
                        v5.append(-100)
                        v1.append(-100)
                        hold.append((x - e) * SPB)
                        continue
                    v5.append(gain(c, e, x, 0.005))
                    v1.append(gain(c, e, x, 0.001))
                    hold.append((x - e) * SPB)
            if v5:
                print(f"    вход +{LAND} бл., выход +{SELL} бл., платёж ≥{minshare * 100:.0f}% пула: попыток {n} ({n / len(cs):.1f} на монету), держим {st.median(hold):.1f} с; 0.005: среднее {st.mean(v5):+6.1f}% плюс {sum(y > 0 for y in v5) * 100 // len(v5)}%; 0.001: {st.mean(v1):+6.1f}%")
