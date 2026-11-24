"""20.11 (user: «заходим в пул, после одной покупки бота выходим»). Automatic version of lp_test.py for nets 3/4 (code 1365,
guard 0x623e7c23 / 0xa70b3f34 in slot 6). Found by 3 live tests (SPY, BOTB, MZG): the creator drains the pool 24-26 s after the FIRST
bot buy that follows our entry, and the bots keep buying meanwhile. So: the creator pays a bot (we see it in a block) -> our contract C1
adds the stake to the pool in one transaction (the share goes to contract C2) -> the paid bot buys, the pool grows -> C2 at once takes
the share out and sells the coins in one transaction. No wallet ever holds the coin. One trade per coin (the creator drains it after).
The operating wallet is our own new key in D:\\bot\\lp_wallet.env (never printed); the contracts are created once (lpzap/contracts.json).
Usage: python lp_auto.py [--usd 1] [--share 0.015] [--max 20] [--loss-usd 3] [--dry]
--dry: no transaction, only logs what it would do. Stop: Ctrl+C or the file analysis/lp_auto_stop.flag.
Results: analysis/lp_auto.csv, analysis/lp_auto.log."""
import csv
import json
import os
import signal
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
_args = sys.argv[1:]
sys.argv = sys.argv[:1]  # bot.py reads sys.argv on import


def arg(name, default, cast=str):
    return cast(_args[_args.index(name) + 1]) if name in _args else default


# 20.11 (user: «ставка $10, чтобы работало автоматически»): settings in analysis/lp_auto.json, the command line overrides them;
# the loss limit counts every trade in lp_auto.csv since `since` (a restart does not reset it); reaching it writes the stop file.
SET_FILE = os.path.join(HERE, "lp_auto.json")
try:
    SET = json.load(open(SET_FILE, encoding="utf-8"))
except Exception:
    SET = {"usd": 10.0, "share": 0.015, "loss_usd": 5.0, "max": 100000, "loss_streak": 5, "big_loss_pct": 30,
           "since": time.strftime("%Y-%m-%d %H:%M:%S")}
    json.dump(SET, open(SET_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
USD = arg("--usd", float(SET.get("usd", 10.0)), float)
SHARE = arg("--share", float(SET.get("share", 0.015)), float)  # a creator payment of at least this share of the pool triggers an entry
MAX_TRADES = arg("--max", int(SET.get("max", 100000)), int)
LOSS_USD = arg("--loss-usd", float(SET.get("loss_usd", 5.0)), float)
SINCE = SET.get("since", "2000-01-01 00:00:00")
LOSS_STREAK = int(SET.get("loss_streak", 5))  # this many losing trades in a row = the strategy stopped working -> stop
BIG_LOSS_PCT = float(SET.get("big_loss_pct", 30))  # one trade losing more than this % of the stake (a drain caught us) -> stop
DRY = "--dry" in _args
LOCK_PORT = 47670  # watchdog: LISTEN = running
PUBLIC_MP = None  # set after bot.py is imported: its ws_url (publicnode)
WAIT_BLOCKS = 14  # after the payment block: no bot buy by then -> out
MAX_AGE_MIN = 25  # the coin's normal drain is at 28.7+ min
LOG = os.path.join(HERE, "lp_auto.log")
CSV = os.path.join(HERE, "lp_auto.csv")
STOP = os.path.join(HERE, "lp_auto_stop.flag")
KEYFILE = os.path.join(ROOT, "lp_wallet.env")
CFILE = os.path.join(HERE, "lpzap", "contracts.json")


def no_quickedit():
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-10)
        mode = ctypes.c_uint32()
        if k.GetConsoleMode(h, ctypes.byref(mode)):
            k.SetConsoleMode(h, (mode.value & ~0x0040) | 0x0080)
    except Exception:
        pass


no_quickedit()


def say(m):
    line = time.strftime("%Y-%m-%d %H:%M:%S") + "  " + m
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


import bot as B  # noqa: E402
from web3 import Web3  # noqa: E402

w3 = B.w3
PUBLIC_MP = B.CFG["ws_url"]
GUARDS = {"0x623e7c23c7c228095f94d9bd567eae55cb87a966": 3, "0xa70b3f34e3ca7d4665b794ce6bb29168b1d2e40d": 4}
FACTORY = Web3.to_checksum_address("0xca143ce32fe78f1f7019d7d551a6402fc5350c73")
PAIRCREATED = "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"
USDT = Web3.to_checksum_address("0x55d398326f99059fF775485246999027B3197955")
LOGS = Web3(Web3.HTTPProvider("https://rpc-bsc.48.club", request_kwargs={"timeout": 30}))
ZAP = json.load(open(os.path.join(HERE, "lpzap", "combined.json"), encoding="utf-8"))["contracts"]["LpZap.sol:LpZap"]
PAIR_ABI = [{"name": "getReserves", "type": "function", "stateMutability": "view", "inputs": [],
             "outputs": [{"type": "uint112"}, {"type": "uint112"}, {"type": "uint32"}]},
            {"name": "token0", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "address"}]},
            {"name": "totalSupply", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "uint256"}]},
            {"name": "balanceOf", "type": "function", "stateMutability": "view", "inputs": [{"type": "address"}], "outputs": [{"type": "uint256"}]}]
