"""18.11 (user): study every code family the net finder sees — source-level restrictions and how the coins are run. Read-only, public nodes.
For 1-2 recent coins of each family (net_finder_events.csv):
  CODE    size, hash, known function names (selectors), opcodes that matter (ORIGIN, TIMESTAMP, NUMBER, DELEGATECALL, SELFDESTRUCT,
          CALL/STATICCALL counts), addresses baked into the code and what they are, storage slots 0-19, owner()
  POOL    start / peak / end BNB, lifetime, how it was emptied (removeLiquidity or one big sell, by whom), buys/sells, buyers
  TAX     buy and sell tax measured from the coin's own Transfer events (share of tokens that went elsewhere than buyer/pool)
  SELL    can an outside holder move its coins to the pool / to a new address (eth_call from 5 holders) = blacklist/honeypot check
  WHO     who ended in plus (BNB in/out by tx sender), the creator's own swaps
Out: family_study.txt (+ family_study.json).  Usage: python family_study.py [coins per family=2] [max blocks per coin=8000]"""
import collections
import csv
import hashlib
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
PER = int(sys.argv[1]) if len(sys.argv) > 1 else 2
MAXB = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
NODES = ["https://rpc-bsc.48.club", "https://rpc.48.club", "https://bsc-dataseed.bnbchain.org", "https://bsc-rpc.publicnode.com"]
WBNB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
ROUTER = "0x10ed43c718714eb63d5aa57b78b54704e256024e"
FACTORY = "0xca143ce32fe78f1f7019d7d551a6402fc5350c73"
SWAP = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
SYNC = "0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1"
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
DEAD = {"0x000000000000000000000000000000000000dead", "0x0000000000000000000000000000000000000000"}
KNOWN_ADDR = {WBNB: "WBNB", ROUTER: "Pancake router v2", FACTORY: "Pancake factory v2", "0x000000000000000000000000000000000000dead": "dead",
              "0x13f4ea83d0bd40e75c8222255bc855a974568dd4": "Pancake router v3", "0x1b81d678ffb9c0263b24a97847620c99d213eb14": "Pancake v3 swap router",
              "0xba56663c1de9423ec886b24ac752d86ff0d9f7c0": "агрегатор (сеть 5)"}
SEL = {"06fdde03": "name", "95d89b41": "symbol", "313ce567": "decimals", "18160ddd": "totalSupply", "70a08231": "balanceOf", "a9059cbb": "transfer",
       "23b872dd": "transferFrom", "095ea7b3": "approve", "dd62ed3e": "allowance", "39509351": "increaseAllowance", "a457c2d7": "decreaseAllowance",
       "8da5cb5b": "owner", "715018a6": "renounceOwnership", "f2fde38b": "transferOwnership", "893d20e8": "getOwner",
       "49bd5a5e": "uniswapV2Pair", "1694505e": "uniswapV2Router", "c9567bf9": "openTrading", "8a8c523c": "enableTrading", "0d295980": "tradingActive?",
       "751039fc": "removeLimits", "7d1db4a5": "_maxTxAmount", "8f9a55c0": "_maxWalletSize", "bbc0c742": "_taxSwapThreshold", "0faee56f": "_maxTaxSwap",
       "51bc3c85": "manualSwap", "437823ec": "excludeFromFee", "ea2f0b37": "includeInFee", "5342acb4": "isExcludedFromFee",
       "f9f92be4": "blacklist", "0ecb93c0": "addBot", "273123b7": "delBot", "b515566a": "addBots", "3bbac579": "isBot",
       "c0246668": "excludeFromFees", "dd467064": "lock?", "a0712d68": "mint", "40c10f19": "mint(to,amt)", "42966c68": "burn", "79cc6790": "burnFrom",
       "3f4ba83a": "unpause", "8456cb59": "pause", "5c975abb": "paused", "e8078d94": "addLiquidity", "f305d719": "addLiquidityETH",
       "791ac947": "swapExactTokensForETHSupportingFee (вызов роутера)", "fb3bdb41": "swapETHForExactTokens", "7ff36ab5": "swapExactETHForTokens",
       "02751cec": "removeLiquidityETH", "af2979eb": "removeLiquidityETHSupportingFee", "e6a43905": "getPair", "c9c65396": "createPair",
       "ad5c4648": "WETH()", "c45a0155": "factory()", "0902f1ac": "getReserves", "022c0d9f": "swap(pair)", "fff6cae9": "sync", "bc337182": "setMaxTx?",
       "ec28438a": "setMaxTxAmount", "8ee88c53": "setSwapThreshold?", "c3c8cd80": "manualsend", "6fc3eaec": "manualSend?", "a8aa1b31": "pair",
       "f887ea40": "router", "0445b667": "swapThreshold?", "6ddd1713": "swapEnabled?", "4fbee193": "excludeFromFee?", "9a7a23d6": "setAutomatedMarketMakerPair",
       "2b112e49": "getCirculatingSupply", "1f53ac02": "setDevWallet?", "cc1776d3": "sellTax?", "4f7041a5": "buyTax?", "dd39f00d": "setFees?",
       "e01af92c": "setSwapEnabled", "4a62bb65": "setTradingEnabled?", "01339c21": "launch", "8b42507f": "isExempt?"}
