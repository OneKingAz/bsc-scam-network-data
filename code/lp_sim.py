"""19.11 (user: LP idea) can an OUTSIDER ADD liquidity to and REMOVE it from a coin's pool? Based on sim_sell.py.
18.11 night: can an OUTSIDER buy and sell a coin of a family right now? No money: eth_call with state override (a fresh address gets
BNB, then gets coins written straight into the token's balance mapping + allowance to the router) and the Pancake router's
fee-on-transfer swaps. Tried at several gas prices (the 4249 template reads GASPRICE and COINBASE).
Finds the newest live coin of the family by the factory's PairCreated logs, or takes a token given on the command line.
Usage: python lp_sim.py <code size> [token|-] [blocks back]"""
import json
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
NODES = ["https://rpc-bsc.48.club", "https://rpc.48.club", "https://bsc-dataseed.bnbchain.org"]  # publicnode/dataseed may return empty logs
WBNB = "bb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
ROUTER = "0x10ed43c718714eb63d5aa57b78b54704e256024e"
FACTORY = "0xca143ce32fe78f1f7019d7d551a6402fc5350c73"
PAIRCREATED = "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"
X = "7a11e000000000000000000000000000000c0ffe"  # a fresh address nobody uses

# --- keccak256 (pure python) ---
RC = [0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000, 0x000000000000808B, 0x0000000080000001, 0x8000000080008081,
      0x8000000000008009, 0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A, 0x000000008000808B, 0x800000000000008B,
      0x8000000000008089, 0x8000000000008003, 0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A, 0x8000000080008081,
      0x8000000000008080, 0x0000000080000001, 0x8000000080008008]
ROT = [[0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61], [28, 55, 25, 21, 56], [27, 20, 39, 8, 14]]
M = (1 << 64) - 1


