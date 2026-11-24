"""18.11 night: can an OUTSIDER buy and sell a coin of a family right now? No money: eth_call with state override (a fresh address gets
BNB, then gets coins written straight into the token's balance mapping + allowance to the router) and the Pancake router's
fee-on-transfer swaps. Tried at several gas prices (the 4249 template reads GASPRICE and COINBASE).
Finds the newest live coin of the family by the factory's PairCreated logs, or takes a token given on the command line.
Usage: python sim_sell.py <code size> [token]"""
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