OPS = {0x32: "ORIGIN", 0x42: "TIMESTAMP", 0x43: "NUMBER", 0x3a: "GASPRICE", 0x41: "COINBASE", 0x40: "BLOCKHASH", 0xf4: "DELEGATECALL",
       0xff: "SELFDESTRUCT", 0xf1: "CALL", 0xfa: "STATICCALL", 0xf0: "CREATE", 0xf5: "CREATE2", 0x31: "BALANCE", 0x3b: "EXTCODESIZE"}


def call(m, p, tries=6):
    err = None
    for k in range(tries):
        u = NODES[k % len(NODES)]
        try:
            r = json.loads(urllib.request.urlopen(urllib.request.Request(u, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": m, "params": p}).encode(),
                                                                         headers={"Content-Type": "application/json"}), timeout=40).read())
            if "error" in r:
                e = str(r["error"])
                if "revert" in e.lower() or "execution" in e.lower():
                    return {"_revert": e[:160]}
                raise RuntimeError(e[:120])
            return r["result"]
        except Exception as e:
            err = e
            time.sleep(0.4 + k)
    raise RuntimeError(f"{m}: {str(err)[:100]}")


def ecall(to, data, frm=None):
    p = {"to": to, "data": data}
    if frm:
        p["from"] = frm
    return call("eth_call", [p, "latest"])


def is_contract(a):
    try:
        return len(call("eth_getCode", [a, "latest"])) > 2
    except Exception:
        return None


def walk(code):
    b = bytes.fromhex(code[2:])
    ops, sels, addrs, i = collections.Counter(), set(), set(), 0
    while i < len(b):
        op = b[i]
        if 0x60 <= op <= 0x7F:
            n = op - 0x5F
            v = b[i + 1:i + 1 + n]
            if n == 4:
                sels.add(v.hex())
            if n == 20 and v.hex() not in ("ff" * 20, "00" * 20):
                addrs.add("0x" + v.hex())
            i += n + 1
            continue
        if op in OPS:
            ops[OPS[op]] += 1
        i += 1
    return ops, sels, addrs


def logs(addr, topics, b0, b1):
    out, b = [], b0
    while b <= b1:
        e = min(b + 399, b1)
        out += call("eth_getLogs", [{"address": addr, "fromBlock": hex(b), "toBlock": hex(e), "topics": topics}]) or []
        b = e + 1
    return out


HEAD = int(call("eth_blockNumber", []), 16)


