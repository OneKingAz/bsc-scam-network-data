"""13.11 (user): everything about the coins of the networks 3/4 (code 1365, guards 0x623e7c23 / 0xa70b3f34) = our network 7.
Read-only, open nodes only, no keys. Out: net7_study.txt (+ net7_txs.csv).
Part A, bot_watch*.csv (all watched coins): launches, pools, drains, the wallets that trade them (who, how much, when, in how many coins).
Part B, the fresh coins (publicnode keeps the state ~1 h): every transaction of each creator (found by a binary search of its nonce),
who funded it, what it paid to whom and when against the bots' buys, where the drained BNB went; the guard's transactions in the same window.
Usage: python net7_study.py [fresh coins per guard=4]"""
import collections
import csv
import itertools
import os
import statistics as st
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
NFRESH = int(sys.argv[1]) if len(sys.argv) > 1 else 4
SPB = 0.45
GUARDS = {"623e7c23c7c228095f94d9bd567eae55cb87a966": "сеть 3", "a70b3f34e3ca7d4665b794ce6bb29168b1d2e40d": "сеть 4"}
OURS = set()
for line in open(os.path.join(os.path.dirname(HERE), "hop_wallets.txt"), encoding="utf-8"):  # addresses only (no keys)
    p = line.strip().split(";")
    if len(p) > 1:
        OURS.add(p[1].lower())
L = []


def say(s=""):
    print(s, flush=True)
    L.append(s)


def short(a):
    return a[:10] if a else "?"


# ---------------- Part A ----------------
rows, seen = [], set()
for fn in ("bot_watch_v1.csv", "bot_watch.csv"):
    p = os.path.join(HERE, fn)
    if os.path.exists(p):
        for r in csv.DictReader(open(p, encoding="utf-8-sig"), delimiter=";"):
            if r["family"].startswith("1365") and (r["tx"], r["pair"]) not in seen:
                seen.add((r["tx"], r["pair"]))
                r["block"], r["bnb"], r["pool"], r["age"] = int(r["block"]), float(r["bnb"]), float(r["pool_after"]), int(r["age_s"])
                rows.append(r)
by = collections.defaultdict(list)
for r in rows:
    by[r["pair"]].append(r)
coins = []
for p, e in by.items():
    e.sort(key=lambda r: (r["block"], int(r["idx"])))
    if e[0]["kind"] != "старт":
        continue  # the watcher came in late
    drain = next((r for r in e if r["kind"] == "слив"), None)
    pre = [r for r in e if not drain or r["block"] < drain["block"]]
    coins.append(dict(pair=p, token=e[0]["token"], net=e[0]["family"][-7:-1], start=e[0]["block"], t=e[0]["time"], creator=e[0]["sender"],
                      pool0=e[0]["pool"], peak=max(r["pool"] for r in pre), drain=drain, ev=e,
                      buys=[r for r in pre if r["kind"] == "покупка"], sells=[r for r in pre if r["kind"] == "продажа"]))
