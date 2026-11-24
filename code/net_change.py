"""18.11 (user): did anything change in nets 1-4 after the restart of 17.11 evening? Read-only, public nodes, no keys.
For the new coin of each net and its last normal coin before the 16.11 stop:
  token code (size, hash, function selectors, storage slots 0-15: slot 6 = guard), pool (start/now BNB, buys/sells, buyers, overlap of
  buyers with the old coin), creator (nonce = how many txs it sent, balance), drain.
Out: net_change.txt.  Usage: python net_change.py"""
import collections
import csv
import hashlib
import json
import os
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
NODES = ["https://rpc-bsc.48.club", "https://rpc.48.club", "https://bsc-dataseed.bnbchain.org"]
WBNB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
FACTORY = "0xca143ce32fe78f1f7019d7d551a6402fc5350c73"
SWAP = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
SYNC = "0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1"


def call(m, p, tries=5):
    err = None
    for k in range(tries):
        u = NODES[k % len(NODES)]
        try:
            r = json.loads(urllib.request.urlopen(urllib.request.Request(u, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": m, "params": p}).encode(),
                                                                         headers={"Content-Type": "application/json"}), timeout=40).read())
            if "error" in r:
                raise RuntimeError(str(r["error"])[:120])
            return r["result"]
        except Exception as e:
            err = e
            time.sleep(0.5 + k)
    raise RuntimeError(f"{m}: {err}")


HEAD = int(call("eth_blockNumber", []), 16)
TS = {}


def ts(b):
    if b not in TS:
        TS[b] = int(call("eth_getBlockByNumber", [hex(b), False])["timestamp"], 16)
    return TS[b]


def block_at(t):
    guess = HEAD - int((ts(HEAD) - t) / 0.45)
    lo, hi = guess - 20000, min(HEAD, guess + 20000)
    while ts(lo) >= t:
        lo -= 20000
    while hi < HEAD and ts(hi) < t:
        hi = min(HEAD, hi + 20000)
    while hi - lo > 1:
        m = (lo + hi) // 2
        if ts(m) < t:
            lo = m
        else:
            hi = m
    return hi


def selectors(code):
    b = bytes.fromhex(code[2:])
    out, i = set(), 0
    while i < len(b):
        op = b[i]
        if 0x60 <= op <= 0x7F:
            n = op - 0x5F
            if op == 0x63:
                out.add(b[i + 1:i + 5].hex())
            i += n + 1
        else:
            i += 1
    return out


