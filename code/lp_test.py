"""19.11 (user: «стать пулом»): ONE live test of the liquidity idea on a coin of network 3 or 4 (code 1365, guard 0x623e7c23 / 0xa70b3f34).
W1's contract buys coins for half the stake and adds them with BNB to the pool in ONE transaction, the pool share (LP) goes to W2's
contract (the guard marks a buyer 2 blocks after the buy since 19.11, so nothing may hold the coin between blocks). At --exit-min minutes after the launch (default 27,
the creator drains at 28.7+ min) W2 takes the share out to a fresh W3, W3 sells the coins, every leftover BNB goes back to A.
Ctrl+C or the file analysis/lp_stop.flag while holding = take the share out now. Results: analysis/lp_test.csv, analysis/lp_test.log.
Usage: python lp_test.py [--usd 1] [--exit-min 27] [--max-age 300] [--token 0x..] [--dry]
--dry: finds the coin and prints the plan, no transaction is sent."""
import csv
import json
import os
import random
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
_args = sys.argv[1:]
sys.argv = sys.argv[:1]  # bot.py reads sys.argv on import


def arg(name, default, cast=str):
    return cast(_args[_args.index(name) + 1]) if name in _args else default


USD = arg("--usd", 1.0, float)
EXIT_MIN = arg("--exit-min", 27.0, float)
MAX_AGE = arg("--max-age", 300, int)  # take only a coin younger than this (s): the share must ride the pumping
TOKEN = arg("--token", None)
DRY = "--dry" in _args
LOG = os.path.join(HERE, "lp_test.log")
CSV = os.path.join(HERE, "lp_test.csv")
STOP = os.path.join(HERE, "lp_stop.flag")


def no_quickedit():
    """19.11: a click in the console window (QuickEdit selection) freezes print() and the whole test, the exit too."""
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
    with open(LOG, "a", encoding="utf-8") as f:  # the file first: a frozen console never loses the log
        f.write(line + "\n")
    print(line, flush=True)


import bot as B  # noqa: E402
from web3 import Web3  # noqa: E402

w3 = B.w3
GUARDS = {"0x623e7c23c7c228095f94d9bd567eae55cb87a966": 3, "0xa70b3f34e3ca7d4665b794ce6bb29168b1d2e40d": 4}
FACTORY = Web3.to_checksum_address("0xca143ce32fe78f1f7019d7d551a6402fc5350c73")
PAIRCREATED = "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"
USDT = Web3.to_checksum_address("0x55d398326f99059fF775485246999027B3197955")
LOGS = Web3(Web3.HTTPProvider("https://rpc-bsc.48.club", request_kwargs={"timeout": 30}))  # publicnode may give empty logs
PAIR_ABI = [{"name": "getReserves", "type": "function", "stateMutability": "view", "inputs": [],
             "outputs": [{"type": "uint112"}, {"type": "uint112"}, {"type": "uint32"}]},
            {"name": "token0", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "address"}]},
            {"name": "totalSupply", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "uint256"}]},
            {"name": "balanceOf", "type": "function", "stateMutability": "view", "inputs": [{"type": "address"}], "outputs": [{"type": "uint256"}]},
            {"name": "approve", "type": "function", "stateMutability": "nonpayable", "inputs": [{"type": "address"}, {"type": "uint256"}], "outputs": [{"type": "bool"}]}]
ROUTER_LP_ABI = [
    {"name": "addLiquidityETH", "type": "function", "stateMutability": "payable",
     "inputs": [{"type": "address"}, {"type": "uint256"}, {"type": "uint256"}, {"type": "uint256"}, {"type": "address"}, {"type": "uint256"}],
     "outputs": [{"type": "uint256"}, {"type": "uint256"}, {"type": "uint256"}]},
    {"name": "removeLiquidityETHSupportingFeeOnTransferTokens", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"type": "address"}, {"type": "uint256"}, {"type": "uint256"}, {"type": "uint256"}, {"type": "address"}, {"type": "uint256"}],
     "outputs": [{"type": "uint256"}]},
    {"name": "getAmountsOut", "type": "function", "stateMutability": "view", "inputs": [{"type": "uint256"}, {"type": "address[]"}],
     "outputs": [{"type": "uint256[]"}]}]