def study_coin(tok, pair, first_block):
    R = {"token": tok, "pair": pair}
    code = call("eth_getCode", [tok, "latest"])
    ops, sels, addrs = walk(code)
    R["size"] = (len(code) - 2) // 2
    R["sha"] = hashlib.sha256(bytes.fromhex(code[2:])).hexdigest()[:10]
    R["ops"] = dict(ops)
    R["funcs"] = sorted(SEL.get(s, "") for s in sels if s in SEL)
    R["unknown_sel"] = len([s for s in sels if s not in SEL])
    R["baked"] = {a: KNOWN_ADDR.get(a) or ("контракт" if is_contract(a) else "кошелёк") for a in sorted(addrs)[:12]}
    R["proxy"] = R["size"] < 100 or "DELEGATECALL" in R["ops"]
    slots = {}
    for s in range(20):
        v = call("eth_getStorageAt", [tok, hex(s), "latest"])
        if int(v, 16):
            vv = int(v, 16)
            slots[s] = ("адрес 0x" + v[-40:] + (" (" + (KNOWN_ADDR.get("0x" + v[-40:]) or ("контракт" if is_contract("0x" + v[-40:]) else "кошелёк")) + ")")
                        if v[2:26] == "0" * 24 and vv > 2 ** 100 else ("0x" + v[2:].lstrip("0") if vv > 2 ** 100 else str(vv)))
    R["slots"] = slots
    if "8da5cb5b" in sels:
        o = ecall(tok, "0x8da5cb5b")
        R["owner"] = ("0x" + o[-40:]) if isinstance(o, str) else "ошибка"
    t0 = ecall(pair, "0x0dfe1681")
    wbnb0 = isinstance(t0, str) and ("0x" + t0[-40:]).lower() == WBNB
    b0 = max(first_block - 300, HEAD - 400000)
    b1 = min(HEAD, b0 + MAXB)
    lg = logs(pair, [[SWAP, SYNC]], b0, b1)
    syncs, swaps = [], []
    for l in lg:
        d = l["data"][2:]
        w = [int(d[i:i + 64], 16) for i in range(0, len(d), 64)]
        blk = int(l["blockNumber"], 16)
        if l["topics"][0] == SYNC:
            syncs.append((blk, (w[0] if wbnb0 else w[1]) / 1e18, l["transactionHash"]))
        else:
            a0i, a1i, a0o, a1o = w
            bin_ = (a0i if wbnb0 else a1i) / 1e18
            bout = (a0o if wbnb0 else a1o) / 1e18
            swaps.append(dict(blk=blk, buy=bin_ > 0, bnb=bin_ or bout, tx=l["transactionHash"], to="0x" + l["topics"][2][-40:]))
    if not syncs:
        R["pool"] = "нет данных пула"
        return R
    start = next((s for s in syncs if s[1] > 0), syncs[0])
    peak = max(syncs, key=lambda s: s[1])
    drain = None
    for (b, v, h), (pb, pv, ph) in zip(syncs[1:], syncs):
        if pv >= 0.1 and v < 0.25 * pv:
            drain = (b, pv, v, h)
            break
    mins = lambda b: (b - start[0]) * 0.45 / 60
    R["pool"] = dict(start=round(start[1], 3), peak=round(peak[1], 3), peak_min=round(mins(peak[0]), 1), end=round(syncs[-1][1], 3),
                     window_min=round((b1 - start[0]) * 0.45 / 60, 1), buys=sum(s["buy"] for s in swaps), sells=sum(not s["buy"] for s in swaps),
                     bnb_in=round(sum(s["bnb"] for s in swaps if s["buy"]), 3), bnb_out=round(sum(s["bnb"] for s in swaps if not s["buy"]), 3))
    creator = None
    try:
        creator = call("eth_getTransactionByHash", [start[2]])["from"].lower()
    except Exception:
        pass
    R["creator"] = creator
    if drain:
        t = call("eth_getTransactionByHash", [drain[3]])
        sel = t["input"][2:10]
        how = {"02751cec": "removeLiquidityETH", "af2979eb": "removeLiquidityETHSupportingFee", "baa2abde": "removeLiquidity", "ded9382a": "removeLiquidityETHWithPermit",
               "5b0d5984": "removeLiquidityETHWithPermitSupportingFee"}.get(sel) or SEL.get(sel) or ("продажа/свап" if any(s["tx"] == drain[3] for s in swaps) else "вызов " + sel)
        R["drain"] = dict(min=round(mins(drain[0]), 1), from_bnb=round(drain[1], 3), to_bnb=round(drain[2], 3), how=how, by=t["from"].lower(),
                          by_role="создатель" if t["from"].lower() == creator else ("владелец" if t["from"].lower() == R.get("owner") else "другой"),
                          to=(t.get("to") or "").lower())
    # who traded: tx sender of up to 400 swaps (spread over the life)
    pick = swaps if len(swaps) <= 400 else swaps[:: max(1, len(swaps) // 400)]
    with ThreadPoolExecutor(6) as ex:
        frm = dict(zip([s["tx"] for s in pick], ex.map(lambda s: call("eth_getTransactionByHash", [s["tx"]]), pick)))
    W = collections.defaultdict(lambda: [0.0, 0.0, 0, 0])
    routers = collections.Counter()
    for s in pick:
        t = frm.get(s["tx"])
        if not t:
            continue
        a = t["from"].lower()
        routers[(t.get("to") or "").lower()] += 1
        w = W[a]
        if s["buy"]:
            w[0] += s["bnb"]; w[2] += 1
        else:
            w[1] += s["bnb"]; w[3] += 1
    R["routers"] = {KNOWN_ADDR.get(k, k): v for k, v in routers.most_common(4)}
    R["wallets"] = len(W)
    closed = {a: w for a, w in W.items() if w[2] and w[3]}
    R["sellers_who_bought"] = len(closed)
    R["winners"] = sum(1 for w in closed.values() if w[1] > w[0])
    R["top"] = [(a, round(w[1] - w[0], 4), w[2], w[3], "создатель" if a == creator else "") for a, w in sorted(W.items(), key=lambda kv: -(kv[1][1] - kv[1][0]))[:5]]
    R["sampled"] = f"{len(pick)} из {len(swaps)}"
    R["creator_swaps"] = sum(1 for s in pick if frm.get(s["tx"]) and frm[s["tx"]]["from"].lower() == creator)
    # tax from Transfer events of the coin in buy / sell txs (first 150 of each)
    tl = logs(tok, [[TRANSFER]], b0, min(b1, b0 + 3000))
    by_tx = collections.defaultdict(list)
    for l in tl:
        by_tx[l["transactionHash"]].append(("0x" + l["topics"][1][-40:], "0x" + l["topics"][2][-40:], int(l["data"], 16) if l["data"] != "0x" else 0))
    pr = pair.lower()
    btax, stax = [], []
    for s in swaps[:600]:
        tr = by_tx.get(s["tx"])
        if not tr:
            continue
        if s["buy"]:
            out = [v for f, t, v in tr if f == pr]
            got = sum(v for f, t, v in tr if f == pr and t == s["to"])
            tot = sum(out)
            if tot and len(btax) < 150:
                btax.append(1 - got / tot if got else None)
        else:
            sent = [(f, t, v) for f, t, v in tr if t == pr]
            if sent:
                seller = sent[0][0]
                tot = sum(v for f, t, v in tr if f == seller)
                topool = sum(v for f, t, v in tr if f == seller and t == pr)
                if tot and len(stax) < 150:
                    stax.append(1 - topool / tot)
    med = lambda xs: (sorted(x for x in xs if x is not None)[len([x for x in xs if x is not None]) // 2] if any(x is not None for x in xs) else None)
    R["tax"] = dict(buy=round(100 * med(btax), 2) if med(btax) is not None else None, sell=round(100 * med(stax), 2) if med(stax) is not None else None,
                    n_buy=len(btax), n_sell=len(stax))
    # can an outside holder move its coins? (latest state)
    holders = [a for a, w in W.items() if a != creator and w[2] > 0][:12]
    tests = []
    for h in holders:
        bal = ecall(tok, "0x70a08231" + h[2:].rjust(64, "0"))
        if not isinstance(bal, str) or int(bal, 16) == 0:
            continue
        amt = hex(max(1, int(bal, 16) // 10))[2:].rjust(64, "0")
        to_pool = ecall(tok, "0xa9059cbb" + pr[2:].rjust(64, "0") + amt, h)
        to_new = ecall(tok, "0xa9059cbb" + "1234567890abcdef1234567890abcdef12345678".rjust(64, "0") + amt, h)
        tests.append(("ok" if isinstance(to_pool, str) else "ОТКАЗ", "ok" if isinstance(to_new, str) else "ОТКАЗ",
                      (to_pool.get("_revert") if isinstance(to_pool, dict) else "")[:60]))
        if len(tests) >= 5:
            break
    R["sell_test"] = tests
    return R


ev = [r for r in csv.DictReader((l.replace("\x00", "") for l in open(os.path.join(HERE, "net_finder_events.csv"), encoding="utf-8-sig", errors="replace")), delimiter=";")
      if (r.get("t") or "").isdigit() and (r.get("block") or "").isdigit() and (r.get("token") or "").startswith("0x") and r.get("hash")]
fams = collections.OrderedDict()
for r in sorted(ev, key=lambda r: -int(r["t"])):
    k = r["hash"]
    f = fams.setdefault(k, dict(size=r["size"], coins=collections.OrderedDict(), buys=0))
    f["buys"] += 1
    old = f["coins"].get(r["token"])
    f["coins"][r["token"]] = (r["pair"], min(int(r["block"]), old[1]) if old else int(r["block"]))  # the first buy the finder saw
want = [h for h in sys.argv[3:]] or list(fams)
P = lambda *a: print(*a, flush=True)
P(f"семей в данных поиска: {len(fams)}; разбираю {len(want)}, по {PER} монеты; голова {HEAD}")
OUT = {}
for h in want:
    f = fams.get(h)
    if not f:
        continue
    # the oldest of the recent coins has the most complete life; take coins with enough buys first
    cnt = collections.Counter(r["token"] for r in ev if r["hash"] == h)
    toks = sorted(f["coins"], key=lambda t: -cnt[t])[:PER]
    OUT[h] = []
    for tok in toks:
        pair, blk = f["coins"][tok]
        try:
            # first block of the pool: first Sync near the first seen buy (step back up to ~2 h)
            R = study_coin(tok, pair, blk)
        except Exception as e:
            R = {"token": tok, "error": str(e)[:150]}
        OUT[h].append(R)
        P(f"{h} ({f['size']} байт): {tok} готово")
json.dump(OUT, open(os.path.join(HERE, "family_study.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)

L = []
for h, coins in OUT.items():
    f = fams[h]
    L.append(f"\n================ СЕМЬЯ {h}  ({f['size']} байт, монет в поиске {len(f['coins'])}, покупок {f['buys']}) ================")
    for R in coins:
        if "error" in R:
            L.append(f"  {R['token']}: ошибка {R['error']}")
            continue
        L.append(f"  монета {R['token']}  пул {R['pair']}")
        L.append(f"   КОД: {R['size']} байт sha {R['sha']}{' ПРОКСИ/DELEGATECALL' if R['proxy'] else ''}; опкоды {R['ops']}")
        L.append(f"   функции: {', '.join(R['funcs']) or '—'} (+{R['unknown_sel']} неизвестных селекторов)")
        L.append(f"   адреса в коде: " + (", ".join(f"{a} {k}" for a, k in R["baked"].items()) or "—"))
        L.append(f"   слоты: " + ("; ".join(f"{s}={v}" for s, v in R["slots"].items()) or "—") + (f"; owner() {R['owner']}" if R.get("owner") else ""))
        p = R.get("pool")
        if isinstance(p, dict):
            L.append(f"   ПУЛ: старт {p['start']} BNB, пик {p['peak']} на {p['peak_min']} мин, конец окна {p['end']} (окно {p['window_min']} мин); "
                     f"покупок {p['buys']} ({p['bnb_in']} BNB), продаж {p['sells']} ({p['bnb_out']} BNB)")
            d = R.get("drain")
            L.append("   СЛИВ: " + (f"на {d['min']} мин, {d['from_bnb']} → {d['to_bnb']} BNB, способ {d['how']}, кто {d['by']} ({d['by_role']})" if d else "в окне нет"))
            L.append(f"   НАЛОГ (медиана): покупка {R['tax']['buy']}% (n={R['tax']['n_buy']}), продажа {R['tax']['sell']}% (n={R['tax']['n_sell']})")
            L.append(f"   КТО: кошельков {R['wallets']} (выборка свапов {R['sampled']}), купили и продали {R['sellers_who_bought']}, из них в плюсе {R['winners']}; "
                     f"свапов создателя {R['creator_swaps']}; через что: {R['routers']}")
            L.append("   топ по BNB: " + "; ".join(f"{a[:10]} {v:+.4f} ({b}/{s}){' ' + r if r else ''}" for a, v, b, s, r in R["top"]))
            L.append("   ПРОДАЖА ПОСТОРОННЕГО (в пул / на новый адрес): " + (", ".join(f"{a}/{b}{' ' + c if c else ''}" for a, b, c in R["sell_test"]) or "держателей с монетой нет"))
        else:
            L.append(f"   ПУЛ: {p}")
open(os.path.join(HERE, "family_study.txt"), "w", encoding="utf-8").write("\n".join(L))
print("\n".join(L))