def code_info(tok):
    c = call("eth_getCode", [tok, "latest"])
    slots = [call("eth_getStorageAt", [tok, hex(s), "latest"]) for s in range(16)]
    return dict(size=(len(c) - 2) // 2, sha=hashlib.sha256(bytes.fromhex(c[2:])).hexdigest()[:12], sel=selectors(c), slots=slots, code=c)


def pair_of(tok):
    r = call("eth_call", [{"to": FACTORY, "data": "0xe6a43905" + tok[2:].lower().rjust(64, "0") + WBNB[2:].rjust(64, "0")}, "latest"])
    return "0x" + r[-40:]


def logs(pair, b0, b1):
    out = []
    b = b0
    while b <= b1:
        e = min(b + 399, b1)
        out += call("eth_getLogs", [{"address": pair, "fromBlock": hex(b), "toBlock": hex(e), "topics": [[SWAP, SYNC]]}])
        b = e + 1
    return out


def pool_study(tok, t_launch, minutes):
    pair = pair_of(tok)
    t0 = call("eth_call", [{"to": pair, "data": "0x0dfe1681"}, "latest"])
    wbnb0 = ("0x" + t0[-40:]).lower() == WBNB
    b0 = block_at(t_launch - 30)
    b1 = min(HEAD, b0 + int(minutes * 60 / 0.45))
    lg = logs(pair, b0, b1)
    syncs, buys, sells, txs = [], [], [], set()
    for l in lg:
        d = l["data"][2:]
        w = [int(d[i:i + 64], 16) for i in range(0, len(d), 64)]
        blk = int(l["blockNumber"], 16)
        if l["topics"][0] == SYNC:
            syncs.append((blk, (w[0] if wbnb0 else w[1]) / 1e18))
            if len(syncs) == 1:
                first_hash = l["transactionHash"]
        else:
            a0i, a1i, a0o, a1o = w
            bnb_in = (a0i if wbnb0 else a1i) / 1e18
            bnb_out = (a0o if wbnb0 else a1o) / 1e18
            (buys if bnb_in > 0 else sells).append((blk, bnb_in or bnb_out, l["transactionHash"]))
            txs.add(l["transactionHash"])
    senders = {}
    for h in list(txs)[:400]:
        t = call("eth_getTransactionByHash", [h])
        senders[h] = (t["from"].lower(), (t.get("to") or "").lower())
    start = syncs[0] if syncs else None
    peak = max(syncs, key=lambda s: s[1]) if syncs else None
    drain = next(((b, v) for (b, v), (pb, pv) in zip(syncs[1:], syncs) if pv > 3 and v < 0.2 * pv), None)
    cur = call("eth_call", [{"to": pair, "data": "0x0902f1ac"}, "latest"])
    r0, r1 = int(cur[2:66], 16), int(cur[66:130], 16)
    creator = call("eth_getTransactionByHash", [first_hash])["from"].lower() if syncs else None
    return dict(creator=creator, pair=pair, b0=b0, start=start, peak=peak, drain=drain, now=(r0 if wbnb0 else r1) / 1e18, buys=buys, sells=sells, senders=senders,
                first_block=syncs[0][0] if syncs else None)


def creator_info(a):
    return dict(nonce=int(call("eth_getTransactionCount", [a, "latest"]), 16), bal=int(call("eth_getBalance", [a, "latest"]), 16) / 1e18)


# coins: new (after the 17.11 restart) and the last normal one before the stop
G1 = list(csv.DictReader(open(os.path.join(HERE, "guard_launches.csv"), encoding="utf-8-sig"), delimiter=";"))
G2 = list(csv.DictReader(open(os.path.join(HERE, "guard2_launches.csv"), encoding="utf-8-sig"), delimiter=";"))
NEW = {1: "0x2e4EDc207B22ea3bacb5Fbc7cdd44a6aFA5cA1Bc", 2: "0xeF6A966eAfeB27f6a755c988700961bc3F8E1788",
       3: "0x66f549F9EfBd5F02975ae0790D1B50dE74B10D65", 4: "0x81836a81d02a8c8D4FE52F95c4d6b2DCAa3b235E"}
OLD = {1: "0x4bacB84Cb5411EC1f7566a91174C028740fAaAE3"}
STOP = time.mktime(time.strptime("2026-11-16 13:20:00", "%Y-%m-%d %H:%M:%S"))
for net in (3, 4):
    c = [r for r in G2 if r["symbol"] == f"net{net}" and int(r["ts"]) < STOP]
    if c:
        OLD[net] = c[-1]["token"]
c = [r for r in G2 if r["symbol"] in ("", "net2") and int(r["ts"]) < STOP - 3 * 86400 + 86400 * 2 and r["token"] != NEW[2]]
bw = list(csv.DictReader(open(os.path.join(HERE, "bot_watch.csv"), encoding="utf-8-sig"), delimiter=";"))
for net in (2, 3, 4):
    if net not in OLD or net == 2:
        old = [r for r in bw if r["family"].endswith(f"(сеть {net})") and r["time"] < "2026-11-16 13:20"]
        if old:
            OLD[net] = old[-1]["token"]
launch_ts = {}
for r in G1 + G2:
    launch_ts.setdefault(r["token"].lower(), int(r["ts"]))
for r in bw:
    if r["token"].lower() not in launch_ts:
        try:
            launch_ts[r["token"].lower()] = int(time.mktime(time.strptime(r["time"], "%Y-%m-%d %H:%M:%S"))) - int(float(r["age_s"] or 0))
        except Exception:
            pass

L = [f"Сети 1–4: новая монета после перезапуска 17.11 против последней нормальной до остановки 16.11 (голова {HEAD}, {time.strftime('%d.%m %H:%M')})"]
for net in (1, 2, 3, 4):
    new, old = NEW[net], OLD.get(net)
    L.append(f"\n=== СЕТЬ {net} ===  новая {new}   старая {old}")
    res = {}
    for name, tok in (("новая", new), ("старая", old)):
        if not tok:
            continue
        ci = code_info(tok)
        t_l = launch_ts.get(tok.lower())
        ps = pool_study(tok, t_l, 40) if t_l else None
        res[name] = (ci, ps)
    if "новая" in res and "старая" in res:
        cn, co = res["новая"][0], res["старая"][0]
        same = cn["code"] == co["code"]
        L.append(f"код: новая {cn['size']} байт sha {cn['sha']}, старая {co['size']} байт sha {co['sha']} → {'ОДИНАКОВЫЙ' if same else 'РАЗНЫЙ'}")
        if not same:
            L.append(f"  функций (селекторы): только в новой {sorted(cn['sel'] - co['sel'])}, только в старой {sorted(co['sel'] - cn['sel'])}")
            # where the bytes differ (constructor-set addresses are usually the only difference)
            a, b = bytes.fromhex(cn["code"][2:]), bytes.fromhex(co["code"][2:])
            diff = sum(x != y for x, y in zip(a, b)) + abs(len(a) - len(b))
            L.append(f"  отличающихся байт {diff}")
        for s in range(16):
            vn, vo = cn["slots"][s], co["slots"][s]
            if vn != vo or s == 6:
                L.append(f"  слот {s}: новая {vn[-40:] if int(vn, 16) else '0'}  старая {vo[-40:] if int(vo, 16) else '0'}")
    for name, (ci, ps) in res.items():
        if not ps:
            L.append(f"{name}: время запуска неизвестно — пул не разбирал")
            continue
        snd = collections.Counter(s for s, _ in ps["senders"].values())
        cr = None
        if ps["buys"] or ps["sells"]:
            pass
        st = ps["start"][1] if ps["start"] else 0
        pk = ps["peak"][1] if ps["peak"] else 0
        dr = ps["drain"]
        age = lambda b: (b - ps["first_block"]) * 0.45 / 60 if ps["first_block"] else 0
        L.append(f"{name}: пул {ps['pair']}, старт {st:.2f} BNB, пик {pk:.2f}, сейчас {ps['now']:.2f}, покупок {len(ps['buys'])} "
                 f"({sum(v for _, v, _ in ps['buys']):.2f} BNB), продаж {len(ps['sells'])}, покупателей {len(snd)}"
                 + (f", слив на {age(dr[0]):.1f} мин" if dr else ", слива в первые 40 мин нет"))
        if ps["buys"]:
            L.append(f"   первая покупка на {age(ps['buys'][0][0]):.1f} мин, последняя на {age(ps['buys'][-1][0]):.1f} мин; суммы: "
                     + ", ".join(f"{v:.4f}" for _, v, _ in ps["buys"][:12]))
        res[name] = (ci, ps, set(snd))
    if all(k in res and len(res[k]) == 3 for k in ("новая", "старая")):
        a, b = res["новая"][2], res["старая"][2]
        L.append(f"общих покупателей/продавцов с прошлой монетой: {len(a & b)} из {len(a)} (у старой {len(b)})")
    for name in ("новая", "старая"):
        if name in res and len(res[name]) == 3 and res[name][1]["creator"]:
            a = res[name][1]["creator"]
            ci = creator_info(a)
            own = sum(1 for s, _ in res[name][1]["senders"].values() if s == a)
            L.append(f"создатель {name}: {a}, исходящих транзакций всего {ci['nonce']}, баланс сейчас {ci['bal']:.3f} BNB, его свапов в пуле {own}")
print("\n".join(L))
open(os.path.join(HERE, "net_change.txt"), "w", encoding="utf-8").write("\n".join(L))
