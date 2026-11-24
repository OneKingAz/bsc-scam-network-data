"""The panel's charts of the signal test: price of every coin and of every attempt (buy, sell, other buys and sells).
Read-only, NodeReal archive; called by after_exit.py, so the running panel gets them without a restart.
Cache: signal_charts_cache.json (only new blocks are read)."""
import csv, json, os, time
from web3 import Web3

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(HERE, "signal_charts_cache.json")
SYNC = "0x" + Web3.keccak(text="Sync(uint112,uint112)").hex().replace("0x", "")
SWAP = "0x" + Web3.keccak(text="Swap(address,uint256,uint256,uint256,uint256,address)").hex().replace("0x", "")
SPB, LIFE, BEFORE, AFTER, DUST = 0.45, 14000, 15, 30, 1e-5  # s per block; blocks of one coin; zoom window, s; a smaller buy is not a bot's


def call(fn, k=5):
    for i in range(k):
        try:
            return fn()
        except Exception:
            if i == k - 1:
                raise
            time.sleep(1 + i)


def fetch(a, B, c, head):
    """Reads the coin's new Sync and Swap logs into c."""
    pair = Web3.to_checksum_address(c["pair"])
    is0 = int(B.WBNB, 16) < int(c["token"], 16)
    end = min(head, c["b0"] + LIFE)
    while c["next"] <= end:
        to = min(end, c["next"] + 399)  # 18.11: the free 48 Club node: <= ~500 blocks per request
        for e in call(lambda: a.eth.get_logs({"address": pair, "fromBlock": c["next"], "toBlock": to, "topics": [[SYNC, SWAP]]})):
            d, topic = bytes(e["data"]), e["topics"][0].hex().replace("0x", "")
            if topic == SYNC[2:]:
                r0, r1 = int.from_bytes(d[:32], "big"), int.from_bytes(d[32:64], "big")
                c["sy"].append([e["blockNumber"], (r0 if is0 else r1) / 1e18, (r1 if is0 else r0) / 1e18])
            else:
                x = [int.from_bytes(d[i:i + 32], "big") for i in (0, 32, 64, 96)]
                c["sw"].append([e["blockNumber"], (x[0] if is0 else x[1]) / 1e18, (x[2] if is0 else x[3]) / 1e18])
        c["next"] = to + 1


def price(s):
    return s[1] / s[2] if s[2] else 0


def coin_out(c, rows):
    if not c["sy"]:
        return None
    last = {s[0]: s for s in c["sy"]}  # the block's last price
    blocks = sorted(last)
    t0, peak = blocks[0], max(s[1] for s in c["sy"])
    i_peak = next(i for i, s in enumerate(c["sy"]) if s[1] == peak)
    drain = next((s[0] for s in c["sy"][i_peak:] if s[1] < 0.2 * peak), None)
    p0 = price(last[t0])
    mins = lambda b: round((b - t0) * SPB / 60, 3)
    pct = lambda s, ref: round((price(s) / ref - 1) * 100, 2) if ref else 0
    out = dict(token=c["token"], net=rows[0].get("_net", 1), sym=rows[0]["symbol"], kind=rows[0]["kind"], drain=mins(drain) if drain else None,
               end=mins(c["next"] - 1), pts=[[mins(b), pct(last[b], p0)] for b in blocks if not drain or b <= drain + 130], att=[])
    for r in rows:
        land, sell, got = int(r["land_block"]), int(r["sell_block"]), float(r["got_bnb"] or 0)
        ref = next((price(last[b]) for b in reversed(blocks) if b < land), p0)
        sec = lambda b: round((b - land) * SPB, 2)
        near = lambda b: -BEFORE <= sec(b) <= AFTER
        stake = round(got - float(r["gas_bnb"]) - float(r["pnl_bnb"]), 6)  # the attempts have different stakes
        ours_buy = next((s for s in c["sw"] if s[0] == land and abs(s[1] - stake) < 1e-9), None)
        ours_sell = next((s for s in c["sw"] if s[0] == sell and got and abs(s[2] - got) < 1e-7), None)
        others = [s for s in c["sw"] if s is not ours_buy and s is not ours_sell]
        buys = [s for s in others if s[1] > DUST]
        out["att"].append(dict(
            n=int(r["attempt"]), time=r["time"][11:19], src=r["source"], share=float(r["share_pct"] or 0), pct=float(r["pnl_pct"] or 0), buy=mins(land), sell=mins(sell),
            at=pct(last[next((b for b in blocks if b >= land), blocks[-1])], p0),
            hold=sec(sell), pts=[[sec(b), pct(last[b], ref)] for b in blocks if near(b)],
            buys=[sec(s[0]) for s in buys if near(s[0])], sells=[sec(s[0]) for s in others if s[2] > DUST and near(s[0])],
            next_buy=next((sec(s[0]) for s in buys if s[0] >= land), None)))
    return out


def build(a, B, head):
    """All the attempts by coin, in the order they were made, and how many attempts the file has."""
    rows = []
    for name, net in [("signal_test.csv", 1)] + [(f"signal_test{n}.csv", n) for n in range(2, 40)]:
        path = os.path.join(HERE, name)
        if os.path.exists(path):
            rows += [dict(r, _net=net) for r in csv.DictReader(open(path, encoding="utf-8-sig"), delimiter=";") if r["pnl_bnb"] and r["land_block"] and r["sell_block"]]
    launch = {}
    for p in (os.path.join(ROOT, "old_data", "analysis", "guard_launches.csv"), os.path.join(HERE, "guard_launches.csv"), os.path.join(HERE, "guard2_launches.csv")):
        if os.path.exists(p):
            launch.update({l["token"].lower(): l for l in csv.DictReader(open(p, encoding="utf-8-sig"), delimiter=";")})
    try:
        cache = json.load(open(CACHE, encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    now = call(lambda: a.eth.get_block(head).timestamp)
    coins, out = {}, []
    for r in rows:
        coins.setdefault(r["token"].lower(), []).append(r)
    for t, rs in coins.items():
        l = launch.get(t)
        if not l:
            continue
        b0 = head - int((now - int(l["ts"])) / SPB) - 60
        c = cache.setdefault(t, dict(token=rs[0]["token"], pair=l["pair"], b0=b0, next=b0, sy=[], sw=[]))
        fetch(a, B, c, head)
        co = coin_out(c, rs)
        if co:
            out.append(co)
    with open(CACHE + ".tmp", "w", encoding="utf-8") as f:
        json.dump(cache, f)
    os.replace(CACHE + ".tmp", CACHE)
    return out, len(rows)