ERC_ABI = [{"name": "allowance", "type": "function", "stateMutability": "view", "inputs": [{"type": "address"}, {"type": "address"}],
            "outputs": [{"type": "uint256"}]},
           {"name": "symbol", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "string"}]}]
RT = w3.eth.contract(B.ROUTER, abi=[{"name": "getAmountsOut", "type": "function", "stateMutability": "view",
                                     "inputs": [{"type": "uint256"}, {"type": "address[]"}], "outputs": [{"type": "uint256[]"}]}])
INTR = {"v": False}


def on_int(*_):
    INTR["v"] = True
    print("  Ctrl+C: доведу текущую сделку до выхода и остановлюсь", flush=True)


def stop_asked():
    return INTR["v"] or (os.path.exists(STOP) and not DRY)  # a dry check ignores the real run's stop file


def retry(fn, tries=5):
    for k in range(tries):
        try:
            return fn()
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(0.3)


def wait(snd, h, sec=30):
    t_end = time.time() + sec
    while time.time() < t_end:
        time.sleep(0.15)
        rc = snd.receipt(h)
        if rc:
            return rc
    return None


def reserves(pair, token):
    """20.11 00:36: through bot.w3 every read took 0.3-0.9 s and the entry came 3 s late; raw eth_call to fast nodes ~0.15 s."""
    r = raw("eth_call", [{"to": pair, "data": "0x0902f1ac"}, "latest"])
    r0, r1 = int(r[2:66], 16), int(r[66:130], 16)
    t0 = COIN_T0.get(pair)
    if not t0:
        t0 = "0x" + raw("eth_call", [{"to": pair, "data": "0x0dfe1681"}, "latest"])[-40:]
        COIN_T0[pair] = t0
    return (r0, r1) if t0.lower() == B.WBNB.lower() else (r1, r0)


def raw_uint(to, data):
    return int(raw("eth_call", [{"to": to, "data": data}, "latest"]), 16)


def lp_of(pair, who):
    return raw_uint(pair, "0x70a08231" + who[2:].lower().rjust(64, "0"))


def raw_balance(a):
    return int(raw("eth_getBalance", [a, "latest"]), 16)


COIN_T0 = {}


def bnb_usd():
    try:
        return RT.functions.getAmountsOut(10 ** 18, [B.WBNB, USDT]).call()[1] / 1e18
    except Exception:
        return 600.0


def tg(m):
    try:
        B.tg(m)
    except Exception:
        pass


# ---------- the operating wallet and the contracts ----------
def op_wallet():
    if not os.path.exists(KEYFILE):
        acct = w3.eth.account.create()
        with open(KEYFILE, "w", encoding="utf-8") as f:
            f.write("LP_KEY=" + acct.key.hex() + "\n")
        say(f"создан рабочий кошелёк теста пула {acct.address} (ключ в D:\\bot\\lp_wallet.env, не показывается)")
    key = next(l.strip().split("=", 1)[1] for l in open(KEYFILE, encoding="utf-8") if l.startswith("LP_KEY="))
    return B.Sender(key)


def deploy(w, price):
    import rlp
    nonce = w.nonce
    try:
        lim = int(w3.eth.estimate_gas({"from": w.address, "data": "0x" + ZAP["bin"]}) * 1.25)
    except Exception:
        lim = 1_350_000
    sg = w.acct.sign_transaction({"chainId": 56, "nonce": nonce, "value": 0, "data": "0x" + ZAP["bin"], "gas": min(lim, 1_400_000), "gasPrice": price})
    raw = "0x" + sg.raw_transaction.hex().removeprefix("0x")
    for u in B.CFG["send_rpc_urls"]:
        try:
            B.http.post(u, json={"jsonrpc": "2.0", "id": 1, "method": "eth_sendRawTransaction", "params": [raw]}, timeout=5)
        except Exception:
            pass
    w.nonce += 1
    rc = wait(w, "0x" + sg.hash.hex().removeprefix("0x"), 40)
    if not (rc and rc["status"] == 1):
        return None
    return Web3.to_checksum_address(Web3.keccak(rlp.encode([bytes.fromhex(w.address[2:]), nonce]))[12:])


def contracts(w, price):
    try:
        c = json.load(open(CFILE, encoding="utf-8"))
        if c.get("owner") == w.address and all(c.get(k) for k in ("enter", "exit", "spare")):
            return c
    except Exception:
        pass
    c = {"owner": w.address}
    for k in ("enter", "exit", "spare"):
        a = deploy(w, price)
        if not a:
            say(f"контракт «{k}» не создан")
            return None
        c[k] = a
        say(f"контракт «{k}» создан: {a}")
    json.dump(c, open(CFILE, "w", encoding="utf-8"), indent=1)
    return c


# ---------- coins and payments ----------
ENTER_CB = {}  # set in main(): try_enter with the trade's context
CFG_C = {}  # our contracts (enter, exit, spare), for the discovery-time checks
COINS = {}  # pair -> dict(token, net, creator, born_block, born_ts, sym, traded, guard)
SEEN = set()  # every pair already looked at (ours or not): the 8-second rescan costs nothing for them
MIN_POOL = 10 * 10 ** 18  # nets 3/4 start at 15 BNB: a smaller pool is drained (the creator's ~27 BNB to the cash box is not a bot payment)
HEAD = {"n": 0}