RT = w3.eth.contract(B.ROUTER, abi=ROUTER_LP_ABI)
ZAP = json.load(open(os.path.join(HERE, "lpzap", "combined.json"), encoding="utf-8"))["contracts"]["LpZap.sol:LpZap"]
ERC = w3.eth.contract(abi=[{"name": "balanceOf", "type": "function", "stateMutability": "view", "inputs": [{"type": "address"}],
                            "outputs": [{"type": "uint256"}]},
                           {"name": "allowance", "type": "function", "stateMutability": "view", "inputs": [{"type": "address"}, {"type": "address"}],
                            "outputs": [{"type": "uint256"}]},
                           {"name": "symbol", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "string"}]}])


def retry(fn, tries=5):
    for k in range(tries):
        try:
            return fn()
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(0.4)


def wait(snd, h, sec=30):
    t_end = time.time() + sec
    while time.time() < t_end:
        time.sleep(0.2)
        rc = snd.receipt(h)
        if rc:
            return rc
    return None


def reserves(pair, token):
    p = w3.eth.contract(pair, abi=PAIR_ABI)
    r0, r1, _ = retry(lambda: p.functions.getReserves().call())
    t0 = retry(lambda: p.functions.token0().call())
    return (r0, r1) if t0.lower() == B.WBNB.lower() else (r1, r0)  # (bnb, tokens)


def find_coin():
    """The newest pool of network 3/4 younger than MAX_AGE: (token, pair, net, launch block)."""
    head = w3.eth.block_number
    back = int(MAX_AGE / 0.45) + 20
    lg = []
    for frm in range(head - back, head + 1, 200):
        lg += retry(lambda: LOGS.eth.get_logs({"address": FACTORY, "fromBlock": frm, "toBlock": min(frm + 199, head), "topics": [PAIRCREATED]}))
    for l in reversed(lg):
        t0, t1 = "0x" + l["topics"][1].hex()[-40:], "0x" + l["topics"][2].hex()[-40:]
        tok = t1 if t0.lower() == B.WBNB.lower() else t0 if t1.lower() == B.WBNB.lower() else None
        if not tok:
            continue
        tok = Web3.to_checksum_address(tok)
        if len(retry(lambda: w3.eth.get_code(tok))) != 1365:
            continue
        g = "0x" + retry(lambda: w3.eth.get_storage_at(tok, 6)).hex()[-40:]
        if g.lower() in GUARDS:
            pair = Web3.to_checksum_address("0x" + bytes(l["data"])[12:32].hex())
            return tok, pair, GUARDS[g.lower()], l["blockNumber"]
    return None


def coin_by_token(tok):
    tok = Web3.to_checksum_address(tok)
    g = "0x" + w3.eth.get_storage_at(tok, 6).hex()[-40:]
    pair = Web3.to_checksum_address(B.factory.functions.getPair(tok, B.WBNB).call()) if hasattr(B, "factory") else None
    if pair is None:
        f = w3.eth.contract(FACTORY, abi=[{"name": "getPair", "type": "function", "stateMutability": "view",
                                           "inputs": [{"type": "address"}, {"type": "address"}], "outputs": [{"type": "address"}]}])
        pair = f.functions.getPair(tok, B.WBNB).call()
    head = w3.eth.block_number
    born = None
    for frm in range(head - 8000, head + 1, 200):
        lg = retry(lambda: LOGS.eth.get_logs({"address": FACTORY, "fromBlock": frm, "toBlock": min(frm + 199, head), "topics": [PAIRCREATED]}))
        for l in lg:
            if tok.lower()[2:] in (l["topics"][1].hex()[-40:].lower(), l["topics"][2].hex()[-40:].lower()):
                born = l["blockNumber"]
        if born:
            break
    return tok, pair, GUARDS.get(g.lower(), 0), born


