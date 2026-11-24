"""10.11 (user: networks off, collect more data on how their bots behave). A 24/7 watcher, no money, no keys, open nodes.
Families: code 436f529b4d (network 5, family 2941), 1f6f5a665a (network 6, family 3553), and the guards of the networks 3/4 (code 1365).
For every new coin of these families: every swap and liquidity change in its pool for 45 min, with the block, the place in the block,
the second after the launch, BNB, the pool after, the sender and its role (creator / a bot of the family = seen in 3+ of its coins /
outsider / us), the method and gas. One row per event in analysis/bot_watch.csv; the outsiders' buys and what follows them can be read later.
Single instance: port 47664. Usage: python bot_watch.py"""
import collections
import csv
import ctypes
import itertools
import os
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from web3 import Web3

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "bot_watch.csv")
SPB, TRACK_S = 0.45, 45 * 60
URLS = ["https://bsc-rpc.publicnode.com", "https://rpc.48.club"]
W3S = [Web3(Web3.HTTPProvider(u, request_kwargs={"timeout": 25})) for u in URLS]
RR, LOCK = itertools.count(), threading.Lock()
F = Web3.to_checksum_address("0xca143ce32fe78f1f7019d7d551a6402fc5350c73")
WB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
PC = "0x" + Web3.keccak(text="PairCreated(address,address,address,uint256)").hex().replace("0x", "")
SW = "0x" + Web3.keccak(text="Swap(address,uint256,uint256,uint256,uint256,address)").hex().replace("0x", "")
SYNC = "0x" + Web3.keccak(text="Sync(uint112,uint112)").hex().replace("0x", "")
CODES = {"436f529b4d": "2941 (сеть 5)", "1f6f5a665a": "3553 (сеть 6)"}
GUARDS = {"623e7c23c7c228095f94d9bd567eae55cb87a966": "1365 (сеть 3)", "a70b3f34e3ca7d4665b794ce6bb29168b1d2e40d": "1365 (сеть 4)"}
METHODS = {"02751cec": "removeLiquidityETH", "af2979eb": "removeLiquidityETHFee", "ded9382a": "removeLiquidityETHPermit", "baa2abde": "removeLiquidity",
           "f305d719": "addLiquidityETH", "7ff36ab5": "swapExactETHForTokens", "b6f9de95": "swapExactETHForTokensFee", "fb3bdb41": "swapETHForExactTokens",
           "791ac947": "swapExactTokensForETHFee", "18cbafe5": "swapExactTokensForETH", "095ea7b3": "approve"}
OURS = set()
try:
    for line in open(os.path.join(HERE, "hop_wallets.txt"), encoding="utf-8"):
        for p in line.replace(";", " ").replace(",", " ").split():
            if p.lower().startswith("0x") and len(p) == 42:
                OURS.add(p.lower())
except OSError:
    pass


def say(m):
    print(time.strftime("%H:%M:%S"), m, flush=True)


def call(f, tries=8):
    for k in range(tries):
        with LOCK:
            w = W3S[next(RR) % len(W3S)]
        try:
            return f(w)
        except Exception:
            time.sleep(0.5 + k * 0.6)
    return None


def no_quickedit():
    try:
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-10)
        mode = ctypes.c_uint32()
        if k.GetConsoleMode(h, ctypes.byref(mode)):
            k.SetConsoleMode(h, (mode.value & ~0x0040) | 0x0080)
    except Exception:
        pass


coins = {}  # pair -> dict(token, wb0, block, fam, creator, prev)
seen_in = collections.defaultdict(set)  # (fam, sender) -> pairs
COLS = ["time", "family", "pair", "token", "block", "idx", "age_s", "kind", "bnb", "pool_after", "sender", "role", "method", "gwei", "tx",
        "tokens", "gas_limit", "nonce", "to"]  # 10.11: tokens = + bought / - sold (token units / 1e18), for the wallets' positions


def rotate_old():
    """A file with the old header goes to bot_watch_v1.csv (the analysis reads both)."""
    try:
        with open(OUT, encoding="utf-8-sig") as f:
            head = f.readline().strip().split(";")
        if head != COLS:
            os.replace(OUT, OUT.replace(".csv", "_v1.csv"))
    except OSError:
        pass


def write(rows):
    new = not os.path.exists(OUT)
    with open(OUT, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLS, delimiter=";")
        if new:
            w.writeheader()
        w.writerows(rows)


def family_of(token):
    c = call(lambda w: bytes(w.eth.get_code(token)))
    if not c:
        return None
    h = Web3.keccak(c).hex().replace("0x", "")[:10]
    if h in CODES:
        return CODES[h]
    if h == "18449a1624":
        s = call(lambda w: w.eth.get_storage_at(token, 6))
        return GUARDS.get(s.hex()[-40:].lower()) if s is not None else None
    return None


def role(fam, c, a):
    if a in OURS:
        return "мы"
    if a == c.get("creator"):
        return "создатель"
    return "бот семьи" if len(seen_in[(fam, a)]) >= 3 else "посторонний"


