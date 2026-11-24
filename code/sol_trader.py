"""18.11 (user: «нам нужны тесты на копеечных суммах… начинай делать бота для Солана»). Solana bot for the PumpSwap farm.
Strategy (paper, sol_watch 12 pools): the farm's creator opens a pool with 55 SOL and in the same slot buys for 35 SOL (pool 90 SOL);
we buy ~2-3 s later, sell at +TP % (default 10 %), or at once if the pool SOL drops > 15 % at once (liquidity withdraw starts),
or after MAX_HOLD s.
Modes:
  (default)  DRY: no money. On every new pool: builds our real buy tx but with a farm wallet as the user and runs simulateTransaction
             (sigVerify off) -> proves the instruction is right; paper trade with the same rules; sell tx simulated the same way.
  --live     real trades from the wallet in D:\\bot\\sol_wallet.env (SOL_KEY, created by --new-wallet, never printed),
             stake --stake SOL (default 0.01), at most --max N trades (default 10), stops when the total result <= --loss SOL
             (default -0.03) or when D:\\bot\\analysis\\sol_stop.flag exists. Every real tx is simulated first; a failed simulation = not sent.
  --new-wallet  creates the wallet file (refuses to overwrite) and prints the PUBLIC address only.
Detection = sol_watch.py (Helius websockets: treasury, sponsor, creators; new creators by a >= 20 SOL transfer with the treasury/sponsor).
Out: analysis/sol_trades.csv, analysis/sol_trader.log.
Usage: D:\\bot\\.venv\\Scripts\\python.exe -X utf8 analysis\\sol_trader.py [--live] [--stake 0.01] [--max 10] [--loss -0.03] [--tp 10]"""
import argparse
import asyncio
import base64
import csv
import json
import os
import struct
import sys
import threading
import time
import urllib.request

import websockets
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction

if sys.stdout:
    sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--live", action="store_true")
ap.add_argument("--new-wallet", action="store_true")
ap.add_argument("--stake", type=float, default=0.01)
ap.add_argument("--max", type=int, default=10)
ap.add_argument("--loss", type=float, default=-0.03)
ap.add_argument("--tp", type=float, default=10.0)
ap.add_argument("--hold", type=float, default=120.0)
ap.add_argument("--prio", type=int, default=100_000, help="priority fee, micro-lamports per CU")
ARGS = ap.parse_args()
SETTINGS_FILE = os.path.join(HERE, "sol_settings.json")
# 19.11 (user: live trading on the ten wallets' signals, smallest stake): signal-mode settings, all in sol_settings.json
#   family_only  buy only pools created by known Family-1 creators (nine_follow's list + creators learned via the treasury); pools found only
#                by the public pool scan (unknown creator) are skipped — those were the 18.11 losses
#   ten_idle     sell when the ten wallets have made no trade in this pool for this many seconds (after they started)
#   ten_wait     sell when the ten have not traded in this pool within this many seconds after our buy
#   since        "YYYY-MM-DD HH:MM" UTC: only LIVE rows from this moment count for --max and --loss (the old -0.212 SOL is not counted again)
SET = dict(family_only=True, ten_idle=20.0, ten_wait=90.0, since="", skip_rug_creators=True)
try:  # 18.11 (user: «увеличим сумму ставок»): stake / max / loss / tp / hold override the command line; when this file changes the bot
    # restarts itself (reload_watch), so the window need not be closed
    for _k, _v in json.load(open(SETTINGS_FILE, encoding="utf-8")).items():
        if _k in ("stake", "max", "loss", "tp", "hold"):
            setattr(ARGS, _k, type(getattr(ARGS, _k))(_v))
        elif _k in SET:
            SET[_k] = type(SET[_k])(_v)
except (OSError, ValueError):
    pass
ACTIVITY_FILE = os.path.join(HERE, "ten_activity.json")  # written every second by nine_follow.py

WALLET_FILE = r"D:\bot\sol_wallet.env"
LOG = os.path.join(HERE, "sol_trader.log")
TRADES = os.path.join(HERE, "sol_trades.csv")
STOP_FLAG = os.path.join(HERE, "sol_stop.flag")
CREATORS_FILE = os.path.join(HERE, "sol_watch_creators.json")

AMM = Pubkey.from_string("pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA")
ATA_PROG = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
TOKEN = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
SYSTEM = Pubkey.from_string("11111111111111111111111111111111")
CBUDGET = Pubkey.from_string("ComputeBudget111111111111111111111111111111")
WSOL = Pubkey.from_string("So11111111111111111111111111111111111111112")
GLOBAL_VOL = Pubkey.find_program_address([b"global_volume_accumulator"], AMM)[0]
SELL_DISC = bytes.fromhex("33e685a4017f83ad")   # PumpSwap «sell» base (=WSOL here) for quote (=the coin) -> this is OUR BUY
BEQI_DISC = bytes.fromhex("c62e1552b4d9e870")   # «buy_exact_quote_in»: spend the coin, get WSOL -> this is OUR SELL
TREASURY = "B7StC42h126Hmtkw4GoRMcaHA5ZuKawfa9fCSTV6UMSj"
SPONSOR = "3azG8rmtPnNUz77a6UqBKt1ykpk3JQpLLNTaoXW31b34"
FEE, SLIP = 0.003, 0.15
SELL_SLIP = 0.30  # 18.11: a sale that does not go through can cost the whole stake; 30 % tolerance, no pre-sell simulation
MIN_ENTRY_SOL = 80.0  # 18.11: buy only while the pool holds the farm's level (85-90 SOL); entries at 66-70 SOL were dumped pools
KEY = None
for _l in open(r"D:\bot\.env", encoding="utf-8-sig"):
    if _l.strip().startswith("HELIUS_KEY="):
        KEY = _l.split("=", 1)[1].strip().strip('"\'')
HTTP = "https://mainnet.helius-rpc.com/?api-key=" + KEY
WSS = "wss://mainnet.helius-rpc.com/?api-key=" + KEY
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58d(s):
    n = 0
    for c in s:
        n = n * 58 + B58.index(c)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return b"\0" * (len(s) - len(s.lstrip("1"))) + raw


# 18.11: a click in the console window (QuickEdit selection) stops print(), and every thread that printed froze (14:47-14:5x, no
# position by luck). Now: QuickEdit is switched off at start, the log file is written first, the console gets lines from its own thread.
import queue as _queue
_CON = _queue.Queue(maxsize=5000)


def _con_loop():
    while True:
        s = _CON.get()
        try:
            print(s, flush=True)
        except Exception:
            pass


def _no_quickedit():
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-10)
        mode = ctypes.c_uint32()
        if k.GetConsoleMode(h, ctypes.byref(mode)):
            k.SetConsoleMode(h, (mode.value & ~0x0040) | 0x0080)  # off: ENABLE_QUICK_EDIT_MODE; on: ENABLE_EXTENDED_FLAGS
    except Exception:
        pass