coins.sort(key=lambda c: c["start"])
say(f"СЕТЬ 7 = монеты сетей 3 и 4 (код 1365, сторожа 0x623e7c23 / 0xa70b3f34). Разбор {time.strftime('%d.%m %H:%M')}")
say(f"\nЧАСТЬ A. Наблюдатель: монет с начала жизни {len(coins)} ({coins[0]['t'] if coins else ''} – {coins[-1]['t'] if coins else ''})")
for net in ("сеть 3", "сеть 4"):
    cs = [c for c in coins if c["net"] == net]
    if not cs:
        continue
    gaps = [(b["start"] - a["start"]) * SPB / 60 for a, b in zip(cs, cs[1:])]
    d = [c for c in cs if c["drain"]]
    say(f"\n{net}: монет {len(cs)}; интервал между запусками, мин: медиана {st.median(gaps):.1f}, мин {min(gaps):.1f}, макс {max(gaps):.1f}")
    say(f"  создателей разных: {len({c['creator'] for c in cs})} (каждый раз новый кошелёк: {len({c['creator'] for c in cs}) == len(cs)})")
    say(f"  стартовый пул: {collections.Counter(round(c['pool0'], 1) for c in cs).most_common(4)}")
    say(f"  пик пула: медиана {st.median(c['peak'] for c in cs):.2f}, от {min(c['peak'] for c in cs):.2f} до {max(c['peak'] for c in cs):.2f} BNB")
    say(f"  слив: у {len(d)}; минута: медиана {st.median(c['drain']['age'] for c in d) / 60:.1f}, от {min(c['drain']['age'] for c in d) / 60:.1f} до {max(c['drain']['age'] for c in d) / 60:.1f};"
        f" сливает создатель: {sum(c['drain']['sender'] == c['creator'] for c in d)}/{len(d)}; метод: {collections.Counter(c['drain']['method'] for c in d).most_common(3)}")
    say(f"  покупок до слива: медиана {st.median(len(c['buys']) for c in cs)}, продаж: медиана {st.median(len(c['sells']) for c in cs)}; "
        f"занесено ботами BNB: медиана {st.median(sum(r['bnb'] for r in c['buys']) for c in cs):.2f}, вынесено продажами: {st.median(-sum(r['bnb'] for r in c['sells']) for c in cs):.2f}")
    last = [(c["drain"]["block"] - c["buys"][-1]["block"]) * SPB for c in d if c["buys"]]
    say(f"  последняя покупка перед сливом: за {st.median(last):.0f} с (от {min(last):.0f} до {max(last):.0f})")
    # overlap of two coins of one network
    live = sum(1 for a, b in zip(cs, cs[1:]) if a["drain"] and b["start"] < a["drain"]["block"])
    say(f"  следующая монета выходит, пока предыдущая жива: {live} из {len(cs) - 1}")

# wallets
W = collections.defaultdict(lambda: dict(coins=set(), nets=set(), buys=[], sells=[], ages=[], idx=[]))
for c in coins:
    for r in c["ev"]:
        if r["kind"] in ("покупка", "продажа") and r["sender"] != c["creator"]:
            w = W[r["sender"]]
            w["coins"].add(c["pair"])
            w["nets"].add(c["net"])
            (w["buys"] if r["kind"] == "покупка" else w["sells"]).append(r["bnb"])
            w["ages"].append(r["age"])
say(f"\nКошельки, торгующие этими монетами (кроме создателей): {len(W)}")
say(f"  наши: {sum(1 for a in W if a in OURS)}; в 1 монете: {sum(1 for a, w in W.items() if len(w['coins']) == 1 and a not in OURS)}; "
    f"в 3+ монетах: {sum(1 for w in W.values() if len(w['coins']) >= 3)}; в обеих сетях: {sum(1 for w in W.values() if len(w['nets']) == 2)}")
say("  постоянные (больше всего монет): адрес | монет | сети | покупок, BNB в сумме | продаж, BNB | итог BNB | секунды от запуска (медиана, 10–90%) | размер покупки")
for a, w in sorted(W.items(), key=lambda kv: -len(kv[1]["coins"]))[:25]:
    ag = sorted(w["ages"])
    net_bnb = -sum(w["buys"]) - sum(w["sells"])
    say(f"  {short(a)} | {len(w['coins'])} | {'/'.join(sorted(n[-1] for n in w['nets']))} | {len(w['buys'])}, {sum(w['buys']):.2f} | {len(w['sells'])}, {-sum(w['sells']):.2f} | "
        f"{net_bnb:+.2f} | {st.median(ag):.0f} ({ag[len(ag) // 10]:.0f}–{ag[len(ag) * 9 // 10]:.0f}) | "
        + (f"{st.median(w['buys']):.3f} ({min(w['buys']):.3f}–{max(w['buys']):.3f})" if w["buys"] else "-"))
out_buyers = [(a, w) for a, w in W.items() if len(w["coins"]) <= 2 and a not in OURS and w["buys"]]
say(f"\n  разовые покупатели (1–2 монеты): {len(out_buyers)}, покупок {sum(len(w['buys']) for a, w in out_buyers)}, "
    f"продали хоть раз: {sum(1 for a, w in out_buyers if w['sells'])}")