def main():
    no_quickedit()
    try:
        lock = socket.socket()
        lock.bind(("127.0.0.1", 47664))
        lock.listen(1)  # 18.11: the watchdog sees a bot alive only by a LISTENING port; bind alone made it start a copy every 2 min
    except OSError:
        return say("наблюдатель уже запущен")
    rotate_old()
    head = call(lambda w: w.eth.block_number)
    last = head - int(10 * 60 / SPB)  # a restart catches the coins of the last 10 min
    say(f"наблюдатель за ботами запущен, файл {OUT}")
    pool = ThreadPoolExecutor(6)
    while True:
        try:
            head = call(lambda w: w.eth.block_number)
            if not head or head <= last:
                time.sleep(2)
                continue
            to = min(head, last + 200)
            for l in call(lambda w: w.eth.get_logs({"address": F, "fromBlock": last + 1, "toBlock": to, "topics": [PC]})) or []:
                t0, t1 = "0x" + l["topics"][1].hex()[-40:], "0x" + l["topics"][2].hex()[-40:]
                if WB not in (t0.lower(), t1.lower()):
                    continue
                tok = Web3.to_checksum_address(t1 if t0.lower() == WB else t0)
                fam = family_of(tok)
                if fam:
                    pair = Web3.to_checksum_address("0x" + bytes(l["data"])[12:32].hex())
                    coins[pair] = dict(token=tok, wb0=t0.lower() == WB, block=l["blockNumber"], fam=fam, creator=None, prev=None)
                    say(f"новая монета {fam}: {tok}")
            live = [p for p, c in coins.items() if (to - c["block"]) * SPB < TRACK_S]
            for p in [p for p in coins if p not in live]:
                coins.pop(p)
            if live:
                logs = []
                for i in range(0, len(live), 30):
                    logs += call(lambda w: w.eth.get_logs({"address": live[i:i + 30], "fromBlock": last + 1, "toBlock": to, "topics": [[SW, SYNC]]})) or []
                txs = list({l["transactionHash"] for l in logs})
                info = dict(zip(txs, pool.map(lambda h: call(lambda w: w.eth.get_transaction(h)), txs)))
                rows = []
                swaps = {}
                for l in logs:
                    if l["topics"][0].hex().replace("0x", "") == SW.replace("0x", ""):
                        swaps[(l["transactionHash"], l["address"])] = l
                for l in sorted(logs, key=lambda x: (x["blockNumber"], x["logIndex"])):
                    if l["topics"][0].hex().replace("0x", "") != SYNC.replace("0x", ""):
                        continue
                    pair = Web3.to_checksum_address(l["address"])
                    c = coins.get(pair)
                    t = info.get(l["transactionHash"])
                    if not c or not t:
                        continue
                    d = bytes(l["data"])
                    r0, r1 = int.from_bytes(d[:32], "big"), int.from_bytes(d[32:64], "big")
                    bnb = (r0 if c["wb0"] else r1) / 1e18
                    a = t["from"].lower()
                    if c["creator"] is None:
                        c["creator"] = a
                    m = METHODS.get(bytes(t["input"])[:4].hex(), bytes(t["input"])[:4].hex())
                    prev = c["prev"]
                    dv = bnb - prev if prev is not None else bnb
                    c["prev"] = bnb
                    sw = swaps.get((l["transactionHash"], l["address"]))
                    tokens = ""
                    if sw is not None:
                        v = [int.from_bytes(bytes(sw["data"])[i:i + 32], "big") for i in (0, 32, 64, 96)]  # a0In a1In a0Out a1Out
                        tokens = round(((v[3] - v[1]) if c["wb0"] else (v[2] - v[0])) / 1e18, 6)
                    if prev is None:
                        kind = "старт"
                    elif sw is None:  # Sync without Swap = Mint/Burn: liquidity, not trading
                        kind = "слив" if dv < 0 else "ликвидность"
                    elif dv < 0 and prev > 0 and -dv > 0.5 * prev:  # one sale takes half the pool: a drain by any method
                        kind = "слив"
                    else:
                        kind = "покупка" if dv > 0 else "продажа"
                    if kind == "покупка":
                        seen_in[(c["fam"], a)].add(pair)
                    rows.append(dict(time=time.strftime("%Y-%m-%d %H:%M:%S"), family=c["fam"], pair=pair, token=c["token"], block=l["blockNumber"], idx=t["transactionIndex"],
                                     age_s=round((l["blockNumber"] - c["block"]) * SPB), kind=kind, bnb=round(dv, 6), pool_after=round(bnb, 4), sender=a,
                                     role=role(c["fam"], c, a), method=m, gwei=round(t["gasPrice"] / 1e9, 3), tx=Web3.to_hex(l["transactionHash"]),
                                     tokens=tokens, gas_limit=t.get("gas", ""), nonce=t.get("nonce", ""), to=(t.get("to") or "").lower()))
                    if kind == "слив":
                        say(f"слив {c['fam']} {c['token'][:10]} на {(l['blockNumber'] - c['block']) * SPB / 60:.1f} мин от {a[:10]} ({role(c['fam'], c, a)})")
                    elif kind == "покупка" and role(c["fam"], c, a) in ("посторонний", "мы"):
                        say(f"посторонняя покупка {c['fam']} {c['token'][:10]} {dv:.4f} BNB от {a[:10]} на {(l['blockNumber'] - c['block']) * SPB:.0f} с")
                if rows:
                    write(rows)
            last = to
            time.sleep(1.5)
        except Exception as e:
            say("ошибка: " + str(e)[:120])
            time.sleep(5)


if __name__ == "__main__":
    main()