if sys.stdout:
    _no_quickedit()
    threading.Thread(target=_con_loop, daemon=True).start()


def say(*a):
    s = time.strftime("%d.%m %H:%M:%S ") + " ".join(str(x) for x in a)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(s + "\n")
    except OSError:
        pass
    if sys.stdout:
        try:
            _CON.put_nowait(s)
        except _queue.Full:
            pass


# 19.11 night check: Helius answered 429 dozens of times a minute (several programs share the key); retrying Helius did not help. Now a
# read-only call or a send of an already signed tx goes once to the free public node right after a 429 (a signed tx cannot run twice),
# and the 429 line is printed at most once a minute.
PUB_FALLBACK = "https://api.mainnet-beta.solana.com"
FALLBACK_OK = {"getLatestBlockhash", "getBalance", "getTokenAccountBalance", "getAccountInfo", "getMultipleAccounts", "getSignatureStatuses",
               "sendTransaction"}
_L429 = [0.0, 0]


def _post(url, method, params, timeout):
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
                                                                    headers={"Content-Type": "application/json"}), timeout=timeout).read())


def rpc(method, params, tries=4, timeout=15):
    err = None
    for k in range(tries):
        try:
            r = _post(HTTP, method, params, timeout)
            if "error" in r:
                err = r["error"]
                if method in ("sendTransaction", "simulateTransaction"):
                    return {"error": err}
            elif r.get("result") is not None or method in ("sendTransaction", "simulateTransaction"):
                return r["result"]
            # an empty result (tx not confirmed yet) -> retry
        except Exception as e:
            err = str(e)
            if "429" in err:
                _L429[1] += 1
                if time.time() - _L429[0] > 60:
                    say(f"Helius: лимит запросов (429), за минуту {_L429[1]} раз (последний {method}) — беру ответ у публичного узла")
                    _L429[0], _L429[1] = time.time(), 0
                if method in FALLBACK_OK:
                    try:
                        r = _post(PUB_FALLBACK, method, params, timeout)
                        if "error" in r and method == "sendTransaction":
                            return {"error": r["error"]}
                        if r.get("result") is not None:
                            return r["result"]
                    except Exception:
                        pass
        time.sleep(0.25 * (k + 1))
    return {"error": err} if method in ("sendTransaction", "simulateTransaction") else None


def ata(owner, mint, prog):
    return Pubkey.find_program_address([bytes(owner), bytes(prog), bytes(mint)], ATA_PROG)[0]


def user_vol(user):
    return Pubkey.find_program_address([b"user_volume_accumulator", bytes(user)], AMM)[0]


# ---------- wallet ----------
if ARGS.new_wallet:
    if os.path.exists(WALLET_FILE):
        sys.exit("кошелёк уже есть, не перезаписываю: " + WALLET_FILE)
    kp = Keypair()
    with open(WALLET_FILE, "w", encoding="utf-8") as f:
        f.write("# Solana wallet of sol_trader.py. SECRET, never share.\nSOL_KEY=" + str(kp) + "\n")
    print("создан кошелёк Solana, адрес для пополнения:", kp.pubkey())
    sys.exit(0)
KP = None
if os.path.exists(WALLET_FILE):
    for _l in open(WALLET_FILE, encoding="utf-8"):
        if _l.startswith("SOL_KEY="):
            KP = Keypair.from_base58_string(_l.split("=", 1)[1].strip())
if ARGS.live and not KP:
    sys.exit("нет кошелька: сначала --new-wallet и пополнить")

# ---------- blockhash cache ----------
BH = {"h": None, "t": 0}


def blockhash():
    if not BH["h"] or time.time() - BH["t"] > 20:
        r = rpc("getLatestBlockhash", [{"commitment": "confirmed"}])
        if r:
            BH["h"], BH["t"] = Hash.from_string(r["value"]["blockhash"]), time.time()
    return BH["h"]


def bh_loop():
    while True:
        try:
            r = rpc("getLatestBlockhash", [{"commitment": "confirmed"}])
            if r:
                BH["h"], BH["t"] = Hash.from_string(r["value"]["blockhash"]), time.time()
        except Exception:
            pass
        time.sleep(5)


# ---------- pool template (accounts of a real farm buy in this pool) ----------
def parse_amm(t):
    """-> list of (disc, accounts[Pubkey], writable[bool], data) of PumpSwap instructions in a tx"""
    msg = t["transaction"]["message"]
    hdr = msg["header"]
    st = msg["accountKeys"]
    la = (t["meta"] or {}).get("loadedAddresses") or {}
    keys = st + la.get("writable", []) + la.get("readonly", [])
    n, nrs, nros, nrou = len(st), hdr["numRequiredSignatures"], hdr["numReadonlySignedAccounts"], hdr["numReadonlyUnsignedAccounts"]

    def w(i):
        if i < n:
            return i < nrs - nros if i < nrs else i < n - nrou
        return i < n + len(la.get("writable", []))
    out = []
    for ins in msg["instructions"]:
        if keys[ins["programIdIndex"]] != str(AMM):
            continue
        d = b58d(ins["data"])
        out.append((d[:8], [Pubkey.from_string(keys[a]) for a in ins["accounts"]], [w(a) for a in ins["accounts"]], d))
    return out


DIAG = {}  # 18.11: why a template was not found (2 of 3 pools at 11:10/11:17; later the same pools gave one in 0.5 s)


def pool_template(pool, want=(SELL_DISC,), limit=40, tries=12):
    """first successful farm tx in the pool with a PumpSwap instruction of the wanted kind -> template + vault balances"""
    seen = set()
    DIAG.update(pages=0, empty=0, txs=0, none=0, amm=0)
    for k in range(tries):
        sigs = rpc("getSignaturesForAddress", [pool, {"limit": limit, "commitment": "confirmed"}]) or []
        DIAG["pages"] += 1
        DIAG["empty"] += not sigs
        for s in reversed(sigs):
            if s.get("err") or s["signature"] in seen:
                continue
            seen.add(s["signature"])
            t = rpc("getTransaction", [s["signature"], {"encoding": "json", "maxSupportedTransactionVersion": 1, "commitment": "confirmed"}])
            DIAG["txs"] += 1
            if not t or not t.get("meta") or t["meta"].get("err"):
                DIAG["none"] += not t
                continue
            for disc, acc, wr, data in parse_amm(t):
                DIAG["amm"] += 1
                if disc in want and str(acc[0]) == pool:
                    return dict(disc=disc, acc=acc, wr=wr, data=data, sig=s["signature"], slot=t["slot"])
        time.sleep(0.3)
    return None