def scan_coins(frm, to):
    for a in range(frm, to + 1, 200):
        lg = retry(lambda: LOGS.eth.get_logs({"address": FACTORY, "fromBlock": a, "toBlock": min(a + 199, to), "topics": [PAIRCREATED]}))
        for l in lg:
            t0, t1 = "0x" + l["topics"][1].hex()[-40:], "0x" + l["topics"][2].hex()[-40:]
            tok = t1 if t0.lower() == B.WBNB.lower() else t0 if t1.lower() == B.WBNB.lower() else None
            if not tok:
                continue
            tok = Web3.to_checksum_address(tok)
            pair = Web3.to_checksum_address("0x" + bytes(l["data"])[12:32].hex())
            if pair in SEEN:
                continue
            SEEN.add(pair)
            try:
                if (len(raw("eth_getCode", [tok, "latest"])) - 2) // 2 != 1365:
                    continue
                g = "0x" + retry(lambda: w3.eth.get_storage_at(tok, 6)).hex()[-40:]
                if g.lower() not in GUARDS:
                    continue
                creator = retry(lambda: w3.eth.get_transaction(l["transactionHash"]))["from"]
                sym = retry(lambda: w3.eth.contract(tok, abi=ERC_ABI).functions.symbol().call())[:16]
                bts = retry(lambda: w3.eth.get_block(l["blockNumber"]))["timestamp"]
                COINS[pair] = dict(token=tok, net=GUARDS[g.lower()], creator=creator.lower(), born=l["blockNumber"], born_ts=bts, sym=sym,
                                   traded=False, guard=Web3.to_checksum_address(g), exit_marked=False)
                if CFG_C.get("exit"):
                    try:
                        COINS[pair]["exit_marked"] = raw_uint(tok, "0xdd62ed3e" + g[2:].rjust(64, "0") + CFG_C["exit"][2:].lower().rjust(64, "0")) > 0
                    except Exception:
                        pass
                say(f"монета сети {GUARDS[g.lower()]}: {sym} {tok}, создатель {creator[:10]}, возраст {time.time() - bts:.0f} с")
                if ENTER_CB.get("f") and time.time() - bts <= ENTER_MAX_AGE_S:
                    ENTER_CB["f"](pair, f"новая монета, возраст {time.time() - bts:.0f} с")
            except Exception as e:
                SEEN.discard(pair)  # try again on the next scan
                say(f"разбор монеты: {str(e)[:70]}")


# ---------- one trade ----------
RUN = {"busy": False}
HOLD_S = 45  # 20.11: the earliest drain after our entry on a young coin was 49 s (MZG): out at 45 s with or without a bot buy


def wait_next_buy(pair, t0, limit):
    """DRY: the next payment of this coin after t0 and its bot's buy."""
    t_end = time.time() + limit
    nxt = None
    while time.time() < t_end:
        if not nxt:
            nxt = next((x for x in PAYS.get(pair, []) if x["t"] > t0), None)
        if nxt and BOT_BUY.get(nxt["to"]):
            return nxt, BOT_BUY[nxt["to"]]
        if nxt and time.time() - nxt["t"] > 12:
            return nxt, None
        time.sleep(0.2)
    return nxt, None


def halt_flag(why):
    if not DRY:
        with open(STOP, "w", encoding="utf-8") as f:
            f.write(f"{why} {time.strftime('%Y-%m-%d %H:%M:%S')}")


def trade(w, c, pair, coin, pay, usd, stats):
    try:
        _trade(w, c, pair, coin, pay, usd, stats)
    except Exception as e:
        say(f"  СБОЙ сделки: {str(e)[:120]} — проверь контракт выхода {c and c.get('exit')}")
        if coin.get("entered") and not coin.get("exited"):  # in the pool: get out first, explain after
            try:
                zout = w3.eth.contract(c["exit"], abi=ZAP["abi"])
                r = wait(w, w.send(c["exit"], zout.encode_abi("exit", args=[pair, coin["token"], 0]), gas=450_000))
                say(f"  аварийный выход: {'ok' if r and r['status'] == 1 else 'НЕ ПРОШЁЛ'}")
            except Exception as e2:
                say(f"  аварийный выход не отправлен: {str(e2)[:80]}")
        halt_flag(f"сбой сделки: {str(e)[:80]}")
    finally:
        RUN["busy"] = False