def bnb_usd():
    try:
        return RT.functions.getAmountsOut(10 ** 18, [B.WBNB, USDT]).call()[1] / 1e18
    except Exception:
        return 600.0


def keys():
    out = {}
    for line in open(os.path.join(ROOT, "hop_wallets.env"), encoding="utf-8"):
        if line.startswith("HOP_KEY_"):
            k, v = line.strip().split("=", 1)
            out[int(k[8:])] = v
    return out


def fresh_ids(n, token):
    """19.11: every W has held some coin by now, so: wallets that never held THIS coin (hop_used.json), random.
    The guard's mark lives in the coin's own contract, so a wallet clean on this coin is clean."""
    try:
        used = set(json.load(open(os.path.join(HERE, "hop_used.json"), encoding="utf-8")).get(token.lower(), []))
    except Exception:
        used = set()
    ks = keys()
    ids = [i for i in ks if f"W{i}" not in used]
    random.shuffle(ids)
    return [(i, ks[i]) for i in ids[:n]]


def mark_used(token, names):
    p = os.path.join(HERE, "hop_used.json")
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        d = {}
    d.setdefault(token.lower(), [])
    d[token.lower()] = sorted(set(d[token.lower()]) | set(names))
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=0)


def back_to_a(A, w, name, price):
    try:
        left = w3.eth.get_balance(w.address) - 21_000 * price
        if left > 0:
            wait(w, w.send(A.address, b"", value=left, gas=21_000, gas_price=price))
    except Exception as e:
        say(f"{name}: остаток на A не вернулся ({str(e)[:60]})")


INTR = {"v": False}


def on_int(*_):
    INTR["v"] = True
    print("  Ctrl+C: доля будет забрана при первой возможности", flush=True)