T22 = Pubkey.from_string("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")
GLOBAL_CFG = Pubkey.from_string("ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw")
PROTO_FEE = Pubkey.from_string("62qc2CNXwrYqQScmEdiZFFAnJR262PxWEuNQtxfafNgV")
EVENT_AUTH = Pubkey.from_string("GS4CU59F31iL7aR2Q8zVS8DRrcRnXX1yjQ66TqNVQnaR")
CREATOR_VAULT_AUTH = Pubkey.from_string("8N3GDaZ2iwN65oxVatKTLPNooAVUJTbfiVJ1ahyqwjSk")
FEE_CFG = Pubkey.from_string("5PHirr8joyTMp9JMm6nW7hNDVyEYdkzDqazxPD7RaTjx")
FEE_PROG = Pubkey.from_string("pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ")
EXTRA_FEE = Pubkey.from_string("A7hAgCzFw14fejgCp387JUJRMNyz4j89JKnhtKU8piqW")
WR_23 = [1, 1, 0, 0, 0, 1, 1, 1, 1, 0, 1, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


def derived_template(pool, mint):
    """18.11: the farm's «old» pools start at 85 SOL and nobody trades the first 30-40 s, so there is no farm buy to copy accounts
    from. Every account of the buy can be derived (checked on 3 pools): vaults from the pool's own data (offsets 139 / 171),
    #10 = ATA(protocol fee recipient, coin), #17 = ATA(creator vault authority, coin), #22 = ATA(#21, coin); the rest are constants.
    Our buy is simulated before it is sent, so a wrong guess costs nothing."""
    info = rpc("getAccountInfo", [pool, {"encoding": "base64", "commitment": "confirmed"}], tries=6)
    if not info or not info.get("value"):
        return None
    d = base64.b64decode(info["value"]["data"][0])
    p = Pubkey.from_string(pool)
    m = Pubkey.from_bytes(d[75:107])
    if str(m) != mint or Pubkey.from_bytes(d[43:75]) != WSOL:
        return None
    acc = [p, Pubkey.default(), GLOBAL_CFG, WSOL, m, Pubkey.default(), Pubkey.default(), Pubkey.from_bytes(d[139:171]), Pubkey.from_bytes(d[171:203]),
           PROTO_FEE, ata(PROTO_FEE, m, T22), TOKEN, T22, SYSTEM, ATA_PROG, EVENT_AUTH, AMM, ata(CREATOR_VAULT_AUTH, m, T22), CREATOR_VAULT_AUTH,
           FEE_CFG, FEE_PROG, EXTRA_FEE, ata(EXTRA_FEE, m, T22)]
    return dict(disc=SELL_DISC, acc=acc, wr=[bool(x) for x in WR_23], data=b"", sig="derived", slot=0)


def vaults(tm):
    """(pool SOL, pool coin) right now, by the template's vault accounts 7 (WSOL) and 8 (coin)"""
    a = rpc("getTokenAccountBalance", [str(tm["acc"][7]), {"commitment": "processed"}])
    b = rpc("getTokenAccountBalance", [str(tm["acc"][8]), {"commitment": "processed"}])
    if not a or not b:
        return None
    return int(a["value"]["amount"]), int(b["value"]["amount"]), int(b["value"]["decimals"])


# ---------- instructions ----------
def cu_ixs(limit=300_000):
    return [Instruction(CBUDGET, bytes([2]) + struct.pack("<I", limit), []),
            Instruction(CBUDGET, bytes([3]) + struct.pack("<Q", ARGS.prio), [])]


def ata_create(payer, owner, mint, prog):
    a = ata(owner, mint, prog)
    return Instruction(ATA_PROG, bytes([1]), [AccountMeta(payer, True, True), AccountMeta(a, False, True), AccountMeta(owner, False, False),
                                              AccountMeta(mint, False, False), AccountMeta(SYSTEM, False, False), AccountMeta(prog, False, False)])


def amm_metas(tm, user, extra_tail=None):
    acc, wr = list(tm["acc"]), list(tm["wr"])
    coin, coin_prog = acc[4], acc[12]
    acc[1], acc[5], acc[6] = user, ata(user, WSOL, TOKEN), ata(user, coin, coin_prog)
    metas = [AccountMeta(a, i == 1, wr[i] or i in (1, 5, 6)) for i, a in enumerate(acc[:19])]
    return metas, acc


def buy_ixs(tm, user, lamports, min_out):
    """our buy of the coin = PumpSwap «sell» of WSOL"""
    metas, acc = amm_metas(tm, user)
    tail = [AccountMeta(a, False, tm["wr"][i]) for i, a in enumerate(tm["acc"][19:], start=19)]
    coin, coin_prog = acc[4], acc[12]
    wsol_ata = ata(user, WSOL, TOKEN)
    return cu_ixs() + [
        ata_create(user, user, WSOL, TOKEN),
        ata_create(user, user, coin, coin_prog),
        transfer(TransferParams(from_pubkey=user, to_pubkey=wsol_ata, lamports=lamports)),
        Instruction(TOKEN, bytes([17]), [AccountMeta(wsol_ata, False, True)]),  # SyncNative
        Instruction(AMM, SELL_DISC + struct.pack("<QQ", lamports, min_out), metas + tail),
        Instruction(TOKEN, bytes([9]), [AccountMeta(wsol_ata, False, True), AccountMeta(user, False, True), AccountMeta(user, True, False)]),  # close WSOL
    ]


def sell_ixs(tm, user, coin_amount, min_sol, close_coin=False):
    """our sell of the coin = PumpSwap «buy_exact_quote_in» (spend the coin, get WSOL). Accounts 0-18 as in the buy template,
    then global/user volume accumulators, then the template's tail (fee config, fee program, 2 more)."""
    metas, acc = amm_metas(tm, user)
    tail = [AccountMeta(GLOBAL_VOL, False, True), AccountMeta(user_vol(user), False, True)] + \
           [AccountMeta(a, False, tm["wr"][i]) for i, a in enumerate(tm["acc"][19:], start=19)]
    wsol_ata = ata(user, WSOL, TOKEN)
    coin_ata = ata(user, acc[4], acc[12])
    ixs = cu_ixs() + [
        ata_create(user, user, WSOL, TOKEN),
        Instruction(AMM, BEQI_DISC + struct.pack("<QQ", coin_amount, min_sol) + b"\x01", metas + tail),
        Instruction(TOKEN, bytes([9]), [AccountMeta(wsol_ata, False, True), AccountMeta(user, False, True), AccountMeta(user, True, False)]),
    ]
    if close_coin:  # 18.11: the coin account's rent (~0.002 SOL = 20 % of a 0.01 stake) comes back; only when we sell everything
        ixs.append(Instruction(acc[12], bytes([9]), [AccountMeta(coin_ata, False, True), AccountMeta(user, False, True), AccountMeta(user, True, False)]))
    return ixs


def make_tx(ixs, payer, signer=None):
    msg = MessageV0.try_compile(payer, ixs, [], blockhash())
    if signer:
        return VersionedTransaction(msg, [signer])
    return VersionedTransaction.populate(msg, [Signature.default()])


def simulate(tx):
    r = rpc("simulateTransaction", [base64.b64encode(bytes(tx)).decode(), {"encoding": "base64", "sigVerify": False, "replaceRecentBlockhash": True, "commitment": "processed"}])
    if isinstance(r, dict) and "error" in r:
        return False, str(r["error"])[:200], None
    v = r["value"]
    if v.get("err"):
        lg = [l for l in (v.get("logs") or []) if "rror" in l or "failed" in l]
        return False, f"{v['err']} {lg[-2:]}", v.get("unitsConsumed")
    return True, "ok", v.get("unitsConsumed")


def send(tx):
    r = rpc("sendTransaction", [base64.b64encode(bytes(tx)).decode(), {"encoding": "base64", "skipPreflight": True, "maxRetries": 3}], tries=2)
    if isinstance(r, dict) and "error" in r:
        return None, str(r["error"])[:200]
    return r, None


def confirm(sig, wait=20):
    t = time.time()
    while time.time() - t < wait:
        r = rpc("getSignatureStatuses", [[sig]])
        v = (r or {}).get("value", [None])[0] if r else None
        if v and v.get("confirmationStatus") in ("confirmed", "finalized"):
            return v.get("err") is None, v.get("err")
        time.sleep(0.4)
    return None, "нет подтверждения"


def sol_balance(a):
    r = rpc("getBalance", [str(a), {"commitment": "confirmed"}])
    return r["value"] if r else None


def token_balance(a):
    r = rpc("getTokenAccountBalance", [str(a), {"commitment": "confirmed"}])
    return int(r["value"]["amount"]) if r else 0


# ---------- trading ----------
STATE = {"n": 0, "pnl": 0.0, "busy": False, "stopped": False}


def _row_utc(s):  # rows keep "dd.mm HH:MM:SS" local time (UTC+5 on this PC) without a year
    try:
        import datetime as _dt
        d = _dt.datetime.strptime(s, "%d.%m %H:%M:%S").replace(year=_dt.datetime.now().year)
        return d.timestamp()
    except Exception:
        return 0


_SINCE = 0
if SET["since"]:
    import datetime as _dt
    _SINCE = _dt.datetime.strptime(SET["since"], "%Y-%m-%d %H:%M").replace(tzinfo=_dt.timezone.utc).timestamp()
try:  # 18.11: the test's count and result survive a restart (otherwise --max and --loss would start again from zero)
    for _r in csv.DictReader(open(TRADES, encoding="utf-8-sig"), delimiter=";"):
        if _SINCE and _row_utc(_r.get("time", "")) < _SINCE:
            continue
        if _r.get("mode") == "LIVE" and _r.get("pnl_sol") not in (None, ""):
            STATE["n"] += 1
            STATE["pnl"] += float(_r["pnl_sol"])
except (OSError, ValueError, KeyError):
    pass


def record(row):
    new = not os.path.exists(TRADES)
    with open(TRADES, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()), delimiter=";")
        if new:
            w.writeheader()
        w.writerow(row)