def _trade(w, c, pair, coin, pay, usd, stats):
    tok, net, sym = coin["token"], coin["net"], coin["sym"]
    if DRY:
        coin["traded"] = True
        rb0, _ = reserves(pair, tok)
        say(f"ВХОД (без денег): {sym} (сеть {net}) {pay['src']}, пул {rb0 / 1e18:.4f} BNB")
        eb0 = HEAD["n"]
        t_end = time.time() + HOLD_S
        got = []
        while time.time() < t_end and not got:
            got = [x for x in BUYS.get(pair, []) if x[0] > eb0 and x[1] >= EXIT_BUY_MIN]
            time.sleep(0.2)
        rb1, _ = reserves(pair, tok)
        if got:
            say(f"  (без денег) следующая покупка {got[0][1] / 1e18:.4f} BNB в блоке {got[0][0]} (вход был бы в ~{eb0}); "
                f"пул {rb0 / 1e18:.4f} -> {rb1 / 1e18:.4f} ({(rb1 / rb0 - 1) * 100:+.2f}%) — тут был бы выход")
        else:
            say(f"  (без денег) покупки за {HOLD_S} с не было; пул {rb0 / 1e18:.4f} -> {rb1 / 1e18:.4f} — выход по времени")
        return
    zin = w3.eth.contract(c["enter"], abi=ZAP["abi"])
    zout = w3.eth.contract(c["exit"], abi=ZAP["abi"])
    pc = w3.eth.contract(pair, abi=PAIR_ABI)
    erc = w3.eth.contract(tok, abi=ERC_ABI)
    coin["traded"] = True
    stake = int(USD / usd * 1e18)
    rb0, _ = reserves(pair, tok)
    if raw_balance(w.address) < stake + 1_000_000 * w.gas_price:
        say(f"  на рабочем кошельке меньше ставки — пропуск (пополнится с A в течение минуты)")
        coin["traded"] = False
        return
    say(f"ВХОД: {sym} (сеть {net}) {pay['src']}, пул {rb0 / 1e18:.4f} BNB")
    if pending(pair):
        say("  у монеты есть неоплаченный платёж боту — не входим (сломали бы его покупку)")
        coin["traded"] = False
        return
    if rb0 < MIN_POOL:
        say(f"  пул {rb0 / 1e18:.3f} BNB — монета уже слита, не входим")
        return
    if coin.get("exit_marked"):  # checked when the coin was found
        say("  контракт выхода помечен на этой монете — пропуск")
        return
    b0 = raw_balance(w.address)
    ts = raw_uint(pair, "0x18160ddd")
    min_lp = int(stake // 2 * ts / rb0 * 0.85)
    t0 = time.time()
    h = w.send(c["enter"], zin.encode_abi("enter", args=[pair, tok, c["exit"], min_lp]), value=stake, gas=450_000)
    OUR_TX.add(h.lower())
    rc = wait(w, h)
    if not rc:  # 20.11 review: a slow receipt is not a failed entry — the share on the exit contract decides
        try:
            if lp_of(pair, c["exit"]) > 0:
                rc = {"status": 1, "logs": [], "gasUsed": 0, "effectiveGasPrice": 0}
                say("  квитанции нет, но доля в пуле есть — считаю, что вошли")
        except Exception:
            pass
    if rc and rc["status"] == 1:
        coin["entered"] = True
    if not (rc and rc["status"] == 1):
        say(f"  вход не прошёл ({'нет квитанции' if not rc else 'отклонён'}) {h}")
        stats["gas"] += (b0 - w3.eth.get_balance(w.address)) / 1e18
        return
    # 20.11 00:40 INFYc: another node did not have the tx's block yet -> int(None) -> the trade crashed AFTER the entry, the exit never
    # ran and the creator drained the pool 25 s later (−$10). Everything after a successful entry is now guarded: the exit always runs.
    eb = HEAD["n"]
    try:
        for _ in range(10):
            t_ = raw("eth_getTransactionReceipt", [h])
            if t_ and t_.get("blockNumber"):
                eb = int(t_["blockNumber"], 16)
                break
            time.sleep(0.2)
    except Exception:
        pass
    try:
        rb1, _ = reserves(pair, tok)
    except Exception:
        rb1 = rb0
    say(f"  ВОШЛИ в блоке {eb}, за {time.time() - t0:.1f} с; пул {rb1 / 1e18:.4f} BNB. Жду следующий платёж боту и его покупку")
    # the NEXT buy in this pool after our entry (a paid bot); then out at once (the creator drains ~25 s after it). Max HOLD_S in the pool.
    why = ""
    nxt_pay, bb = None, None
    t_wait = time.time()
    while True:
        if time.time() - t_wait > HOLD_S:
            why = f"{HOLD_S} с без покупки"
            break
        got = [x for x in BUYS.get(pair, []) if x[0] > eb and x[1] >= EXIT_BUY_MIN]
        if got:
            bb = got[0][0]
            nxt_pay = dict(value=got[0][1], block=got[0][0])
            why = f"покупка {got[0][1] / 1e18:.4f} BNB в блоке {bb}"
            break
        try:
            rb, _ = reserves(pair, tok)
            if rb < rb1 * 0.97:
                why = "пул падает"
                break
        except Exception:
            pass
        time.sleep(0.2)
    # out: one transaction; a failed exit moves the share to the spare copy
    out = False
    coin["exited"] = True  # the exit below runs now; the crash handler must not send a second one
    for k in range(3):
        try:
            h2 = w.send(c["exit"], zout.encode_abi("exit", args=[pair, tok, 0]), gas=450_000)
            OUR_TX.add(h2.lower())
            r2 = wait(w, h2)
            if r2 and r2["status"] == 1:
                out = True
                break
        except Exception as e:
            say(f"  выход не отправлен: {str(e)[:70]}")
            w.resync()
        try:
            if lp_of(pair, c["exit"]) == 0:
                out = True
                break
        except Exception:
            pass
    if not out:
        try:
            zsp = w3.eth.contract(c["spare"], abi=ZAP["abi"])
            wait(w, w.send(c["exit"], zout.encode_abi("moveLp", args=[pair, c["spare"]]), gas=150_000))
            r3 = wait(w, w.send(c["spare"], zsp.encode_abi("exit", args=[pair, tok, 0]), gas=450_000))
            out = bool(r3 and r3["status"] == 1)
            say(f"  выход через запасной контракт: {'ok' if out else 'НЕ ПРОШЁЛ'}")
        except Exception as e:
            say(f"  запасной выход: {str(e)[:70]}")
    time.sleep(1.5)
    pnl = (w3.eth.get_balance(w.address) - b0) / 1e18
    stats["n"] += 1
    stats["pnl"] += pnl
    try:
        rb2, _ = reserves(pair, tok)
    except Exception:
        rb2 = 0
    note = why + ("" if out else f"; ДОЛЯ ОСТАЛАСЬ НА КОНТРАКТЕ {c['exit']}")
    say(f"  ВЫШЛИ ({why}): итог {pnl:+.8f} BNB ({pnl / (stake / 1e18) * 100:+.2f}%, ${pnl * usd:+.3f}); пул {rb1 / 1e18:.4f} → {rb2 / 1e18:.4f}; "
        f"всего {stats['n']} сделок, {stats['pnl']:+.8f} BNB (${stats['pnl'] * usd:+.2f})")
    pv = nxt_pay["value"] if nxt_pay else 0
    row = dict(time=time.strftime("%Y-%m-%d %H:%M:%S"), net=net, symbol=sym, token=tok, pay_bnb=round(pv / 1e18, 5),
               pay_share_pct=round(pv / rb0 * 100, 2), pay_block=nxt_pay["block"] if nxt_pay else "", enter_block=eb, stake_bnb=round(stake / 1e18, 6),
               pool_in=round(rb1 / 1e18, 4), pool_out=round(rb2 / 1e18, 4), pnl_bnb=f"{pnl:.8f}", pnl_pct=round(pnl / (stake / 1e18) * 100, 2), note=note,
               src=pay.get("src", ""))
    new = not os.path.exists(CSV)
    with open(CSV, "a", newline="", encoding="utf-8-sig") as f:
        dw = csv.DictWriter(f, fieldnames=list(row), delimiter=";")
        if new:
            dw.writeheader()
        dw.writerow(row)
    tg(f"💧 Пул {sym} (сеть {net}): {pnl:+.6f} BNB (${pnl * usd:+.2f}); {why}")
    stats["streak"] = stats.get("streak", 0) + 1 if pnl < 0 else 0
    halt = ""
    if pnl < -BIG_LOSS_PCT / 100 * stake / 1e18:
        halt = f"крупный убыток {pnl / (stake / 1e18) * 100:.0f}% ставки на {sym}"
    elif stats["streak"] >= LOSS_STREAK:
        halt = f"{stats['streak']} убыточных сделок подряд — стратегия, похоже, перестала работать"
    if halt:
        say(f"!!! СТОП: {halt}")
        tg(f"🛑 Пул авто остановлен: {halt}. Продолжить: удали analysis/lp_auto_stop.flag")
        with open(STOP, "w", encoding="utf-8") as f:
            f.write(f"{halt} {time.strftime('%Y-%m-%d %H:%M:%S')}")
    if not out:
        halt_flag(f"доля осталась на контракте {c['exit']} (монета {tok})")
        tg(f"⚠️ Пул {sym}: доля осталась на контракте {c['exit']} — торговля остановлена")


BOT_BUY = {}  # paid bot -> block of its first tx after its payment


def top_up(w, usd, price, extra=0):
    """The operating wallet holds stake + gas of a few trades; what is missing comes from A (profit stays on the wallet)."""
    need = int((USD / usd) * 1e18 * 1.10) + 4_000_000 * price + extra
    have = raw_balance(w.address)  # fast: this runs every minute inside the block loop
    if have >= int((USD / usd) * 1e18) + 1_000_000 * price + extra:
        return True
    try:
        A = B.Sender(B.load_private_key())
        A.refresh_gas_price(force=True)
        if w3.eth.get_balance(A.address) < need - have + 21_000 * price * 5:
            say(f"на кошельке A мало BNB для пополнения ({(need - have) / 1e18:.6f}) — жду")
            return False
        rc = wait(A, A.send(w.address, b"", value=need - have, gas=21_000, gas_price=price))
        ok = bool(rc and rc["status"] == 1)
        say(f"A → рабочий кошелёк {w.address}: {(need - have) / 1e18:.6f} BNB: {'ok' if ok else 'НЕ ПРОШЛО'}")
        return ok
    except Exception as e:
        say(f"пополнение рабочего кошелька: {str(e)[:80]}")
        return False


def streak_now():
    """Losing trades in a row at the end of lp_auto.csv (since SINCE): a restart keeps counting."""
    try:
        rows = [r for r in csv.DictReader(open(CSV, encoding="utf-8-sig"), delimiter=";") if r.get("time", "") >= SINCE]
    except Exception:
        return 0
    k = 0
    for r in reversed(rows):
        if float(r.get("pnl_bnb") or 0) < 0:
            k += 1
        else:
            break
    return k


def done_pnl():
    """(trades, BNB) in lp_auto.csv since SINCE."""
    try:
        rows = [r for r in csv.DictReader(open(CSV, encoding="utf-8-sig"), delimiter=";") if r.get("time", "") >= SINCE]
        return len(rows), sum(float(r.get("pnl_bnb") or 0) for r in rows)
    except Exception:
        return 0, 0.0


def main():
    import socket
    lock = socket.socket()
    try:
        lock.bind(("127.0.0.1", LOCK_PORT + (1 if DRY else 0)))  # a dry check may run next to the real one
        lock.listen(1)
    except OSError:
        print("второй экземпляр lp_auto уже работает — выхожу")
        return 0
    signal.signal(signal.SIGINT, on_int)
    while os.path.exists(STOP) and not DRY:  # a stop (by hand or by the loss limit) survives restarts: delete the file to go on
        if not INTR.get("said"):
            say(f"стоит СТОП-файл {STOP} ({open(STOP, encoding='utf-8').read().strip()[:80]}) — жду, пока его удалят")
            INTR["said"] = True
        time.sleep(10)
        if INTR["v"]:
            return 0
    INTR["said"] = False
    usd = bnb_usd()
    say(f"=== ПУЛ АВТО: ставка ${USD} = {USD / usd:.6f} BNB, вход в новую монету (до {ENTER_MAX_AGE_S} с), выход после первой покупки бота или через {HOLD_S} с, до {MAX_TRADES} сделок, "
        f"стоп при убытке ${LOSS_USD}{', БЕЗ ДЕНЕГ' if DRY else ''} ===")
    w = op_wallet()
    w.refresh_gas_price(force=True)
    price = w.gas_price
    c = None
    if not DRY:
        deploy_gas = 0 if os.path.exists(CFILE) else 3 * 1_400_000 * price
        if not top_up(w, usd, price, deploy_gas):
            return 1
        w.resync()
        w.gas_price = price
        c = contracts(w, price)
        if not c:
            return 1
        CFG_C.update(c)
    head = w3.eth.block_number
    scan_coins(head - int(MAX_AGE_MIN * 60 / 0.45), head)
    HEAD["n"] = head
    last_scan = time.time()
    head = raw_head()  # 20.11 00:57: the coin scan takes ~40-60 s; reading from the old head made the loop replay a minute of blocks
    HEAD["n"] = head
    n0, p0 = done_pnl()
    stats = {"n": n0, "pnl": p0, "gas": 0.0, "streak": streak_now()}
    say(f"с {SINCE}: {n0} сделок, итог {p0:+.8f} BNB (${p0 * usd:+.2f}), лимит убытка ${LOSS_USD}")
    nxt = head + 1
    START["t"] = time.time()
    ENTER_CB["f"] = lambda p, why: try_enter(p, w, c, usd, stats, why)
    for url, name in (("ws://127.0.0.1:8547", "p2p"), (PUBLIC_MP, "publicnode")):
        threading.Thread(target=mempool_loop, args=(url, name, w, c, usd, stats), daemon=True).start()
    say(f"слежу за платежами создателей сетей 3/4 (монет сейчас {len(COINS)}): блоки + мемпул p2p + мемпул publicnode")
    while not stop_asked():
        if stats["n"] >= MAX_TRADES:
            say("достигнут предел сделок")
            break
        if (stats["pnl"] + stats["gas"]) * usd <= -LOSS_USD:
            say(f"достигнут лимит убытка ${LOSS_USD}")
            tg(f"🛑 Пул авто: лимит убытка ${LOSS_USD}, остановлен (удали analysis/lp_auto_stop.flag, чтобы продолжить)")
            if not DRY:
                with open(STOP, "w", encoding="utf-8") as f:
                    f.write(f"лимит убытка ${LOSS_USD} {time.strftime('%Y-%m-%d %H:%M:%S')}")
            break
        try:
            head = raw_head()
        except Exception:
            time.sleep(0.3)
            continue
        if not DRY and not RUN["busy"] and time.time() - TOPUP["t"] > 60:
            TOPUP["t"] = time.time()
            with TRIG:
                free = not RUN["busy"]
                if free:
                    RUN["busy"] = True  # 20.11 review: a trade starting during a top-up would count the top-up as profit
            if free:
                try:
                    top_up(w, usd, price)
                finally:
                    RUN["busy"] = False
        if time.time() - last_scan > 3 and not SCAN["busy"]:  # in the background: new pairs must never hold up the block loop
            SCAN["busy"] = True
            last_scan = time.time()
            threading.Thread(target=bg_scan, args=(HEAD["n"] - 30, head), daemon=True).start()
        if nxt > head:
            time.sleep(0.1)
            continue
        HEAD["n"] = head
        if head - nxt > 200:  # far behind (a long pause): jump, old payments are useless
            say(f"отстал на {head - nxt} блоков — перескакиваю к голове")
            nxt = head - 2
        # 20.11 00:58: whole blocks took 1-6 s per 8 (the chain makes 8 in 3.6 s) and the loop fell 25-60 s behind. Only the pools
        # matter: Sync logs of the live pairs, one small request per poll; a BNB increase in a pool not made by us = a buy.
        pairs = [p for p, cn in list(COINS.items()) if time.time() - cn["born_ts"] < MAX_AGE_MIN * 60]
        to_b = min(head, nxt + 60)
        if pairs:
            try:
                lg = logs_sync(pairs, nxt, to_b)
            except Exception:
                time.sleep(0.2)
                continue
            for l in lg:
                on_sync(l, w, c, usd, stats)
        nxt = to_b + 1
    while RUN["busy"]:
        time.sleep(0.5)  # a running trade finishes its exit first
    say(f"=== СТОП: {stats['n']} сделок, итог {stats['pnl']:+.8f} BNB (${stats['pnl'] * usd:+.2f}) ===")
    return 0


SCAN = {"busy": False}
SYNC_T = "0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1"
LOG_NODES = ["https://rpc-bsc.48.club", "https://bsc-rpc.publicnode.com", "https://bsc-dataseed1.binance.org"]
LAST_R = {}  # pair -> last BNB reserve seen
OUR_TX = set()  # our own entry / exit hashes: their Sync is not a buy
BUYS = {}  # pair -> list of (block, delta BNB, time seen)


def logs_sync(pairs, frm, to):
    if not hasattr(_SESS, "s"):
        _SESS.s = requests.Session()
    body = {"jsonrpc": "2.0", "id": 1, "method": "eth_getLogs",
            "params": [{"address": pairs, "fromBlock": hex(frm), "toBlock": hex(to), "topics": [SYNC_T]}]}
    err = None
    for u in LOG_NODES:
        try:
            r = _SESS.s.post(u, json=body, timeout=4).json()
            if isinstance(r.get("result"), list):
                return r["result"]
            err = r.get("error")
        except Exception as e:
            err = e
    raise RuntimeError(f"logs: {str(err)[:60]}")


def on_sync(l, w, c, usd, stats):
    """A pool changed: BNB up and not our tx = someone bought (in nets 3/4 almost always a paid bot)."""
    p = Web3.to_checksum_address(l["address"])
    cn = COINS.get(p)
    if not cn:
        return
    d = l["data"][2:]
    r0, r1 = int(d[:64], 16), int(d[64:128], 16)
    t0 = COIN_T0.get(p)
    if not t0:
        try:
            reserves(p, cn["token"])
            t0 = COIN_T0.get(p)
        except Exception:
            return
    rb = r0 if t0.lower() == B.WBNB.lower() else r1
    prev = LAST_R.get(p)
    LAST_R[p] = rb
    blk = int(l["blockNumber"], 16)
    if prev is None or l["transactionHash"].lower() in OUR_TX:
        return
    delta = rb - prev
    if delta >= 3 * 10 ** 15:  # a buy of 0.003 BNB or more
        BUYS.setdefault(p, []).append((blk, delta, time.time()))
        # 20.11 01:10 (user): no entries after bot buys any more — RIOT (9.8 min old) was drained 29 s after our entry with no bot buy.


def bg_scan(frm, to):
    try:
        scan_coins(frm, to)
    except Exception as e:
        say(f"поиск монет: {str(e)[:70]}")
    finally:
        SCAN["busy"] = False


def consider(fr, to, val, num, src, w, c, usd, stats):
    """One BNB transfer: is it a live creator paying a bot at least SHARE of the pool? Then trade (one at a time, once per coin)."""
    live = {cn["creator"]: p for p, cn in list(COINS.items()) if time.time() - cn["born_ts"] < MAX_AGE_MIN * 60}
    p = live.get(fr)
    if not p or not to or val <= 0:
        return
    cn = COINS[p]
    if to in (p.lower(), B.ROUTER.lower()):
        return
    # 20.11 00:47 (DXY): entering ON the payment broke the bot — our entry buys half the stake (price +0.1 %) after the bot already
    # fixed its price (tolerance 0.01 %). The manual tests that worked entered in a QUIET moment, before the next payment. So a payment
    # is only remembered here: who was paid, when. The entry starts in scan_block right after a paid bot's buy is seen.
    with TRIG:
        if to not in BOT_WAIT or BOT_BUY.get(to):  # first sight of this payment (mempool or block)
            BOT_WAIT[to] = num
            BOT_BUY.pop(to, None)
            BOT_COIN[to] = p
            PAYS.setdefault(p, []).append(dict(to=to, value=val, block=num, t=time.time(), src=src))


TRIG = threading.Lock()
BOT_COIN = {}  # paid bot -> pair of its coin
PAYS = {}  # pair -> payments seen (dict to, value, block, t, src)


def pending(p):
    """Payments of this coin seen in the mempool in the last 15 s with no buy in its pool after them."""
    last_buy_t = max((t for _, _, t in BUYS.get(p, [])), default=0)
    return [x for x in PAYS.get(p, []) if x["t"] > last_buy_t and time.time() - x["t"] < 15]


def try_enter(p, w, c, usd, stats, why):
    """20.11 01:15 (user: «зайти в начале, подождать бота и выйти сразу после его покупки»): a NEW coin, at most ENTER_MAX_AGE_S old.
    Manual tests at 47 s - 2.6 min of age: the bot bought 25-75 s after our entry, the drain came 24-26 s after that buy."""
    cn = COINS.get(p)
    if not cn or cn["traded"] or RUN["busy"]:
        return
    if time.time() - cn["born_ts"] > ENTER_MAX_AGE_S or pending(p):
        return
    with TRIG:
        if cn["traded"] or RUN["busy"]:
            return
        RUN["busy"] = True
    pay = dict(to="", value=0, block=HEAD["n"], src=why, t=time.time())
    threading.Thread(target=trade, args=(w, c, p, cn, pay, usd, stats), daemon=True).start()


START = {"t": time.time()}
ENTER_MAX_MIN = 20  # (old rule, not used for entries)
ENTER_MAX_AGE_S = 120  # enter only a coin this young
EXIT_BUY_MIN = 2 * 10 ** 16  # the first buy of 0.02 BNB or more after our entry = the paid bot; dust at the start does not count
MP_STATS = {}  # source -> transactions seen
MP_LOG = {}


def mempool_loop(url, name, w, c, usd, stats):
    """20.11 (user: «и неподтверждённые, параллельно, надёжно и быстро»): pending transactions from our p2p listener and from
    publicnode at the same time; a creator's payment seen here starts the entry before its block. Reconnects for ever."""
    from websockets.sync.client import connect
    while not stop_asked():
        try:
            with connect(url, open_timeout=10, max_size=None, ping_interval=20) as ws:
                ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_subscribe", "params": ["newPendingTransactions", True]}))
                if time.time() - MP_LOG.get(name, 0) > 300:
                    say(f"мемпул {name}: подключён")
                    MP_LOG[name] = time.time()
                while not stop_asked():
                    try:
                        msg = json.loads(ws.recv(timeout=20))
                    except TimeoutError:
                        raise RuntimeError("молчит 20 с")
                    tx = (msg.get("params") or {}).get("result")
                    if not isinstance(tx, dict) or not tx.get("from"):
                        continue
                    MP_STATS[name] = MP_STATS.get(name, 0) + 1
                    val = int(tx.get("value") or "0x0", 16)
                    if val > 0 and tx.get("to"):
                        consider(tx["from"].lower(), tx["to"].lower(), val, HEAD["n"], "мемпул " + name, w, c, usd, stats)
        except Exception as e:
            if time.time() - MP_LOG.get(name + "_err", 0) > 300:  # 20.11: publicnode drops often; one line per 5 min
                say(f"мемпул {name}: {str(e)[:60]} — переподключаюсь (дальше молча 5 мин)")
                MP_LOG[name + "_err"] = time.time()
            time.sleep(2)


def scan_block(blk, num, w, c, usd, stats):
    """Creators' payments to bots and the paid bots' next transaction, from one raw block."""
    bts = int(blk["timestamp"], 16)
    live = {p: cn for p, cn in list(COINS.items()) if (bts - cn["born_ts"]) < MAX_AGE_MIN * 60}
    creators = {cn["creator"]: p for p, cn in live.items()}
    nxt = num
    if True:
        for tx in blk["transactions"]:
            fr = tx["from"].lower()
            if fr in BOT_WAIT and fr not in BOT_BUY and nxt > BOT_WAIT[fr]:
                BOT_BUY[fr] = nxt
                if BOT_COIN.get(fr) and HEAD["n"] - nxt <= 3:  # only a FRESH bot buy marks a quiet moment (not one replayed from history)
                    try_enter(BOT_COIN[fr], w, c, usd, stats, f"после покупки бота {fr[:10]} в блоке {nxt}")
            p = creators.get(fr)
            val = int(tx.get("value") or "0x0", 16)
            if not p or not tx.get("to") or val <= 0:
                continue
            consider(fr, tx["to"].lower(), val, nxt, "блок", w, c, usd, stats)
            continue
            tx = dict(tx, value=val)
            cn = COINS[p]
            to = tx["to"].lower()
            if to in (p.lower(), B.ROUTER.lower()):
                continue
            try:
                rb, _ = reserves(p, cn["token"])
            except Exception:
                continue
            share = tx["value"] / rb if rb else 0
            if share < SHARE or cn["traded"] or RUN["busy"]:
                continue
            BOT_WAIT[to] = nxt
            BOT_BUY.pop(to, None)
            pay = dict(to=to, value=tx["value"], block=nxt)
            RUN["busy"] = True  # one trade at a time: the result is the operating wallet's balance change
            threading.Thread(target=trade, args=(w, c, p, cn, pay, usd, stats), daemon=True).start()


BOT_WAIT = {}  # paid bot -> payment block
TOPUP = {"t": 0.0}
# 20.11 00:32: reading a full block through bot.w3 took ~0.9 s (> 0.45 s per block), the loop fell 123 blocks behind and
# HIGGSFIELD's payment was seen 55 s late. Raw JSON-RPC to fast nodes takes 130-180 ms; up to 8 blocks are read at once.
import requests  # noqa: E402
from concurrent.futures import ThreadPoolExecutor  # noqa: E402
RAW_NODES = ["https://bsc-dataseed1.binance.org", "https://bsc-rpc.publicnode.com", "https://bsc-dataseed2.defibit.io", "https://rpc-bsc.48.club"]
_SESS = threading.local()
POOL = ThreadPoolExecutor(8)


def raw(m, p, k=0):
    if not hasattr(_SESS, "s"):
        _SESS.s = requests.Session()
    err = None
    for i in range(len(RAW_NODES)):
        u = RAW_NODES[(k + i) % len(RAW_NODES)]
        try:
            r = _SESS.s.post(u, json={"jsonrpc": "2.0", "id": 1, "method": m, "params": p}, timeout=5).json()
            if r.get("result") is not None:
                return r["result"]
            err = r.get("error")
        except Exception as e:
            err = e
    raise RuntimeError(f"{m}: {str(err)[:60]}")


def raw_head():
    out = []
    for k in range(2):
        try:
            out.append(int(raw("eth_blockNumber", [], k), 16))
        except Exception:
            pass
    if not out:
        raise RuntimeError("нет головы")
    return max(out)

if __name__ == "__main__":
    sys.exit(main())
