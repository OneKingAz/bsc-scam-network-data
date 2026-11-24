"""07.11 (user): a service of its own, 24/7: finds networks that work by our rule and shows them in the panel (button "Добавить").
Our rule: the coin's creator pays a wallet exactly the sum of its buy a few blocks before that wallet buys.
Read-only, public node, no keys, no money. Watches the BNB pools created in the last 3 hours; for every buy >= 0.01 BNB checks
whether the buyer got that BNB within 10 blocks before, from whom (the creator?) and how big the buy was against the pool.
Out: net_finder.json (families, every 30 s), net_finder_events.csv (every checked buy, 48 h kept). One copy only (port 47663).
Run: run_net_finder.cmd (the watchdog keeps it running)."""
import collections
import csv
import json
import os
import socket
import statistics as st
import sys
import threading
import time

def _no_quickedit():
    """07.11: a click in the console window (QuickEdit selection) freezes print() and with it the whole program until Enter is pressed
    (scam_watch stood ~10 h like this). QuickEdit is switched off for this console."""
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h, mode = k.GetStdHandle(-10), ctypes.c_uint()
        if k.GetConsoleMode(h, ctypes.byref(mode)):
            k.SetConsoleMode(h, (mode.value | 0x80) & ~0x40)
    except Exception:
        pass


_no_quickedit()
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

try:
    LOCK = socket.socket()
    LOCK.bind(("127.0.0.1", 47663))
    LOCK.listen(1)
except OSError:
    print("уже запущен")
    sys.exit(0)

NODES = ["https://rpc.48.club", "https://bsc-dataseed.bnbchain.org", "https://bsc-rpc.publicnode.com"]
WS = []
for u in NODES:
    w = Web3(Web3.HTTPProvider(u, request_kwargs={"timeout": 15}))
    w.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    WS.append(w)
w3 = WS[0]
SPB, WBNB = 0.45, "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
FACTORY = Web3.to_checksum_address("0xca143ce32fe78f1f7019d7d551a6402fc5350c73")
T = lambda s: "0x" + Web3.keccak(text=s).hex().replace("0x", "")
PAIR, SWAP, SYNC = T("PairCreated(address,address,address,uint256)"), T("Swap(address,uint256,uint256,uint256,uint256,address)"), T("Sync(uint112,uint112)")
PAIR_ABI = [{"name": "getReserves", "inputs": [], "outputs": [{"type": "uint112"}, {"type": "uint112"}, {"type": "uint32"}], "stateMutability": "view", "type": "function"}]
BACK, KEEP_PAIRS, WINDOW = 10, 3 * 3600, 24 * 3600
OUT = os.path.join(HERE, "net_finder.json")
EVENTS = os.path.join(HERE, "net_finder_events.csv")
COLS = ["t", "block", "size", "hash", "slot6", "token", "pair", "sender", "bnb", "pool", "funded", "lag", "payer", "ratio", "creator_paid"]
KNOWN = {"5faca89801": "сеть 1"}  # code hashes we trade; guards come from hop_trader's nets
GUARDS = {"0xdbbf08242c22a634960ceaad44d9f2564fb26452": "сеть 2", "0x623e7c23c7c228095f94d9bd567eae55cb87a966": "сеть 3",
          "0xa70b3f34e3ca7d4665b794ce6bb29168b1d2e40d": "сеть 4"}


def say(msg):
    print(time.strftime("%H:%M:%S ") + msg, flush=True)


def call(fn, tries=4):
    err = None
    for k in range(tries):
        try:
            return fn(WS[k % len(WS)])
        except Exception as e:
            err = e
            time.sleep(0.4 + k)
    raise err


pairs = {}  # pair -> dict(token, wb0, size, hash, slot6, t, creator)
events = deque_events = collections.deque()
lock = threading.Lock()


def load_events():
    if not os.path.exists(EVENTS):
        return
    now, keep = time.time(), []
    try:
        with open(EVENTS, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f, delimiter=";"):
                if now - float(r["t"]) < 2 * WINDOW:
                    keep.append(r)
    except Exception:
        return
    with open(EVENTS, "w", newline="", encoding="utf-8-sig") as f:  # 48 h kept, the rest dropped
        w = csv.DictWriter(f, fieldnames=COLS, delimiter=";")
        w.writeheader()
        w.writerows(keep)
    for r in keep:
        if now - float(r["t"]) < WINDOW:
            events.append(r)


def save_event(r):
    with lock:
        events.append({k: str(v) for k, v in r.items()})
        try:
            new = not os.path.exists(EVENTS)
            with open(EVENTS, "a", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=COLS, delimiter=";")
                if new:
                    w.writeheader()
                w.writerow(r)
        except OSError:
            pass