def can_trade():
    if STATE["stopped"] or STATE["busy"]:
        return False
    if os.path.exists(STOP_FLAG):  # 19.11: not latched — deleting the file resumes trading without a restart
        if not STATE.get("flag_said"):
            say("пауза: найден sol_stop.flag — новые сделки не начинаю (удалить файл = продолжить)")
            STATE["flag_said"] = True
        return False
    if STATE.get("flag_said"):
        say("sol_stop.flag удалён — торговля продолжается")
        STATE["flag_said"] = False
    if ARGS.live and STATE["n"] >= ARGS.max:
        say(f"СТОП: сделано {STATE['n']} сделок из {ARGS.max}")
        STATE["stopped"] = True
        return False
    if ARGS.live and STATE["pnl"] <= ARGS.loss:
        say(f"СТОП: итог {STATE['pnl']:+.5f} SOL дошёл до лимита {ARGS.loss}")
        STATE["stopped"] = True
        return False
    return True


def quote_out(sol, coin, lamports):
    a = lamports * (1 - FEE)
    return int(coin * a / (sol + a))


def quote_sol(sol, coin, amount):
    a = amount * (1 - FEE)
    return int(sol * a / (coin + a))


def trade(pool, mint, creator, t0):
    """runs in a thread; t0 = local time we learned of the pool"""
    STATE["busy"] = True
    STATE["cur"] = dict(mint=mint, pool=pool, phase="покупаю", since=0)
    row = dict(time=time.strftime("%d.%m %H:%M:%S"), mode="LIVE" if ARGS.live else "DRY", pool=pool, mint=mint, creator=creator[:8],
               stake_sol=ARGS.stake, t_template_s="", t_buy_sent_s="", buy_sim="", buy_ok="", entry_pool_sol="", tokens="",
               exit_reason="", t_exit_s="", exit_pool_sol="", got_sol="", fees_sol="", pnl_sol="", pnl_pct="", sell_sim="", note="")
    try:
        tm = derived_template(pool, mint)
        if tm:  # check it at once: a farm wallet (or any funded wallet) buying with it must pass the simulation
            v0 = vaults(tm)
            probe = KP.pubkey() if KP else None
            if v0 and probe:
                ok0, why0, _ = simulate(make_tx(buy_ixs(tm, probe, 1_000_000, 1), probe))
                if not ok0:
                    say(f"шаблон из данных пула не прошёл симуляцию ({why0[:80]}) — ищу сделку фермы")
                    tm = None
        if not tm:
            tm = pool_template(pool)
        row["t_template_s"] = round(time.time() - t0, 2)
        if not tm:
            row["note"] = (f"нет шаблона покупки в пуле: запросов списка {DIAG.get('pages')}, пустых {DIAG.get('empty')}, "
                           f"транзакций {DIAG.get('txs')}, не отдал узел {DIAG.get('none')}, инструкций пула {DIAG.get('amm')}")
            say(row["note"])
            return
        v = vaults(tm)
        if not v:
            row["note"] = "не прочитал пул"
            return
        sol, coin, dec = v
        lam = int(ARGS.stake * 1e9)
        exp = quote_out(sol, coin, lam)
        row["entry_pool_sol"] = round(sol / 1e9, 2)
        if sol < MIN_ENTRY_SOL * 1e9:  # 18.11: a pool below the farm's 85-90 SOL has been dumped already (HFvLebU9 at 66.8 SOL: -100 %)
            row["note"] = f"не покупаю: в пуле {sol / 1e9:.1f} SOL < {MIN_ENTRY_SOL:g} (пул уже обвалили или это не ферма)"
            say(row["note"])
            return
        farm_user =tm["acc"][1] if tm["sig"] != "derived" else (KP.pubkey() if KP else None)
        if farm_user is None:
            row["note"] = "шаблон из данных пула, а проверять не от кого (нет кошелька)"
            return
        if ARGS.live:
            user = KP.pubkey()
            bal0 = sol_balance(user)
            if bal0 is None or bal0 < lam + 10_000_000:
                row["note"] = f"мало SOL на кошельке: {bal0}"
                say("ПРОПУСК: мало SOL на кошельке", bal0)
                return
            tx = make_tx(buy_ixs(tm, user, lam, int(exp * (1 - SLIP))), user, KP)
            ok, why, cu = simulate(tx)
            row["buy_sim"] = why
            if not ok:
                say(f"ПОКУПКА НЕ ОТПРАВЛЕНА (симуляция): {why}")
                return
            tx = make_tx(buy_ixs(tm, user, lam, int(exp * (1 - SLIP))), user, KP)
            sig, err = send(tx)
            row["t_buy_sent_s"] = round(time.time() - t0, 2)
            if not sig:
                row["note"] = "отправка: " + err
                return
            ok, err = confirm(sig)
            row["buy_ok"] = ok
            if not ok:
                row["note"] = f"покупка не прошла: {err} {sig}"
                say("ПОКУПКА НЕ ПРОШЛА", err, sig)
                return
            tokens = token_balance(ata(user, tm["acc"][4], tm["acc"][12]))
            say(f"КУПИЛ {mint[:8]} на {ARGS.stake} SOL: {tokens / 10 ** dec:.0f} монет, через {row['t_buy_sent_s']} с, tx {sig}")
        else:
            user = farm_user
            ok, why, cu = simulate(make_tx(buy_ixs(tm, user, lam, int(exp * (1 - SLIP))), user))
            row["buy_sim"] = f"{why} cu={cu}"
            row["t_buy_sent_s"] = round(time.time() - t0, 2)
            tokens = exp
            say(f"[DRY] {mint[:8]}: симуляция нашей покупки (от кошелька фермы): {why}; вход при {sol / 1e9:.2f} SOL через {row['t_buy_sent_s']} с")
        row["tokens"] = tokens
        entry_price = lam / tokens if tokens else None
        # hold
        last_sol, peak_sol, reason, tstart = sol, sol, "время", time.time()
        ten_seen = 0
        sell_tm_checked = False
        while time.time() - tstart < ARGS.hold:
            time.sleep(0.4)
            v = vaults(tm)
            if not v:
                continue
            s2, c2, _ = v
            if not sell_tm_checked:
                sell_tm_checked = True
                if not ARGS.live:  # validate our sell builder on a farm wallet that holds the coin: the template's user right after its buy
                    pass
            if s2 < last_sol * 0.85:
                reason = f"вывод ликвидности ({last_sol / 1e9:.1f}→{s2 / 1e9:.1f} SOL)"
                last_sol = s2
                break
            last_sol, peak_sol = s2, max(peak_sol, s2)
            val = quote_sol(s2, c2, tokens)
            STATE["cur"] = dict(mint=mint, pool=pool, phase="держу", since=round(time.time() - tstart, 1), pool_sol=round(s2 / 1e9, 2),
                                entry_pool_sol=row["entry_pool_sol"], value_pct=round((val / lam - 1) * 100, 2), tp=ARGS.tp)
            if val >= lam * (1 + ARGS.tp / 100):
                reason = f"цель +{ARGS.tp:g}%"
                break
            # 19.11 signal exits: the ten wallets' activity in this pool (nine_follow.py -> ten_activity.json)
            act = ten_activity()[0].get(pool)
            now = time.time()
            if act and act.get("n"):
                ten_seen = max(ten_seen, act["n"])
                if act.get("last") and now - act["last"] >= SET["ten_idle"]:
                    reason = f"десять закончили (нет их сделок {SET['ten_idle']:g} с, всего {act['n']})"
                    break
            elif now - tstart >= SET["ten_wait"]:
                reason = f"десять не пришли за {SET['ten_wait']:g} с"
                break
        row["exit_reason"] = reason
        row["t_exit_s"] = round(time.time() - t0, 2)
        row["note"] = (row["note"] + " " if row["note"] else "") + f"сделок десяти в пуле за время держания: {ten_seen}"
        v = vaults(tm)
        s2, c2 = (v[0], v[1]) if v else (last_sol, coin)
        row["exit_pool_sol"] = round(s2 / 1e9, 2)
        exp_sol = quote_sol(s2, c2, tokens)
        if ARGS.live:
            # 18.11: HFvLebU9 — the target was hit at 72 SOL, but the pre-sell simulation failed twice on slippage (the price jumps every
            # slot), the bot waited, and the creator withdrew the pool 4 s later: -100 %. Now: no simulation, 30 % tolerance, a fresh quote
            # and an immediate retry (up to 5 times); the coin account is closed only by the sale that sells everything.
            got = None
            tokens = token_balance(ata(user, tm["acc"][4], tm["acc"][12])) or tokens  # sell exactly what the wallet holds
            b_before = sol_balance(user)
            for attempt in range(5):
                if attempt:
                    v = vaults(tm)
                    if v:
                        exp_sol = quote_sol(v[0], v[1], tokens)
                tx = make_tx(sell_ixs(tm, user, tokens, max(1, int(exp_sol * (1 - SELL_SLIP))), close_coin=True), user, KP)  # min 0 = ZeroBaseAmount
                sig, err = send(tx)
                if not sig:
                    say(f"ПРОДАЖА: не отправлена (попытка {attempt + 1}): {err}")
                    continue
                ok, err = confirm(sig)
                row["sell_sim"] = f"попытка {attempt + 1}"
                if ok:
                    got = sol_balance(user) - b_before
                    say(f"ПРОДАЛ {mint[:8]}: {reason}, получил {got / 1e9:.6f} SOL (с вычетом комиссий сети), tx {sig}")
                    break
                say(f"ПРОДАЖА не прошла (попытка {attempt + 1}): {str(err)[:120]} — сразу повторяю по новой цене")
            bal1 = sol_balance(user)
            pnl = (bal1 - bal0) / 1e9 if bal1 is not None else None
            row.update(got_sol=round(got / 1e9, 6) if got else "", pnl_sol=round(pnl, 6) if pnl is not None else "",
                       pnl_pct=round(pnl / ARGS.stake * 100, 2) if pnl is not None else "")
            if not got:
                row["note"] = "ПРОДАТЬ НЕ УДАЛОСЬ: монеты на кошельке"
                say("ВНИМАНИЕ: продать не удалось, монеты остались на кошельке", mint)
                STATE["stopped"] = True
            STATE["pnl"] += pnl or 0
            STATE["n"] += 1
            if pnl is not None and pnl / ARGS.stake < -0.5 or reason.startswith("вывод ликвидности"):
                add_rug_creator(creator)
        else:
            # validate the sell builder: a farm wallet that bought in this pool sells its own coin balance
            fu = farm_user
            fbal = token_balance(ata(fu, tm["acc"][4], tm["acc"][12]))
            if fbal:
                v3 = vaults(tm)
                ok, why, cu = simulate(make_tx(sell_ixs(tm, fu, fbal, max(1, int(quote_sol(v3[0], v3[1], fbal) * 0.8)) if v3 else 1), fu))
                row["sell_sim"] = f"{why} cu={cu}"
            else:
                row["sell_sim"] = "у кошелька фермы монет нет — не проверить"
            pnl = exp_sol / lam - 1
            row.update(got_sol=round(exp_sol / 1e9, 6), pnl_pct=round(pnl * 100, 2), pnl_sol=round(pnl * ARGS.stake, 6))
            say(f"[DRY] {mint[:8]}: выход «{reason}» через {row['t_exit_s']} с при {s2 / 1e9:.2f} SOL → {pnl * 100:+.1f}% (без комиссии сети); "
                f"симуляция продажи: {row['sell_sim']}")
    except Exception as e:
        row["note"] = f"ошибка: {type(e).__name__} {e}"[:200]
        say("ОШИБКА СДЕЛКИ", row["note"])
    finally:
        record(row)
        STATE["busy"] = False
        STATE["cur"] = None
        if ARGS.live:
            say(f"итог боевых сделок: {STATE['n']} из {ARGS.max}, {STATE['pnl']:+.6f} SOL")