def main():
    import signal
    signal.signal(signal.SIGINT, on_int)
    if os.path.exists(os.path.join(HERE, "signal_on.json")) and not DRY:
        say("СТОП: в боте включена торговля (analysis/signal_on.json). Нажми «Стоп» в панели — кошелёк A общий, иначе собьются nonce.")
        return 2
    if os.path.exists(STOP):
        os.remove(STOP)
    usd = bnb_usd()
    stake = round(USD / usd, 6)
    say(f"=== ТЕСТ ЛИКВИДНОСТИ: ставка ${USD} = {stake} BNB (BNB ${usd:.0f}), выход на {EXIT_MIN} мин после запуска монеты ===")
    coin = None
    if TOKEN:
        coin = coin_by_token(TOKEN)
        if not coin[2]:
            say("это не монета сети 3/4 (сторож в слоте 6 не тот)")
            return 2
    else:
        say(f"жду новую монету сети 3 или 4 (моложе {MAX_AGE} с)…")
        t_end = time.time() + 3 * 3600
        while coin is None and time.time() < t_end:
            try:
                coin = find_coin()
            except Exception as e:
                say(f"поиск: {str(e)[:80]}")
            if coin is None:
                time.sleep(10)
        if coin is None:
            say("за 3 ч новой монеты не было")
            return 2
    token, pair, net, born = coin
    born_ts = w3.eth.get_block(born)["timestamp"] if born else time.time()
    sym = retry(lambda: w3.eth.contract(token, abi=ERC.abi).functions.symbol().call())[:20]
    rb, rt = reserves(pair, token)
    age = time.time() - born_ts
    say(f"монета {sym} {token}, сеть {net}, пул {pair}, {rb / 1e18:.3f} BNB, возраст {age:.0f} с")
    if age > EXIT_MIN * 60 - 120:
        say("монета слишком старая для теста")
        return 2
    if DRY:
        say(f"ПЛАН (без денег): контракт W1 одной транзакцией покупает на {stake / 2:.6f} BNB и вносит в пул, доля → контракт W2; "
            f"на {EXIT_MIN} мин (через {(EXIT_MIN * 60 - age) / 60:.1f} мин) контракт W2 одной транзакцией забирает долю и продаёт, остатки → A")
        return 0
    A = B.Sender(B.load_private_key())
    A.refresh_gas_price(force=True)
    price = A.gas_price
    a0 = w3.eth.get_balance(A.address)  # the result is the change of A: every leftover comes back there
    # 19.11 23:33: the guard of net 3 marked a buyer 2 blocks after the buy, so W1 could neither add nor sell (INO, −$1).
    # Now one transaction does it all: contract C1 (W1's) buys half + adds, the share goes to contract C2 (W2's), which never
    # holds the coin until its own one-transaction exit (burn + sell). No wallet ever holds the coin between blocks.
    (i1, k1), (i2, k2) = fresh_ids(2, token)
    W1, W2 = B.Sender(k1), B.Sender(k2)
    names = [f"W{i1}", f"W{i2}"]
    mark_used(token, names)
    say(f"кошельки: W{i1} (контракт входа), W{i2} (контракт выхода); газ {price / 1e9:.3f} gwei")
    gas_w = (1_400_000 + 450_000 + 21_000) * price  # 20.11: the deploy takes ~1.07M gas (900k ran out)
    need_a = int(stake * 1e18) + 2 * gas_w + 2 * 21_000 * price
    if w3.eth.get_balance(A.address) < need_a + 21_000 * price * 4:
        say(f"на A мало BNB: нужно {need_a / 1e18:.6f}")
        return 2
    gas_spent = 0.0
    rows = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "net": net, "token": token, "symbol": sym, "pair": pair, "stake_bnb": stake,
            "age_in_s": "", "pool_in": "", "pool_out": "", "lp": "", "got_bnb": "", "gas_bnb": "", "pnl_bnb": "", "pnl_pct": "", "note": ""}

    def tx(snd, h, what, sec=30):
        nonlocal gas_spent
        rc = wait(snd, h, sec)
        if rc:
            gas_spent += B.gas_cost(rc)
        ok = bool(rc and rc["status"] == 1)
        say(f"  {what}: {'ok' if ok else 'НЕ ПРОШЛО' if rc else 'нет квитанции'}  {h}")
        return ok, rc

    def finish(note, got=0.0):
        for w, n in ((W1, names[0]), (W2, names[1])):
            back_to_a(A, w, n, price)
        time.sleep(2)
        pnl = (w3.eth.get_balance(A.address) - a0) / 1e18
        rows.update(got_bnb=f"{got:.8f}", gas_bnb=f"{gas_spent:.8f}", pnl_bnb=f"{pnl:.8f}", pnl_pct=round(pnl / stake * 100, 2), note=note)
        write_row(rows)
        say(f"=== ИТОГ: ставка {stake} BNB, получено {got:.6f} BNB, газ {gas_spent:.6f}, итог {pnl:+.6f} BNB ({pnl / stake * 100:+.1f}%, "
            f"${pnl * usd:+.2f}); пул {rows['pool_in']} → {rows['pool_out']} BNB; {note} ===")

    for w, amt, n in ((W1, int(stake * 1e18) + gas_w, names[0]), (W2, gas_w, names[1])):
        ok, _ = tx(A, A.send(w.address, b"", value=amt, gas=21_000, gas_price=price), f"A → {n} {amt / 1e18:.6f} BNB")
        if not ok:
            say("пополнение не прошло — стоп")
            finish("пополнение не прошло")
            return 1
    for w in (W1, W2):
        w.resync()
        w.gas_price = price
    C1 = deploy(W1, price, tx, names[0])
    C2 = deploy(W2, price, tx, names[1])
    if not (C1 and C2):
        finish("контракт не создан")
        return 1
    z1, z2 = w3.eth.contract(C1, abi=ZAP["abi"]), w3.eth.contract(C2, abi=ZAP["abi"])
    pc = w3.eth.contract(pair, abi=PAIR_ABI)
    rb, rt = reserves(pair, token)
    ts = retry(lambda: pc.functions.totalSupply().call())
    min_lp = int(int(stake * 1e18) // 2 * ts / rb * 0.85)
    if INTR["v"] or os.path.exists(STOP):
        finish("остановлено до входа")
        return 1
    ok, rc = tx(W1, W1.send(C1, z1.encode_abi("enter", args=[pair, token, C2, min_lp]), value=int(stake * 1e18), gas=450_000),
                f"{names[0]} ВХОД одной транзакцией: {stake} BNB → доля пула на контракт {names[1]}")
    if not ok:
        finish("вход не прошёл")
        return 1
    L = retry(lambda: pc.functions.balanceOf(C2).call())
    ts = retry(lambda: pc.functions.totalSupply().call())
    rb, rt = reserves(pair, token)
    in_value = 2 * L * rb / ts / 1e18
    rows.update(age_in_s=round(time.time() - born_ts), pool_in=round(rb / 1e18, 4), lp=L)
    say(f"В ПУЛЕ: доля {L / ts * 100:.4f}% пула = {in_value:.6f} BNB (ставка {stake}); пул {rb / 1e18:.4f} BNB. Жду до {EXIT_MIN} мин…")
    peak, why, last = rb, "время", 0
    while True:
        age = time.time() - born_ts
        if age >= EXIT_MIN * 60:
            break
        if os.path.exists(STOP) or INTR["v"]:
            why = "стоп-файл" if os.path.exists(STOP) else "Ctrl+C"
            break
        try:
            rb, rt = reserves(pair, token)
            ts = pc.functions.totalSupply().call()
        except Exception:
            time.sleep(1)
            continue
        peak = max(peak, rb)
        if rb < peak * 0.95:
            why = f"пул упал с {peak / 1e18:.3f} до {rb / 1e18:.3f} BNB"
            break
        if time.time() - last > 30:
            v = 2 * L * rb / ts / 1e18
            say(f"  {age / 60:5.1f} мин: пул {rb / 1e18:.4f} BNB, доля стоит {v:.6f} BNB ({(v / stake - 1) * 100:+.1f}% к ставке)")
            last = time.time()
        time.sleep(2)
    say(f"ВЫХОД ({why}): контракт {names[1]} забирает долю и продаёт монеты одной транзакцией")
    got, out = 0.0, False
    w2_before = w3.eth.get_balance(W2.address)
    for k in range(4):
        try:
            ok, rc = tx(W2, W2.send(C2, z2.encode_abi("exit", args=[pair, token, 0]), gas=450_000), f"{names[1]} ВЫХОД")
            if ok:
                out = True
                break
        except Exception as e:
            say(f"  выход не отправлен: {str(e)[:80]}")
            W2.resync()
        if retry(lambda: pc.functions.balanceOf(C2).call()) == 0:
            out = True
            break
        time.sleep(1)
    rb_out, _ = reserves(pair, token)
    rows["pool_out"] = round(rb_out / 1e18, 4)
    if out:
        got = (w3.eth.get_balance(W2.address) - w2_before) / 1e18 + 450_000 * price / 1e18 * 0  # shown roughly; the result is A's change
        finish(why, max(got, 0.0))
        return 0
    say(f"!!! ДОЛЯ ОСТАЛАСЬ НА КОНТРАКТЕ {C2} ({names[1]}) — газ на {names[1]} оставлен, напиши Claude, заберём вручную")
    back_to_a(A, W1, names[0], price)
    rows.update(gas_bnb=f"{gas_spent:.8f}", note=why + f"; ДОЛЯ НА КОНТРАКТЕ {C2}")
    write_row(rows)
    return 1


def deploy(w, price, tx, name):
    """Our LpZap from analysis/lpzap/combined.json; the address is computed from the sender and its nonce."""
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
    ok, _ = tx(w, "0x" + sg.hash.hex().removeprefix("0x"), f"{name} создаёт контракт")
    if not ok:
        return None
    return Web3.to_checksum_address(Web3.keccak(rlp.encode([bytes.fromhex(w.address[2:]), nonce]))[12:])


def write_row(r):
    new = not os.path.exists(CSV)
    with open(CSV, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(r), delimiter=";")
        if new:
            w.writeheader()
        w.writerow(r)


if __name__ == "__main__":
    sys.exit(main())