def add_pairs(lo, hi, head):
    new = []
    for b in range(lo, hi + 1, 1000):
        for lg in call(lambda x: x.eth.get_logs({"address": FACTORY, "fromBlock": b, "toBlock": min(hi, b + 999), "topics": [PAIR]})):
            t0, t1 = "0x" + lg["topics"][1].hex()[-40:], "0x" + lg["topics"][2].hex()[-40:]
            if WBNB not in (t0.lower(), t1.lower()):
                continue
            new.append((Web3.to_checksum_address("0x" + bytes(lg["data"])[12:32].hex()), Web3.to_checksum_address(t1 if t0.lower() == WBNB else t0), t0.lower() == WBNB,
                        time.time() - (head - lg["blockNumber"]) * SPB, lg["blockNumber"]))

    def info(x):
        pair, tok, wb0, t, blk = x
        try:
            c = bytes(call(lambda w: w.eth.get_code(tok)))
            if not 100 < len(c) < 30000:
                return None
            s6 = bytes(call(lambda w: w.eth.get_storage_at(tok, 6))).hex()[-64:]
            slot6 = "0x" + s6[-40:] if s6[:24] == "0" * 24 and int(s6, 16) else ""
            return pair, dict(token=tok, wb0=wb0, size=len(c), hash=Web3.keccak(c).hex().replace("0x", "")[:10], slot6=slot6.lower(), t=t, block=blk, creator=None)
        except Exception:
            return None
    with ThreadPoolExecutor(6) as ex:
        for v in ex.map(info, new):
            if v:
                pairs[v[0]] = v[1]


def creator_of(pair, p):
    """The sender of the pool's first Sync. 07.11: an error used to be kept as "no creator" for good, and every payment of that coin
    counted as not the creator's (net 4: the payer was the creator)."""
    if not p["creator"]:
        try:
            # 07.11 19:59: the start was guessed from the time (0.45 s a block) and missed: every payment of net 3/4 looked like not the creator's
            head = call(lambda w: w.eth.block_number)
            b0 = p["block"]
            logs = call(lambda w: w.eth.get_logs({"address": pair, "fromBlock": b0, "toBlock": min(head, b0 + 1999), "topics": [SYNC]}))
            if logs:
                p["creator"] = call(lambda w: w.eth.get_transaction(logs[0]["transactionHash"]))["from"].lower()
        except Exception:
            pass
    return p["creator"] or ""


blocks = {}


def block_txs(n):
    if n not in blocks:
        blocks[n] = call(lambda w: w.eth.get_block(n, full_transactions=True))["transactions"]
        while len(blocks) > 300:
            blocks.pop(min(blocks))
    return blocks[n]


def check(ev):
    b, pair, bnb, txh = ev
    p = pairs.get(pair)
    if not p:
        return
    r = dict(t=round(time.time()), block=b, size=p["size"], hash=p["hash"], slot6=p["slot6"], token=p["token"], pair=pair, sender="", bnb=round(bnb, 5),
             pool="", funded=0, lag="", payer="", ratio="", creator_paid=0)
    try:
        tx = call(lambda w: w.eth.get_transaction(txh))
        S = tx["from"]
        r["sender"] = S.lower()
        # 17.11: the old test wanted the wallet nearly empty before the payment; the network 5 bots keep a rest on the wallet and
        # their payer (0x274bb656) was never seen. Now: the balance GREW by at least half of the buy within BACK blocks.
        b0 = call(lambda w: w.eth.get_balance(S, b - BACK)) / 1e18
        if call(lambda w: w.eth.get_balance(S, b - 1)) / 1e18 - b0 >= bnb * 0.5:
            lo, hi = b - BACK, b - 1
            while hi - lo > 1:
                m = (lo + hi) // 2
                if call(lambda w: w.eth.get_balance(S, m)) / 1e18 - b0 >= bnb * 0.5:
                    hi = m
                else:
                    lo = m
            pay = next((t for t in block_txs(hi) if (t["to"] or "").lower() == S.lower() and t["value"] > 0), None)
            r.update(funded=1, lag=b - hi)
            if pay:
                r.update(payer=pay["from"].lower(), ratio=round(pay["value"] / 1e18 / bnb, 4))
                r["creator_paid"] = int(r["payer"] == creator_of(pair, p))
                rb = call(lambda w: w.eth.contract(pair, abi=PAIR_ABI).functions.getReserves().call(block_identifier=b - 1))
                r["pool"] = round((rb[0] if p["wb0"] else rb[1]) / 1e18, 4)
    except Exception:
        r["funded"] = "?"
    save_event(r)