# ---------- detection (as sol_watch.py) ----------
def load_creators():
    cr = set()
    try:
        cr |= {r["creator"] for r in csv.DictReader(open(os.path.join(HERE, "sol_pools.csv"), encoding="utf-8-sig"), delimiter=";")}
    except Exception:
        pass
    try:
        cr |= set(json.load(open(CREATORS_FILE, encoding="utf-8")))
    except Exception:
        pass
    return cr


CREATORS = load_creators()


def create_info(sig, creator):
    t = rpc("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1, "commitment": "confirmed"}], tries=6)
    if not t or not t.get("meta") or t["meta"].get("err"):
        return None
    own = {}
    for b in t["meta"].get("postTokenBalances") or []:
        own.setdefault(b.get("owner"), set()).add(b["mint"])
    for o, mm in own.items():
        if o != creator and str(WSOL) in mm and len(mm) == 2:
            return o, [m for m in mm if m != str(WSOL)][0]
    return None


def counterparties(sig, a):
    t = rpc("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1, "commitment": "confirmed"}])
    out = []
    if not t or not t.get("meta") or t["meta"].get("err"):
        return out
    for ins in t["transaction"]["message"].get("instructions") or []:
        p = ins.get("parsed") if isinstance(ins, dict) else None
        if isinstance(p, dict) and p.get("type") == "transfer" and ins.get("program") == "system":
            i = p.get("info") or {}
            if int(i.get("lamports", 0)) < 20e9:
                continue
            if i.get("source") == a:
                out.append(i["destination"])
            elif a == TREASURY and i.get("destination") == TREASURY:
                out.append(i["source"])
    return out


def plain_transfer(logs):
    progs = {l.split()[1] for l in logs if l.startswith("Program ") and " invoke [" in l}
    return bool(progs) and progs <= {"11111111111111111111111111111111", "ComputeBudget111111111111111111111111111111"}


RELOAD_CODE = 3  # 18.11 (user: «научись его перезапускать самостоятельно»): run_sol_trader_live.cmd starts the bot again on this exit code


def reload_watch():
    """When this file changes: wait for the current trade to finish, check the new code compiles, exit with RELOAD_CODE;
    the .cmd loop starts the same command again (same mode and limits, the trade count comes from sol_trades.csv)."""
    import py_compile
    me = os.path.abspath(__file__)
    mt = lambda p: os.path.getmtime(p) if os.path.exists(p) else 0
    seen = (mt(me), mt(SETTINGS_FILE))
    while True:
        time.sleep(5)
        try:
            now = (mt(me), mt(SETTINGS_FILE))
        except OSError:
            continue
        if now == seen:
            continue
        time.sleep(2)  # the file may still be being written
        seen = (mt(me), mt(SETTINGS_FILE))
        try:
            py_compile.compile(me, doraise=True)
            json.load(open(SETTINGS_FILE, encoding="utf-8")) if os.path.exists(SETTINGS_FILE) else None
        except Exception as e:
            say(f"новый код или настройки с ошибкой, остаюсь на старых: {str(e)[:150]}")
            continue
        STATE["stopped"] = True  # no new trade starts; the limits are checked again after the restart (count from sol_trades.csv)
        while STATE["busy"]:
            time.sleep(1)
        say("код или настройки обновлены — перезапускаюсь")
        os._exit(RELOAD_CODE)


def ten_activity():
    """{pool: {first,last,n,creator,...}} from nine_follow.py; {} if the file is missing or older than 30 s (follower down)"""
    try:
        d = json.load(open(ACTIVITY_FILE, encoding="utf-8"))
        if time.time() - d.get("t", 0) > 30:
            return {}, set()
        return d.get("pools", {}), set(d.get("creators", []))
    except Exception:
        return {}, set()


# Family-1 pool creators whose pools the ten wallets traded (tasks 9-11) + Семья 1 by the 35 SOL creator buy (sol_buyers). sol_pools.csv holds
# creators of other families too (С2-С4: no fleet, no ten), so it is NOT used for the family filter.
FAMILY1_KNOWN = {"Czg8kh3EYpqgLMUdrV3X3iiBaUdgP7aZYRMYm8xU2BdL", "6e7XUsDbJyxMdDe8zEHHMgWxkC73YAe428mJRSV65sSC", "29iqxoNvRDdx8G5peK3FbDVVt4mayaKKAesvn7m6R9ko",
                 "CPC8iEE1PripNpd7VjofSHENYnoKpFnjmZFDtE8m7QT2", "89coAVTUZetQ4K6Ct4nsqtLtMCaR4X2mWStdBsmdECur", "GLEBhVYdFuP6Gh4Q7Qan66gYeSc7AjcFYC8gM7aMN6wd",
                 "8vB8UUGxTDhZFEW9BRspXdaqNNauYgrLL5GZwbynw9XL", "6kpQZpC1QQYhGKkvndAfVyMqvA312Tdctmzc6kB2h39Q", "DjxroBir4VNDCwaXKYTp8GVKi3sF14pgtrV5BRnYyoGY",
                 "ES8hw8vF5ifzystLcRVwKkWWEKEd9pu7awmR9sNQepXM", "6yf7adkH46HHzTQ4wFAwGXCKAkWc8aLeWztgVk3vu7YJ"}


def family_creators():
    """19.11 fix: also creators learned through the treasury / sponsor (a 35 SOL treasury transfer is the Семья 1 mark; written to
    sol_watch_creators.json by helius_loop) and creators of pools where the ten wallets already traded. Before this, BLqnNLrm (73 trades
    of the ten in its pool) was skipped as «not Семья 1»."""
    pools, funders = ten_activity()
    fam = FAMILY1_KNOWN | funders | {p.get("creator") for p in pools.values() if p.get("n") and p.get("creator")}
    try:
        fam |= set(json.load(open(CREATORS_FILE, encoding="utf-8")))
    except Exception:
        pass
    return fam


# 19.11 (user: «добавим это правило»): a creator who has pulled the liquidity under one of our trades is not traded again.
# On 19.11 all 3 live wipe-outs and all paper wipe-outs were 6e7XUsDb / 7N7eEVRu, while FJPmsoDM (14) and HqmqxtdJ (5) had none.
# Kept in analysis/sol_rug_creators.json (full addresses or 8-char prefixes); seeded from the LIVE rows of sol_trades.csv with a loss
# below -50 % (their creator column holds the 8-char prefix). Switch off: "skip_rug_creators": false in sol_settings.json.
RUG_FILE = os.path.join(HERE, "sol_rug_creators.json")


def rug_creators():
    try:
        return set(json.load(open(RUG_FILE, encoding="utf-8")))
    except Exception:
        return set()


def add_rug_creator(c):
    s = rug_creators()
    if c and c not in s:
        s.add(c)
        with open(RUG_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(sorted(s), f)
        os.replace(RUG_FILE + ".tmp", RUG_FILE)
        say(f"создатель {c} вывел пул под нашей сделкой — больше его пулы не беру")


def is_rug_creator(c):
    return any(c == x or c.startswith(x) for x in rug_creators())


if not os.path.exists(RUG_FILE):
    try:
        _seed = {r["creator"] for r in csv.DictReader(open(TRADES, encoding="utf-8-sig"), delimiter=";")
                 if r.get("mode") == "LIVE" and r.get("pnl_pct") not in (None, "") and float(r["pnl_pct"]) < -50 and r.get("creator")}
        json.dump(sorted(_seed), open(RUG_FILE, "w", encoding="utf-8"))
    except Exception:
        pass

HANDLED = set()  # pools already started by one of the two searches
HANDLED_LOCK = threading.Lock()


def start_pool(pool, mint, creator, t0, how):
    with HANDLED_LOCK:
        if pool in HANDLED:
            return
        HANDLED.add(pool)
    say(f"НОВЫЙ ПУЛ {pool} монета {mint} — {how}")
    if SET["family_only"] and creator not in family_creators():
        say(f"  пропуск: создатель {creator[:8]} не из Семьи 1 (режим только пулы фермы)")
        return
    if SET["skip_rug_creators"] and is_rug_creator(creator):
        say(f"  пропуск: создатель {creator[:8]} уже выводил пул под нашей сделкой")
        return
    if can_trade():
        threading.Thread(target=trade, args=(pool, mint, creator, t0), daemon=True).start()
    else:
        say("  пропуск: идёт другая сделка или торговля остановлена")


# 18.11 (user: «ищи другие способы поиска монет, подключайся к блокчейну напрямую»): a second search that does not depend on knowing the
# farm's wallets. The free public Solana node streams every change of PumpSwap pools whose BASE mint is WSOL (the farm's pools are
# «reversed»; ~40 such pools are active, ~66 KB/s, all of them were delivered in a test while the full PumpSwap log stream is ~390 tx/s
# and the public node drops most of it; on Helius this would cost ~3 M credits a month). An unknown pool is checked once: younger than
# POOL_MAX_AGE s (its oldest of <= 30 signatures) and 50-95 SOL in its SOL vault = the farm's start -> traded like a creator's pool.
PUBLIC_WSS, PUBLIC_HTTP = "wss://api.mainnet-beta.solana.com", "https://api.mainnet-beta.solana.com"
POOL_MAX_AGE, POOL_SOL = 60, (50.0, 95.0)
SEEN_POOLS = set()


def public_rpc(method, params):
    for k in range(3):
        try:
            r = json.loads(urllib.request.urlopen(urllib.request.Request(PUBLIC_HTTP, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
                                                                         headers={"Content-Type": "application/json"}), timeout=10).read())
            if r.get("result") is not None:
                return r["result"]
        except Exception:
            pass
        time.sleep(0.5 * (k + 1))
    return rpc(method, params)  # the public node failed: Helius once


def check_new_pool(pool, data, t0):
    """runs in a thread: is this unknown reversed pool a fresh farm pool?"""
    try:
        if len(data) < 203 or Pubkey.from_bytes(data[43:75]) != WSOL:
            return
        mint = str(Pubkey.from_bytes(data[75:107]))
        sigs = []
        for k in range(6):  # a pool seen at its creation: the node may not list its transactions for a second or two
            sigs = public_rpc("getSignaturesForAddress", [pool, {"limit": 30, "commitment": "confirmed"}]) or []
            if sigs:
                break
            time.sleep(1)
        if not sigs or len(sigs) >= 30:
            return  # an old busy pool (or nothing found)
        bt = min((s.get("blockTime") or 0) for s in sigs)
        if not bt or time.time() - bt > POOL_MAX_AGE:
            return
        b = public_rpc("getTokenAccountBalance", [str(Pubkey.from_bytes(data[139:171]))])
        sol = float(b["value"]["uiAmount"] or 0) if b else 0
        if not POOL_SOL[0] <= sol <= POOL_SOL[1]:
            say(f"новый перевёрнутый пул {pool[:8]}, но в нём {sol:.1f} SOL — не похоже на ферму, пропускаю")
            return
        creator = str(Pubkey.from_bytes(data[11:43]))
        start_pool(pool, mint, creator, t0, f"поиск по пулам: возраст {time.time() - bt:.0f} с, {sol:.1f} SOL, создатель {creator[:8]}")
    except Exception as e:
        say(f"проверка пула {pool[:8]}: {type(e).__name__} {str(e)[:80]}")


async def pool_feed():
    loop = asyncio.get_running_loop()
    drops = [0, 0.0]  # drops since the last report, time of the last report
    while True:
        try:
            async with websockets.connect(PUBLIC_WSS, ping_interval=20, ping_timeout=30, max_size=2 ** 24) as ws:
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "programSubscribe", "params": [str(AMM), {
                    "encoding": "base64", "commitment": "processed",
                    "filters": [{"dataSize": 301}, {"memcmp": {"offset": 43, "bytes": str(WSOL)}}]}]}))
                if not drops[0] or time.time() - drops[1] > 600:
                    say("поиск по пулам: подключён к публичному узлу Solana")
                FEEDS["pools"] = True
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("method") != "programNotification":
                        continue
                    v = msg["params"]["result"]["value"]
                    pool = v["pubkey"]
                    if pool in SEEN_POOLS or pool in HANDLED:
                        continue
                    SEEN_POOLS.add(pool)
                    data = base64.b64decode(v["account"]["data"][0])
                    loop.run_in_executor(None, check_new_pool, pool, data, time.time())
        except Exception as e:
            # 19.11 (user: the window keeps printing «обрыв»): the free public node drops connections every few minutes and the reconnect is
            # immediate; one line per 10 minutes with the count instead of a line per drop. Trading does not depend on this search.
            FEEDS["pools"] = False
            drops[0] += 1
            if time.time() - drops[1] > 600:
                say(f"поиск по пулам (запасной, публичный узел): обрывов за последние 10 мин {drops[0]}, последний {type(e).__name__}; переподключаюсь сам")
                drops[0], drops[1] = 0, time.time()
            await asyncio.sleep(5)