def keccak(data: bytes) -> bytes:
    rate = 136
    d = bytearray(data) + b"\x01"
    while len(d) % rate:
        d += b"\x00"
    d[-1] |= 0x80
    s = [[0] * 5 for _ in range(5)]
    for off in range(0, len(d), rate):
        for i in range(rate // 8):
            s[i % 5][i // 5] ^= int.from_bytes(d[off + 8 * i: off + 8 * i + 8], "little")
        for rnd in range(24):
            c = [s[x][0] ^ s[x][1] ^ s[x][2] ^ s[x][3] ^ s[x][4] for x in range(5)]
            dd = [c[(x - 1) % 5] ^ (((c[(x + 1) % 5] << 1) | (c[(x + 1) % 5] >> 63)) & M) for x in range(5)]
            s = [[s[x][y] ^ dd[x] for y in range(5)] for x in range(5)]
            b = [[0] * 5 for _ in range(5)]
            for x in range(5):
                for y in range(5):
                    r = ROT[x][y]
                    b[y][(2 * x + 3 * y) % 5] = ((s[x][y] << r) | (s[x][y] >> (64 - r))) & M if r else s[x][y]
            s = [[b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y]) for y in range(5)] for x in range(5)]
            s[0][0] ^= RC[rnd]
    out = b""
    for i in range(4):
        out += s[i % 5][i // 5].to_bytes(8, "little")
    return out


assert keccak(b"").hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"


def call(m, p, tries=4):
    err = None
    for k in range(tries):
        try:
            r = json.loads(urllib.request.urlopen(urllib.request.Request(NODES[k % len(NODES)], data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": m, "params": p}).encode(),
                                                                         headers={"Content-Type": "application/json"}), timeout=30).read())
            return r
        except Exception as e:
            err = e
            time.sleep(0.5)
    return {"error": str(err)}


def res(r):
    if "result" in r:
        return True, r["result"]
    e = r.get("error") or {}
    d = e.get("data") if isinstance(e, dict) else None
    if isinstance(d, str) and d.startswith("0x08c379a0"):
        return False, bytes.fromhex(d[138:138 + 2 * int(d[74:138], 16)]).decode(errors="replace")
    return False, (e.get("message") if isinstance(e, dict) else str(e))[:120]


def word(v):
    return hex(v)[2:].rjust(64, "0")


def addr(a):
    return a.lower().replace("0x", "").rjust(64, "0")


size = int(sys.argv[1])
head = int(call("eth_blockNumber", [])["result"], 16)
tok = sys.argv[2] if len(sys.argv) > 2 else None
pair = None
LOOK = int(sys.argv[3]) if len(sys.argv) > 3 else 6000  # blocks to look back for a new pair
if tok == "-":
    tok = None
if not tok:
    for back in range(0, LOOK, 400):
        lg = call("eth_getLogs", [{"address": FACTORY, "fromBlock": hex(head - back - 399), "toBlock": hex(head - back), "topics": [PAIRCREATED]}]).get("result") or []
        for l in reversed(lg):
            t0, t1 = "0x" + l["topics"][1][-40:], "0x" + l["topics"][2][-40:]
            t = t1 if t0[2:] == WBNB else t0 if t1[2:] == WBNB else None
            if not t:
                continue
            code = call("eth_getCode", [t, "latest"]).get("result") or "0x"
            if (len(code) - 2) // 2 == size:
                p = "0x" + l["data"][26:66]
                rv = call("eth_call", [{"to": p, "data": "0x0902f1ac"}, "latest"]).get("result") or "0x"
                if len(rv) > 130 and max(int(rv[2:66], 16), int(rv[66:130], 16)) > 0:
                    tok, pair = t, p
                    age = (head - int(l["blockNumber"], 16)) * 0.45
                    print(f"живая монета {t}, пул {p}, возраст {age:.0f} с")
                    break
        if tok:
            break
if not tok:
    sys.exit("живой монеты этой семьи нет в последних ~45 мин")
if not pair:
    pair = "0x" + call("eth_call", [{"to": FACTORY, "data": "0xe6a43905" + addr(tok) + addr(WBNB)}, "latest"])["result"][-40:]
    print(f"монета {tok}, пул {pair}")

deadline = word(int(time.time()) + 600)
Y = "7a11e000000000000000000000000000000beef1"
bal_slot = None
for s in range(0, 12):
    key = "0x" + keccak(bytes.fromhex(addr(X) + word(s))).hex()
    r = call("eth_call", [{"to": tok, "data": "0x70a08231" + addr(X)}, "latest", {tok: {"stateDiff": {key: "0x" + word(10 ** 24)}}}]).get("result")
    if r and int(r, 16) >= 10 ** 23:
        bal_slot = s
        break
print("слот балансов монеты:", bal_slot)
rv = call("eth_call", [{"to": pair, "data": "0x0902f1ac"}, "latest"])["result"]
t0 = call("eth_call", [{"to": pair, "data": "0x0dfe1681"}, "latest"])["result"]
r0, r1 = int(rv[2:66], 16), int(rv[66:130], 16)
rb, rt = (r0, r1) if t0[-40:] == WBNB else (r1, r0)
print(f"пул: {rb / 1e18:.4f} BNB")
bnb = 5 * 10 ** 15  # 0.005 BNB side
amount = rt * bnb // rb
allow_slot = None
bkey = "0x" + keccak(bytes.fromhex(addr(X) + word(bal_slot))).hex()
for al in range(0, 12):
    if al == bal_slot:
        continue
    inner = keccak(bytes.fromhex(addr(X) + word(al)))
    akey = "0x" + keccak(bytes.fromhex(addr(ROUTER) + inner.hex())).hex()
    r = call("eth_call", [{"to": tok, "data": "0xdd62ed3e" + addr(X) + addr(ROUTER)}, "latest", {tok: {"stateDiff": {akey: "0x" + "f" * 64}}}]).get("result")
    if r and int(r, 16) > 0:
        allow_slot = al
        break
print("слот разрешений монеты:", allow_slot)
inner = keccak(bytes.fromhex(addr(X) + word(allow_slot)))
akey = "0x" + keccak(bytes.fromhex(addr(ROUTER) + inner.hex())).hex()
o = {tok: {"stateDiff": {bkey: "0x" + word(amount * 2), akey: "0x" + "f" * 64}}, "0x" + X: {"balance": hex(10 ** 18)}}
# A) addLiquidityETH(token, amountTokenDesired, amountTokenMin, amountETHMin, to, deadline)
add = "0xf305d719" + addr(tok) + word(amount) + word(0) + word(0) + addr(X) + deadline
for g in (0.05, 0.1, 1.0):
    ok, r = res(call("eth_call", [{"from": "0x" + X, "to": ROUTER, "data": add, "value": hex(bnb * 11 // 10), "gasPrice": hex(int(g * 1e9)), "gas": hex(1500000)}, "latest", o]))
    if ok:
        print(f"А) ВНЕСТИ в пул 0.005 BNB + монеты, газ {g}: проходит; монет {int(r[2:66],16)}, BNB {int(r[66:130],16)/1e18:.6f}, доля-жетонов {int(r[130:194],16)}")
    else:
        print(f"А) ВНЕСТИ в пул, газ {g}: ОТКАЗ: {r}")
# B) remove: X owns LP (pair storage: totalSupply 0, balanceOf 1, allowance 2)
ts = int(call("eth_call", [{"to": pair, "data": "0x18160ddd"}, "latest"])["result"], 16)
L = ts // 300
lb = "0x" + keccak(bytes.fromhex(addr(X) + word(1))).hex()
la = "0x" + keccak(bytes.fromhex(addr(ROUTER) + keccak(bytes.fromhex(addr(X) + word(2))).hex())).hex()
o2 = {pair: {"stateDiff": {lb: "0x" + word(L), la: "0x" + "f" * 64}}, "0x" + X: {"balance": hex(10 ** 18)}}
chk = int(call("eth_call", [{"to": pair, "data": "0x70a08231" + addr(X)}, "latest", o2])["result"], 16)
print(f"жетоны пула у X (подмена): {chk == L} ({L} из {ts})")
for name, sel in (("removeLiquidityETHSupportingFee", "0xaf2979eb"), ("removeLiquidityETH", "0x02751cec")):
    rem = sel + addr(tok) + word(L) + word(0) + word(0) + addr(Y) + deadline
    for g in (0.05, 0.1):
        ok, r = res(call("eth_call", [{"from": "0x" + X, "to": ROUTER, "data": rem, "gasPrice": hex(int(g * 1e9)), "gas": hex(1500000)}, "latest", o2]))
        print(f"Б) ЗАБРАТЬ из пула ({name}) на свежий адрес, газ {g}: {'проходит' + ((', BNB ' + format(int(r[-64:],16)/1e18,'.6f')) if r and len(r) >= 66 else '') if ok else 'ОТКАЗ: ' + str(r)}")
# C) the fresh address Y sells the coins it got
yb = "0x" + keccak(bytes.fromhex(addr(Y) + word(bal_slot))).hex()
ya = "0x" + keccak(bytes.fromhex(addr(ROUTER) + keccak(bytes.fromhex(addr(Y) + word(allow_slot))).hex())).hex()
o3 = {tok: {"stateDiff": {yb: "0x" + word(amount), ya: "0x" + "f" * 64}}, "0x" + Y: {"balance": hex(10 ** 17)}}
sell = "0x791ac947" + word(amount) + word(0) + word(160) + addr(Y) + deadline + word(2) + addr(tok) + addr(WBNB)
for g in (0.05, 0.1):
    ok, r = res(call("eth_call", [{"from": "0x" + Y, "to": ROUTER, "data": sell, "gasPrice": hex(int(g * 1e9)), "gas": hex(900000)}, "latest", o3]))
    print(f"В) свежий адрес ПРОДАЁТ полученные монеты, газ {g}: {'проходит' if ok else 'ОТКАЗ: ' + str(r)}")
