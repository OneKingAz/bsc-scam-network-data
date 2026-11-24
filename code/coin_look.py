"""Read-only look at one pair or token: DexScreener summary, guard slot, pool history, buys and sells.
Run: python analysis\\coin_look.py <pair or token address>"""
import contextlib, csv, io, json, os, sys, time, urllib.request
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
ADDR = sys.argv[1]
sys.argv = sys.argv[:1]
with contextlib.redirect_stdout(io.StringIO()):
    import bot as B
    import journal as J
from web3 import Web3

req = urllib.request.Request(f"https://api.dexscreener.com/latest/dex/pairs/bsc/{ADDR}", headers={"User-Agent": "coin-look"})
ds = {}
try:
    ds = json.load(urllib.request.urlopen(req, timeout=15))
except Exception as e:
    print("DexScreener:", str(e)[:100])
p = (ds.get("pairs") or [None])[0] if ds else None
if p:
    print("DexScreener:", {k: p.get(k) for k in ("dexId", "pairAddress", "priceUsd", "fdv", "marketCap", "pairCreatedAt")})
    print(" монета:", p.get("baseToken"), "| пара к:", (p.get("quoteToken") or {}).get("symbol"))
    print(" ликвидность:", p.get("liquidity"), "| объём:", p.get("volume"))
    print(" изменение:", p.get("priceChange"), "| сделки:", p.get("txns"))
    print(" возраст, мин:", round((time.time() - p["pairCreatedAt"] / 1000) / 60, 1) if p.get("pairCreatedAt") else None)
else:
    print("DexScreener: пары нет в ответе")
a = Web3(Web3.HTTPProvider("https://bsc-mainnet.nodereal.io/v1/" + J.NODEREAL_KEY, request_kwargs={"timeout": 30}))
a.middleware_onion.inject(B.ExtraDataToPOAMiddleware, layer=0)
addr = Web3.to_checksum_address(ADDR)
pair = Web3.to_checksum_address(p["pairAddress"]) if p else addr
c = a.eth.contract(pair, abi=B.PAIR_ABI)
t0 = c.functions.token0().call()
token = Web3.to_checksum_address(p["baseToken"]["address"]) if p else t0
is0 = t0.lower() == B.WBNB.lower()
print("пара", pair, "монета", token, "код монеты, байт:", len(a.eth.get_code(token)))
slot = a.eth.get_storage_at(token, 6).hex()[-40:].lower()
print("слот 6:", "0x" + slot, "| основной сторож" if slot == B.GUARD[2:].lower() else "| второй сторож" if slot.startswith("dbbf0824") else "| сторожа сети нет")
for fn in ("guard_launches.csv",):
    for r in csv.DictReader(open(os.path.join(HERE, fn), encoding="utf-8-sig"), delimiter=";"):
        if r["token"].lower() == token.lower():
            print("в журнале запусков:", r["time"], r["symbol"])
r0, r1, _ = c.functions.getReserves().call()
print("в пуле сейчас:", round((r0 if is0 else r1) / 1e18, 3), "BNB (если пара к WBNB)")
SYNC = "0x" + Web3.keccak(text="Sync(uint112,uint112)").hex().replace("0x", "")
SWAP = "0x" + Web3.keccak(text="Swap(address,uint256,uint256,uint256,uint256,address)").hex().replace("0x", "")
head = a.eth.block_number
sy, sw = [], []
for b in range(head - 12000, head, 2000):
    for e in a.eth.get_logs({"address": pair, "fromBlock": b, "toBlock": min(head, b + 1999), "topics": [[SYNC, SWAP]]}):
        d = bytes(e["data"])
        if e["topics"][0].hex().replace("0x", "") == SYNC[2:]:
            x, y = int.from_bytes(d[:32], "big"), int.from_bytes(d[32:64], "big")
            sy.append((e["blockNumber"], (x if is0 else y) / 1e18))
        else:
            x = [int.from_bytes(d[i:i + 32], "big") for i in (0, 32, 64, 96)]
            sw.append((e["blockNumber"], (x[0] if is0 else x[1]) / 1e18, (x[2] if is0 else x[3]) / 1e18))
print(f"за последние ~90 мин: свапов {len(sw)}, покупок {sum(s[1] > 0 for s in sw)}, продаж {sum(s[2] > 0 for s in sw)}")
if sy:
    z = sy[0][0]
    print("старт пула в окне:", round(sy[0][1], 3), "BNB, пик", round(max(s[1] for s in sy), 3), ", возраст окна мин", round((head - z) * 0.45 / 60, 1))
    last = -1
    for b, v in sy:
        m = int((b - z) * 0.45 / 60 // 2)
        if m != last:
            print(f"  {(b - z) * 0.45 / 60:5.1f} мин: {v:.3f} BNB")
            last = m
    big = sorted(sw, key=lambda s: -max(s[1], s[2]))[:8]
    print("крупные (мин, купил BNB, продал BNB):", [(round((s[0] - z) * 0.45 / 60, 1), round(s[1], 3), round(s[2], 3)) for s in sorted(big)])