STATUS_FILE = os.path.join(HERE, "sol_status.json")
FEEDS = {"helius": False, "pools": False}


def status_loop():
    """18.11 (user: Solana in the BSC panel): every 3 s the bot's state goes to sol_status.json; hop_trader.py serves it at /api/sol."""
    bal, sol_usd, t_bal, t_px = None, None, 0, 0
    while True:
        try:
            if KP and time.time() - t_bal > 15:
                b = sol_balance(KP.pubkey())
                bal, t_bal = (b / 1e9 if b is not None else bal), time.time()
            if time.time() - t_px > 60:
                try:
                    j = json.loads(urllib.request.urlopen(f"https://lite-api.jup.ag/price/v3?ids={WSOL}", timeout=10).read())
                    sol_usd = float(j[str(WSOL)]["usdPrice"])
                except Exception:
                    pass
                t_px = time.time()
            st = dict(t=time.time(), mode="LIVE" if ARGS.live else "DRY", wallet=str(KP.pubkey()) if KP else None, balance_sol=bal, sol_usd=sol_usd,
                      stake=ARGS.stake, max=ARGS.max, loss=ARGS.loss, tp=ARGS.tp, hold=ARGS.hold, n=STATE["n"], pnl=round(STATE["pnl"], 6),
                      busy=STATE["busy"], stopped=STATE["stopped"], cur=STATE.get("cur"), feeds=dict(FEEDS), creators=len(CREATORS),
                      stop_flag=os.path.exists(STOP_FLAG), signal=dict(SET), ten_follower_alive=bool(ten_activity()[1]))
            with open(STATUS_FILE + ".tmp", "w", encoding="utf-8") as f:
                json.dump(st, f, ensure_ascii=False)
            os.replace(STATUS_FILE + ".tmp", STATUS_FILE)
        except Exception:
            pass
        time.sleep(3)