allb = [r["bnb"] for c in coins for r in c["buys"]]
say(f"  все покупки: {len(allb)}, размер медиана {st.median(allb):.3f} BNB, 10% {sorted(allb)[len(allb) // 10]:.3f}, 90% {sorted(allb)[len(allb) * 9 // 10]:.3f}")
by_min = collections.Counter(min(30, r["age"] // 60) for c in coins for r in c["buys"])
say("  покупки по минутам жизни монеты: " + ", ".join(f"{m}:{by_min[m]}" for m in range(31)))
by_min_s = collections.Counter(min(30, r["age"] // 60) for c in coins for r in c["sells"])
say("  продажи по минутам: " + ", ".join(f"{m}:{by_min_s[m]}" for m in range(31)))

# ---------------- Part B ----------------
URLS = ["https://bsc-rpc.publicnode.com"]
W3 = Web3(Web3.HTTPProvider(URLS[0], request_kwargs={"timeout": 25}))
W3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
LOG_W3 = [Web3(Web3.HTTPProvider(u, request_kwargs={"timeout": 25})) for u in ("https://bsc-rpc.publicnode.com", "https://rpc.48.club")]
for w in LOG_W3:
    w.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
RR = itertools.count()


def call(f, tries=6, w=None):
    for k in range(tries):
        try:
            return f(w or W3)
        except Exception:
            time.sleep(0.4 + 0.5 * k)
    return None


F = Web3.to_checksum_address("0xca143ce32fe78f1f7019d7d551a6402fc5350c73")
WB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
PC = "0x" + Web3.keccak(text="PairCreated(address,address,address,uint256)").hex().replace("0x", "")
SW = "0x" + Web3.keccak(text="Swap(address,uint256,uint256,uint256,uint256,address)").hex().replace("0x", "")
SYNC = "0x" + Web3.keccak(text="Sync(uint112,uint112)").hex().replace("0x", "")
METH = {"02751cec": "removeLiquidityETH", "f305d719": "addLiquidityETH", "7ff36ab5": "swapExactETHForTokens", "b6f9de95": "swapExactETHForTokensFee",
        "791ac947": "swapExactTokensForETHFee", "18cbafe5": "swapExactTokensForETH", "095ea7b3": "approve", "a9059cbb": "transfer", "": "перевод BNB",
        "2f54bf6e": "2f54bf6e"}
head = W3.eth.block_number
say(f"\nЧАСТЬ B. Свежие монеты (состояние кошельков доступно ~1 ч назад; голова {head})")
fresh = []
lo = head - 9000
for b in range(lo, head, 2000):
    for l in call(lambda w: w.eth.get_logs({"address": F, "fromBlock": b, "toBlock": min(head, b + 1999), "topics": [PC]})) or []:
        t0, t1 = "0x" + l["topics"][1].hex()[-40:], "0x" + l["topics"][2].hex()[-40:]
        if WB not in (t0.lower(), t1.lower()):
            continue
        tok = Web3.to_checksum_address(t1 if t0.lower() == WB else t0)
        code = call(lambda w: bytes(w.eth.get_code(tok)))
        if not code or Web3.keccak(code).hex().replace("0x", "")[:10] != "18449a1624":
            continue
        g = call(lambda w: w.eth.get_storage_at(tok, 6))
        g = g.hex()[-40:].lower() if g is not None else ""
        if g in GUARDS:
            fresh.append(dict(token=tok, pair=Web3.to_checksum_address("0x" + bytes(l["data"])[12:32].hex()), wb0=t0.lower() == WB,
                              block=l["blockNumber"], net=GUARDS[g], guard=Web3.to_checksum_address("0x" + g)))
pick = []
for net in ("сеть 3", "сеть 4"):
    cs = [c for c in fresh if c["net"] == net and (head - c["block"]) * SPB > 33 * 60]  # finished coins only
    pick += cs[-NFRESH:]
say(f"  найдено свежих монет {len(fresh)}, завершённых для разбора {len(pick)}")


def nonce(a, b):
    return call(lambda w: w.eth.get_transaction_count(a, b))


def tx_blocks(a, lo, hi, nlo=None, nhi=None):
    """Blocks in (lo, hi] where the account's nonce grows = its own transactions."""
    nlo = nonce(a, lo) if nlo is None else nlo
    nhi = nonce(a, hi) if nhi is None else nhi
    if nlo is None or nhi is None or nlo == nhi:
        return []
    if hi - lo == 1:
        return [hi]
    m = (lo + hi) // 2
    nm = nonce(a, m)
    if nm is None:
        return []
    return tx_blocks(a, lo, m, nlo, nm) + tx_blocks(a, m, hi, nm, nhi)


def own_txs(a, lo, hi):
    out = []
    for b in tx_blocks(a, lo, hi):
        blk = call(lambda w: w.eth.get_block(b, full_transactions=True))
        for x in blk.transactions if blk else []:
            if x["from"].lower() == a.lower():
                out.append((b, x))
    return out


def funder(a, before):
    """The last BNB received before block `before` by a binary search of the balance (the biggest jump)."""
    hi_, lo_ = before, before - 3000
    bh, bl = call(lambda w: w.eth.get_balance(a, hi_)), call(lambda w: w.eth.get_balance(a, lo_))
    if bh is None or bl is None or bh <= bl:
        return None
    while hi_ - lo_ > 1:
        m = (lo_ + hi_) // 2
        bm = call(lambda w: w.eth.get_balance(a, m))
        if bm is None:
            return None
        if bm < bh - (bh - bl) * 0.5:
            lo_ = m
        else:
            hi_ = m
    blk = call(lambda w: w.eth.get_block(hi_, full_transactions=True))
    for x in blk.transactions if blk else []:
        if (x["to"] or "").lower() == a.lower():
            return dict(block=hi_, frm=x["from"].lower(), bnb=x["value"] / 1e18)
    return dict(block=hi_, frm="внутренний перевод", bnb=(bh - bl) / 1e18)


TXROWS = []


def study(c):
    pair, start = c["pair"], c["block"]
    end = min(head, start + int(36 * 60 / SPB))
    logs = []
    for b in range(start, end, 1500):
        logs += call(lambda w: w.eth.get_logs({"address": pair, "fromBlock": b, "toBlock": min(end, b + 1499), "topics": [[SW, SYNC]]})) or []
    syncs = [l for l in logs if l["topics"][0].hex().replace("0x", "") == SYNC[2:]]
    swaps = {(l["transactionHash"], l["address"]) for l in logs if l["topics"][0].hex().replace("0x", "") == SW[2:]}
    ev, prev = [], None
    for l in sorted(syncs, key=lambda x: (x["blockNumber"], x["logIndex"])):
        d = bytes(l["data"])
        bnb = (int.from_bytes(d[:32], "big") if c["wb0"] else int.from_bytes(d[32:64], "big")) / 1e18
        t = call(lambda w: w.eth.get_transaction(l["transactionHash"]))
        kind = ("старт" if prev is None else ("слив" if bnb < prev * 0.5 else "ликвидность") if (l["transactionHash"], l["address"]) not in swaps
                else "слив" if prev and bnb < prev * 0.5 else "покупка" if bnb > prev else "продажа")
        ev.append(dict(block=l["blockNumber"], kind=kind, bnb=bnb - (prev or 0), pool=bnb, frm=t["from"].lower() if t else "?",
                       to=(t["to"] or "").lower() if t else "", gwei=t["gasPrice"] / 1e9 if t else 0))
        prev = bnb
    creator = ev[0]["frm"] if ev else None
    c.update(ev=ev, creator=creator)
    if not creator:
        return c
    cr = Web3.to_checksum_address(creator)
    c["funding"] = funder(cr, start - 1)
    c["txs"] = own_txs(cr, start - 400, end)
    g = c["guard"]
    c["guard_txs"] = own_txs(g, start, end)
    return c


with ThreadPoolExecutor(4) as ex:
    done = list(ex.map(study, pick))
buyers_paid = collections.Counter()
for c in done:
    ev = c.get("ev") or []
    if not ev:
        continue
    st0 = c["block"]
    drain = next((e for e in ev if e["kind"] == "слив"), None)
    say(f"\n— {c['net']} монета {c['token']} (пул {short(c['pair'])}), запуск блок {st0} ({time.strftime('%H:%M', time.localtime(time.time() - (head - st0) * SPB))})")
    say(f"  создатель {c['creator']}; пул старт {ev[0]['pool']:.2f}, пик {max(e['pool'] for e in ev):.2f}, слив " +
        (f"на {(drain['block'] - st0) * SPB / 60:.1f} мин кошельком {short(drain['frm'])} ({'создатель' if drain['frm'] == c['creator'] else 'другой'})" if drain else "не найден"))
    f = c.get("funding")
    if f:
        say(f"  создателя пополнил {f['frm']} на {f['bnb']:.3f} BNB за {st0 - f['block']} блоков до пула")
    buys = {}
    for e in ev:
        if e["kind"] == "покупка":
            buys.setdefault(e["frm"], []).append(e)
    say(f"  покупок {sum(len(v) for v in buys.values())} от {len(buys)} кошельков, наших {sum(1 for a in buys if a in OURS)}")
    pays = []
    for b, x in c.get("txs") or []:
        to, val, meth = (x["to"] or "").lower(), x["value"] / 1e18, METH.get(bytes(x["input"])[:4].hex(), bytes(x["input"])[:4].hex())
        nxt_buy = next((e for e in buys.get(to, []) if e["block"] >= b), None)
        TXROWS.append(dict(net=c["net"], token=c["token"], who="создатель", frm=c["creator"], block=b, age_s=round((b - st0) * SPB), to=to, bnb=round(val, 6),
                           method=meth, gwei=x["gasPrice"] / 1e9, nonce=x["nonce"], buy_after_blocks=(nxt_buy["block"] - b) if nxt_buy else "",
                           buy_bnb=round(nxt_buy["bnb"], 6) if nxt_buy else ""))
        if val > 0 and to in buys:
            pays.append((b, to, val, nxt_buy))
            buyers_paid[to] += 1
    tx = c.get("txs") or []
    say(f"  транзакций создателя в окне: {len(tx)}; методы: {collections.Counter(METH.get(bytes(x['input'])[:4].hex(), bytes(x['input'])[:4].hex()) for b, x in tx).most_common(6)}")
    say(f"  платежей создателя покупателям: {len(pays)}; оплаченных покупок {sum(1 for p in pays if p[3])} из {sum(len(v) for v in buys.values())}")
    for b, to, val, nb in pays[:30]:
        say(f"    {(b - st0) * SPB:6.0f} с: создатель → {short(to)} {val:.4f} BNB" + (f"; покупка через {nb['block'] - b} бл. на {nb['bnb']:.4f} BNB ({nb['bnb'] / val * 100 - 100:+.2f}%), пул {nb['pool'] - nb['bnb']:.2f} → доля {nb['bnb'] / (nb['pool'] - nb['bnb']) * 100:.1f}%" if nb else "; покупки нет"))
    after = [(b, x) for b, x in tx if drain and b >= drain["block"]]
    for b, x in after[:6]:
        say(f"    после слива +{(b - drain['block']) * SPB:.0f} с: → {short((x['to'] or '').lower())} {x['value'] / 1e18:.4f} BNB ({METH.get(bytes(x['input'])[:4].hex(), bytes(x['input'])[:4].hex())})")
    gt = c.get("guard_txs") or []
    marks = []
    for b, x in gt:
        inp = bytes(x["input"])
        tgt = "0x" + inp[16:36].hex() if len(inp) >= 36 else ""
        marks.append(((b - st0) * SPB, (x["to"] or "").lower(), inp[:4].hex(), tgt))
        TXROWS.append(dict(net=c["net"], token=c["token"], who="сторож", frm=c["guard"].lower(), block=b, age_s=round((b - st0) * SPB), to=(x["to"] or "").lower(), bnb=0,
                           method=METH.get(inp[:4].hex(), inp[:4].hex()), gwei=x["gasPrice"] / 1e9, nonce=x["nonce"], buy_after_blocks="", buy_bnb="", target=tgt))
    mine = [m for m in marks if m[1] == c["token"].lower()]
    say(f"  сторож за жизнь монеты: транзакций {len(gt)}, на эту монету {len(mine)}: " +
        "; ".join(f"{m[0]:.0f} с {METH.get(m[2], m[2])} {short(m[3])}{' (НАШ)' if m[3] in OURS else ' (покупатель)' if m[3] in buys else ''}" for m in mine[:12]))
say(f"\nОплаченные создателями покупатели (по числу монет): " + ", ".join(f"{short(a)}×{n}" for a, n in buyers_paid.most_common(15)))
with open(os.path.join(HERE, "net7_txs.csv"), "w", newline="", encoding="utf-8-sig") as f:
    cols = ["net", "token", "who", "frm", "block", "age_s", "to", "bnb", "method", "gwei", "nonce", "buy_after_blocks", "buy_bnb", "target"]
    w = csv.DictWriter(f, fieldnames=cols, delimiter=";")
    w.writeheader()
    for r in TXROWS:
        w.writerow({k: r.get(k, "") for k in cols})
open(os.path.join(HERE, "net7_study.txt"), "w", encoding="utf-8").write("\n".join(L))