def families():
    """Families by contract code; a slot-6 address shared by several coins (a guard) splits a family into networks."""
    now = time.time()
    with lock:
        ev = [e for e in events if now - float(e["t"]) < WINDOW]
    slot_coins = collections.defaultdict(set)
    for e in ev:
        if e["slot6"]:
            slot_coins[(e["hash"], e["slot6"])].add(e["token"])
    g = collections.defaultdict(list)
    for e in ev:
        shared = e["slot6"] and len(slot_coins[(e["hash"], e["slot6"])]) >= 2
        g[(e["hash"], e["slot6"] if shared else "")].append(e)
    out = []
    for (h, guard), es in g.items():
        f1 = [e for e in es if e["funded"] == "1"]
        cp = [e for e in f1 if e["creator_paid"] == "1"]
        exact = [e for e in cp if e["ratio"] and 0.98 <= float(e["ratio"]) <= 1.05]
        strong = [e for e in exact if e["pool"] and float(e["pool"]) > 0 and float(e["bnb"]) / float(e["pool"]) >= 0.03]
        rise = [2 * float(e["bnb"]) / float(e["pool"]) * 100 - 0.5 for e in strong]
        coins = {e["token"] for e in es}
        known = GUARDS.get(guard) or KNOWN.get(h) or ""
        ok = len(exact) >= 10 and len({e["token"] for e in exact}) >= 2 and len(exact) >= 0.5 * max(1, len(f1))
        out.append(dict(key=f"{h}:{guard or '*'}", hash=h, guard=guard, size=int(es[0]["size"]), coins=len(coins), buys=len(es), funded=len(f1), creator_paid=len(cp),
                        exact=len(exact), strong=len(strong), strong_coins=len({e["token"] for e in strong}), rise=round(st.median(rise), 1) if rise else None,
                        lags=collections.Counter(e["lag"] for e in exact).most_common(4), last=max(float(e["t"]) for e in es), known=known, fits=ok,
                        examples=sorted({e["token"] for e in exact})[:3],
                        payers=collections.Counter(e["payer"] for e in f1 if e["payer"] and e["ratio"] and 0.98 <= float(e["ratio"]) <= 1.05 and now - float(e["t"]) < 7200).most_common(5)))
    out.sort(key=lambda x: (-x["fits"], -x["strong"], -x["exact"], -x["buys"]))
    return out


def writer():
    while True:
        try:
            data = dict(at=time.time(), window_h=WINDOW // 3600, pairs=len(pairs), checked=len(events), families=families()[:40])
            with open(OUT + ".tmp", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(OUT + ".tmp", OUT)
        except Exception as e:
            say("запись: " + str(e)[:80])
        time.sleep(30)


def main():
    load_events()
    head = call(lambda w: w.eth.block_number)
    add_pairs(head - int(KEEP_PAIRS / SPB), head, head)
    say(f"служба поиска сетей: пулов за 3 ч {len(pairs)}, покупок в памяти {len(events)}")
    threading.Thread(target=writer, daemon=True).start()
    pool = ThreadPoolExecutor(8)
    last = head
    queue = []
    while True:
        try:
            h = call(lambda w: w.eth.block_number)
            if h <= last:
                time.sleep(0.4)
                continue
            if h - last > 60:
                say(f"отстал на {h - last} блоков, пропускаю покупки (новые пулы всё равно добавлю)")
                add_pairs(last + 1, h - 5, h)  # 17.11: the skipped blocks used to lose their new pools for good (2 pools tracked)
                last = h - 5
            # 17.11: new pools every round, not once per 200 blocks: a failed round no longer loses them
            add_pairs(last + 1, h, h)
            if h // 200 != last // 200:
                now = time.time()
                for k in [k for k, v in pairs.items() if now - v["t"] > KEEP_PAIRS]:
                    pairs.pop(k, None)
            logs = call(lambda w: w.eth.get_logs({"fromBlock": last + 1, "toBlock": h, "topics": [SWAP]}))
            for lg in logs:
                p = pairs.get(Web3.to_checksum_address(lg["address"]))
                if not p:
                    continue
                d = bytes(lg["data"])
                bnb = int.from_bytes(d[0:32] if p["wb0"] else d[32:64], "big") / 1e18
                if bnb >= 0.01:
                    queue.append(pool.submit(check, (lg["blockNumber"], Web3.to_checksum_address(lg["address"]), bnb, lg["transactionHash"])))
            queue = [q for q in queue if not q.done()]
            if len(queue) > 400:
                say(f"очередь проверок {len(queue)}: не успеваю, часть покупок пропущу")
                for q in queue[200:]:
                    q.cancel()
            last = h
        except Exception as e:
            say("ошибка: " + str(e)[:100])
            time.sleep(3)


if __name__ == "__main__":
    main()