async def main():
    threading.Thread(target=bh_loop, daemon=True).start()
    threading.Thread(target=reload_watch, daemon=True).start()
    threading.Thread(target=status_loop, daemon=True).start()
    await asyncio.gather(helius_loop(), pool_feed())


async def helius_loop():
    nid = [10]
    while True:
        subs, subkey = {}, {}
        try:
            async with websockets.connect(WSS, ping_interval=20, ping_timeout=20, max_size=2 ** 24) as ws:
                async def sub(a):
                    nid[0] += 1
                    subs[nid[0]] = a
                    await ws.send(json.dumps({"jsonrpc": "2.0", "id": nid[0], "method": "logsSubscribe", "params": [{"mentions": [a]}, {"commitment": "processed"}]}))
                for a in [TREASURY, SPONSOR] + sorted(CREATORS):
                    await sub(a)
                say(f"подключён; создателей {len(CREATORS)}; режим {'БОЕВОЙ' if ARGS.live else 'ПРОВЕРКА БЕЗ ДЕНЕГ'}")
                FEEDS["helius"] = True
                loop = asyncio.get_running_loop()
                t_merge = 0
                while True:
                    if time.time() - t_merge > 10:  # 19.11: creators learned by nine_follow (they fund the ten wallets) are watched here too
                        t_merge = time.time()
                        for a in sorted(ten_activity()[1] - set(CREATORS) - {TREASURY, SPONSOR}):
                            CREATORS.add(a)
                            await sub(a)
                            say(f"создатель {a} (пополняет десять кошельков) — слежу")
                    try:
                        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                    except asyncio.TimeoutError:
                        continue
                    if "id" in msg and "result" in msg and msg["id"] in subs:
                        subkey[msg["result"]] = subs[msg["id"]]
                        continue
                    if msg.get("method") != "logsNotification":
                        continue
                    v = msg["params"]["result"]["value"]
                    a = subkey.get(msg["params"]["subscription"])
                    if not a or v.get("err"):
                        continue
                    logs = v.get("logs") or []
                    if a in CREATORS and any("CreatePool" in l for l in logs):
                        t0 = time.time()
                        info = await loop.run_in_executor(None, create_info, v["signature"], a)
                        if not info:
                            say(f"CreatePool у {a[:8]}, но не разобрал транзакцию {v['signature'][:16]}")
                            continue
                        pool, mint = info
                        start_pool(pool, mint, a, t0, f"создатель {a[:8]} (узнали за {time.time() - t0:.1f} с разбора)")
                    elif a in (TREASURY, SPONSOR) and plain_transfer(logs):
                        for dst in await loop.run_in_executor(None, counterparties, v["signature"], a):
                            if dst not in CREATORS and dst not in (TREASURY, SPONSOR):
                                CREATORS.add(dst)
                                try:
                                    json.dump(sorted(CREATORS), open(CREATORS_FILE, "w", encoding="utf-8"))
                                except Exception:
                                    pass
                                await sub(dst)
                                say(f"новый создатель {dst} — слежу")
        except Exception as e:
            FEEDS["helius"] = False
            say(f"обрыв связи: {type(e).__name__} {str(e)[:100]}; переподключаюсь")
            await asyncio.sleep(3)


if __name__ == "__main__":
    # 18.11 (user: autostart): one live copy only; the watchdog sees the bot alive by this listening port (47665 live, 47666 dry)
    import socket
    _LOCK = socket.socket()
    try:
        _LOCK.bind(("127.0.0.1", 47665 if ARGS.live else 47666))
        _LOCK.listen(1)
    except OSError:
        say(f"другая копия sol_trader ({'боевая' if ARGS.live else 'проверка'}) уже работает — выхожу")
        sys.exit(0)
    say(f"СТАРТ sol_trader: {'БОЕВОЙ' if ARGS.live else 'проверка без денег'}; ставка {ARGS.stake} SOL, цель +{ARGS.tp:g}%, держать до {ARGS.hold:g} с, "
        f"сделок до {ARGS.max}, стоп при {ARGS.loss} SOL" + (f"; кошелёк {KP.pubkey()}" if KP else "")
        + (f"; уже сделано {STATE['n']}, итог {STATE['pnl']:+.6f} SOL" if ARGS.live else ""))
    asyncio.run(main())
