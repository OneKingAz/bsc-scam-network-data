"""27.10 (user): trade the guard launches (token keeps 0xbd418ce6 in storage slot 6; the guard blacklists every new holder
~6-13 s after it receives tokens) by hopping the tokens between fresh wallets faster than the guard marks them,
then selling from a seller wallet that was approved in advance and never held the tokens before.

One trade:  wallet A buys `stake` BNB `entry` s after the pool appears -> every `every` s the tokens go to the next
clean hop wallet (hop_wallets.env, HOP_KEY_1..100; a wallet already marked on this token is skipped) -> when the
value reaches +tp %, falls to -sl %, `hold` s pass, or "Продать сейчас" is pressed, the last hop goes to the seller
wallet, which sells at once and sends the BNB back to A.

Real money. Nothing is bought until "Старт" is pressed in the panel; after `trades` trades it stops by itself.
Run: run_hop_trader.cmd -> http://127.0.0.1:8773 . Out: analysis\\hop_trades.csv, analysis\\hop_trader.log.
Keys are never printed. Wallet A is the live bot's wallet: keep live on pause while this runs (shared nonce)."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import random, csv, ipaddress, itertools, json, math, os, statistics as st, sys, threading, time, traceback
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bot as B
from web3 import Web3

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PORT, LOCK_PORT = 8773, 47662
# --lan <ip>: the panel is also reachable from the local network, buttons included (27.10, user: used mostly over LAN);
# only private addresses (192.168.x, 10.x, 172.16-31.x) may control it
LAN_IP = sys.argv[sys.argv.index("--lan") + 1] if "--lan" in sys.argv else ""
SET_FILE = os.path.join(HERE, "hop_trader.json")
TRADES = os.path.join(HERE, "hop_trades.csv")
w3 = B.w3
GUARD = Web3.to_checksum_address(B.GUARD)
TOK_ABI = B.TRANSFER_ABI + [
    {"name": "balanceOf", "type": "function", "stateMutability": "view", "inputs": [{"type": "address"}],
     "outputs": [{"type": "uint256"}]},
    {"name": "allowance", "type": "function", "stateMutability": "view", "inputs": [{"type": "address"}] * 2,
     "outputs": [{"type": "uint256"}]},
    {"name": "symbol", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "string"}]}]
# trades=0: no limit (unattended); the run then stops on max_loss_usd or max_stuck stuck trades in a row
DEFAULTS = dict(stake=0.002, tp=100.0, sl=50.0, hold=120.0, every=3.0, entry=30.0, trades=1, min_pool=8.0,
                max_loss_usd=2.0, max_stuck=2, auto=False, active_age=0.0, stake_pct=0.0, stake_auto=True,
                hop_gwei=0.1, hop_dyn=True, tick_mode=True, chain_mode=True, hold_auto=True, patient=True, bot_exit=True, chain_lean=False, skip25=True, shield=True, real_edge=True, all_fast=True)
# hop_gwei (27.10, user): gas price of the hops and the sell. The guard pays 0.051 gwei; in a block shared with its
# approve() a higher price puts our transfer first, so the mark lands on an already empty wallet. The buy stays normal.
# tick_mode (27.10, user): the guard reads the holders on one 15 s timer for all coins (every 100/3 blocks; the mark
# lands 2-7 blocks after the read, guard_period.py). The buy and every hop are sent when the head is TICK_LEAD blocks
# before a tick, so they land 1-2 blocks before it: the guard has read the old holder (the mark hits an empty wallet)
# and does not see the new one until the next tick 15 s later. One hop per 15 s instead of one per `every` s.
# Without a known phase the old `every` hopping is used.
# chain_mode (27.10, user; tests 15:27 SONYc): the trade is held by a chain of holders instead of fast hops. Right
# after the buy and then every CHAIN_EVERY s the holder sends crumbs to CHAIN_K new addresses and, in the same burst,
# everything else to the next clean W: the guard marks ~1 new holder per 5 s in about the order they got the tokens,
# so the holder always stands behind the crumbs. At the end the holder approves the router and sells itself.
# hold_auto (27.10, user): in chain_mode the holding time comes from the coin's type, not from `hold`. The network
# runs one coin at a time and empties the pool at a fixed age by the starting pool (guard_drains.py, 23 launches):
# 1 BNB at 7.6-8.1 min, 10 BNB at 15.5-16.6 min, 25 BNB at 51-60 min; the price grows until then. HOLD_BY_TYPE is
# the sell moment counted from the pool's appearance, a few minutes before that. "+5 мин" adds to the current trade.
# patient (27.10 18:05, user, after STONKBROKER: flat for 10 min, then +295%, while we had sold at minute 4.5 at a
# loss; "before the sale look how much was bought, if very few people bought — wait; in the red — be patient; this rule
# is for the 1 BNB coins"): a 1 BNB coin is sold at the planned minute only if it is SELL_MIN_PNL % up and the buying
# has stopped (pool BNB grew less than FLOW_PCT % in the last FLOW_WIN s). Few buyers so far, or still buying — it
# waits; the stop-loss is off for it. It is sold anyway at HARD_MAX after the launch, or at once when the pool falls
# DRAIN_DROP below its peak since the buy (the network empties a pool in 3-4 min from the peak, guard_drains.py).
# bot_exit (27.10 19:05, user; bot_sequence.py on 26 coins): the network's bots buy a fixed number of times per coin type
# before the creator dumps the pool (25 BNB: 91-99, 10 BNB: 26 or 51-52, 1 BNB: 8-11), and the last bot buy is always
# 3.5-3.9 min before the dump (then 211-235 s of silence). The chain trade counts the bots' buys (BotCounter) and sells
# once the count reached BOT_MIN[type] and no bot bought for BOT_SILENCE s; the planned minute no longer sells, only
# the backstop BOT_BACKSTOP[type] after the launch and a pool falling DRAIN_DROP below its peak do.
# shield (28.10, user: "protect the coin before the sale"; stuck_study.py): the guard marks the new holders it found
# in one pass in RANDOM order, the first ~8 s (17-19 blocks) after they appear, then one every ~5 s. Our first burst
# (5 crumbs + W1) used to be that whole first pass, so W1 was marked first in ~1 case of 3 (NOKc, CRML) before its
# hand-over at 15 s. Now SHIELD_N decoy wallets buy dust first; we wait until the guard marks one of them (its pass
# over them has started) and only then buy: the chain lands in the next pass, after the decoys' ~SHIELD_N*5 s.
# hop_dyn (27.10, user): instead of hop_gwei, twice the price of the guard's last mark, within 0.1..1 gwei (guard_gas()).
# stake_auto (27.10, user: "I don't want to work out the stake"): before every trade the stake is AUTO_SHARE of the free
# balance of A (balance minus the gas reserve), at least MIN_STAKE; it grows after wins and shrinks after losses.
# It overrides stake and stake_pct. AUTO_SHARE 25%: one stuck trade (the whole stake lost) leaves ~75% of the wallet.
AUTO_SHARE, MIN_STAKE, GOOD_STAKE = 0.25, 0.0004, 0.002  # below GOOD_STAKE the gas eats the profit (history 19-24.10)
# MIN_STAKE 29.10 (user: test on what is left): 0.0004, not 0.0005
# stake_pct > 0: the stake is this % of wallet A's balance at the moment of the trade (minus a gas reserve), not `stake`
GAS_RESERVE = 0.0002  # BNB kept on A for the buy, top-ups and the sell. 29.10 (user: test on what is left): 0.0002, not
# 0.0006 — the hop wallets already hold their gas, prefund() takes those first
# active_age > 0: while waiting, also take a guard coin that is already trading if it is younger than this many minutes

HOLD_BY_TYPE = {25: 40 * 60, 10: 13 * 60, 1: 5 * 60}  # 10: 13 min since 27.10 17:30 (user; BNQ +28% at 10, +55% at 14)  # s after the pool appeared, see hold_auto
# 27.10 (user: "add these rules to the time"): the price growth by minute from the pool's appearance, median of the
# 23 launches of 27.10 (guard_curve.py: price ~ BNB in pool squared, entry at 0.5 min, % to that entry), and the
# chain's gas: CHAIN_GAS_MIN a minute (5 crumbs + hand-over every 15 s at 0.05 gwei) plus CHAIN_GAS_FIXED (buy,
# approve, sell, return, top-ups). best_hold() picks the minute with the best expected result for this stake; a coin
# whose best is below MIN_EDGE is not bought. Paper numbers: no fee, no token tax.
GROWTH = {25: {1: 4, 2: 8, 3: 11, 5: 14, 8: 25, 10: 29, 12: 35, 15: 40, 20: 52, 30: 80, 40: 105},
          10: {1: 9, 2: 37, 3: 40, 5: 69, 8: 112, 10: 146, 13: 265},  # 12-15 min: +265% (same)
          1: {1: 42, 2: 167, 3: 264, 5: 487}}
CHAIN_GAS_MIN, CHAIN_GAS_FIXED = 0.000076, 0.00003  # BNB
# 28.10 real_edge: the trades of 27.10 grossed far less than GROWTH (10 BNB: +92/+25/+56% at ~10 min against a paper
# +146%; 25 BNB: +29% at 24 min against ~+60%; 1 BNB: -12% once), so the paper growth is cut to this share; the fast
# first minute (bare hand-overs every 2 s) costs ~CHAIN_FAST_GAS more
REAL_SHARE = {25: 0.5, 10: 0.25, 1: 0.25}
# 29.10 all_fast: those trades sold early (4-12 min); fast_econ.py (80 coins, exit 3 min before the drain) got the
# full paper growth, so with the bot counter's exit only half of it is assumed (token tax and slippage unknown)
REAL_SHARE_FAST = 0.5
FAST_GAS_MIN, FAST_GAS_FIXED = 0.000266, 0.00006  # 05.11 ORCLc: 0.000142 gas + the rest stuck on holders (~3.3e-6 each); was 0.000145  # BNB a minute; buy, seller's gas and approve, sell, return
CHAIN_FAST_GAS = 0.0001  # BNB
PATIENT_TYPES = (1,)  # coin types the patient rule is for
FLOW_PCT, FLOW_WIN = 3.0, 60  # patient: "still buying" = pool BNB grew this many % within this many s
# (3 %, not 10: the price goes as BNB squared, +10 % price is only ~+5 % BNB — 10 % sold at the very start of a pump)
SELL_MIN_PNL = 10.0  # %: patient — below this at the planned minute "few people bought", wait
HARD_MAX = {25: 48 * 60, 10: 15 * 60, 1: 16 * 60}  # s after the launch: patient — sold even in the red
DRAIN_DROP = 0.25  # patient / bot_exit: pool BNB this far below its peak since the buy — sold at once
BOT_MIN = {25: 88, 10: 24, 1: 7}  # bot_exit: bot buys before the final silence counts
BOT_SILENCE = 50  # s without a bot buy after BOT_MIN: the dump comes ~3.5 min after the last buy
BOT_BACKSTOP = {25: 57 * 60, 10: 30 * 60, 1: 25 * 60}  # s after the launch: sold even without the signal
# 1: 25 min, not 16 (04.11: GMEc/XTEN drained at 22-23 min; at 16 min the price was x1.4-2.5, at the bots' end x5)
SWAP_TOPIC = "0x" + Web3.keccak(text="Swap(address,uint256,uint256,uint256,uint256,address)").hex().replace("0x", "")
DUMP_MARKER = "0xf71d73a4"  # buys 0 BNB 1-4 s after every dump: not a bot buy
OUR_ADDRS = set()


def our_addrs():
    """A and the hop wallets (hop_wallets.txt: addresses only), lower case: their buys are not the bots'."""
    if not OUR_ADDRS:
        OUR_ADDRS.add(A.address.lower())
        try:
            for line in open(os.path.join(ROOT, "hop_wallets.txt"), encoding="utf-8"):
                p = line.strip().split(";")
                if len(p) > 1 and p[1].startswith("0x"):
                    OUR_ADDRS.add(p[1].lower())
        except Exception:
            pass
    return OUR_ADDRS


class BotCounter:
    """Counts the bots' buys in a pair since its launch (Swap events with BNB in, sent by anyone but us and the dump
    marker) and the block of the last one; a thread reads the new blocks every 2 s."""

    def __init__(self, pair, token, launch_t):
        self.pair = Web3.to_checksum_address(pair)
        self.wbnb_is0 = int(Web3.to_checksum_address(B.WBNB), 16) < int(Web3.to_checksum_address(token), 16)
        self.count, self.last_buy, self.head, self.stop = 0, None, None, False
        head = w3.eth.block_number
        self.next = head - int((time.time() - launch_t) / SPB) - 60
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        ours = our_addrs()
        while not self.stop:
            try:
                head = w3.eth.block_number
                while head >= self.next:  # the node takes at most ~2000 blocks a call
                    to = min(head, self.next + 1999)
                    for lg in w3.eth.get_logs({"address": self.pair, "fromBlock": self.next, "toBlock": to,
                                               "topics": [SWAP_TOPIC]}):
                        d = bytes(lg["data"])
                        bnb_in = int.from_bytes(d[0:32] if self.wbnb_is0 else d[32:64], "big")
                        if not bnb_in:
                            continue
                        frm = retry(lambda: w3.eth.get_transaction(lg["transactionHash"])["from"]).lower()
                        if frm in ours or frm.startswith(DUMP_MARKER):
                            continue
                        self.count += 1
                        self.last_buy = lg["blockNumber"]
                    self.next = to + 1
                self.head = head
            except Exception:
                time.sleep(2)
            time.sleep(2)

    def silence(self):
        """s since the last bot buy (0 while unknown)."""
        if self.last_buy is None or self.head is None:
            return 0.0
        return (self.head - self.last_buy) * SPB
MIN_EDGE = 10.0  # %: expected result after gas below this — the coin is skipped


EXIT_TABLE = {}


def exit_row(kind):
    """04.11 TZ: analysis\\exit_table.py (163 coins, the real entry at ~36 s, tp, the bots' exit, sale lag, own impact):
    gross % and minutes for this coin type at the tp set now (the nearest row; a tp above the table = no target)."""
    if not EXIT_TABLE:
        try:
            EXIT_TABLE.update(json.load(open(os.path.join(HERE, "exit_table.json"), encoding="utf-8"))["table"])
        except Exception:
            return None
    rows = EXIT_TABLE.get(str(kind))
    if not rows:
        return None
    tp = SET["tp"]
    nums = [int(k) for k in rows if k != "None"]
    return rows["None"] if tp > 2 * max(nums) else rows[str(min(nums, key=lambda k: abs(k - tp)))]


def best_hold(kind, stake, age_min):
    """(minute from the pool's appearance to sell at, expected % after gas) for a buy at age_min, or (None, best %)."""
    row = exit_row(kind) if SET["all_fast"] and SET["bot_exit"] and age_min <= 2 else None
    if row:  # GROWTH below is 27.10 only and ignores tp; a late re-entry still uses it (it knows the growth missed)
        gain = row["gross"] * (REAL_SHARE_FAST if SET["real_edge"] else 1)
        return math.ceil(age_min + row["minutes"]), gain - (FAST_GAS_FIXED + FAST_GAS_MIN * row["minutes"]) / stake * 100
    g = GROWTH[kind]
    base = max((v for m, v in g.items() if m <= age_min), default=0)  # the growth already missed before the buy
    best = (None, -1e9)
    for m, v in g.items():
        if m < age_min + 1 or m * 60 > HOLD_BY_TYPE[kind]:
            continue
        gain = ((1 + v / 100) / (1 + base / 100) - 1) * 100
        if SET["real_edge"]:  # 28.10 (user: "do not take them at a small stake"): the paper growth, cut to what we got
            gain *= REAL_SHARE_FAST if SET["all_fast"] else REAL_SHARE[kind]
        gas_min = CHAIN_GAS_MIN * (0.55 if SET["chain_lean"] else 1)  # lean: 3 transfers a cycle, not 6
        fixed = CHAIN_GAS_FIXED + (CHAIN_FAST_GAS if SET["real_edge"] else 0)
        if SET["all_fast"]:  # 29.10 fast_econ.py: a bare hand-over + a top-up every ~2.3 s
            gas_min, fixed = FAST_GAS_MIN, FAST_GAS_FIXED
        gas = (fixed + gas_min * (m - age_min)) / stake * 100
        if gain - gas > best[1]:
            best = (m, gain - gas)
    return best


SYNC_TOPIC_HT = "0x" + Web3.keccak(text="Sync(uint112,uint112)").hex().replace("0x", "")


def start_bnb(pair, token, t_seen):
    """BNB in the pool at its first Sync (the network starts it at exactly 1, 10 or 25), None if not found."""
    try:
        wbnb_is0 = int(Web3.to_checksum_address(B.WBNB), 16) < int(Web3.to_checksum_address(token), 16)
        head = w3.eth.block_number
        b = head - int((time.time() - t_seen) / SPB) - 120
        while b <= head:
            logs = w3.eth.get_logs({"address": Web3.to_checksum_address(pair), "fromBlock": b,
                                    "toBlock": min(head, b + 1999), "topics": [SYNC_TOPIC_HT]})
            if logs:
                d = bytes(logs[0]["data"])
                return int.from_bytes(d[:32] if wbnb_is0 else d[32:64], "big") / 1e18
            b += 2000
    except Exception:
        pass
    return None


def coin_type(bnb):
    """The network's pool type by the BNB in the pool at the buy (it starts at 25, 10 or 1 and grows slowly)."""
    return 25 if bnb >= 20 else 10 if bnb >= 5 else 1


S = dict(hold_now=0.0, running=False, phase="Остановлено", detail="", token="", sym="", holder="", hops=0, pnl_pct=None,
         value=None, held=0.0, done=0, ready=0, balance=None, sell_now=False, stop_after=False, seller="",
         run_pnl=0.0, stuck_streak=0, bnb_usd=742.0, wallets=[], manual="", plan=[], net=None, wallets_total=None, wallets_n=0, guard_gwei=0.051, hop_gwei_now=0.102, tick="", decoy=False, chain=False)
ACTIVE = {}  # pair -> guard coin still trading: token, sym, block, bnb, price0, price, a_marked
SPB = 0.45  # BSC seconds per block
USED_FILE = os.path.join(HERE, "hop_used.json")
# 27.10 history of the guard series (50.0): one cash desk funded 178 of 187 launches, a launch every ~52 min round the
# clock (27% of gaps < 18 min, none > 90 min). Watching the desk warns before the pool appears.
CASH = [Web3.to_checksum_address(a) for a in ("0xde8eb937cb5475eee5ac96dce6ba2d18e439c473",
                                              "0xf51dd7340c90bf1683bb32d6397ed3acefb6b6a7")]
LAUNCH_FILE = os.path.join(HERE, "guard_launches.csv")
CASH_FILE = os.path.join(HERE, "cash_watch.csv")
CASH_EVENTS = deque(maxlen=30)  # dict(time, cash, to, bnb, sym, lead)
LOG = deque(maxlen=200)
RUN_FILE = os.path.join(HERE, "hop_run.json")  # start time of the current run
SKIP = set()  # tokens this process decided not to trade: active_pick must not pick them again and again


OUT = deque(maxlen=2000)  # console lines: printed by their own thread


def console():
    """28.10 21:07-21:56: a click in the console window (QuickEdit selection) froze print(), and with it every thread
    that called say(), the worker inside trade() included. Mid-chain that would leave a holder to the guard. So the
    console only gets lines from this thread; the trade never waits for it."""
    while True:
        try:
            while OUT:
                print(OUT.popleft(), flush=True)
        except Exception:
            pass
        time.sleep(0.2)


def no_quickedit():
    """Switch QuickEdit off for this console (a click selects text and pauses the output until a key is pressed)."""
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h, mode = k.GetStdHandle(-10), ctypes.c_uint()
        if k.GetConsoleMode(h, ctypes.byref(mode)):
            k.SetConsoleMode(h, (mode.value | 0x80) & ~0x40)  # ENABLE_EXTENDED_FLAGS on, ENABLE_QUICK_EDIT_MODE off
    except Exception:
        pass


no_quickedit()
threading.Thread(target=console, daemon=True).start()


def say(msg):
    line = time.strftime("%H:%M:%S ") + msg
    LOG.append(line)
    OUT.append(line)
    try:
        with open(os.path.join(HERE, "hop_trader.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def tg(msg):
    """Only what matters (27.10, user: no spam): trade results, automatic stops, waiting for / receiving money.
    Telegram through the live bot's notifier (bot.py reads the token itself; it is never printed here)."""
    try:
        B.tg.q.put("🔁 Перекидыватель: " + msg)
    except Exception:
        pass


def phase(p, detail=""):
    S["phase"], S["detail"] = p, detail


def conv(k, v):
    if isinstance(DEFAULTS[k], bool):
        return v if isinstance(v, bool) else str(v).lower() in ("1", "true", "on", "да")
    return type(DEFAULTS[k])(v)


def load_settings():
    s = dict(DEFAULTS)
    try:
        s.update({k: conv(k, v) for k, v in json.load(open(SET_FILE, encoding="utf-8")).items() if k in DEFAULTS})
    except Exception:
        pass
    return s


SET = load_settings()


def save_settings(new):
    for k, v in new.items():
        if k in DEFAULTS:
            SET[k] = conv(k, v)
    SET["trades"] = max(0, SET["trades"])
    SET["max_loss_usd"] = max(0.1, SET["max_loss_usd"])
    SET["max_stuck"] = max(1, SET["max_stuck"])
    SET["stake_pct"] = max(0.0, min(100.0, SET["stake_pct"]))
    SET["hop_gwei"] = max(0.05, min(1.0, SET["hop_gwei"]))
    SET["every"] = max(1.0, min(5.0, SET["every"]))  # 27.10 11:19 the guard marked W6 3.1 s after it got the tokens
    with open(SET_FILE, "w", encoding="utf-8") as f:
        json.dump(SET, f, ensure_ascii=False, indent=1)


def hop_keys():
    keys = {}
    for line in open(os.path.join(ROOT, "hop_wallets.env"), encoding="utf-8"):
        if line.startswith("HOP_KEY_"):
            k, v = line.strip().split("=", 1)
            keys[int(k[8:])] = v
    return keys


# 10.11 (user): the 48 Club privacy RPC landed the next block 3 times of 14, our rpc-bsc.48.club 0 of 15 (send_test.csv). Every transaction
# goes to all sending nodes at once, so one more costs nothing; only this program, config.json is not touched
for _u in ("https://rpc.48.club",):
    if _u not in B.CFG["send_rpc_urls"]:
        B.CFG["send_rpc_urls"] = [_u] + list(B.CFG["send_rpc_urls"])
# 19.11 (user): our own BSC p2p listener (D:\bot\p2p_listener, bsc_p2p_listener.exe) is the mempool: it hears 30+ BSC nodes directly,
# saw 75-90 % of block txs ~0.46 s before the block (publicnode ~0.35 s). bot.Mempool reads CFG["ws_url"] on every reconnect, so this
# thread only switches the url: the listener while its /status answers, publicnode otherwise. In memory only: config.json is not touched.
P2P_WS, P2P_STATUS = "ws://127.0.0.1:8547", "http://127.0.0.1:8547/status"
PUBLIC_WS = B.CFG["ws_url"]
MP_SRC = {"url": PUBLIC_WS, "peers": 0}


def p2p_ok():
    try:
        st = B.http.get(P2P_STATUS, timeout=2).json()
        MP_SRC["peers"] = int(st.get("bsc_peers") or 0)
        return MP_SRC["peers"] >= 3
    except Exception:
        MP_SRC["peers"] = 0
        return False


def mp_source_loop():
    while True:
        want = P2P_WS if p2p_ok() else PUBLIC_WS
        if want != B.CFG["ws_url"]:
            B.CFG["ws_url"] = want
            say(f"мемпул: теперь {'наш p2p-слушатель (' + str(MP_SRC['peers']) + ' узлов BSC)' if want == P2P_WS else 'publicnode (слушатель не отвечает)'}")
            if SIG_MP_REF.get("mp") is not None:
                SIG_MP_REF["mp"].switch = True  # makes the running stream reconnect to the new url at once
        MP_SRC["url"] = want
        S["mempool_src"] = dict(MP_SRC)
        time.sleep(15)


class SwitchMempool(B.Mempool):
    """bot.Mempool that also drops its stream when mp_source_loop changes the source (bot.Mempool reconnects only on an error)."""
    switch = False

    def run(self):
        while True:
            try:
                with B.connect(B.CFG["ws_url"], open_timeout=10, max_size=None, ping_interval=20) as ws:
                    self.switch = False
                    ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_subscribe", "params": ["newPendingTransactions", True]}))
                    while True:
                        if self.switch:
                            raise RuntimeError("смена источника")
                        try:
                            msg = json.loads(ws.recv(timeout=3))
                        except TimeoutError:
                            raise RuntimeError("мемпул молчит 3 с")
                        tx = (msg.get("params") or {}).get("result")
                        if not isinstance(tx, dict) or "hash" not in tx:
                            continue
                        now = self.last_msg = time.time()
                        self.count += 1
                        h = tx["hash"].lower()
                        self.seen[h] = (now, (tx.get("from") or "").lower(), (tx.get("to") or "").lower(),
                                        (tx.get("input") or "")[:10], int(tx.get("gasPrice") or tx.get("maxFeePerGas") or "0x0", 16) / 1e9)
                        self.order.append((now, h))
                        for w in self.watchers:
                            try:
                                w(tx)
                            except Exception:
                                pass
                        while self.order and now - self.order[0][0] > self.keep:
                            self.seen.pop(self.order.popleft()[1], None)
            except Exception as e:
                B.log(f"Мемпул ({'p2p' if B.CFG['ws_url'] == P2P_WS else 'publicnode'}): обрыв ({str(e)[:60]}), переподключаюсь")
                time.sleep(0.5 if self.switch or 'смена' in str(e) else 2)


SIG_MP_REF = {"mp": None}
if p2p_ok():
    B.CFG["ws_url"] = P2P_WS
threading.Thread(target=mp_source_loop, daemon=True).start()
A = B.Sender(B.load_private_key())
KEYS = hop_keys()
_wallets = {}


def wallet(i):
    if i not in _wallets:
        s = B.Sender(KEYS[i])
        # 18.11: a node answered nonce 0 for a W that had sent 9 txs -> «nonce too low», the signal attempt was lost.
        # The first nonce is the highest of all our read nodes.
        for node in HEAD_NODES.values():
            try:
                s.nonce = max(s.nonce, node.eth.get_transaction_count(s.address, "latest"))
            except Exception:
                pass
        _wallets[i] = s
    return _wallets[i]


def load_used():
    """token -> wallet names (A, W1..W100) that ever held it: such a wallet is never used on that token again
    (the guard may have marked it, or mark it late). Seeded from the earlier hop_test.csv runs."""
    used = {}
    try:
        used = {k: set(v) for k, v in json.load(open(USED_FILE, encoding="utf-8")).items()}
    except Exception:
        pass
    old = os.path.join(HERE, "hop_test.csv")
    if os.path.exists(old):
        with open(old, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f, delimiter=";"):
                if r.get("token") and r.get("step", "").startswith(("buy", "hop")):
                    used.setdefault(r["token"].lower(), set()).add(r["wallet"])
    return used


USED = load_used()
USED_LOCK = threading.RLock()  # 06.11: two networks trade at once


def mark_used(token, name):
    with USED_LOCK:
        USED.setdefault(token.lower(), set()).add(name)
        with open(USED_FILE, "w", encoding="utf-8") as f:
            json.dump({k: sorted(v) for k, v in USED.items()}, f, ensure_ascii=False, indent=0)


def used(token, name):
    return name in USED.get(token.lower(), ())


# random holders (28.10, user): the chain used to start with W1, W2, W3 on every coin, and the guard has seen them
# dozens of times (it may keep its own list). Holders now come in a random order fixed for the run, wallets that held
# no coin yet first; one that held any coin is taken only when the fresh ones run out.
PLAN = random.Random().sample(sorted(KEYS), len(KEYS))


def plan_ids():
    with USED_LOCK:
        ever = set().union(*USED.values()) if USED else set()
    return [i for i in PLAN if f"W{i}" not in ever] + [i for i in PLAN if f"W{i}" in ever]


def wait(snd, h, sec=20):
    t_end = time.time() + sec
    while time.time() < t_end:
        time.sleep(0.15)
        rc = snd.receipt(h)
        if rc:
            return rc
    return None


def retry(fn, tries=4):
    for k in range(tries):
        try:
            return fn()
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(0.2)


class Trade:
    def __init__(self):
        self.gas = 0.0

    def tx(self, snd, h, sec=20):
        rc = wait(snd, h, sec)
        if rc:
            self.gas += B.gas_cost(rc)
        return rc


def stake_now(bal=None):
    if not SET["stake_auto"] and SET["stake_pct"] <= 0:
        return SET["stake"]
    bal = w3.eth.get_balance(A.address) / 1e18 if bal is None else bal
    free = max(0.0, bal - GAS_RESERVE)
    if SET["stake_auto"]:
        return round(min(free, max(MIN_STAKE, free * AUTO_SHARE)), 6)
    return round(free * min(SET["stake_pct"], 100) / 100, 6)


def stake_text():
    st_ = stake_now()
    if SET["stake_auto"]:
        warn = f" — меньше {GOOD_STAKE}, газ съест прибыль" if st_ < GOOD_STAKE else ""
        return f"авто: {AUTO_SHARE:.0%} свободного баланса, сейчас {st_} BNB{warn}"
    if SET["stake_pct"]:
        return f"{SET['stake_pct']}% кошелька (сейчас {st_} BNB)"
    return f"{SET['stake']} BNB"


TICK = dict(block=None, at=None, wallet="", n=0, hist=[], stale=False)  # the timer's phase: a float block, mean of recent marks
TICK_P = 100 / 3  # blocks per guard tick: 15 s at 0.45 s a block (VLTX marks 257/291/325, ASTSc 7200 blocks = 216 ticks)
TICK_LEAD = 2  # send when the head is 2 blocks before a tick: the tx lands 1 block before it (or in it, after the
# mark thanks to the gas). The guard reads >= 2 blocks before its mark lands; marks sit +-1 block off the grid
TICK_MAX_AGE = 6 * 3600  # an older phase is not trusted (it moved between 08:08 and 08:44 on 27.10)
NOT_VICTIMS = ("0x8737a10d", "0x426603ba")  # marked by another process on its own clock: no phase from them
MARKS = os.path.join(HERE, "guard_marks.csv")  # guard_period.py writes it; guard_gas() appends new marks
GUARD_GAS = dict(gwei=0.051, block=None, seen=None)  # the guard's last mark; 0.051 gwei on every mark seen so far
HOP_LIMIT = 100_000  # gas limit of a hop: a guard-coin transfer uses ~63.5k; the node wants limit * price on the wallet


def tick_on():
    return SET["tick_mode"] and TICK["block"] is not None and time.time() - TICK["at"] < TICK_MAX_AGE \
        and not TICK["stale"]


def next_tick(head):
    """The first guard tick block after `head`."""
    k = math.floor((head - TICK["block"]) / TICK_P) + 1
    t = round(TICK["block"] + k * TICK_P)
    return t if t > head else round(TICK["block"] + (k + 1) * TICK_P)


HEAD = dict(block=0, t=0.0, by={})
# 27.10 (user): the head is read from two nodes and the higher one is taken: 48club alone was 1-3 blocks behind in
# 5 of 40 reads, enough to send a tick hop late (TICK_LEAD is 2 blocks). Each node is polled by its own thread, so a
# slow node (dataseed ~250 ms, spikes to 2 s) never delays the hop.
HEAD_NODES = {"48club": w3, "dataseed": Web3(Web3.HTTPProvider("https://bsc-dataseed.bnbchain.org",
                                                                 request_kwargs={"timeout": 2})),
              "publicnode": Web3(Web3.HTTPProvider("https://bsc-rpc.publicnode.com", request_kwargs={"timeout": 2}))}


def head_poll(name, node):
    while True:
        try:
            b = node.eth.block_number
            HEAD["by"][name] = b
            if b > HEAD["block"]:
                HEAD.update(block=b, t=time.time())
        except Exception:
            time.sleep(0.5)
        time.sleep(0.08)


def head_now():
    """The chain head: the highest block any of HEAD_NODES has reported."""
    if not HEAD["by"]:  # before the pollers run (or if they died): read it here
        try:
            HEAD["block"] = max(HEAD["block"], w3.eth.block_number)
        except Exception:
            pass
    return HEAD["block"]


def wait_window(why, strict=True):
    """Sleeps until the head is TICK_LEAD blocks before a tick and returns that tick. strict: a window already half
    gone (head 1 block before the tick) is skipped for the next one; used for the buy, where waiting costs nothing."""
    while True:
        head = head_now()
        t = next_tick(head)
        if t - head <= TICK_LEAD and (not strict or t - head >= 2):
            return t
        phase(S["phase"], f"{why}: до окна сторожа {(t - TICK_LEAD - head) * SPB:.1f} с")
        time.sleep(0.05)


def tick_text():
    if SET["chain_mode"] and SET["bot_exit"]:
        return ((f"{SHIELD_N} приманок перед покупкой (ждём, пока сторож начнёт их метить); " if SET["shield"] else "")
                + (f"цепочка: передача каждые ~{CHAIN_FAST_EVERY} с всю сделку, без крошек, продавец готов заранее; продаю, когда "
                   if SET["all_fast"] else
                   f"цепочка: {CHAIN_K} крошек + передача сразу после покупки, первую {CHAIN_FAST_S} с передача каждые {CHAIN_FAST_EVERY} с и по {CHAIN_FAST_K} крошке, "
                   f"дальше каждые {CHAIN_EVERY} с; продаю, когда ")
                + f"боты закончили покупать (25 BNB: ≥88 покупок, 10 BNB: ≥24, 1 BNB: ≥7) и {BOT_SILENCE} с тишины; "
                "при падении пула на 25% от пика — сразу")
    if SET["chain_mode"]:
        return (f"цепочка: {CHAIN_K} крошек + передача сразу после покупки и каждые {CHAIN_EVERY} с"
                + ("; 1-BNB монеты: продаю от +10%, когда покупки стихли, мало покупок — жду, при падении пула или на "
                   "16-й мин — сразу" if SET["patient"] else "")
                + ("; держать по типу и газу (1 BNB ≤5 мин, 10 BNB ≤13, 25 BNB ≤40; монету, где газ съест рост, "
                      "пропускаю)" if SET["hold_auto"] else f"; держать {SET['hold']:g} с"))
    if not SET["tick_mode"]:
        return "выключено: перекид каждые %g с" % SET["every"]
    if TICK["block"] is None:
        return "фаза таймера сторожа неизвестна — перекид каждые %g с" % SET["every"]
    age = (time.time() - TICK["at"]) / 60
    if TICK["stale"]:
        return f"сторож делал не-метки после последней метки — жду новую метку, пока перекид каждые {SET['every']:g} с"
    if not tick_on():
        return f"последняя метка сторожа {age:.0f} мин назад — старая, перекид каждые {SET['every']:g} с"
    return f"по таймеру сторожа (последняя метка {age:.0f} мин назад, фаза по {TICK['n']} меткам): один перекид за 15 с"


def set_tick(block, at, wallet):
    if wallet[:10].lower() in NOT_VICTIMS:
        return
    if TICK["block"] is not None:
        d = (block - TICK["block"]) / TICK_P
        off = (d - round(d)) * TICK_P
        if abs(off) > 2.5:  # the guard was restarted: a new phase
            say(f"таймер сторожа сдвинулся на {off:+.0f} блоков (метка в блоке {block})")
            TICK["hist"] = []
    TICK["hist"] = [(b, a) for b, a in TICK["hist"] if at - a < TICK_MAX_AGE][-11:] + [(block, at)]
    # the phase is the mean of the recent marks, each moved onto the newest one's tick
    ref = st.mean(b + round((block - b) / TICK_P) * TICK_P for b, a in TICK["hist"])
    TICK.update(block=ref, at=at, wallet=wallet, n=len(TICK["hist"]))


TICK_CHECK = os.path.join(HERE, "tick_check.csv")


def tick_check(block, wallet, token):
    """27.10 (user): every new mark against the tick the phase predicted for it, before the mark updates the phase:
    the live accuracy of tick_mode, with no trade (want diff 0/+1; stale = the phase was not trusted then)."""
    if TICK["block"] is None:
        return
    pred = next_tick(block - 3)
    new = not os.path.exists(TICK_CHECK)
    with open(TICK_CHECK, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        if new:
            w.writerow(["time", "block", "predicted", "diff", "stale", "phase_marks", "victim", "wallet", "token"])
        w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), block, pred, block - pred, int(TICK["stale"]), TICK["n"],
                    int(wallet[:10].lower() not in NOT_VICTIMS), wallet, token])


def load_marks():
    try:
        rows = list(csv.DictReader(open(MARKS, encoding="utf-8-sig"), delimiter=";"))
    except Exception:
        return
    for r in rows:
        if r["method"] == "095ea7b3":
            set_tick(int(r["block"]), int(r["ts"]), r["wallet"])


def hop_price():
    if SET["hop_dyn"]:
        return min(int(1e9), max(A.gas_price, int(max(0.1, GUARD_GAS["gwei"] * 2) * 1e9)))
    return max(A.gas_price, int(SET["hop_gwei"] * 1e9))


def hop_gas():
    return int(150_000 * hop_price() * 1.3)  # enough for two hops, and for one after the price doubles


def seller_gas():
    return int((B.CFG["gas_limit_sell"] + 100_000 + 21_000) * hop_price() * 1.3)


def fund(i, need, tr=None):
    """Tops wallet Wi up to `need` wei from A. The BNB stays there for later trades; only the transfer's gas is spent."""
    w = wallet(i)
    have = w3.eth.get_balance(w.address)
    if have >= need:
        return True
    h = A.send(w.address, b"", value=need - have, gas=21_000)
    rc = (tr.tx(A, h) if tr else wait(A, h))
    return bool(rc and rc["status"] == 1)


def prefund():
    """Before waiting for a launch: gas on as many hop wallets as one trade can use (+5 spare for marked ones)."""
    step = CHAIN_EVERY if SET["chain_mode"] else 15 if tick_on() else SET["every"]
    # 27.10 (user: "the gas wallets are refilled every time, over $1 sits there"): the chain tops the next holder up
    # on the way (make_next_holder), so only CHAIN_PREFUND wallets are filled in advance, not hold/step + 3
    n = CHAIN_PREFUND if SET["chain_mode"] else min(len(KEYS) - 1, int(math.ceil(SET["hold"] / step)) + 5)
    if SET["chain_mode"] and SET["shield"]:  # the decoys come from the top of the list: their gas and dust ready
        for i in sorted(KEYS, reverse=True)[:SHIELD_N]:
            if not (S["running"] or S["manual"]):
                return False
            if w3.eth.get_balance(wallet(i).address) < shield_need():
                phase("Готовлю кошельки", f"газ и пыль для приманки W{i}")
                fund(i, shield_need())
    A.refresh_gas_price()
    need, ready = (fast_need() if SET["all_fast"] else chain_need()) if SET["chain_mode"] else hop_gas(), 0
    keep = max(MIN_STAKE, stake_now()) + 0.00015  # the stake must stay on A: with a small balance fund fewer wallets
    PREP.clear()
    # 29.10: wallets that already hold the gas first (the panel's wallet scan), so a small A funds none or few
    t_w = time.time()
    while SET["chain_mode"] and not S.get("wallets") and time.time() - t_w < 400:  # the first scan after a start (1000 wallets: ~3-5 min)
        phase("Готовлю кошельки", "смотрю, на каких кошельках уже есть газ")
        time.sleep(1)
    enough = ((HOP_LIMIT if SET["all_fast"] else (CHAIN_K + 1) * HOP_LIMIT) * chain_price() if SET["chain_mode"]
              else need)  # what make_next_holder asks
    has = {x["n"] for x in S.get("wallets") or [] if (x.get("bnb") or 0) * 1e18 >= enough}
    order = sorted(plan_ids(), key=lambda i: i not in has) if SET["chain_mode"] else []
    for i in (order[:n] if SET["chain_mode"] else range(1, n + 1)):
        if not (S["running"] or S["manual"]):
            return False
        bal_i = w3.eth.get_balance(wallet(i).address)
        if bal_i < enough and w3.eth.get_balance(A.address) / 1e18 - need / 1e18 < keep:
            say(f"газ только на {ready} кошельков из {n}: больше нельзя, иначе на A не останется на ставку — "
                f"сделка продержится ~{ready * (CHAIN_EVERY if SET['chain_mode'] else 4.9):.0f} с")
            return ready >= 3
        phase("Готовлю кошельки", f"газ на W{i} ({ready + 1} из {n})")
        if bal_i < enough and not fund(i, need):
            say(f"газ на W{i} не дошёл — мало BNB на A?")
            return False
        wallet(i).gas_price = A.gas_price  # hop wallets never refresh it themselves
        PREP.append(i)
        ready += 1
        S["ready"] = ready
    return True


PREP = []  # wallets prefund() filled for the next chain, in plan order: the fast minute takes them with no node call


def active_pick():
    """An already trading guard coin for the unattended mode: young enough, never bought by A, A not marked."""
    if SET["active_age"] <= 0:
        return None
    head = w3.eth.block_number
    for pair, c in sorted(ACTIVE.items(), key=lambda kv: -kv[1]["block"]):
        if (head - c["block"]) * SPB / 60 <= SET["active_age"] and not used(c["token"], "A") \
                and c["token"].lower() not in SKIP \
                and not c["a_marked"] and (c["bnb"] or 0) >= SET["min_pool"]:
            return pair, c["token"], time.time() - SET["entry"]
    return None


def next_guard_launch(since=None):
    """since: the block before the hop wallets got their gas, so a pool created during that minute is not missed."""
    last = since if since is not None else w3.eth.block_number
    while S["running"] and not S["stop_after"] and not S["manual"]:
        time.sleep(1)
        found = active_pick()
        if found:
            say(f"беру уже торгующуюся монету {ACTIVE[found[0]]['sym']}")
            return found
        try:
            head = w3.eth.block_number
            if head <= last:
                continue
            logs = w3.eth.get_logs({"address": B.FACTORY.address, "topics": [B.PAIR_CREATED_TOPIC],
                                    "fromBlock": last + 1, "toBlock": head})
            last = head
        except Exception:
            continue
        for l in logs:
            r = B.parse_pair_created(l)
            if not r:
                continue
            pair, token = r
            if is_guard(token):
                return pair, token, time.time()
    return None


DEALS = os.path.join(HERE, "hop_deals.csv")
DEAL_MARKS = os.path.join(HERE, "hop_deal_marks.csv")
DEAL = {}  # filled by chain_trade while a trade runs; record() writes it. A journal error never touches the trade.
DEAL_COLS = ["time", "token", "symbol", "kind", "stake_bnb", "buy_age_s", "buy_block", "sell_block", "blocks_held",
             "pool_buy_bnb", "pool_sell_bnb", "tokens_quoted", "tokens_bought", "buy_diff_pct", "tokens_sold",
             "token_loss_pct", "quote_bnb", "got_bnb", "sell_diff_pct", "reason", "hops", "hold_med_s", "hold_max_s",
             "bots", "gas_bnb", "pnl_bnb", "note"]


def block_later(key):
    """The block now, read in the background: a slow node must not hold up a hand-over."""
    def run():
        try:
            DEAL[key] = retry(lambda: w3.eth.block_number)
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()


def deal_marks(token, sym, addrs, holder):
    """90 s after a trade: which of our holders the guard marked (allowance(guard, wallet) > 0)."""
    try:
        time.sleep(90)
        tok = w3.eth.contract(token, abi=TOK_ABI)
        one = lambda a: retry(lambda: tok.functions.allowance(GUARD, a[1]).call() > 0)
        with ThreadPoolExecutor(6) as ex:
            hit = [n for (n, _), m in zip(addrs, ex.map(one, addrs)) if m]
        new = not os.path.exists(DEAL_MARKS)
        with open(DEAL_MARKS, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            if new:
                w.writerow(["time", "token", "symbol", "holders", "marked", "last_holder", "last_marked", "marked_names"])
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), token, sym, len(addrs), len(hit), holder, int(holder in hit),
                        " ".join(hit[:40])])
    except Exception as e:
        say("журнал меток: " + str(e)[:80])


def write_deal(token, sym, stake, hops, reason, got, gas, pnl, note):
    if not DEAL:
        return
    d, new = dict(DEAL), not os.path.exists(DEALS)
    DEAL.clear()
    pct = lambda a, b: round((a / b - 1) * 100, 2) if a and b else ""
    holds = d.get("holds") or [0.0]
    a, b = d.get("buy_block"), d.get("sell_block")
    row = dict(time=time.strftime("%Y-%m-%d %H:%M:%S"), token=token, symbol=sym, kind=d.get("kind", ""), stake_bnb=stake,
               buy_age_s=d.get("buy_age_s", ""), buy_block=a or "", sell_block=b or "", blocks_held=b - a if a and b else "",
               pool_buy_bnb=d.get("pool_buy_bnb", ""), pool_sell_bnb=d.get("pool_sell_bnb", ""),
               tokens_quoted=d.get("tokens_quoted", ""), tokens_bought=d.get("tokens_bought", ""),
               buy_diff_pct=pct(d.get("tokens_bought"), d.get("tokens_quoted")), tokens_sold=d.get("tokens_sold", ""),
               token_loss_pct=(lambda x: "" if x == "" else -x)(pct(d.get("tokens_sold"), d.get("tokens_bought"))),
               quote_bnb=d.get("quote_bnb", ""), got_bnb=f"{got:.8f}", sell_diff_pct=pct(got, d.get("quote_bnb")),
               reason=reason, hops=hops, hold_med_s=round(st.median(holds), 2), hold_max_s=round(max(holds), 2),
               bots=d.get("bots", ""), gas_bnb=f"{gas:.8f}", pnl_bnb=f"{pnl:.8f}", note=note)
    with open(DEALS, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=DEAL_COLS, delimiter=";")
        if new:
            w.writeheader()
        w.writerow(row)
    if d.get("addrs"):
        threading.Thread(target=deal_marks, args=(token, sym, d["addrs"], d["addrs"][-1][0]), daemon=True).start()


def record(token, sym, stake, hops, held, reason, got, gas, note=""):
    pnl = got - stake - gas
    S["run_pnl"] += pnl
    S["stuck_streak"] = S["stuck_streak"] + 1 if got == 0 else 0
    new = not os.path.exists(TRADES)
    with open(TRADES, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        if new:
            w.writerow(["time", "token", "symbol", "stake_bnb", "hops", "held_s", "reason", "got_bnb", "gas_bnb",
                        "pnl_bnb", "pnl_pct", "note"])
        w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), token, sym, stake, hops, round(held, 1), reason,
                    f"{got:.8f}", f"{gas:.8f}", f"{pnl:.8f}", round(pnl / stake * 100, 1), note])
    try:
        write_deal(token, sym, stake, hops, reason, got, gas, pnl, note)
    except Exception as e:
        say("журнал сделки: " + str(e)[:80])
    say(f"ИТОГ {sym}: {reason}, получено {got:.6f} BNB, газ {gas:.6f}, итог {pnl:+.6f} BNB ({pnl / stake * 100:+.1f}%)")
    usd = S["bnb_usd"]
    tg(f"{'✅' if pnl >= 0 else '🔻'} {sym}: {reason}\nитог {pnl:+.6f} BNB ({pnl / stake * 100:+.1f}%, {pnl * usd:+.2f} $)\n"
       f"ставка {stake} · получено {got:.6f} · газ {gas:.6f} · перекидов {hops}" + (f"\n{note}" if note else "")
       + f"\nза запуск: {S['run_pnl'] * usd:+.2f} $")


def trade(pair, token, t_seen):
    tr, stake = Trade(), stake_now()
    if SET["stake_auto"]:
        say(f"ставка на эту сделку: {stake_text()}")
    tok = w3.eth.contract(token, abi=TOK_ABI)
    try:
        sym = tok.functions.symbol().call()[:20]
    except Exception:
        sym = token[:8]
    S.update(token=token, sym=sym, holder="", hops=0, pnl_pct=None, value=None, held=0.0, sell_now=False)
    marked = lambda addr: tok.functions.allowance(GUARD, addr).call() > 0
    say(f"монета со сторожем: {sym} {token}")
    a_dirty = used(token, "A") or marked(A.address)
    if SET["chain_mode"]:
        return chain_trade(tr, tok, token, sym, stake, pair, t_seen, marked, a_dirty)
    if a_dirty:
        SKIP.add(token.lower())
        say("A уже покупал эту монету или помечен на ней — пропускаю")
        return False

    # seller: the highest clean wallet, approved now, holds the tokens only for the final sell
    phase("Готовлю продавца", sym)
    seller_i = next((i for i in sorted(KEYS, reverse=True)
                     if not used(token, f"W{i}") and not marked(wallet(i).address)), None)
    if seller_i is None or not fund(seller_i, seller_gas(), tr):
        SKIP.add(token.lower())
        say("нет продавца с газом — пропускаю монету")
        return False
    seller = wallet(seller_i)
    seller.gas_price = A.gas_price
    S["seller"] = f"W{seller_i}"
    rc = tr.tx(seller, seller.approve(token))
    if not rc or rc["status"] != 1:
        SKIP.add(token.lower())
        say("approve продавца не прошёл — пропускаю монету")
        return False
    hop_ids = iter([i for i in sorted(KEYS) if i != seller_i])

    def next_hop():
        for i in hop_ids:
            if used(token, f"W{i}"):
                continue
            w = wallet(i)
            if marked(w.address):
                continue
            # 27.10 12:13 (ASTSc): no top-up from A here, it costs a confirmation (~5 s) while a wallet holds the
            # tokens; prefund() fills them before the trade, a wallet short of gas is skipped
            if w3.eth.get_balance(w.address) >= HOP_LIMIT * hop_price():
                w.gas_price = A.gas_price
                return i, w
        return None, None

    pool = B.Pool(pair, token, sym, 0, 0)
    while time.time() - t_seen < SET["entry"]:
        if S["stop_after"]:  # "Стоп" before the buy cancels the trade
            say(f"{sym}: стоп до покупки — отменено")
            return False
        phase("Жду момент входа", f"{sym}: покупка через {SET['entry'] - (time.time() - t_seen):.0f} с")
        time.sleep(0.3)
    pool.refresh()
    if pool.r_bnb / 1e18 < SET["min_pool"]:
        SKIP.add(token.lower())
        say(f"в пуле {pool.r_bnb / 1e18:.1f} BNB < {SET['min_pool']} — пропускаю")
        return False
    if S["stop_after"]:
        say(f"{sym}: стоп до покупки — отменено")
        return False
    if w3.eth.get_balance(A.address) / 1e18 < stake + 0.00015:  # the hop wallets' gas was taken from A meanwhile
        say(f"мало BNB на A для ставки {stake} — пропускаю")
        return False

    phase("Покупаю", sym)
    A.refresh_gas_price()
    if tick_on():  # A must get the tokens just after a guard read, not just before one
        wait_window(f"{sym}: покупка в окне таймера")
    mark_used(token, "A")
    try:
        rc = tr.tx(A, A.buy(token, Web3.to_wei(stake, "ether"), 0))
    except Exception as e:
        say(f"покупка не отправлена: {str(e)[:80]}")
        rc = {"status": 0}
    bought = retry(lambda: tok.functions.balanceOf(A.address).call()) > 0  # a slow receipt may still be a buy
    if not bought:
        record(token, sym, stake, 0, 0, "покупка не прошла", stake, tr.gas)  # the stake stayed on A: only gas lost
        return False
    try:
        hold_and_sell(tr, tok, token, sym, stake, pool, seller_i, seller, marked, next_hop)
    except Exception as e:  # never leave a trade unrecorded: the tokens sit on the last holder
        say("ошибка во время сделки: " + str(e)[:150])
        record(token, sym, stake, S["hops"], S["held"], "ошибка во время сделки", 0.0, tr.gas,
               f"токены на {S['holder'] or 'A'}: {str(e)[:60]}")
    return True


TIMING = os.path.join(HERE, "hop_timing.csv")


def hop_timing(sym, n, frm, to, wait_s, confirm_s, ok):
    """One row per hop: how long the holder kept the tokens before we sent them on, how long the transfer took."""
    new = not os.path.exists(TIMING)
    with open(TIMING, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        if new:
            w.writerow(["time", "symbol", "hop", "from", "to", "wait_s", "confirm_s", "held_s", "ok"])
        w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), sym, n, frm, to, round(wait_s, 2), round(confirm_s, 2),
                    round(wait_s + confirm_s, 2), int(ok)])


TRANSFER_TOPIC = Web3.keccak(text="Transfer(address,address,uint256)").hex().replace("0x", "")


def received(rc, token, to):
    """Tokens that reached `to` according to the receipt's Transfer events, 0 if none."""
    got = 0
    for lg in (rc or {}).get("logs", []):
        t = [x.hex().replace("0x", "") for x in lg["topics"]]
        if lg["address"].lower() == token.lower() and len(t) == 3 and t[0] == TRANSFER_TOPIC and                 t[2][-40:] == to.lower()[2:]:
            got += int(bytes(lg["data"]).hex() or "0", 16)
    return got


TICKS = os.path.join(HERE, "hop_ticks.csv")


def tick_log(sym, n, to, tick, sent_head, landed, ok):
    """One row per hop in tick_mode: where it landed against the guard tick (want -2/-1; 0 only by the gas race)."""
    new = not os.path.exists(TICKS)
    with open(TICKS, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        if new:
            w.writerow(["time", "symbol", "hop", "to", "tick", "sent_at", "landed", "landed_vs_tick", "ok"])
        w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), sym, n, to, tick, sent_head - tick, landed, landed - tick,
                    int(ok)])
    if landed - tick > 0:
        say(f"{sym}: перекид на {to} попал на {landed - tick} блок(а) ПОСЛЕ тика сторожа")


def chain_price():
    """27.10 15:45 SONYc: 24 transfers at hop_price() (0.102 gwei) were 38% of a 0.0005 stake. In the chain the
    holder is covered by the queue of crumbs, not by beating a mark in the same block: the network price is enough."""
    return A.gas_price


def fast_need():
    """all_fast: gas for one bare hand-over (a holder only passes the coin on)."""
    return int(HOP_LIMIT * chain_price() * 1.3)


FEED_AHEAD = 15  # all_fast: wallets with gas kept ready ahead of the chain by a background thread
FEED_BATCH = 6  # 04.11 TZ: top-ups sent at once (A's next nonces), confirmed in parallel: one at a time could not keep up
FEED_WAIT = 5.0  # s next_holder waits for the feed before topping a wallet up itself (GMEc: 7 s in that search)


def make_next_holder(token, marked, tr, ready=None, reserve=None):
    """The next clean W for a chain on `token`: never held it (so it cannot be marked on it), with gas.
    ready (29.10 DINAR): wallets prefund() just filled, taken first with no node call at all. 29.10 SKUUB/ABI: both
    stuck right after the 30 ready ones ran out; with all_fast, feed() (a background thread) keeps FEED_AHEAD wallets
    topped up ahead of the chain, so a hand-over never waits for a top-up or a node answer."""
    ids = iter(plan_ids())
    ready = deque(ready or [])
    taken = set(ready)
    lock = threading.Lock()
    feeding = []  # non-empty while feed() runs
    need = lambda: fast_need() if SET["all_fast"] else chain_need()
    enough = lambda: HOP_LIMIT * chain_price() if SET["all_fast"] else (CHAIN_K + 1) * HOP_LIMIT * chain_price()

    def fresh():
        with lock:
            for i in ids:
                if used(token, f"W{i}") or i in taken or i in BUSY:  # 13.11: BUSY = a stake or coin of another network's attempt
                    continue
                taken.add(i)
                if reserve is not None:  # 13.11 (network 7): the chain's wallets are reserved, so a parallel network never sends from one
                    with WALLET_LOCK:
                        BUSY.add(i)
                    reserve.append(i)
                return i
        return None

    def next_holder():
        t_w = time.time()
        while feeding and not ready and time.time() - t_w < FEED_WAIT:
            time.sleep(0.02)
        while ready:
            i = ready.popleft()
            if not used(token, f"W{i}"):
                w = wallet(i)
                w.gas_price = A.gas_price
                return i, w
        for _ in range(5):  # 29.10 review: A out of BNB must not keep the holder waiting through 800 wallets
            i = fresh()
            if i is None:
                return None, None
            w = wallet(i)
            if w3.eth.get_balance(w.address) >= enough() or fund(i, need(), tr):
                w.gas_price = A.gas_price
                return i, w
        return None, None

    def feed(stop):
        flying = [0]
        ex = ThreadPoolExecutor(FEED_BATCH * 2)

        def balance(i):  # wallet(i) reads the node on first use: done here, not in the trade loop
            return retry(lambda: w3.eth.get_balance(wallet(i).address))

        def land(i, h):
            try:
                rc = wait(A, h)
                if rc:
                    with lock:
                        tr.gas += B.gas_cost(rc)
                if rc and rc["status"] == 1:
                    ready.append(i)
            except Exception:
                pass
            finally:
                with lock:
                    flying[0] -= 1

        feeding.append(1)
        try:
            while not stop():
                gap = min(FEED_AHEAD - len(ready) - flying[0], FEED_BATCH)
                if gap <= 0:
                    time.sleep(0.1)
                    continue
                ids = [i for i in (fresh() for _ in range(gap)) if i is not None]
                if not ids:
                    time.sleep(1)
                    continue
                try:
                    bals = list(ex.map(balance, ids))
                except Exception:
                    time.sleep(0.5)
                    continue
                for i, have in zip(ids, bals):
                    if have >= enough():
                        ready.append(i)
                        continue
                    try:
                        h = A.send(wallet(i).address, b"", value=need() - have, gas=21_000)
                    except Exception as e:  # A out of BNB or no node took it: the wallet is skipped, the loop goes on
                        say(f"пополнение W{i} не отправлено: {str(e)[:60]}")
                        time.sleep(0.5)
                        continue
                    with lock:
                        flying[0] += 1
                    ex.submit(land, i, h)
        finally:
            feeding.clear()
            ex.shutdown(wait=False)
    next_holder.feed, next_holder.fresh, next_holder.ready = feed, fresh, ready
    return next_holder


def prepare_seller(token, next_holder, tr, out):
    """all_fast: a fresh wallet gets the sell gas and approves the router while the chain runs, so the coin goes to it
    only at the sale and it sells at once (the last holder used to wait for a top-up and an approve, ~3 confirmations)."""
    for _ in range(3):
        i = next_holder.fresh()
        if i is None:
            return
        w = wallet(i)
        w.gas_price = A.gas_price
        try:
            if not fund(i, seller_gas(), tr):
                continue
            rc = tr.tx(w, w.approve(token))
            if rc and rc["status"] == 1:
                out["i"], out["w"] = i, w
                return
        except Exception as e:
            say(f"продавец W{i} не готов: {str(e)[:60]}")


CUR_FILE = os.path.join(HERE, "hop_current.json")
CUR = {}
# 27.10 (user: everything in the autostart, unattended): the chain trade in progress is kept on disk after every
# hand-over; after a restart (watchdog, reboot) recover_trade() goes on from the holder written there instead of
# leaving the tokens to the guard.


def save_current(**kw):
    CUR.update(kw)
    tmp = CUR_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(CUR, f)
    os.replace(tmp, CUR_FILE)


def clear_current():
    CUR.clear()
    try:
        os.remove(CUR_FILE)
    except FileNotFoundError:
        pass


def recover_trade():
    try:
        c = json.load(open(CUR_FILE, encoding="utf-8"))
    except Exception:
        return
    token = Web3.to_checksum_address(c["token"])
    tok = w3.eth.contract(token, abi=TOK_ABI)
    sym, stake, h_i = c["sym"], c["stake"], c["holder_i"]
    h, name = (A, "A") if h_i is None else (wallet(h_i), f"W{h_i}")
    marked = lambda a: tok.functions.allowance(GUARD, a).call() > 0
    tr = Trade()
    tr.gas = c.get("gas", 0.0)
    say(f"ВОССТАНОВЛЕНИЕ после перезапуска: сделка {sym}, монета на {name}")
    S.update(token=token, sym=sym, holder=name, hops=c.get("hops", 0), sell_now=False)
    try:
        if retry(lambda: tok.functions.balanceOf(h.address).call()) == 0:
            say(f"восстановление {sym}: на {name} монеты уже нет — сделку не продолжаю")
            return
        if retry(lambda: marked(h.address)):
            return record(token, sym, stake, c.get("hops", 0), time.time() - c["t_buy"], f"застряли на {name}", 0.0,
                          tr.gas, "перезапуск посреди сделки: держателя пометили, пока программа не работала")
        pool = B.Pool(c["pair"], token, sym, 0, 0)
        pool.refresh()
        S["hold_now"] = max(0.0, c["hold"] - (time.time() - c["t_buy"]))
        S["launch_t"], S["kind"] = c.get("launch_t", c["t_buy"] - 30), c.get("kind", 25)
        next_holder = make_next_holder(token, marked, tr)
        nxt_i, nxt = next_holder()
        crumbs = []
        try:
            chain_hold_and_sell(tr, tok, token, sym, stake, pool, marked, next_holder, nxt_i, nxt, None, crumbs,
                                h, h_i, name, hops0=c.get("hops", 0))
        except Exception as e:
            say("ошибка во время восстановленной сделки: " + str(e)[:150])
            record(token, sym, stake, S["hops"], time.time() - c["t_buy"], "ошибка во время сделки", 0.0,
                   tr.gas + crumb_gas(crumbs), f"токены на {S['holder']}: {str(e)[:60]}")
    finally:
        clear_current()


def shield_wallets(token, marked, n):
    """Decoy wallets for `token`, from the top of the list (the chain takes W1, W2, ... from the bottom)."""
    out = []
    for i in sorted(KEYS, reverse=True):
        if len(out) >= n:
            break
        if used(token, f"W{i}"):
            continue
        try:
            if retry(lambda: marked(wallet(i).address)):
                continue
        except Exception:
            continue
        out.append(i)
    return out


def shield_need():
    return int(SHIELD_GAS * chain_price() * 1.3) + int(SHIELD_BNB * 1e18)


def raise_shield(tr, tok, token, sym, marked, crumbs, t_seen):
    """Decoys buy dust; returns once the guard has marked one of them (its pass started) or after SHIELD_WAIT s.
    The decoys' transactions go to `crumbs` (their gas is counted in the trade), the dust to tr.gas."""
    ids = shield_wallets(token, marked, SHIELD_N)
    if len(ids) < SHIELD_N // 2:
        say(f"{sym}: мало чистых кошельков для приманок ({len(ids)}) — покупаю без них")
        return False
    phase("Ставлю приманки", f"{sym}: газ и пыль на {len(ids)} кошельков")
    for i in ids:
        if not fund(i, shield_need(), tr):
            say(f"{sym}: приманка W{i} не получила BNB — покупаю без приманок")
            return False
    price = chain_price()
    sent = []
    for k, i in enumerate(ids):
        w = wallet(i)
        w.gas_price = price
        mark_used(token, f"W{i}")
        data = B.router.encode_abi("swapExactETHForTokensSupportingFeeOnTransferTokens",
                                   args=[0, [B.WBNB, token], w.address, int(time.time()) + 60])
        try:
            h = w.send(B.ROUTER, data, value=int(SHIELD_BNB * 1e18), gas=SHIELD_GAS, gas_price=price)
            crumbs.append((w, h))
            sent.append(w)
            tr.gas += SHIELD_BNB  # the dust stays on a marked wallet: a cost of the trade
        except Exception as e:
            say(f"{sym}: приманка W{i} не отправлена ({str(e)[:50]})")
        time.sleep(0.2)
    if not sent:
        return False
    say(f"{sym}: {len(sent)} приманок купили пыль — жду первую метку сторожа (до {SHIELD_WAIT} с)")
    t0 = time.time()
    while time.time() - t0 < SHIELD_WAIT:
        if S["stop_after"]:
            return False
        for w in sent:
            try:
                if marked(w.address):
                    say(f"{sym}: сторож начал метить приманки через {time.time() - t0:.0f} с — покупаю, цепочка "
                        f"попадёт в его следующий проход")
                    return True
            except Exception:
                pass
        phase("Ставлю приманки", f"{sym}: жду метку сторожа на приманках, {time.time() - t0:.0f} из {SHIELD_WAIT} с")
        time.sleep(0.5)
    say(f"{sym}: сторож за {SHIELD_WAIT} с не пометил приманок — покупаю (приманки в одной очереди с нами)")
    return True


def chain_need():
    return int((CHAIN_K + 1) * HOP_LIMIT * chain_price() * 1.3)


def chain_trade(tr, tok, token, sym, stake, pair, t_seen, marked, a_dirty=False):
    """chain_mode: A buys, then the chain of holders keeps the tokens until tp / sl / hold / "Продать сейчас";
    the holder at that moment approves and sells, and sends the BNB back to A.
    a_dirty (27.10, user: "let me go back in and buy"): A held this coin before or is marked on it, so a marked A
    could buy but never pass the tokens on. A clean W gets the stake from A and buys instead."""
    pool = B.Pool(pair, token, sym, 0, 0)
    # 05.11 review: all node reads come BEFORE the wait. After it they took ~5 s, so entry 30 bought at 35-36 s, and on
    # GMEc that was right behind a 0.3 BNB buy at +69% (gmec_check.py).
    next_holder = make_next_holder(token, marked, tr, ready=[i for i in PREP if not used(token, f"W{i}")])
    # 27.10 review: by the pool's START (a 10 BNB coin grows to ~20 and would pass for 25 BNB on a late re-entry,
    # then wait for 88 bot buys that never come and sleep through the dump)
    first = start_bnb(pair, token, t_seen)
    plan = {}

    def decide():
        """Type, time and gas for this coin; False = skip it."""
        kind, hold, edge = coin_type(first if first is not None else pool.r_bnb / 1e18), SET["hold"], None
        plan.update(kind=kind, hold=hold, edge=edge)
        if kind == 25 and SET["skip25"]:  # 27.10 20:10 (user): at a ~0.002 stake the ~50 min of gas eat the growth
            SKIP.add(token.lower())
            say(f"{sym}: монета типа «25 BNB» — пропускаю (так решено при малой ставке, настройка skip25)")
            return False
        if SET["hold_auto"]:
            m, edge = best_hold(kind, stake, (time.time() - t_seen) / 60)
            if m is None or edge < MIN_EDGE:
                SKIP.add(token.lower())
                say(f"{sym}: тип «{kind} BNB», при ставке {stake} BNB газ съест рост (лучшее {edge:+.0f}%) — пропускаю")
                return False
            plan.update(hold=max(60.0, m * 60 - (time.time() - t_seen)), edge=edge)
        if SET["all_fast"]:  # 04.11 JOB/XL: A ran dry mid-trade, the feed could not fund holders, sold early at a loss
            gas_need = FAST_GAS_MIN * BOT_BACKSTOP[kind] / 60 + FAST_GAS_FIXED - 0.8 * (S.get("wallets_total") or 0)
            need = stake + max(0.0003, gas_need)
            if w3.eth.get_balance(A.address) / 1e18 < need:
                say(f"{sym}: на A нет газа на всю сделку (нужно ~{need:.4f} BNB со ставкой) — пропускаю")
                return False
        return True

    if not decide():
        return False
    A.refresh_gas_price()
    nxt_i, nxt = next_holder()  # ready before the buy: the first hand-over goes right after it
    if nxt is None:
        SKIP.add(token.lower())
        say("нет кошельков с газом для цепочки — пропускаю")
        return False
    while time.time() - t_seen < SET["entry"]:
        if S["stop_after"]:
            say(f"{sym}: стоп до покупки — отменено")
            return False
        phase("Жду момент входа", f"{sym}: покупка через {SET['entry'] - (time.time() - t_seen):.0f} с")
        time.sleep(0.1)
    pool.refresh()
    if first is None and not decide():  # the first Sync was not found: the type by the pool as it is now
        return False
    kind, hold, edge = plan["kind"], plan["hold"], plan["edge"]
    if pool.r_bnb / 1e18 < SET["min_pool"]:
        SKIP.add(token.lower())
        say(f"в пуле {pool.r_bnb / 1e18:.1f} BNB < {SET['min_pool']} — пропускаю")
        return False
    if S["stop_after"]:
        say(f"{sym}: стоп до покупки — отменено")
        return False
    if w3.eth.get_balance(A.address) / 1e18 < stake + 0.00015:
        say(f"мало BNB на A для ставки {stake} — пропускаю")
        return False
    buyer, buyer_i, buyer_name = A, None, "A"
    if a_dirty:
        buyer_i, buyer = next_holder()
        if buyer is None:
            say("нет чистого кошелька для повторной покупки — пропускаю")
            return False
        buyer_name = f"W{buyer_i}"
        phase("Покупаю", f"{sym}: повторный заход, ставку переводим на {buyer_name}")
        need = Web3.to_wei(stake, "ether") + chain_need() + int(B.CFG["gas_limit_buy"] * A.gas_price * 1.3)
        if not fund(buyer_i, need, tr):
            say(f"ставка на {buyer_name} не дошла — сделка отменена")
            return False
        say(f"{sym}: A уже был на этой монете — покупает чистый {buyer_name}")
    crumbs = []
    if SET["shield"]:
        raise_shield(tr, tok, token, sym, marked, crumbs, t_seen)
        if S["stop_after"]:
            say(f"{sym}: стоп до покупки — отменено (приманки уже куплены)")
            record(token, sym, stake, 0, 0, "отменено после приманок", stake, tr.gas + crumb_gas(crumbs))
            return False
    phase("Покупаю", sym)
    mark_used(token, buyer_name)
    DEAL.clear()
    try:
        DEAL.update(kind=kind, buy_age_s=round(time.time() - t_seen, 1), pool_buy_bnb=round(pool.r_bnb / 1e18, 4),
                    tokens_quoted=pool.quote_buy(Web3.to_wei(stake, "ether")), addrs=[(buyer_name, buyer.address)], holds=[])
    except Exception:
        pass
    try:
        rc = tr.tx(buyer, buyer.buy(token, Web3.to_wei(stake, "ether"), 0))
        block_later("buy_block")
    except Exception as e:
        say(f"покупка не отправлена: {str(e)[:80]}")
        rc = {"status": 0}
    bought = retry(lambda: tok.functions.balanceOf(buyer.address).call()) > 0
    if not bought:
        if buyer is not A:  # the stake went to the buyer: bring it back
            left = w3.eth.get_balance(buyer.address) - 21_000 * A.gas_price - chain_need()
            if left > 0:
                tr.tx(buyer, buyer.send(A.address, b"", value=left, gas=21_000))
        record(token, sym, stake, 0, 0, "покупка не прошла", stake, tr.gas + crumb_gas(crumbs))
        return False
    try:
        S["hold_now"], S["launch_t"], S["kind"] = hold, t_seen, kind
        say(f"{sym}: пул {pool.r_bnb / 1e18:.1f} BNB — тип «{kind} BNB», ориентир {hold / 60:.1f} мин"
            + (f" (ожидается {edge:+.0f}% после газа, расчёт)" if SET["hold_auto"] else ""))
        CUR.clear()
        save_current(token=token, pair=pair, sym=sym, stake=stake, t_buy=time.time(), hold=hold, holder_i=buyer_i,
                     hops=0, gas=tr.gas, launch_t=t_seen, kind=kind)
        chain_hold_and_sell(tr, tok, token, sym, stake, pool, marked, next_holder, nxt_i, nxt, rc, crumbs,
                            buyer, buyer_i, buyer_name)
    except Exception as e:  # never leave a trade unrecorded: the tokens sit on the last holder
        say("ошибка во время сделки: " + str(e)[:150])
        record(token, sym, stake, S["hops"], S["held"], "ошибка во время сделки", 0.0, tr.gas + crumb_gas(crumbs),
               f"токены на {S['holder'] or 'A'}: {str(e)[:60]}")
    finally:
        clear_current()
    return True


def crumb_gas(crumbs):
    """Gas of the crumb transfers (not waited for one by one during the trade), BNB."""
    total = 0.0
    for snd, h in crumbs:
        try:
            total += B.gas_cost(w3.eth.get_transaction_receipt(h))
        except Exception:
            total += 63_500 * chain_price() / 1e18
    return total


def chain_hold_and_sell(tr, tok, token, sym, stake, pool, marked, next_holder, nxt_i, nxt, rc, crumbs,
                        buyer, buyer_i, buyer_name, hops0=0):
    t0 = time.time()
    h, h_i, name, hops = buyer, buyer_i, buyer_name, hops0
    amt = received(rc, token, h.address) or retry(lambda: tok.functions.balanceOf(h.address).call())
    DEAL["tokens_bought"] = amt
    say((f"продолжаю {sym} с {name} после перезапуска" if hops0 else f"куплено {sym} на {stake} BNB")
        + (f" — цепочка: передача каждые ~{CHAIN_FAST_EVERY} с всю сделку, без крошек" if SET["all_fast"] else
           f" — цепочка: {CHAIN_K} крошек + передача сразу, первую {CHAIN_FAST_S} с каждые {CHAIN_FAST_EVERY} с и по {CHAIN_FAST_K} крошке,"
           f" дальше каждые {CHAIN_EVERY} с"))
    px = {"value": stake, "amt": amt, "stop": False, "bnb": None, "peak": 0.0, "hist": deque(), "flow": 0.0}

    def pricer():
        while not px["stop"]:
            try:
                pool.refresh()
                px["value"] = pool.quote_sell(px["amt"])
                now, bnb = time.time(), pool.r_bnb / 1e18
                px["bnb"], px["peak"] = bnb, max(px["peak"], bnb)
                px["hist"].append((now, bnb))
                while now - px["hist"][0][0] > FLOW_WIN:
                    px["hist"].popleft()
                px["flow"] = (bnb / px["hist"][0][1] - 1) * 100  # % of pool BNB bought in the last FLOW_WIN s
            except Exception:
                pass
            time.sleep(0.7)
    threading.Thread(target=pricer, daemon=True).start()
    watch = {"addr": None if buyer is A else buyer.address, "hit": None}

    def guard_watch():
        """29.10 DINAR: the holder's mark check ran in the trade loop, and one slow node call (up to 10 s) held up
        the hand-over. Now it runs here; the loop only reads the result."""
        while not px["stop"]:
            a = watch["addr"]
            try:
                if a and marked(a):
                    watch["hit"] = a
            except Exception:
                pass
            time.sleep(1)
    threading.Thread(target=guard_watch, daemon=True).start()

    def gas_watch():
        """05.11 review: A.gas_price was read once before the buy and stayed for the whole trade."""
        while not px["stop"]:
            try:
                A.refresh_gas_price()
            except Exception:
                pass
            time.sleep(5)
    threading.Thread(target=gas_watch, daemon=True).start()
    bots = BotCounter(pool.pair, token, S.get("launch_t", t0)) if SET["bot_exit"] else None
    t_hold, t_check, reason = t0 - CHAIN_EVERY, 0.0, ""
    fast = lambda: SET["all_fast"] or time.time() - t0 < CHAIN_FAST_S
    seller = {}
    if SET["all_fast"] and hasattr(next_holder, "feed"):  # 29.10: bare hand-overs for the whole trade
        threading.Thread(target=next_holder.feed, args=(lambda: px["stop"],), daemon=True).start()
        threading.Thread(target=prepare_seller, args=(token, next_holder, tr, seller), daemon=True).start()
    every = lambda: CHAIN_FAST_EVERY if fast() else CHAIN_EVERY
    slow_started, t_search = False, 0.0
    try:
        while True:
            value = px["value"]
            pnl = (value / stake - 1) * 100
            held = time.time() - t0
            S.update(holder=name, hops=hops, value=value, pnl_pct=round(pnl, 1), held=round(held, 1))
            phase("Держу и перекидываю", f"{sym} на {name}, {pnl:+.0f}%, {held:.0f} из {S['hold_now']:.0f} с, "
                                         f"передача через {max(0, every() - (time.time() - t_hold)):.0f} с")
            kind = S.get("kind", 25)
            age = time.time() - S.get("launch_t", t0)
            patient = SET["patient"] and kind in PATIENT_TYPES
            drain = px["bnb"] is not None and px["peak"] and px["bnb"] < px["peak"] * (1 - DRAIN_DROP)
            bot_done = bots is not None and bots.count >= BOT_MIN[kind] and bots.silence() >= BOT_SILENCE
            if bots is not None:  # the counter decides; the planned minute does not sell
                reason = ("цель +%g%%" % SET["tp"] if pnl >= SET["tp"]
                          else "пул падает: %.2f BNB при пике %.2f" % (px["bnb"], px["peak"]) if drain
                          else "боты закончили: %d покупок, тишина %.0f с" % (bots.count, bots.silence()) if bot_done
                          else "предел %d мин от запуска" % (BOT_BACKSTOP[kind] / 60) if age >= BOT_BACKSTOP[kind]
                          else "продать сейчас" if S["sell_now"] else "")
                if reason:
                    break
                phase("Держу и перекидываю", f"{sym} на {name}, {pnl:+.0f}%, {held:.0f} с; боты: {bots.count} из "
                                             f"~{BOT_MIN[kind]}, тишина {bots.silence():.0f} с; передача через "
                                             f"{max(0, every() - (time.time() - t_hold)):.0f} с")
            reason = "" if bots is not None else ("цель +%g%%" % SET["tp"] if pnl >= SET["tp"]
                      else "пул падает: %.2f BNB при пике %.2f" % (px["bnb"], px["peak"]) if patient and drain
                      else "стоп −%g%%" % SET["sl"] if not patient and pnl <= -SET["sl"]
                      else "предел %d мин от запуска" % (HARD_MAX[kind] / 60) if patient and age >= HARD_MAX[kind]
                      else "время %g с" % S["hold_now"] if held >= S["hold_now"]
                      and (not patient or pnl >= SELL_MIN_PNL and px["flow"] < FLOW_PCT)
                      else "продать сейчас" if S["sell_now"] else "")
            if reason:
                break
            if bots is None and patient and held >= S["hold_now"]:
                why = (f"мало покупок ({pnl:+.0f}%) — жду" if pnl < SELL_MIN_PNL
                       else f"покупают (+{px['flow']:.0f}% BNB за {FLOW_WIN} с) — держу")
                phase("Держу и перекидываю", f"{sym} на {name}, {why} (предел {HARD_MAX[kind] / 60:.0f} мин"
                                             f" от запуска, сейчас {age / 60:.1f})")
            if h is not A and watch["hit"] == h.address:  # the guard got the holder: nothing left but to record
                return record(token, sym, stake, hops, held, f"застряли на {name}", 0.0,
                              tr.gas + crumb_gas(crumbs), "сторож пометил держателя")
            if time.time() - t_hold < every():
                time.sleep(0.2)
                continue
            if nxt is None:
                try:
                    nxt_i, nxt = next_holder()
                except Exception as e:
                    say(f"следующий держатель не найден ({str(e)[:60]}) — продаю")
                if nxt is None:
                    reason = "кончились кошельки"
                    break
            mark_used(token, f"W{nxt_i}")
            crumb = max(1, amt // CHAIN_CRUMB)
            try:
                # 28.10 guard_shift: holders were only ever caught by the first marks of a trade (17-21 blocks after
                # receipt); a holder kept <15 blocks was never caught. So for CHAIN_FAST_S s bare hand-overs every
                # CHAIN_FAST_EVERY s, then the first slow holder stands behind a full CHAIN_K queue of crumbs.
                if SET["all_fast"]:  # 29.10 ABI: the guard marks the biggest holder; crumbs no longer cover anyone
                    k = 0
                elif fast() and hops > 0:
                    # 29.10 SKUUB: with no crumbs in the fast minute the guard's queue was empty, and the first slow
                    # holder (got the coin in the same block as its 5 crumbs) was marked first, 18 blocks after receipt.
                    # One crumb per fast hand-over leaves ~25 OLDER crumbs ahead of it by the end of the minute.
                    k = CHAIN_FAST_K
                elif not fast() and not slow_started:
                    k, slow_started = CHAIN_K, True
                else:
                    k = crumbs_for(hops + 1, SET["chain_lean"])
                for _ in range(k):  # fresh addresses nobody holds the key of: the crumbs there are gone
                    to = w3.eth.account.create().address
                    crumbs.append((h, h.send(token, tok.encode_abi("transfer", args=[to, crumb]), gas=HOP_LIMIT,
                                             gas_price=chain_price())))
                hb = h.send(token, tok.encode_abi("transfer", args=[nxt.address, amt - crumb * k]),
                            gas=HOP_LIMIT, gas_price=chain_price())
            except Exception as e:  # no node took a transfer: try the whole burst again in a moment
                say(f"передача {name} → W{nxt_i} не отправлена ({str(e)[:60]}) — повторяю")
                # 29.10 review: all_fast holders get gas for ONE transfer at the price of the moment; if the network
                # price rose since (A.gas_price is re-read every 30 s), the node refuses it for want of gas and the
                # retry would refuse forever while the holder keeps the coin. Top the holder up first.
                if h_i is not None:
                    try:
                        if w3.eth.get_balance(h.address) < HOP_LIMIT * chain_price() * 1.05:
                            fund(h_i, int(HOP_LIMIT * chain_price() * 1.5), tr)
                    except Exception:
                        pass
                time.sleep(0.3)
                continue
            t_sent = time.time()
            rc = tr.tx(h, hb) or tr.tx(h, hb, 40)  # 05.11 review: 20 s without a receipt is not a lost coin
            ok = bool(rc and rc["status"] == 1)
            held_by = t_sent - t_hold if hops else 0.0  # s this holder kept the coin before we sent it on
            hop_timing(sym, hops + 1, name, f"W{nxt_i}", held_by, time.time() - t_sent, ok)
            if hops and fast() and held_by + (time.time() - t_sent) > 5:
                say(f"{sym}: {name} держал {held_by + time.time() - t_sent:.1f} с в быстрой минуте (поиск "
                    f"{t_search:.1f} с, подтверждение {time.time() - t_sent:.1f} с)")
            if not ok:
                why = "сторож пометил до передачи" if rc else "передача не подтвердилась за 20 с"
                return record(token, sym, stake, hops, time.time() - t0, f"застряли на {name}", 0.0,
                              tr.gas + crumb_gas(crumbs), why)
            DEAL.setdefault("holds", []).append(round(held_by + time.time() - t_sent, 2))
            DEAL.setdefault("addrs", []).append((f"W{nxt_i}", nxt.address))
            h, h_i, name, hops, t_hold = nxt, nxt_i, f"W{nxt_i}", hops + 1, time.time()
            watch["addr"] = h.address
            save_current(holder_i=h_i, hops=hops, gas=tr.gas)
            amt = px["amt"] = received(rc, token, h.address) or retry(lambda: tok.functions.balanceOf(h.address).call())
            t_search = time.time()
            try:
                nxt_i, nxt = next_holder()  # the one after, while this holder waits its CHAIN_EVERY s
            except Exception:
                nxt_i, nxt = None, None  # looked for again at the next hand-over
            t_search = time.time() - t_search
    finally:
        px["stop"] = True
        if bots is not None:
            bots.stop = True

    phase("Продаю", f"{sym} с {name}")
    DEAL.update(quote_bnb=px["value"], pool_sell_bnb=px["bnb"], bots=bots.count if bots is not None else "")
    ready_seller = False
    if seller.get("w") is not None and h is not seller["w"]:  # all_fast: the coin goes to the approved seller
        s_i, s_w = seller["i"], seller["w"]
        mark_used(token, f"W{s_i}")
        try:
            hs = h.send(token, tok.encode_abi("transfer", args=[s_w.address, amt]), gas=HOP_LIMIT,
                        gas_price=chain_price())
            rc = tr.tx(h, hs) or tr.tx(h, hs, 40)
        except Exception as e:
            say(f"передача продавцу W{s_i} не отправлена: {str(e)[:60]}")
            rc = None
        if rc and rc["status"] == 1:
            DEAL.setdefault("addrs", []).append((f"W{s_i}", s_w.address))
            h, h_i, name, hops, ready_seller = s_w, s_i, f"W{s_i}", hops + 1, True
            save_current(holder_i=h_i, hops=hops, gas=tr.gas)
            phase("Продаю", f"{sym} с {name} (продавец готов заранее)")
    if not ready_seller:
        if h_i is not None and not fund(h_i, seller_gas(), tr):
            say(f"газ на продажу с {name} не дошёл — пробую так")
        h.gas_price = A.gas_price
        rc = tr.tx(h, h.approve(token))  # the holder that sells approves the router now (it never held this token)
        if not rc or rc["status"] != 1:  # 28.10 review: once more — without it every sell probe below fails
            say(f"approve с {name} не прошёл — повторяю")
            try:
                rc = tr.tx(h, h.approve(token))
            except Exception as e:
                say(f"approve с {name} снова не отправлен: {str(e)[:60]}")
    amt = retry(lambda: tok.functions.balanceOf(h.address).call())
    DEAL["tokens_sold"] = amt
    got, t_sell = 0.0, time.time()
    while not got and time.time() - t_sell < 20:  # 28.10 review: 20 s, not 8 — a slow node must not cost the stake
        data = B.router.encode_abi("swapExactTokensForETHSupportingFeeOnTransferTokens",
                                   args=[amt, 0, [token, B.WBNB], h.address, int(time.time()) + 120])
        try:
            w3.eth.call({"from": h.address, "to": B.ROUTER, "data": data})
        except Exception as e:
            say(f"проба продажи не прошла: {str(e)[:80]}")
            time.sleep(0.5)
            continue
        try:
            hs = h.sell(token, amt, 0, gas_price=chain_price())
            rc = tr.tx(h, hs) or tr.tx(h, hs, 40)
        except Exception as e:
            say(f"продажа не отправлена: {str(e)[:80]}")
            rc = None
        if rc and rc["status"] == 1:
            got = B.bnb_received(rc)
            block_later("sell_block")
    held = time.time() - t0
    gas = crumb_gas(crumbs)
    if not got:
        return record(token, sym, stake, hops, held, f"{reason}: продажа не прошла", 0.0, tr.gas + gas,
                      f"токены на {name}")
    note = ""
    if h is not A:
        rc = tr.tx(h, h.send(A.address, b"", value=int(got * 1e18), gas=21_000))
        note = "" if rc and rc["status"] == 1 else f"BNB остались на {name}"
    record(token, sym, stake, hops, held, reason, got, tr.gas + gas, note)


def hold_and_sell(tr, tok, token, sym, stake, pool, seller_i, seller, marked, next_hop):
    """27.10 11:19 the guard marked a holder 3.1 s after it got the tokens (it used to take 6.8 s+), so a hop must be
    as short as possible: the price is read by a side thread, the next wallet is chosen (mark check, gas) while the
    current transfer confirms, and the dry run is gone (a transfer from a marked holder just fails on chain)."""
    t0 = t_recv = time.time()
    holder, holder_name, hops = A, "A", 0
    amt = retry(lambda: tok.functions.balanceOf(A.address).call())
    say(f"куплено {sym} на {stake} BNB")
    px = {"value": stake, "amt": amt, "stop": False}

    def pricer():
        while not px["stop"]:
            try:
                pool.refresh()
                px["value"] = pool.quote_sell(px["amt"])
            except Exception:
                pass
            time.sleep(0.7)
    threading.Thread(target=pricer, daemon=True).start()
    ex = ThreadPoolExecutor(1)
    nxt = ex.submit(next_hop)
    tick = tick_on()  # hop (and hand to the seller) only in the window before a guard tick
    due = next_tick(head_now() + TICK_LEAD) if tick else None
    if tick:
        say(f"{sym}: перекиды по таймеру сторожа, первый у блока {due}")
    try:
        while True:
            value = px["value"]
            pnl = (value / stake - 1) * 100
            held = time.time() - t0
            S.update(holder=holder_name, hops=hops, value=value, pnl_pct=round(pnl, 1), held=round(held, 1))
            reason = ("цель +%g%%" % SET["tp"] if pnl >= SET["tp"] else "стоп −%g%%" % SET["sl"] if pnl <= -SET["sl"]
                      else "время %g с" % SET["hold"] if held >= SET["hold"] else "продать сейчас" if S["sell_now"]
                      else "")
            if tick:
                head = head_now()
                left = (due - TICK_LEAD - head) * SPB
                phase("Держу и перекидываю", f"{sym} на {holder_name}, {pnl:+.0f}%, {held:.0f} с, "
                                             f"{'продам' if reason else 'перекид'} через {max(0, left):.1f} с")
                if left > 0:  # the seller too: a hand-over just before a guard read would get it marked
                    time.sleep(0.05)
                    continue
            else:
                phase("Держу и перекидываю", f"{sym} на {holder_name}, {pnl:+.0f}%, {held:.0f} с")
            if not tick and not reason and time.time() - t_recv < SET["every"] - 1.0:  # the transfer takes ~1 s
                time.sleep(0.05)
                continue
            target_i, target = (seller_i, seller) if reason else nxt.result()
            if target is None:
                reason, target_i, target = "кончились кошельки", seller_i, seller
            name = f"W{target_i}"
            mark_used(token, name)
            t_send = time.time()
            h = holder.send(token, tok.encode_abi("transfer", args=[target.address, amt]), gas=HOP_LIMIT,
                            gas_price=hop_price())
            if target is not seller:
                nxt = ex.submit(next_hop)  # the wallet after this one, while the transfer confirms
            rc = tr.tx(holder, h)
            t_conf = time.time()
            ok = bool(rc and rc["status"] == 1) or retry(lambda: tok.functions.balanceOf(target.address).call()) > 0
            hop_timing(sym, hops + 1, holder_name, name, t_send - t_recv, t_conf - t_send, ok)
            if tick and rc:  # bot.parse_raw_receipt() has no blockNumber (13:11 NOKc, 13:14 QUBTc crashed here)
                try:
                    landed = w3.eth.get_transaction(h)["blockNumber"]
                except Exception:
                    landed = head_now()
                tick_log(sym, hops + 1, name, due, head, landed, ok)
                due = next_tick(landed + TICK_LEAD)
            if not ok:
                why = "сторож пометил до перевода" if rc else "перевод не подтвердился за 20 с"
                return record(token, sym, stake, hops, held, f"застряли на {holder_name}", 0.0, tr.gas, why)
            holder, holder_name, t_recv, hops = target, name, t_conf, hops + 1
            # the amount for the next hop comes from the receipt (a transfer tax lowers it): a balance read here kept
            # W3 on ASTSc ~5 s longer when the node was slow
            amt = px["amt"] = received(rc, token, holder.address) or retry(
                lambda: tok.functions.balanceOf(holder.address).call())
            if target is seller:
                break
    finally:
        px["stop"] = True
        ex.shutdown(wait=False)

    phase("Продаю", sym)
    amt = retry(lambda: tok.functions.balanceOf(seller.address).call())
    got = 0.0
    t_sell = time.time()
    while not got and time.time() - t_sell < 5:
        data = B.router.encode_abi("swapExactTokensForETHSupportingFeeOnTransferTokens",
                                   args=[amt, 0, [token, B.WBNB], seller.address, int(time.time()) + 120])
        try:
            w3.eth.call({"from": seller.address, "to": B.ROUTER, "data": data})
        except Exception as e:
            say(f"проба продажи не прошла: {str(e)[:80]}")
            time.sleep(0.5)
            continue
        try:
            rc = tr.tx(seller, seller.sell(token, amt, 0, gas_price=hop_price()))
        except Exception as e:
            say(f"продажа не отправлена: {str(e)[:80]}")
            rc = None
        if rc and rc["status"] == 1:
            got = B.bnb_received(rc)
    held = time.time() - t0
    if not got:
        return record(token, sym, stake, hops, held, f"{reason}: продажа не прошла", 0.0, tr.gas, f"токены на W{seller_i}")
    rc = tr.tx(seller, seller.send(A.address, b"", value=int(got * 1e18), gas=21_000))
    note = "" if rc and rc["status"] == 1 else f"BNB остались на W{seller_i}"
    record(token, sym, stake, hops, held, reason, got, tr.gas, note)


DECOY_N, DECOY_BNB, DECOY_GAP, DECOY_WATCH, DECOY_GAS = 8, 0.00001, 0.3, 180, 250_000  # 8: 27.10 (user)
# 27.10 14:45 (user): the decoys buy at once (0.3 s apart); "main" buys only when the guard's nonce grows, i.e. right
# after the first mark of the cycle that found the decoys. If the guard reads the holders once and then marks them one
# by one (~5 s each) and only after that waits ~15 s, "main" is not in this cycle and lives ~N*5 + 15 s.
DECOY_MAIN_WAIT = 60  # s without a guard mark after the decoys: "main" buys anyway (noted in the role)
DECOY_FILE = os.path.join(HERE, "decoy_test.csv")


def decoy_run():
    """27.10 (user): the decoy test, one fresh guard coin. DECOY_N decoys buy dust DECOY_GAP s apart, then a "main"
    wallet buys dust too; for DECOY_WATCH s every wallet is checked for the guard's mark. Answers: does the guard mark
    one holder per tick, in which order (first/last buyer), and does "main" live while the decoys are marked.
    ~0.0001 BNB in all; the dust stays on the marked wallets."""
    if isinstance(S["decoy"], str):  # a coin given in the panel (27.10: MAX, 7 min old), not the next launch
        tk = Web3.to_checksum_address(S["decoy"])
        return decoy_test(B.FACTORY.functions.getPair(tk, B.WBNB).call(), tk, time.time() - SET["entry"])
    phase("Тест приманок", "жду новую монету со сторожем")
    say("ТЕСТ ПРИМАНОК: жду новую монету со сторожем")
    found = wait_launch("decoy")
    if found:
        return decoy_test(*found)
    say("тест приманок отменён")


def wait_launch(flag):
    """The next new guard pool, while S[flag] stays set: (pair, token, seen time) or None."""
    last = w3.eth.block_number
    while S[flag]:
        time.sleep(1)
        try:
            head = w3.eth.block_number
            if head <= last:
                continue
            logs = w3.eth.get_logs({"address": B.FACTORY.address, "topics": [B.PAIR_CREATED_TOPIC],
                                    "fromBlock": last + 1, "toBlock": head})
            last = head
        except Exception:
            continue
        for lg in logs:
            r = B.parse_pair_created(lg)
            if r and is_guard(r[1]):
                return r[0], r[1], time.time()
    return None


def decoy_test(pair, token, t_seen):
    tok = w3.eth.contract(token, abi=TOK_ABI)
    try:
        sym = tok.functions.symbol().call()[:20]
    except Exception:
        sym = token[:8]
    marked = lambda a: retry(lambda: tok.functions.allowance(GUARD, a).call()) > 0
    say(f"тест приманок: {sym} {token}")
    ids = list(itertools.islice((i for i in range(95, 0, -1) if not used(token, f"W{i}")
                                 and not marked(wallet(i).address)), DECOY_N + 1))
    A.refresh_gas_price()
    price = A.gas_price
    need = int(DECOY_GAS * price * 1.2) + int(DECOY_BNB * 1e18)
    for k, i in enumerate(ids):
        if not S["decoy"]:
            return say("тест приманок отменён")
        phase("Тест приманок", f"{sym}: газ и пыль на W{i} ({k + 1} из {len(ids)})")
        if not fund(i, need):
            return say(f"тест приманок: W{i} не получил BNB — отменён")
    while time.time() - t_seen < SET["entry"]:
        if not S["decoy"]:
            return say("тест приманок отменён")
        phase("Тест приманок", f"{sym}: покупки через {SET['entry'] - (time.time() - t_seen):.0f} с")
        time.sleep(0.3)
    pool = B.Pool(pair, token, sym, 0, 0)
    pool.refresh()
    if pool.r_bnb / 1e18 < SET["min_pool"]:
        say(f"тест приманок: в пуле {pool.r_bnb / 1e18:.1f} BNB < {SET['min_pool']}")
        if isinstance(S["decoy"], str):
            return None
        return decoy_run()
    rows = []
    nonce0 = None
    for k, i in enumerate(ids):
        if k == DECOY_N:  # "main": wait for the guard's first mark after the decoys
            phase("Тест приманок", f"{sym}: жду первую метку сторожа, потом покупает основной")
            t_wait, role_note = time.time(), "не дождался метки"
            while time.time() - t_wait < DECOY_MAIN_WAIT and S["decoy"]:
                try:
                    if w3.eth.get_transaction_count(GUARD) > nonce0:
                        role_note = f"после метки, ждал {time.time() - t_wait:.1f} с"
                        break
                except Exception:
                    pass
                time.sleep(0.1)
        w = wallet(i)
        w.gas_price = price
        mark_used(token, f"W{i}")
        data = B.router.encode_abi("swapExactETHForTokensSupportingFeeOnTransferTokens",
                                   args=[0, [B.WBNB, token], w.address, int(time.time()) + 60])
        role = f"приманка {k + 1}" if k < DECOY_N else f"основной ({role_note})"
        phase("Тест приманок", f"{sym}: покупает {role} (W{i})")
        rows.append(dict(role=role, name=f"W{i}", w=w, h=w.send(B.ROUTER, data, value=int(DECOY_BNB * 1e18),
                                                                       gas=DECOY_GAS), buy=None, mark=None))
        if k == 0:
            nonce0 = retry(lambda: w3.eth.get_transaction_count(GUARD))
        if k < DECOY_N - 1:
            time.sleep(DECOY_GAP)
    for r in rows:
        rc = wait(r["w"], r["h"])
        if rc and rc["status"] == 1:
            try:
                r["buy"] = w3.eth.get_transaction(r["h"])["blockNumber"]
            except Exception:
                r["buy"] = head_now()
        else:
            say(f"тест приманок: покупка {r['role']} {r['name']} не прошла")
    t_end = time.time() + DECOY_WATCH
    while time.time() < t_end and S["decoy"]:
        head = head_now()
        for r in rows:
            if r["buy"] and r["mark"] is None and marked(r["w"].address):
                r["mark"] = head  # first head that shows the mark; refined from guard_marks.csv below
                say(f"тест приманок: сторож пометил {r['role']} {r['name']} (купил в {r['buy']}, метка ~{head})")
        done = sum(1 for r in rows if r["mark"] or not r["buy"])
        phase("Тест приманок", f"{sym}: помечено {done} из {len(rows)}, осталось {t_end - time.time():.0f} с")
        if done == len(rows):
            break
        time.sleep(0.4)
    time.sleep(8)  # guard_gas() has written the exact mark blocks by now
    try:
        exact = {(m["token"].lower(), m["wallet"].lower()): int(m["block"]) for m in
                 csv.DictReader(open(MARKS, encoding="utf-8-sig"), delimiter=";") if m["method"] == "095ea7b3"}
    except Exception:
        exact = {}
    for r in rows:
        r["mark"] = exact.get((token.lower(), r["w"].address.lower()), r["mark"])
    new = not os.path.exists(DECOY_FILE)
    with open(DECOY_FILE, "a", newline="", encoding="utf-8-sig") as f:
        wr = csv.writer(f, delimiter=";")
        if new:
            wr.writerow(["time", "token", "symbol", "role", "wallet", "address", "buy_block", "mark_block",
                         "mark_after_buy"])
        for r in rows:
            wr.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), token, sym, r["role"], r["name"], r["w"].address,
                         r["buy"], r["mark"], r["mark"] - r["buy"] if r["mark"] and r["buy"] else ""])
    order = sorted((r for r in rows if r["mark"]), key=lambda r: r["mark"])
    text = ", ".join(f"{r['role']}@{r['mark']}" for r in order) or "никого"
    main = rows[-1]
    last_decoy = max((r["mark"] for r in rows[:-1] if r["mark"]), default=None)
    res = (f"ИТОГ ТЕСТА ПРИМАНОК {sym}: порядок меток: {text}. {main['role'].capitalize()}: "
           + (f"помечен {order.index(main) + 1}-м, через {main['mark'] - main['buy']} блоков после покупки"
              + (f", через {main['mark'] - last_decoy} после последней приманки" if last_decoy else "")
              if main["mark"] and main["buy"] else f"НЕ помечен за {DECOY_WATCH} с"))
    say(res)
    tg("🧪 " + res)


# fast start (28.10, user; mark_order.py): in a new pass the guard marks the biggest holder first, ~17-21 blocks after
# it got the coin (NOKc, CRML, BNQ: W1 stuck at the 15 s hand-over). The first CHAIN_FAST holders keep it CHAIN_FAST_EVERY s.
CHAIN_FAST, CHAIN_FAST_EVERY = 4, 1  # 29.10 (user): 1 s, not 2 — a holder keeps ~4-5 blocks; the fastest mark seen came 7 blocks after receipt
CHAIN_FAST_K = 1  # crumbs per hand-over in the fast minute (29.10 SKUUB)
CHAIN_FAST_S = 60  # 28.10: fast bare hand-overs for the first minute after the buy (see chain_hold_and_sell)
CHAIN_K, CHAIN_EVERY, CHAIN_HOLD, CHAIN_CRUMB = 5, 15, 300, 10_000  # 27.10 15:55 (user): back to 5 crumbs  # crumbs per cycle, s per holder, s, 1/share
# chain_lean (27.10 19:15, user: "too much gas"): the guard marks ~1 address in 5-10 s and is still marking our crumbs
# 10-20 min after a trade, so after LEAN_WARM cycles of CHAIN_K crumbs the backlog is long enough and LEAN_K crumbs a
# cycle keep it: about -47 % gas. The chain test always runs lean; a trade only with the chain_lean setting.
LEAN_WARM, LEAN_K = 3, 2
CHAIN_PREFUND = 30  # 29.10: 30 (1 s hand-overs: ~25 holders in the first minute); wallets filled with gas before a coin (the rest on the way). 28.10: 20, not 5 — in the first
# CHAIN_FAST_S s a hand-over comes every ~3.5 s (~17 holders); a holder topped up on the way waits for that top-up
SHIELD_N, SHIELD_WAIT, SHIELD_BNB, SHIELD_GAS = 8, 45, 0.00001, 250_000  # decoys, s to wait for the guard, dust, gas
SWEEP_MIN = 0.000005  # BNB: below this a wallet is not swept back to A (the transfer costs ~0.000001)


def crumbs_for(cycle, lean):
    """Crumbs to send in hand-over number `cycle` (1, 2, ...)."""
    return CHAIN_K if not lean or cycle <= LEAN_WARM else LEAN_K
CHAIN_FILE = os.path.join(HERE, "chain_test.csv")
# 27.10 (user): the decoy tests showed the guard marks one new holder every ~5 s, about in the order they got the
# tokens. A holder that gets the tokens just after CHAIN_K fresh crumb receivers stands behind them: ~K*5 s. So every
# CHAIN_EVERY s the holder sends 1/CHAIN_CRUMB of the tokens to each of CHAIN_K new random addresses (no keys, no gas
# needed there) and, in the same burst (next nonce, same block), everything else to the next holder, our clean W.
# The test buys only dust (DECOY_BNB) and holds CHAIN_HOLD s; it does not sell (the dust is worth less than the gas).


CATCH_N, CATCH_PER_COIN, CATCH_STAKE = 10, 3, 0.001  # 05.11 (user): real test of "catch one bot buy"; any coin type (user: it tests the selling)
CATCH_MAX_BOTS = {1: 5, 10: 18, 25: 70}  # the dump comes ~3.5 min after the last bot buy: not near the end
CATCH_AGE = {1: 6 * 60, 10: 12 * 60, 25: 45 * 60}  # s after the launch
CATCH_W, CATCH_SEND, CATCH_HOLD = 51, 2, 5  # blocks: land 23 s after a bot buy, sent 2 blocks ahead, sold ~2 s after landing
CATCH_FILE = os.path.join(HERE, "catch_test.csv")


class FastBots:
    """BotCounter for the catch test: reads the new blocks about 4 times a second, keeps the block of the last buy."""

    def __init__(self, pair, token, launch_t):
        self.pair = Web3.to_checksum_address(pair)
        self.wbnb_is0 = int(Web3.to_checksum_address(B.WBNB), 16) < int(Web3.to_checksum_address(token), 16)
        self.count, self.last, self.stop = 0, None, False
        self.next = w3.eth.block_number - int((time.time() - launch_t) / SPB) - 60
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        ours = our_addrs()
        while not self.stop:
            try:
                head = w3.eth.block_number
                while head >= self.next:  # a coin already trading: its past comes in pieces, the node gives ~2000 blocks a call
                    to = min(head, self.next + 1999)
                    for lg in w3.eth.get_logs({"address": self.pair, "fromBlock": self.next, "toBlock": to, "topics": [SWAP_TOPIC]}):
                        d = bytes(lg["data"])
                        if not int.from_bytes(d[0:32] if self.wbnb_is0 else d[32:64], "big"):
                            continue
                        frm = retry(lambda: w3.eth.get_transaction(lg["transactionHash"])["from"]).lower()
                        if frm in ours or frm.startswith(DUMP_MARKER):
                            continue
                        self.count += 1
                        self.last = lg["blockNumber"]
                    self.next = to + 1
            except Exception:
                pass
            time.sleep(0.25)


def catch_row(token, sym, n, before, gap, hit, held, got, gas, why):
    new = not os.path.exists(CATCH_FILE)
    with open(CATCH_FILE, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        if new:
            w.writerow(["time", "token", "symbol", "attempt", "bots_before", "gap_blocks", "hit", "held_s", "stake", "got_bnb", "gas_bnb", "pnl_bnb", "note"])
        w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), token, sym, n, before, gap, int(hit), round(held, 2), CATCH_STAKE, f"{got:.8f}", f"{gas:.8f}",
                    f"{got - CATCH_STAKE - gas:.8f}", why])


def catch_back(w, name):
    """Everything left on the wallet goes back to A."""
    try:
        left = w3.eth.get_balance(w.address) - 21_000 * chain_price()
        if left > 0:
            wait(w, w.send(A.address, b"", value=left, gas=21_000, gas_price=chain_price()))
    except Exception as e:
        say(f"тест ловли: {name} не вернул BNB ({str(e)[:60]})")


def catch_live():
    """The network's newest coin from guard_launches.csv: (pair, token, launch time), or None."""
    try:
        with open(os.path.join(HERE, "guard_launches.csv"), encoding="utf-8-sig") as f:
            r = list(csv.DictReader(f, delimiter=";"))[-1]
        return Web3.to_checksum_address(r["pair"]), Web3.to_checksum_address(r["token"]), float(r["ts"])
    except Exception:
        return None


def catch_test():
    say(f"ТЕСТ ЛОВЛИ ПОКУПКИ: до {CATCH_N} попыток по {CATCH_STAKE} BNB, монета любого типа")
    done, S["run_pnl"] = 0, 0.0
    tried = set()
    while S["chain"] and done < CATCH_N:
        phase("Тест ловли покупки", f"сделано попыток {done} из {CATCH_N}, ищу монету")
        found = catch_live()
        if not found or found[1].lower() in tried:
            found = wait_launch("chain")
        if not found:
            break
        tried.add(found[1].lower())
        first = start_bnb(found[0], found[1], found[2])
        kind = coin_type(first) if first is not None else None
        if kind is None or time.time() - found[2] >= CATCH_AGE[kind]:
            say(f"тест ловли: {found[1][:10]} поздно или нет данных о пуле, жду следующую")
            continue
        n, stuck = catch_coin(*found, done, kind)
        done += n
        if stuck:
            say("ТЕСТ ЛОВЛИ ОСТАНОВЛЕН: кошелёк заблокирован, ставка потеряна")
            break
    res = f"ИТОГ ТЕСТА ЛОВЛИ: попыток {done}, результат {S['run_pnl']:+.6f} BNB ({S['run_pnl'] * S['bnb_usd']:+.2f} $), подробно catch_test.csv"
    say(res)
    tg("🧪 " + res)


def catch_coin(pair, token, t_seen, done, kind):
    tok = w3.eth.contract(token, abi=TOK_ABI)
    try:
        sym = tok.functions.symbol().call()[:20]
    except Exception:
        sym = token[:8]
    say(f"тест ловли: {sym} {token}, тип {kind} BNB")
    A.refresh_gas_price()
    bots, pool = FastBots(pair, token, t_seen), B.Pool(pair, token, sym, 0, 0)
    wei = Web3.to_wei(CATCH_STAKE, "ether")
    head = lambda: max(HEAD["block"], w3.eth.block_number if not HEAD["block"] else 0)
    n, w, wn, tr, spent, peak = 0, None, "", None, None, 0.0
    ids = iter(plan_ids())
    try:
        while S["chain"] and n < CATCH_PER_COIN and done + n < CATCH_N and bots.count <= CATCH_MAX_BOTS[kind] and time.time() - t_seen < CATCH_AGE[kind]:
            pool.refresh()
            peak = max(peak, pool.r_bnb / 1e18)
            if pool.r_bnb / 1e18 < 0.5 * peak:
                say(f"тест ловли: {sym}: пул упал, эту монету закончил")
                break
            if w is None:  # the wallet waits funded and approved, the stake stays there only while this coin runs
                i = next((x for x in ids if not used(token, f"W{x}")), None)
                if i is None:
                    break
                tr, w, wn = Trade(), wallet(i), f"W{i}"
                w.gas_price = A.gas_price
                phase("Тест ловли покупки", f"{sym}: готовлю {wn}")
                need = wei + int((B.CFG["gas_limit_buy"] + 100_000 + B.CFG["gas_limit_sell"] + 21_000) * chain_price() * 1.2)
                if not fund(i, need, tr):
                    say(f"тест ловли: {wn} не получил BNB, эту монету закончил")
                    w = None
                    break
                rc = tr.tx(w, w.approve(token))
                if not rc or rc["status"] != 1:
                    say(f"тест ловли: approve с {wn} не прошёл, эту монету закончил")
                    break
            if bots.last is None or bots.last == spent:
                phase("Тест ловли покупки", f"{sym}: жду покупку бота (куплено {bots.count}), попыток на монете {n}")
                time.sleep(0.05)
                continue
            now = head()
            target = bots.last + CATCH_W - CATCH_SEND
            if now < target:
                phase("Тест ловли покупки", f"{sym}: покупка через {(target - now) * SPB:.1f} с")
                time.sleep(0.03)
                continue
            if now > target + 3:  # too late for this gap (a wallet was being prepared): the next one
                spent = bots.last
                continue
            base_block, before, t_send = bots.last, bots.count, time.time()
            spent, n = base_block, n + 1
            mark_used(token, wn)
            phase("Покупаю", f"{sym}: попытка {done + n} с {wn}")
            rc = tr.tx(w, w.buy(token, wei, 0))
            amt = received(rc, token, w.address) or retry(lambda: tok.functions.balanceOf(w.address).call())
            if not rc or rc["status"] != 1 or not amt:
                say(f"тест ловли: покупка с {wn} не прошла")
                catch_row(token, sym, done + n, before, "", False, 0, 0.0, tr.gas, "покупка не прошла")
                continue
            land, t_land = head(), time.time()
            gap = land - base_block
            while head() < land + CATCH_HOLD and bots.count == before and time.time() - t_land < 3:  # a stale head must not hold the coin
                time.sleep(0.03)
            hit = bots.count > before
            phase("Продаю", f"{sym}: {'бот купил' if hit else 'бот не купил'}, продаю с {wn}")
            got, why = 0.0, ""
            for _ in range(5):
                try:
                    hs = w.sell(token, amt, 0, gas_price=chain_price())
                    rc = tr.tx(w, hs) or tr.tx(w, hs, 40)
                except Exception as e:
                    why, rc = f"продажа не отправлена: {str(e)[:50]}", None
                if rc and rc["status"] == 1:
                    got = B.bnb_received(rc)
                    break
                why = why or "продажа отклонена (метка сторожа?)"
                time.sleep(0.4)
            held = time.time() - t_land
            catch_row(token, sym, done + n, before, gap, hit, held, got, tr.gas, "" if got else why)
            catch_back(w, wn)
            record(token, sym, CATCH_STAKE, 0, held, "ловля: попали" if hit else "ловля: мимо", got, tr.gas, why if not got else "")
            w = None
            if not got:
                return n, True
        return n, False
    finally:
        bots.stop = True
        if w is not None:
            catch_back(w, wn)


# 05.11 (user): the coin's creator pays a bot the exact sum of its buy ~4 blocks before it (creator_signal.py). Test: buy on that
# payment, sell right after the bot's buy. No hand-overs, one fresh wallet per attempt, the sum back on A after each.
SIG_PCT, SIG_STAKE_MIN, SIG_STAKE_MAX = 0.30, 0.00001, 0.03  # 06.11 (user): the stake is a share of wallet A, so it grows only with real profit
# 19.11 (user): BUNDLE TEST. run_bundle_test.ps1 writes bundle_test.json {"on": true, "usd": 0.01}: then every enabled network buys ONLY
# by bundle (payment seen in the mempool), at that stake, the pretick mode is off, payments seen only in a block are skipped; every bundle
# (landed or not) is a row of bundle_test.csv and a line of bundle_test.log. The file off/absent = normal trading. Read on every call (mtime).
BT_FILE = os.path.join(HERE, "bundle_test.json")
BT_CSV = os.path.join(HERE, "bundle_test.csv")
BT_LOG = os.path.join(HERE, "bundle_test.log")
BT_COLS = ["time", "net", "token", "symbol", "pay_hash", "pay_bnb", "pool_bnb", "share_pct", "seen_age_ms", "send_head", "result", "builders", "pay_block",
           "land_block", "bot_block", "hold_s", "stake", "got_bnb", "gas_bnb", "pnl_bnb", "pnl_before_gas_pct", "note"]
BT = {"mtime": None, "cfg": {}}
BT_SEEN = {}
BT_LOCK = threading.Lock()


def bt():
    """The bundle test settings while it is on, else {}."""
    try:
        m = os.path.getmtime(BT_FILE)
    except OSError:
        BT.update(mtime=None, cfg={})
        return {}
    if m != BT["mtime"]:
        try:
            BT.update(mtime=m, cfg=json.load(open(BT_FILE, encoding="utf-8-sig")))
        except Exception:
            pass
    return BT["cfg"] if BT["cfg"].get("on") else {}


def bt_stake():
    """The test stake in BNB ($ by the current price, never below 0.00001 BNB)."""
    return round(max(0.00001, float(bt().get("usd", 0.01)) / (S.get("bnb_usd") or 700.0)), 6)


def bt_log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d} " + msg
    try:
        with BT_LOCK, open(BT_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    say("ТЕСТ ПАКЕТОВ: " + msg)


def bt_row(**r):
    try:
        with BT_LOCK:
            new = not os.path.exists(BT_CSV)
            with open(BT_CSV, "a", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=BT_COLS, delimiter=";")
                if new:
                    w.writeheader()
                w.writerow({c: r.get(c, "") for c in BT_COLS})
    except Exception as e:
        say("тест пакетов: запись не удалась: " + str(e)[:60])


SIG_STAKE_CAP = 0.06  # BNB: never more, whatever the price says
SIG_STAKE_USD, SIG_BNB_FALLBACK, SIG_KEEP = 5.0, 700.0, 0.002  # 18.11 (user, after the test: 22 of 22 in plus before gas): $5 on every network  # 18.11 (user): $0.01 on every network for the 10-trade test (TEST_*), gas ignored  # 14.11 (user): $5 on the networks 1/2 (woke up after a week, may have changed): was $30  # $; BNB price before the panel knows it; BNB left on A for the gas
SIG_LOSS_PCT = 0.20  # the run stops after falling by this share of wallet A at its start from the run's best result
SIG_MIN_BNB = 0.001  # 05.11 (user): no filter by the share of the pool; only dust that cannot be a bot's buy is skipped
SIG_N, SIG_PER_COIN, SIG_MAX_AGE, SIG_HOLD = 10**9, 100000, 0.9, 4  # s after a signal; blocks without a bot buy
SIG_MAX_BNB, SIG_HOLD_S, SIG_DEAD_BNB = 3.0, 2.0, 0.2  # a bigger payment is the next cash desk, not a bot; s without a bot buy; a pool below this is finished
SIG_FAST_HOLD, SIG_FAST_HOLD_S = {3: 1, 4: 1}, 1.0  # 24.11: nets 3/4 sell 1 block (max 1 s) after landing unless the bot bought earlier (guard marks at +4)
# any coin that is trading (user): a payment means a bot buy is seconds away, and the dump comes ~3.5 min after the LAST buy
SIG_GWEI = 0.06  # above the guard's and the bots' 0.051
# 08.11 (user): 159 trades of networks 3/4 were all sent in the payment's block but landed +1..+5 blocks later: +1 won 9/9, +2 25/50,
# +3 and later 0/33 (the paid bot had already priced its buy with 0.01% slippage). The buy alone goes at this price to land sooner
SIG_BUY_GWEI = 0.3
SIG_MEMPOOL = True
SIG_MP = None  # one mempool stream for all runs: it has no stop of its own
# 05.11 (user): money only on payments that can pay; all the others are written down without buying and compared later (sig_skip.py).
# 110 attempts: the price rises by about 2 x the payment's share of the pool - 0.5 % (the swap's fees), the gas is about the same at any stake.
SIG_GAS, SIG_MARGIN = 0.00002, 1.3
TEST_IGNORE_GAS = True  # 24.11 (user): $0.01 test of the fast sale on nets 3/4: the payment filter ignores gas (else ~every signal is «мелкий»)  # 18.11 back to $5: the filter counts gas again  # 18.11 $0.01 test: the payment filter ignores gas (see sig_gas_part); set False when stakes go back up
SIG_REAL_SHARE = 0.03  # 05.11 (user: checked signals only): with this filter 13 of 14 coins were in plus; below it the signals are only written down
SKIP_FILE = os.path.join(HERE, "signal_skip.csv")
SIG_READ = None  # nodes that read a new block; the first answer is taken (one can take seconds, net_speed.py)
SIG_POOL = ThreadPoolExecutor(12)
SIG_OLD_COIN = 2 * 3600  # s: the longest coin lives about an hour; an older one is not looked at
SIG_LATE_BLOCKS = 2  # a payment read this many blocks after its block: the bot buys about now, we would come after it
# 06.11 20:17 (user): the test is over (10 of 10 sold, +0.00076 BNB): the second network is now traded with no limit of attempts.
# 06.11 (user): a separate small test on the second network (desk 0xF51DD734, guard 0xdbbf0824, guard2_today.py). 36 of 43 of its coins die
# in ~35 s and have no creator payments, 7 live 12-17 min with the same payments. Nobody outside the network is known to have sold there.
SIG2_GUARD = "0xDBbF08242c22a634960ceAAD44D9f2564fB26452"
SIG2_STAKE, SIG2_MAX, SIG2_AGE = 0.01, 10**9, 120  # BNB; attempts over all restarts; s: a newer coin is looked at, an older one is left
SIG2_FILE = os.path.join(HERE, "signal_test2.csv")
SKIP2_FILE = os.path.join(HERE, "signal_skip2.csv")
SECOND = {}  # token: (pair, token, launch time)
SECOND_ON = False
SIG2_PROVEN = 90  # s
SIG2_QUIET = 300  # s without a creator's payment: a newer coin is taken
SIG5_QUIET = 45  # 17.11: a payer network (5) pays its bots every few seconds on a live coin; 45 s of silence = move to a newer coin
NET2_LAUNCHES = os.path.join(HERE, "guard2_launches.csv")  # for the panel's charts: the same columns as guard_launches.csv
# 07.11 (user): a test on the third and fourth networks: the same contract (1365 bytes) with their own guards and desks (0x1ac2c5dd, 0x238d7170),
# coins of 15 BNB living ~31 min, few strong payments (10-20 a day). Like the first test of the second network: a small stake, 10 attempts.
NET_CFG = {2: dict(guard=SIG2_GUARD, file=SIG2_FILE, skip=SKIP2_FILE, stake_usd=5.0, max=SIG2_MAX),  # 18.11 (user): $5  # 18.11 (user): $0.01 for the 10-trade test  # 14.11 16:00 (user): $15 (was the network 1's $5)
           3: dict(guard="0x623e7c23C7C228095f94d9BD567Eae55cB87A966", file=os.path.join(HERE, "signal_test3.csv"), skip=os.path.join(HERE, "signal_skip3.csv"), stake_usd=0.01, max=10**9, share=0.02),
           4: dict(guard="0xA70B3f34E3ca7D4665b794CE6bb29168b1D2e40d", file=os.path.join(HERE, "signal_test4.csv"), skip=os.path.join(HERE, "signal_skip4.csv"), stake_usd=0.01, max=10**9, share=0.02)}  # 23.11 (user): $2 on nets 3/4 — A holds ~$8, a $5 stake + 0.002 keep did not fit  # 08.11 (user): 2% of the pool; $15 and $5 made their bots' buys revert (6 of 8, 5 of 8): back to $1 to see if the win rate returns
# 07.11 20:20 (user): the network 5 = family 2941 (code 436f529b4d, pools ~22 BNB living ~6 min). Its bots are paid by one wallet for all coins
# (0x950f0340 on 07.11, 743 payments in its life of < 6 h), 3-7 blocks before the buy, the sum of the buy + ~0.3 %. bank_2941.py on paper: buys from
# 1 % of the pool: 366, 361 in plus, +2.4 % after fees and gas at $30. The payer changes: net_finder.py names the current ones (auto_add_loop).
NET_CFG[5] = dict(guard=None, code="436f529b4d", kind="payer", payers={"0x950f0340b133b276be5855622f34149b79a3a9f3"}, share=0.01,
                  file=os.path.join(HERE, "signal_test5.csv"), skip=os.path.join(HERE, "signal_skip5.csv"), stake_usd=5.0, max=10**9)  # 08.11 (user): $15; 17.11 (user): $5, the least the gas (~$0.03 a trade) allows
# 08.11: the payers learned on the way are kept: after a restart the network 5 knew only the first one until net_finder saw 5 new payments
PAYERS_FILE = os.path.join(HERE, "net5_payers.json")
try:
    NET_CFG[5]["payers"].update(str(p).lower() for p in json.load(open(PAYERS_FILE, encoding="utf-8")))
except (OSError, ValueError, TypeError):
    pass


def save_payers():
    try:
        with open(PAYERS_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(NET_CFG[5]["payers"]), f)
    except OSError:
        pass
# 07.11 (user): the networks 3, 4 and the added ones: $1 a trade, no limit of trades; a refused sale still stops that network


def net_stake(cfg):
    """BNB for one trade on a network 2+: $1 by the current price for the small ones, else the first network's stake."""
    if bt():
        return bt_stake()
    if cfg.get("stake_usd"):
        return round(min(cfg["stake_usd"] / (S.get("bnb_usd") or SIG_BNB_FALLBACK), SIG_STAKE_CAP), 6)
    return sig_stake()
# 07.11 (user): networks found by net_finder.py and added with the panel's button "Добавить": nets_extra.json, ids from 5 on
NETS_EXTRA = os.path.join(HERE, "nets_extra.json")
EXTRA_STAKE_USD, EXTRA_MAX = 1.0, 10**9
NET_KEYS = {"5faca89801:0xbd418ce6a83995971de9f1cf9f61f345e91aeadd": 1}  # net_finder's key -> our network
NET_KEYS.update({f"18449a1624:{NET_CFG[n]['guard'].lower()}": n for n in (2, 3, 4)})
NET_KEYS["436f529b4d:0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"] = 5  # slot 6 of that contract keeps WBNB: the finder's key of the family
NET_KEYS["436f529b4d:*"] = 5


def payer_text(cfg):
    return ", ".join(sorted(p[:10] for p in list(cfg.get("payers", ()))))


def extra_cfg(n, e):
    return dict(guard=e.get("guard") or None, code=e.get("hash"), key=e["key"], file=os.path.join(HERE, f"signal_test{n}.csv"), skip=os.path.join(HERE, f"signal_skip{n}.csv"),
                stake_usd=EXTRA_STAKE_USD, max=EXTRA_MAX, added=e.get("added"))


# 10.11 (user): the network 6 = family 3553 (code 1f6f5a665a, pool always 58 BNB, a coin every ~30 min, no guard, no sell tax by simulation).
# The price climbs evenly (+10 % at 10 min, +20 % at 18 min) and the pool is drained at 27-33 min; 4 of 51 coins a day were drained at 1-6 min
# and 2 at 18.2 / 21.9 min (hist3553.py). Not a signal network: buy at HOLD_ENTRY s if the pool is alive and growing, sell at HOLD_EXIT s,
# at once if the pool falls from its peak or the trading is stopped. Paper over 51 coins: entry 10 min, exit 18 min: 47 of 47 in plus, +7.9 %.
HOLD_ENTRY, HOLD_LATEST, HOLD_EXIT = 600, 660, 1050  # s from the pool: buy at 10 min (not later than 11), sell at 17.5 min
HOLD_MIN_BNB, HOLD_DROP = 59.0, 0.985  # the pool at entry (starts at 58); a fall below 98.5 % of its peak = the drain is starting
NET_CFG[6] = dict(guard=None, code="1f6f5a665a", kind="hold", file=os.path.join(HERE, "signal_test6.csv"), skip=os.path.join(HERE, "signal_skip6.csv"),
                  stake_usd=1.0, max=10**9, entry=HOLD_ENTRY, latest=HOLD_LATEST, exit=HOLD_EXIT, min_bnb=HOLD_MIN_BNB, drop=HOLD_DROP,
                  cur=os.path.join(HERE, "hold_current.json"), fam="3553", pool_kind=58)
NET_KEYS["1f6f5a665a:*"] = 6
# 11.11 (user, a $0.1 test): the network 7 = the coins of the networks 3 and 4 (code 1365 = hash 18449a1624, guards 0x623e7c23 / 0xa70b3f34) held.
# bot_watch 10-11.11: all 37 drains by the creator at 29.3-32.4 min, not caused by outsiders; paper entry 30 s -> exit 28 min: 38 of 38 in plus,
# +31..+36 %. Untested: whether their guards mark a holder of 28 min. The pool dips up to ~4 % on the bots' sells, the drain is one tx -> drop 0.90.
NET_CFG[7] = dict(guard=None, kind="hold", hold_code="18449a1624", hold_guards={"623e7c23c7c228095f94d9bd567eae55cb87a966", "a70b3f34e3ca7d4665b794ce6bb29168b1d2e40d"},
                  file=os.path.join(HERE, "signal_test7.csv"), skip=os.path.join(HERE, "signal_skip7.csv"), stake_usd=0.1, max=10**9,
                  entry=30, latest=90, exit=27 * 60, min_bnb=14.9, drop=0.90, cur=os.path.join(HERE, "hold_current7.json"), fam="1365 (сети 3/4)", pool_kind=15)
# 13.11 DACITY: the guard 0xa70b3f34 marked our holder 16 blocks (~7 s) after the buy, the sale reverted. Like the network 1: the coin is handed on
# to a fresh wallet every ~1 s (hold_hop). First a ~$0.5 test (user): $0.1, 120 s of hand-overs, then sell, one trade (max=1), its own files.
# 13.11 20:47 MP: 60 hand-overs in 122 s, sold, 24 holders marked but all after they passed the coin on. Second test (user): $0.01, ~20 min, the 2nd row of the file.
NET_CFG[7].update(stake_usd=0.01)
NET_CFG[7].update(hop=True, hop_hold=20 * 60, max=2, file=os.path.join(HERE, "signal_test7h.csv"), cur=os.path.join(HERE, "hold_current7h.json"))

try:
    for _e in json.load(open(NETS_EXTRA, encoding="utf-8")):
        NET_CFG[int(_e["id"])] = extra_cfg(int(_e["id"]), _e)
        NET_KEYS[_e["key"]] = int(_e["id"])
except (OSError, ValueError):
    pass
GUARD_NET = {c["guard"][2:].lower(): n for n, c in NET_CFG.items() if c.get("guard")}
CODE_NET = {c["code"]: n for n, c in NET_CFG.items() if c.get("code") and not c.get("guard") and c.get("kind") != "hold"}  # a family without a guard: by its code; the hold network finds its coins itself
S["net_keys"] = NET_KEYS
S["net_auto"] = True
S["net_ids"] = sorted(NET_CFG)
for _n in [k for k in NET_CFG if k >= 3]:
    S[f"net{_n}"] = dict(stake=net_stake(NET_CFG[_n]), n=0, plus=0, pnl=0.0, off=False, coins_h=0, last_s=None, dead_h=0, max=NET_CFG[_n]["max"])
    S[f"sig{_n}_run"] = 0
S["net2"] = dict(stake=SIG2_STAKE, n=0, plus=0, pnl=0.0, off=False, coins_h=0, last_s=None)
S["sig2_run"] = 0
S["sig_cfg"] = dict(usd=SIG_STAKE_USD, n=SIG_N, pct=SIG_PCT, stake_min=SIG_STAKE_MIN, stake_max=SIG_STAKE_MAX, share=SIG_REAL_SHARE, loss_pct=SIG_LOSS_PCT)


def sig_need(stake):
    """The payment's share of the pool from which an attempt pays at this stake."""
    return max(SIG_REAL_SHARE, (0.005 + sig_gas_part(stake)) / 2 * SIG_MARGIN)


def sig_gas_part(stake):
    """gas as a share of the stake; 18.11 (user: «плевать на комиссию газа» in the $0.01 test): ignored while the test runs,
    otherwise at a $0.01 stake the gas (> the stake) raised the needed payment to ~77 % of the pool and every signal was «мелкий»."""
    return 0.0 if TEST_IGNORE_GAS or bt() else SIG_GAS / stake


def sig_skip(sig, token, sym, kind, pool_bnb, why, file=None):
    """A signal that is not bought: written down only."""
    cols = ["time", "token", "symbol", "kind", "source", "pay_block", "head", "bnb_paid", "pool_bnb", "share_pct", "why"]
    file = file or SKIP_FILE
    try:
        new = not os.path.exists(file)
        with open(file, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=cols, delimiter=";")
            if new:
                w.writeheader()
            w.writerow(dict(time=time.strftime("%Y-%m-%d %H:%M:%S"), token=token, symbol=sym, kind=kind, source=sig["src"], pay_block=sig["block"] or "", head=head_now(),
                            bnb_paid=round(sig["value"], 5), pool_bnb=round(pool_bnb, 3), share_pct=round(sig["value"] / pool_bnb * 100, 2) if pool_bnb else "", why=why))
    except OSError as e:
        say(f"тест по сигналу: запись пропуска не удалась: {str(e)[:60]}")


def sig_block(n):
    """Block n from whichever node answers first."""
    global SIG_READ
    if SIG_READ is None:
        pn = Web3(Web3.HTTPProvider("https://bsc-rpc.publicnode.com", request_kwargs={"timeout": 2}))
        pn.middleware_onion.inject(B.ExtraDataToPOAMiddleware, layer=0)
        SIG_READ = [w3, pn]
    jobs = [SIG_POOL.submit(x.eth.get_block, n, full_transactions=True) for x in SIG_READ]
    err = None
    for j in as_completed(jobs):
        try:
            return j.result()
        except Exception as e:
            err = e
    raise err
SIG_FILE = os.path.join(HERE, "signal_test.csv")


def sig_stake():
    """06.11 (user): the next attempt's stake is $30 by the current BNB price on both networks; less only if A does not have it."""
    if bt():
        return bt_stake()
    bal = retry(lambda: w3.eth.get_balance(A.address)) / 1e18
    return round(max(SIG_STAKE_MIN, min(SIG_STAKE_USD / (S.get("bnb_usd") or SIG_BNB_FALLBACK), SIG_STAKE_CAP, bal - SIG_KEEP)), 6)


REC_LOCK, WALLET_LOCK = threading.Lock(), threading.Lock()
BUSY = set()  # wallets holding a stake or a coin of an attempt right now
S["sig_now"] = {}


def nphase(net, p, d=""):
    """Each network's own line; the top of the panel shows a buy or a sale of either first."""
    S["sig_now"][str(net)] = dict(phase=p, detail=d)
    now = S["sig_now"]
    vals = list(now.values())
    top = (next((x for x in vals if x["phase"] in ("Покупаю", "Продаю")), None) or next((x for x in vals if x["phase"] != "Выключена"), None)
           or now.get("1") or now.get("2"))
    phase(top["phase"], top["detail"])


BUY_FAIL_ALERT = (3, 10)  # refused buys in a row on one network: the moments to raise the alarm


def buy_failed(net, sym, wn, rc):
    """07.11 (user): notice at once if a network starts refusing our buys (a blacklist of buyers). Counts refused buys in a row per network;
    one success resets the count. The trading goes on: a refused buy costs only its gas."""
    k = f"buyfail{net}"
    S[k] = S.get(k, 0) + 1
    why = "отклонена сетью" if rc and rc["status"] != 1 else "нет ответа узла"
    say(f"сеть {net}: покупка {sym} с {wn} не прошла ({why}), подряд {S[k]}")
    if S[k] in BUY_FAIL_ALERT:
        msg = (f"ВНИМАНИЕ, СЕТЬ {net}: {S[k]} покупки подряд не прошли (последняя {sym}, {why}). Похоже, нам запрещают покупать: "
               f"проверь журнал. Торговля продолжается, каждая такая попытка стоит только газ")
        say(msg)
        tg("⚠️ " + msg)


def buy_ok(net):
    if S.get(f"buyfail{net}"):
        say(f"сеть {net}: покупка снова прошла после {S[f'buyfail{net}']} отказов")
    S[f"buyfail{net}"] = 0


def take_wallet(ids, token):
    """The next wallet free of this coin and not serving the other network at this moment."""
    with WALLET_LOCK:
        for x in ids:
            if not used(token, f"W{x}") and x not in BUSY:
                BUSY.add(x)
                return x
    return None


# 14.11 (user): $5 a trade, no limit of trades, all trading stops once the trades since LOSS_SINCE lost LOSS_LIMIT_USD in all.
# Counted from the trades file, so a restart does not reset it. A new limit = a new LOSS_SINCE.
LOSS_LIMIT_USD, LOSS_SINCE = 5.0, "2026-11-14 00:00:00"
LOSS = {"mtime": None, "bnb": 0.0, "net": {}}
# 14.11 (user): the networks 1 and 2 woke up after a week: $5 a trade and their own limit, each is switched off by itself after losing $5
NET_LOSS_LIMIT, NET_LOSS_SINCE = {1: 5.0, 2: 5.0, 5: 5.0}, "2026-11-14 12:50:00"  # 17.11 (user): the network 5 too (no trades of it since 10.11)


# 18.11 (user): the networks 1-4 came back after two days and may work differently: a test of TEST_N trades at $1. After TEST_N sold trades
# the result with gas below TEST_MIN_USD stops all trading (the strategy no longer works); at or above it nothing changes. An early stop
# at TEST_EARLY_USD so a broken strategy does not lose much before the 10th trade. Counted from the trades file (a restart keeps it).
# At $0.01 a trade the gas (~$0.01-0.03) is bigger than the stake, so the user said to ignore it: the test is judged by the result BEFORE
# gas (any minus = fail: at $0.01 a "−0.5 cent" could never be reached). TEST_EARLY_USD with gas only caps what the test may spend on gas.
TEST_SINCE, TEST_N, TEST_MIN_USD, TEST_EARLY_USD = "2026-11-18 00:30:00", 10, 0.0, -0.60
TEST = {"mtime": None, "n": 0, "pnl": 0.0, "gross": 0.0, "cancel": 0, "said": False}


def test_state():
    """Sold signal trades since TEST_SINCE, their result with gas and before gas, and the deadline cancels (gas only)."""
    try:
        m = os.path.getmtime(TRADES)
        if m != TEST["mtime"]:
            with open(TRADES, encoding="utf-8-sig") as f:
                rows = [r for r in csv.DictReader(f, delimiter=";") if r["time"] >= TEST_SINCE and "сигнал" in (r.get("reason") or "")]
            sold = [r for r in rows if float(r.get("got_bnb") or 0) > 0]
            TEST.update(mtime=m, n=len(sold), pnl=sum(float(r["pnl_bnb"] or 0) for r in rows),
                        gross=sum(float(r["pnl_bnb"] or 0) + float(r["gas_bnb"] or 0) for r in sold), cancel=len(rows) - len(sold))
    except (OSError, ValueError, KeyError):
        pass
    usd = S.get("bnb_usd") or SIG_BNB_FALLBACK
    S["test"] = dict(n=TEST["n"], of=TEST_N, usd=round(TEST["pnl"] * usd, 4), gross_usd=round(TEST["gross"] * usd, 4), cancel=TEST["cancel"])
    return TEST["n"], TEST["pnl"] * usd


TEST_ON = False  # 18.11: the test is over (22 of 22 in plus before gas, the user raised stakes to $5): its limits no longer stop trading


def test_failed():
    """True = stop all trading: the test ended below TEST_MIN_USD, or lost TEST_EARLY_USD before its end."""
    n, usd = test_state()
    gross = S["test"]["gross_usd"]
    if not TEST_ON:
        return None
    if usd <= TEST_EARLY_USD:
        return f"ТЕСТ 10 СДЕЛОК ОСТАНОВЛЕН: газ теста дошёл до {usd:+.3f} $ (предел {TEST_EARLY_USD:g} $) после {n} сделок, до газа {gross:+.4f} $"
    if n >= TEST_N and gross < TEST_MIN_USD:
        return f"ТЕСТ 10 СДЕЛОК ПРОВАЛЕН: {n} сделок, до газа {gross:+.4f} $ (минус), с газом {usd:+.3f} $, отменено сроком {TEST['cancel']}"
    if n >= TEST_N and not TEST["said"]:
        TEST["said"] = True
        msg = f"ТЕСТ 10 СДЕЛОК ПРОЙДЕН: {n} сделок, до газа {gross:+.4f} $, с газом {usd:+.3f} $ — торговля продолжается без изменений"
        say(msg)
        tg("✅ " + msg)
    return None


def trade_net(reason):
    """The network of a trade from its reason: "сеть N, ..." or the network 1 ("сигнал: ...")."""
    r = reason or ""
    if r.startswith("сеть "):
        d = r[5:].split(",")[0].split(" ")[0]
        if d.isdigit():
            return int(d)
    return 1


def loss_total():
    """Sum of pnl_bnb of the trades since LOSS_SINCE (re-read only when the file changes); per network since NET_LOSS_SINCE."""
    try:
        m = os.path.getmtime(TRADES)
        if m != LOSS["mtime"]:
            with open(TRADES, encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f, delimiter=";"))
            LOSS["bnb"] = sum(float(r["pnl_bnb"] or 0) for r in rows if r["time"] >= LOSS_SINCE)
            per = {}
            for r in rows:
                if r["time"] >= NET_LOSS_SINCE:
                    n = trade_net(r.get("reason"))
                    per[n] = per.get(n, 0.0) + float(r["pnl_bnb"] or 0)
            LOSS["net"] = per
            LOSS["mtime"] = m
    except (OSError, ValueError, KeyError):
        pass
    return LOSS["bnb"]


def net_loss_hit(net):
    """True once this network lost its own limit since NET_LOSS_SINCE; it is then switched off (switching it on again is the user's)."""
    lim = NET_LOSS_LIMIT.get(net)
    if not lim:
        return False
    loss_total()
    lost = LOSS["net"].get(net, 0.0) * (S.get("bnb_usd") or SIG_BNB_FALLBACK)
    if lost > -lim:
        return False
    NET_LOSS_LIMIT.pop(net, None)  # once: a hand switch-on afterwards is not undone
    try:
        set_net_off(net, True)
    except Exception:
        NET_OFF.add(net)
    msg = f"СЕТЬ {net} ВЫКЛЮЧЕНА: убыток с {NET_LOSS_SINCE[11:16]} дошёл до {lost:+.2f} $ (её лимит −{lim:g} $)"
    say(msg)
    tg("🛑 " + msg)
    return True


def sig_alive():
    """06.11: a run without an end: counted from the start, a long profit would let it lose all of it before stopping."""
    S["sig_peak"] = max(S.get("sig_peak", 0.0), S["run_pnl"])
    S["loss_usd"], S["loss_limit"], S["loss_since"] = round(loss_total() * (S.get("bnb_usd") or SIG_BNB_FALLBACK), 2), LOSS_LIMIT_USD, LOSS_SINCE[:16]  # the panel's chip
    if S["loss_usd"] <= -LOSS_LIMIT_USD:
        return False
    if test_failed():
        return False
    return S["run_pnl"] > S["sig_peak"] - S["sig_max_loss"]


def sig_note(fn, **k):
    """Writing a row must not end an attempt: a csv open in Excel is locked, and a lost wallet must still stop the run."""
    try:
        fn(**k)
    except Exception as e:
        say(f"тест по сигналу: запись не удалась: {str(e)[:80]}")


def sig_first(pair, token, t_seen):
    """BNB at the pool's first Sync and who made that transaction (the creator), or (None, None)."""
    try:
        wbnb_is0 = int(Web3.to_checksum_address(B.WBNB), 16) < int(Web3.to_checksum_address(token), 16)
        head = w3.eth.block_number
        b = head - int((time.time() - t_seen) / SPB) - 120
        step = 2000
        while b <= head:
            try:
                logs = w3.eth.get_logs({"address": Web3.to_checksum_address(pair), "fromBlock": b, "toBlock": min(head, b + step - 1), "topics": [SYNC_TOPIC_HT]})
            except Exception as e:  # 18.11: a node answered «exceed maximum block range 100» and a coin of the network 3 was skipped
                if step > 100 and ("range" in str(e) or "limit" in str(e) or "too many" in str(e).lower()):
                    step = 100
                    continue
                raise
            if logs:
                d = bytes(logs[0]["data"])
                frm = retry(lambda: w3.eth.get_transaction(logs[0]["transactionHash"])["from"]).lower()
                return int.from_bytes(d[:32] if wbnb_is0 else d[32:64], "big") / 1e18, frm
            b += step
    except Exception as e:
        say("тест по сигналу: " + str(e)[:80])
    return None, None


class BlockFeed:
    """06.11: the new blocks are read once and given to every coin being watched: two networks must not cost twice the traffic and CPU.
    Reads nothing while no coin is watched."""

    def __init__(self):
        self.watchers, self.next, self.on = [], None, False
        self.lock = threading.Lock()

    def add(self, w):
        with self.lock:
            self.watchers.append(w)
            if not self.on:
                self.on = True
                threading.Thread(target=self.run, daemon=True).start()

    def remove(self, w):
        with self.lock:
            if w in self.watchers:
                self.watchers.remove(w)

    def run(self):
        while True:
            try:
                with self.lock:
                    now = list(self.watchers)
                if not now:
                    self.next = None
                    time.sleep(0.05)
                    continue
                head = head_now() or w3.eth.block_number
                if self.next is None:
                    self.next = head
                if head < self.next:
                    time.sleep(0.03)
                    continue
                n = max(self.next, head - 3)
                block = sig_block(n)
                for w in now:
                    try:
                        w.on_block(n, block)
                    except Exception:
                        pass
                self.next = n + 1
            except Exception:
                time.sleep(0.1)


FEED = BlockFeed()


BUY_ROUTERS = {B.ROUTER.lower(), "0xba56663c1de9423ec886b24ac752d86ff0d9f7c0"}  # 17.11 venue_scan: the network 5 bots' aggregator


# 19.11 (user): bundles. A creator's payment seen in the public mempool is sent to the block builders TOGETHER with our buy, as one
# bundle [their payment, our buy]: our buy then lands in the payment's own block, right after it (the nets 1/2 bots quote at the end of
# the payment block, so this is the only place that does not break them). A bundle lands whole or not at all: if the payment goes into
# a block without us, our buy never happens and costs nothing (user: "if we miss the block, cancel the buy"). Free, no key: 48club and
# BlockRazor (>96 % of BSC blocks); tested 19.11 18:00 — a 2-tx bundle landed at once, the txs side by side.
BUNDLE_ON = True
BUNDLE_URLS = ["https://puissant-builder.48.club", "https://bsc.blockrazor.xyz"]
BUNDLE_BLOCKS, BUNDLE_WAIT_S = 2, 4  # the bundle is valid for the next blocks only; how long we wait for our receipt
BUNDLE_STATS = {"sent": 0, "landed": 0, "refused": 0}
S["bundle"] = dict(BUNDLE_STATS)


def raw_of(tx):
    """The signed raw bytes of a pending tx rebuilt from its mempool fields; None unless keccak(raw) == its hash."""
    try:
        import rlp
        t = int(tx.get("type") or "0x0", 16)
        I = lambda k: int(tx[k], 16)
        to = bytes.fromhex(tx["to"][2:]) if tx.get("to") else b""
        data = bytes.fromhex((tx.get("input") or "0x")[2:])
        if t == 0:
            raw = rlp.encode([I("nonce"), I("gasPrice"), I("gas"), to, I("value"), data, I("v"), I("r"), I("s")])
        elif t == 2:
            acl = [[bytes.fromhex(a["address"][2:]), [bytes.fromhex(k[2:]) for k in a["storageKeys"]]] for a in tx.get("accessList") or []]
            raw = b"\x02" + rlp.encode([I("chainId"), I("nonce"), I("maxPriorityFeePerGas"), I("maxFeePerGas"), I("gas"), to, I("value"), data, acl,
                                        int(tx.get("yParity") or tx["v"], 16), I("r"), I("s")])
        else:
            return None
        if Web3.keccak(raw).hex().removeprefix("0x") != tx["hash"].lower().removeprefix("0x"):
            return None
        return "0x" + raw.hex()
    except Exception:
        return None


def sign_buy(w, token, wei, deadline, gas_price):
    """Our buy signed but NOT broadcast (it goes only inside a bundle). The wallet's nonce moves only if it lands."""
    data = B.router.encode_abi("swapExactETHForTokensSupportingFeeOnTransferTokens", args=[0, [B.WBNB, token], w.address, deadline])
    with w.lock:
        sg = w.acct.sign_transaction({"chainId": 56, "nonce": w.nonce, "to": B.ROUTER, "value": wei, "data": data,
                                      "gas": B.CFG["gas_limit_buy"], "gasPrice": gas_price})
    return "0x" + sg.hash.hex().removeprefix("0x"), "0x" + sg.raw_transaction.hex().removeprefix("0x")


def bundle_warm():
    """19.11: a cold connection to a builder costs 0.8-3 s (TLS), a warm one 90 ms (BlockRazor) / 280 ms (48club): keep them warm."""
    while True:
        for u in BUNDLE_URLS:
            try:
                B.http.post(u, json={"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []}, timeout=3)
            except Exception:
                pass
        time.sleep(10)


if BUNDLE_ON:
    threading.Thread(target=bundle_warm, daemon=True).start()


def bundle_send(raws, max_block):
    """Send one bundle to every builder at once; returns {url: answer}. Never raises."""
    body = {"jsonrpc": "2.0", "id": 1, "method": "eth_sendBundle", "params": [{"txs": raws, "maxBlockNumber": int(max_block)}]}
    out = {}

    def one(u):
        try:
            out[u] = B.http.post(u, json=body, timeout=2).json()
        except Exception as e:
            out[u] = {"error": {"message": str(e)[:60]}}

    th = [threading.Thread(target=one, args=(u,), daemon=True) for u in BUNDLE_URLS]
    for t in th:
        t.start()
    for t in th:
        t.join(2.2)
    BUNDLE_STATS["sent"] += 1
    if not any("result" in (v or {}) for v in out.values()):
        BUNDLE_STATS["refused"] += 1
    return out


def bundle_buy(w, tr, token, wei, deadline, gas_price, pay_raw):
    """[their payment, our buy] to the builders. Returns (hash, receipt) if our buy landed, (hash, None) if the bundle did not land."""
    hb, raw = sign_buy(w, token, wei, deadline, gas_price)
    ans = bundle_send([pay_raw, raw], (head_now() or w3.eth.block_number) + BUNDLE_BLOCKS)
    errs = [str((v or {}).get("error", {}).get("message", ""))[:50] for v in ans.values() if "result" not in (v or {})]
    S["bundle_last"] = "; ".join(f"{u.split('/')[2].split('.')[0]}: {'принят' if 'result' in (v or {}) else str((v or {}).get('error', {}).get('message', ''))[:40]}"
                                 for u, v in ans.items()) or "нет ответа"
    rc = tr.tx(w, hb, BUNDLE_WAIT_S)
    if rc:
        with w.lock:
            w.nonce += 1
        BUNDLE_STATS["landed"] += 1
    S["bundle"] = dict(BUNDLE_STATS)
    if errs and not rc:
        say("пакет: строители ответили: " + "; ".join(errs))
    return hb, rc


class SigWatch:
    """The creator's payments and the bots' buys: every new block is read whole; the public mempool can show a payment earlier.
    07.11: `creator` may also be a set of payers (the network 5): the set is shared with NET_CFG, so a new payer is watched at once."""

    def __init__(self, creator, mempool=None):
        self.who = {creator.lower()} if isinstance(creator, str) else creator
        self.stop, self.signals, self.bought, self.seen = False, deque(), {}, set()
        self.src = {"мемпул": 0, "блок": 0}
        self.next = w3.eth.block_number + 1
        if mempool is not None:
            mempool.watchers.append(self.pending)
            self.mempool = mempool
        else:
            self.mempool = None
        FEED.add(self)

    last_ts = None  # 13.11: the time (s) of the newest block read: a mempool payment lands in the next one, at this second or the next

    def add(self, h, to, value, block, src, ts=None, raw=None):
        if h in self.seen or not to or value <= 0 or to in our_addrs() or to == B.ROUTER.lower():
            return
        self.seen.add(h)
        self.src[src] += 1
        self.signals.append(dict(t=time.time(), to=to, value=value / 1e18, block=block, src=src, h=h, ts=ts if ts is not None else self.last_ts, raw=raw))

    def pending(self, tx):
        if (tx.get("from") or "").lower() in self.who:
            self.add(tx.get("hash", "").lower(), (tx.get("to") or "").lower(), int(tx.get("value") or "0x0", 16), None, "мемпул",
                     raw=(tx.get("raw") or raw_of(tx)) if BUNDLE_ON else None)

    def on_block(self, n, block):
        try:
            ts = int(block["timestamp"])
            self.last_ts = max(self.last_ts or 0, ts)
        except Exception:
            ts = None
        if n < self.next:
            return
        for tx in block["transactions"]:
            frm, to = (tx["from"] or "").lower(), (tx["to"] or "").lower()
            if frm in self.who:
                h = tx["hash"].hex() if hasattr(tx["hash"], "hex") else str(tx["hash"])
                self.add(h if h.startswith("0x") else "0x" + h, to, tx["value"], n, "блок", ts)
            elif to in BUY_ROUTERS and tx["value"]:  # 17.11: the network 5 bots buy through the aggregator 0xba56663c, not the router
                self.bought[frm] = n

    def close(self):
        self.stop = True
        FEED.remove(self)
        if self.mempool is not None and self.pending in self.mempool.watchers:
            self.mempool.watchers.remove(self.pending)


def sig_row(**k):
    cols = ["time", "token", "symbol", "kind", "attempt", "source", "pay_block", "bnb_paid", "pool_bnb", "share_pct", "signal_age_s", "send_head", "land_block", "bot_buy_block", "before_bot",
            "sell_block", "hold_s", "got_bnb", "gas_bnb", "pnl_bnb", "pnl_pct", "note"]
    file = k.get("file") or SIG_FILE
    new = not os.path.exists(file)
    with open(file, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, delimiter=";")
        if new:
            w.writeheader()
        w.writerow({c: k.get(c, "") for c in cols})


def tx_block(h):
    try:
        return retry(lambda: w3.eth.get_transaction(h)["blockNumber"])
    except Exception:
        return ""


def sig2_state(net=2):
    """(attempts so far, was a sale refused): from the file, so that a restart neither repeats the test nor forgets a lost wallet."""
    cfg, off = NET_CFG[net], S.get(f"sig{net}_off", False)
    if not os.path.exists(cfg["file"]):
        return 0, off
    try:
        with open(cfg["file"], encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f, delimiter=";"))
        return len(rows), off or any(r.get("got_bnb") and float(r["got_bnb"]) == 0 for r in rows)
    except Exception:  # open in Excel or spoiled: no attempts on a state that cannot be read
        return cfg["max"], True


def net2_status():
    """What the panel shows about the networks 2-4."""
    try:
        stake2 = sig_stake()
    except Exception:
        stake2 = S.get("sig2_stake") or SIG_STAKE_MIN
    now = time.time()
    for net, cfg in NET_CFG.items():
        n, plus, pnl = 0, 0, 0.0
        try:
            if os.path.exists(cfg["file"]):
                with open(cfg["file"], encoding="utf-8-sig") as f:
                    for r in csv.DictReader(f, delimiter=";"):
                        n += 1
                        v = float(r.get("pnl_bnb") or 0)
                        pnl += v
                        plus += v > 0
        except Exception:
            pass
        ts = [v[2] for v in list(SECOND.values()) if (v[3] if len(v) > 3 else 2) == net]
        S[f"net{net}"] = dict(payer=payer_text(cfg), share=cfg.get("share"), stake=net_stake(cfg) if cfg.get("stake_usd") else stake2, n=n, plus=plus, pnl=round(pnl, 8), off=sig2_state(net)[1], coins_h=sum(now - t < 3600 for t in ts), last_s=round(now - max(ts)) if ts else None,
                              dead_h=sum(now - t < 3600 for t, k in NET2_DEAD if k == net), max=cfg["max"] if cfg["max"] < 10**6 else None)


def second_note(pair, token, t, net=2):
    """One row per coin found, so that the charts can draw it; a locked file only costs that chart. The symbol column keeps "net3"/"net4"."""
    try:
        new = not os.path.exists(NET2_LAUNCHES)
        with open(NET2_LAUNCHES, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            if new:
                w.writerow(["time", "ts", "token", "symbol", "pair"])
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)), round(t), token, "" if net == 2 else f"net{net}", pair])
    except OSError:
        pass


NET2_COINS = {}  # pair: the second network's coins of the last hour, alive, for the panel's list
NET2_DEAD = deque(maxlen=500)  # when a coin of the list died


def net2_add(pair, token, t, net=2):
    if pair in NET2_COINS:
        return
    c = dict(pair=pair, token=token, t=t, sym=token[:8], is0=None, bnb=None, peak=0.0, price=None, price0=None, net=net)
    NET2_COINS[pair] = c
    try:
        c["sym"] = w3.eth.contract(token, abi=TOK_ABI).functions.symbol().call()[:20]
    except Exception:
        pass


def net2_refresh_once():
    """Only the coins still alive are read: most die in ~35 s and leave the list."""
    now = time.time()
    for pair, c in list(NET2_COINS.items()):
        try:
            if now - c["t"] > 3600:
                del NET2_COINS[pair]
                continue
            ct = w3.eth.contract(pair, abi=B.PAIR_ABI)
            if c["is0"] is None:
                c["is0"] = ct.functions.token0().call().lower() == c["token"].lower()
            r0, r1, _ = ct.functions.getReserves().call()
            rt, rb = (r0, r1) if c["is0"] else (r1, r0)
            c["bnb"], c["price"] = rb / 1e18, (rb / rt if rt else 0)
            c["price0"] = c["price0"] or c["price"]
            c["peak"] = max(c["peak"], c["bnb"])
            if c["peak"] and c["bnb"] < max(0.2 * c["peak"], SIG_DEAD_BNB):
                del NET2_COINS[pair]
                NET2_DEAD.append((now, c.get("net", 2)))
        except Exception:
            pass


def net2_refresh():
    try:  # after a restart: the coins of the last hour are known from the file
        if os.path.exists(NET2_LAUNCHES):
            with open(NET2_LAUNCHES, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f, delimiter=";"):
                    if time.time() - float(r["ts"]) < 3600:
                        net2_add(Web3.to_checksum_address(r["pair"]), Web3.to_checksum_address(r["token"]), float(r["ts"]), int(r["symbol"][3:]) if str(r.get("symbol", "")).startswith("net") else 2)
    except Exception:
        pass
    while True:
        net2_refresh_once()
        time.sleep(5)


def start_second():
    global SECOND_ON
    if not SECOND_ON:
        SECOND_ON = True
        threading.Thread(target=second_scan, daemon=True).start()
        threading.Thread(target=net2_refresh, daemon=True).start()


def second_scan():
    """New pools whose token keeps the second guard in storage slot 6."""
    # 08.11: after a power cut the coins launched while the PC was off were never seen (the coins of the networks 3/4 live ~31 min):
    # the scan starts 45 min back; dead ones are dropped by the pool check of the coin list
    last, seen_at = w3.eth.block_number - int(45 * 60 / SPB), 0.0
    while True:
        if time.time() - seen_at > 5:
            try:
                net2_status()
            except Exception as e:
                say("состояние сетей: " + str(e)[:80])
            seen_at = time.time()
        try:
            head = w3.eth.block_number
            if head > last:
                to = min(head, last + 500)
                logs = w3.eth.get_logs({"address": B.FACTORY.address, "topics": [B.PAIR_CREATED_TOPIC], "fromBlock": last + 1, "toBlock": to})
                for l in logs:
                    r = B.parse_pair_created(l)
                    net = GUARD_NET.get(w3.eth.get_storage_at(r[1], 6).hex()[-40:].lower()) if r else None
                    if r and not net and CODE_NET:
                        net = CODE_NET.get(Web3.keccak(bytes(w3.eth.get_code(r[1]))).hex().replace("0x", "")[:10])
                    if net:
                        bn = l["blockNumber"] if isinstance(l["blockNumber"], int) else int(l["blockNumber"], 16)
                        t_new = time.time() - (head - bn) * SPB
                        if r[1].lower() not in SECOND:
                            second_note(r[0], r[1], t_new, net)
                            net2_add(r[0], r[1], t_new, net)
                        SECOND[r[1].lower()] = (r[0], r[1], t_new, net)
                last = to
        except Exception:
            time.sleep(2)
        time.sleep(1.5)


def second_pick(tried, net=2):
    """The newest coin of this network (2-4) not looked at yet, while its trading is on."""
    n, blocked = sig2_state(net)
    if blocked or n >= NET_CFG[net]["max"]:
        return None
    now = time.time()
    # 06.11: any coin still alive, as on the first network (a restart lost the rest of KII). The dead ones leave NET2_COINS.
    # Older than ~35 s + a margin first: such a coin has shown that it lives, a newer one usually dies at once
    alive = [c for c in list(NET2_COINS.values()) if c.get("net", 2) == net and c["token"].lower() not in tried and (c["bnb"] is not None or now - c["t"] < SIG2_AGE)]
    if not alive:
        return None
    proven = [c for c in alive if now - c["t"] > SIG2_PROVEN]
    # 17.11: the network 5 runs one coin of ~6 min after another; "proven first" kept it on an old empty coin (PEIPEI) and it missed
    # two live coins (19:47-19:56, 19:55-20:06). A payer network takes the newest coin.
    c = max(alive if NET_CFG[net].get("kind") == "payer" else (proven or alive), key=lambda x: x["t"])
    return c["pair"], c["token"], c["t"]


def signal_second(found, mp, net=2):
    cfg = NET_CFG[net]
    first, creator = sig_first(*found)
    if cfg.get("kind") == "payer":  # the bots of this network are paid by a wallet of its own, the creator does not matter
        creator = cfg["payers"]
    elif not creator:
        return say(f"тест по сигналу, сеть {net}: {found[1][:10]}: создатель не найден, пропускаю")
    n0, _ = sig2_state(net)
    try:
        n, stuck = signal_coin(*found, n0, coin_type(first) if first is not None else None, creator, mp, net2=True, net_id=net)
    except Exception as e:
        return say(f"тест по сигналу, сеть {net}: ошибка на {found[1][:10]}: {str(e)[:100]}")
    if stuck:
        S[f"sig{net}_off"] = True  # also when the row could not be written
        msg = f"ТОРГОВЛЯ НА СЕТИ {net} ОСТАНОВЛЕНА: продажа не прошла, ставка {S.get(f'sig{net}_stake')} BNB потеряна. Остальные сети продолжают, если не сработал общий лимит падения"
        say(msg)
        tg("🧪 " + msg)
    elif n and n0 + n >= cfg["max"]:
        msg = f"ТЕСТ СЕТИ {net} ЗАКОНЧЕН: {cfg['max']} попыток, подробно {os.path.basename(cfg['file'])}"
        say(msg)
        tg("🧪 " + msg)


NET_THREADS = []


NET_ADD_LOCK = threading.Lock()
OUR_CODES = ("5faca89801", "18449a1624")  # the code of the networks 1 and 2-4: such a family without a guard of its own would take their coins too


def add_net(e):
    """The panel's button: a family found by net_finder.py becomes a network of its own (small test stake); a running trading picks it up at once.
    The button and the automatic adding may come at once: one at a time."""
    with NET_ADD_LOCK:
        return _add_net(e)


def _add_net(e):
    if e["key"] in NET_KEYS:
        return NET_KEYS[e["key"]], False
    if e.get("hash") in OUR_CODES and not e.get("guard") or any(c.get("kind") in ("payer", "hold") and c.get("code") == e.get("hash") for c in NET_CFG.values()):
        return None, False
    n = max(max(NET_CFG), 5) + 1
    rec = dict(id=n, key=e["key"], hash=e.get("hash"), guard=e.get("guard") or "", size=e.get("size"), added=time.strftime("%Y-%m-%d %H:%M:%S"))
    try:
        old = json.load(open(NETS_EXTRA, encoding="utf-8"))
    except (OSError, ValueError):
        old = []
    with open(NETS_EXTRA, "w", encoding="utf-8") as f:
        json.dump(old + [rec], f, ensure_ascii=False, indent=1)
    NET_CFG[n] = extra_cfg(n, rec)
    NET_KEYS[rec["key"]] = n
    if NET_CFG[n]["guard"]:
        GUARD_NET[NET_CFG[n]["guard"][2:].lower()] = n
    else:
        CODE_NET[NET_CFG[n]["code"]] = n
    S["net_ids"] = sorted(NET_CFG)
    S[f"net{n}"] = dict(stake=net_stake(NET_CFG[n]), n=0, plus=0, pnl=0.0, off=False, coins_h=0, last_s=None, dead_h=0, max=None)
    S[f"sig{n}_run"] = 0
    say(f"ДОБАВЛЕНА СЕТЬ {n}: код {rec['hash']}, сторож {rec['guard'] or 'нет'}, ставка ${EXTRA_STAKE_USD:g}, без ограничения сделок, стоп при блокировке")
    if S["chain"] == "SIGNAL" and not S.get("sig_stop"):
        S["sig_now"][str(n)] = dict(phase="Тест по сигналу", detail="ищу монету")
        t = threading.Thread(target=second_loop, args=(SIG_MP, n), daemon=True)
        t.start()
        NET_THREADS.append(t)
    return n, True


NETS_OFF_FILE = os.path.join(HERE, "nets_off.json")  # 08.11 (user): networks switched off in the panel; kept across restarts


def _load_off():
    try:
        return {int(x) for x in json.load(open(NETS_OFF_FILE, encoding="utf-8"))}
    except (OSError, ValueError, TypeError):
        return set()


NET_OFF = _load_off()
S["nets_off"] = sorted(NET_OFF)


def net_on(net):
    """Off = no new coin and no new attempt; a buy already sent is still sold, a funded wallet gives its BNB back."""
    if net in NET_OFF:
        return False
    return not net_loss_hit(net)


def set_net_off(net, off):
    (NET_OFF.add if off else NET_OFF.discard)(net)
    with open(NETS_OFF_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(NET_OFF), f)
    S["nets_off"] = sorted(NET_OFF)
    if S.get("chain") == "SIGNAL":
        nphase(net, "Выключена" if off else "Тест по сигналу", "торговля этой сети выключена в панели" if off else "ищу монету")
    say(f"сеть {net}: торговля " + ("ВЫКЛЮЧЕНА (новых сделок не будет, начатая доведётся до продажи)" if off else "включена"))


NET_AUTO_FILE = os.path.join(HERE, "net_auto.json")
AUTO_MIN_STRONG, AUTO_PER_DAY = 3, 10**9  # besides "fits": strong signals (>= 3 % of the pool) seen; 07.11 (user): no daily limit of networks


def auto_on():
    try:
        return bool(json.load(open(NET_AUTO_FILE, encoding="utf-8")).get("on", True))
    except (OSError, ValueError):
        return True


def auto_add_loop():
    """07.11 (user): a network that fits our rule is added by itself, as with the button. Stricter than the button: it also needs strong
    signals, and not more than AUTO_PER_DAY a day (a run of false families must not flood the trading)."""
    while True:
        time.sleep(60)
        S["net_auto"] = auto_on()
        try:
            with open(os.path.join(HERE, "net_finder.json"), encoding="utf-8") as f:
                d = json.load(f)
            if not d.get("at") or time.time() - d["at"] > 600:
                continue
            for n, cfg in list(NET_CFG.items()):
                if cfg.get("kind") != "payer":
                    continue
                for fam in d.get("families", []):
                    if fam.get("hash") == cfg["code"]:
                        for p, cnt in fam.get("payers", []):
                            if cnt >= 5 and p not in cfg["payers"]:
                                cfg["payers"].add(p)  # the set SigWatch reads: a new payer is watched at once
                                S[f"net{n}"]["payer"] = payer_text(cfg)
                                if n == 5:
                                    save_payers()
                                say(f"сеть {n}: новый плательщик ботам {p[:10]} ({cnt} оплаченных покупок за 2 ч), слежу и за ним")
            if not S["net_auto"]:
                continue
            day = time.strftime("%Y-%m-%d")
            today = sum(1 for c in NET_CFG.values() if str(c.get("added") or "").startswith(day))
            for fam in d.get("families", []):
                if today >= AUTO_PER_DAY:
                    break
                if fam.get("fits") and fam.get("strong", 0) >= AUTO_MIN_STRONG and fam["key"] not in NET_KEYS:
                    n, new = add_net(fam)
                    if n and new:
                        today += 1
                        msg = f"СЕТЬ {n} ДОБАВЛЕНА АВТОМАТИЧЕСКИ: код {fam['size']} байт {fam['hash']}, за сутки {fam['exact']} оплаченных создателем покупок, {fam['strong']} сильных; ставка $1"
                        say(msg)
                        tg("🆕 " + msg)
        except Exception as e:
            say("автодобавление сетей: " + str(e)[:80])


def second_loop(mp, net=2):
    """06.11 (user): the networks 2-4 trade at the same time as the first, each in its own thread."""
    tried = set()
    while S["chain"] and not S.get("sig_stop") and sig_alive():
        try:
            if not net_on(net):
                nphase(net, "Выключена", "торговля этой сети выключена в панели")
                time.sleep(1)
                continue
            nphase(net, "Тест по сигналу", "ищу монету")
            second = second_pick(tried, net)
            if not second:
                time.sleep(0.3)
                continue
            tried.add(second[1].lower())
            signal_second(second, mp, net)
            if not net_on(net):  # switched off while on this coin: when it is on again, the coin may still be alive
                tried.discard(second[1].lower())
        except Exception as e:
            say(f"тест по сигналу, сеть {net}: {str(e)[:100]}")
            time.sleep(3)


def hold_loop(net=6):
    """10.11 (user): the network 6 (kind "hold", family 3553). Finds its new pools by the token's code, buys once per coin at HOLD_ENTRY s,
    sells at HOLD_EXIT s or earlier (the pool falls from its peak, the trading stops, the network is switched off)."""
    cfg = NET_CFG[net]
    code, guards = cfg.get("hold_code") or cfg["code"], cfg.get("hold_guards")
    entry, latest = cfg["entry"], cfg["latest"]

    def ours(tok):
        if Web3.keccak(bytes(w3.eth.get_code(tok))).hex().replace("0x", "")[:10] != code:
            return False
        return not guards or w3.eth.get_storage_at(tok, 6).hex()[-40:].lower() in guards  # slot 6 = the guard (the networks 3/4)
    last = w3.eth.block_number - int(latest / SPB) - 20  # a restart still catches a coin before its entry
    coins, done = {}, set()
    try:
        hold_resume(net)
    except Exception as e:
        say(f"сеть {net}: восстановление сделки: {str(e)[:100]}")
    while S["chain"] and not S.get("sig_stop") and sig_alive():
        try:
            n_done, stopped = sig2_state(net)
            if stopped:
                nphase(net, "Остановлена", "продажа не прошла, сеть остановлена")
                time.sleep(5)
                continue
            if n_done >= cfg["max"]:  # 13.11: the network 7 test is one trade
                nphase(net, "Тест окончен", f"сделок {n_done} из {cfg['max']}")
                time.sleep(5)
                continue
            if not net_on(net):
                nphase(net, "Выключена", "торговля этой сети выключена в панели")
                time.sleep(1)
                continue
            head = w3.eth.block_number
            if head > last:
                for l in w3.eth.get_logs({"address": B.FACTORY.address, "topics": [B.PAIR_CREATED_TOPIC], "fromBlock": last + 1, "toBlock": min(head, last + 500)}):
                    r = B.parse_pair_created(l)
                    if r and r[1].lower() not in coins and ours(r[1]):
                        bn = l["blockNumber"] if isinstance(l["blockNumber"], int) else int(l["blockNumber"], 16)
                        coins[r[1].lower()] = (r[0], r[1], time.time() - (head - bn) * SPB)
                        say(f"сеть {net}: новая монета семьи {cfg['fam']} {r[1][:10]}, возраст {(head - bn) * SPB:.0f} с; вход на {entry} с")
                last = min(head, last + 500)
            now = time.time()
            for k in [k for k, v in coins.items() if now - v[2] > latest + 60]:
                coins.pop(k)
                done.discard(k)
            ready = [v for k, v in coins.items() if k not in done and entry <= now - v[2] <= latest]
            if not ready:
                wait_s = min((entry - (now - v[2]) for k, v in coins.items() if k not in done and now - v[2] < entry), default=None)
                nphase(net, "Тест по сигналу", f"жду монету: вход через {wait_s:.0f} с" if wait_s else f"жду новую монету семьи {cfg['fam']}")
                time.sleep(2)
                continue
            pair, token, t0 = ready[0]
            done.add(token.lower())
            hold_coin(pair, token, t0, net)
        except Exception as e:
            if "beyond current head" in str(e):  # 13.11: one node a block behind the other; not an error, look again soon
                time.sleep(0.5)
                continue
            say(f"сеть {net}: {str(e)[:100]}")
            time.sleep(3)


SEND_TEST_FILE = os.path.join(HERE, "send_test.csv")


def send_test(per_node=20, urls=None):
    """10.11 (user): which sending node lands a transaction soonest. A free W wallet gets a little BNB from A and sends 0-BNB transfers to itself,
    each through ONE node only, one at a time; for each: the head when sent (head_now, the fastest of two nodes), the block it landed in.
    ~21000 gas × 0.06 gwei each. Results in send_test.csv and the log; the BNB goes back to A."""
    urls = list(urls or B.CFG["send_rpc_urls"])
    i = take_wallet(iter(plan_ids()), "0x" + "00" * 20)
    if i is None:
        return say("тест скорости узлов: нет свободного кошелька")
    w, wn = wallet(i), f"W{i}"
    price = max(A.gas_price, int(SIG_GWEI * 1e9))
    res = {u: [] for u in urls}
    try:
        if not fund(i, int(21_000 * price * (len(urls) * per_node + 5) * 1.5)):
            return say("тест скорости узлов: кошелёк не получил BNB")
        w.resync()
        say(f"ТЕСТ СКОРОСТИ УЗЛОВ: {len(urls)} узла × {per_node} переводов с {wn}")
        new = not os.path.exists(SEND_TEST_FILE)
        with open(SEND_TEST_FILE, "a", newline="", encoding="utf-8-sig") as f:
            wr = csv.writer(f, delimiter=";")
            if new:
                wr.writerow(["time", "node", "send_head", "land_block", "blocks", "post_ms", "error"])
            for k in range(per_node):
                for u in urls:
                    if S.get("sendtest_stop"):
                        return say("тест скорости узлов остановлен")
                    tx = {"chainId": 56, "nonce": w.nonce, "to": w.address, "value": 0, "data": b"", "gas": 21_000, "gasPrice": price}
                    raw = "0x" + w.acct.sign_transaction(tx).raw_transaction.hex().removeprefix("0x")
                    h0, t0 = head_now(), time.time()
                    err, land = "", ""
                    try:
                        w._post(u, raw)
                        w.nonce += 1
                    except Exception as e:
                        err = str(e)[:60]
                        w.resync()
                    ms = round((time.time() - t0) * 1000)
                    if not err:
                        h = Web3.to_hex(Web3.keccak(hexstr=raw))
                        for _ in range(60):  # up to ~30 s
                            try:
                                land = w3.eth.get_transaction(h)["blockNumber"] or ""
                            except Exception:
                                land = ""
                            if land:
                                break
                            time.sleep(0.5)
                        if not land:
                            err = "не попала за 30 с"
                            w.resync()
                    if land:
                        res[u].append(land - h0)
                    wr.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), u, h0, land, (land - h0) if land else "", ms, err])
                    f.flush()
                    nphase(0, "Тест узлов", f"{k + 1}/{per_node}: {u.split('//')[1][:24]} → +{(land - h0) if land else '?'} бл.")
        lines = []
        for u in urls:
            v = sorted(res[u])
            if v:
                lines.append(f"{u.split('//')[1]}: попала {len(v)}/{per_node}, медиана +{v[len(v) // 2]} бл., +1 и раньше {sum(x <= 1 for x in v)}, +3 и позже {sum(x >= 3 for x in v)}")
            else:
                lines.append(f"{u.split('//')[1]}: ни одна не попала")
        msg = "ИТОГ ТЕСТА СКОРОСТИ УЗЛОВ (блоков от отправки до попадания):\n" + "\n".join(lines)
        say(msg)
        tg("🧪 " + msg)
    finally:
        S["sig_now"].pop("0", None)
        catch_back(w, wn)
        BUSY.discard(i)


HOLD_CUR = os.path.join(HERE, "hold_current.json")  # the network 6 holds a coin ~7.5 min: a restart in between must still sell it


def hold_resume(net=6):
    """At the start of the network 6: a coin bought before a restart is held on (or sold at once if its time is over)."""
    try:
        c = json.load(open(NET_CFG[net]["cur"], encoding="utf-8"))
    except (OSError, ValueError):
        return
    say(f"сеть {net}: после перезапуска найдена купленная монета {c['sym']} на W{c['i']} — веду её до продажи")
    with WALLET_LOCK:
        BUSY.add(c["i"])
    hold_coin(Web3.to_checksum_address(c["pair"]), Web3.to_checksum_address(c["token"]), c["t0"], net, resume=c)


HOP7_GAS_MIN, HOP7_GAS_FIXED = 0.00015, 0.0004  # BNB: hand-overs a minute (~30, with the top-ups) and the buy, seller, sell, returns


def hop_kit(token, tr):
    """13.11 (network 7): the network 1's all_fast tools for a held coin: feed() keeps fresh wallets with gas for one transfer
    ready ahead, prepare_seller() approves a fresh wallet for the final sale, and the first next holder is taken before the buy."""
    taken = []
    nh = make_next_holder(token, None, tr, reserve=taken)
    kit = dict(nh=nh, stop=False, seller={}, taken=taken)
    threading.Thread(target=nh.feed, args=(lambda: kit["stop"],), daemon=True).start()
    threading.Thread(target=prepare_seller, args=(token, nh, tr, kit["seller"]), daemon=True).start()
    try:
        kit["nxt"] = nh()
    except Exception:
        kit["nxt"] = (None, None)
    return kit


def hold_hop(kit, tok, token, sym, pool, w, i, wn, amt, t0, t_buy, bnb0, cfg, net, tr, cur):
    """13.11 DACITY: the guards of the networks 3/4 mark an outside holder ~16 blocks after it gets the coin. The coin goes on
    to a fresh wallet every CHAIN_FAST_EVERY s (~5 blocks held) until the exit, then to the approved seller.
    Returns (wallet, index, name, amount, why, hand-overs, stuck reason or "")."""
    guard = Web3.to_checksum_address("0x" + w3.eth.get_storage_at(token, 6).hex()[-40:])
    marked = lambda a: tok.functions.allowance(guard, a).call() > 0
    nh, seller = kit["nh"], kit["seller"]
    nxt_i, nxt = kit.get("nxt") or (None, None)
    buyer_w, buyer_i, buyer_n = w, i, wn
    h, h_i, name, hops, t_hold = w, i, wn, 0, 0.0
    b0 = pool.r_bnb / 1e18
    px = dict(b=b0, peak=b0, hit=None, addr=h.address)

    def watch():  # the pool and the holder's mark, read here so a slow node never holds up a hand-over
        while not kit["stop"]:
            try:
                pool.refresh()
                px["b"] = pool.r_bnb / 1e18
                px["peak"] = max(px["peak"], px["b"])
            except Exception:
                pass
            try:
                a = px["addr"]
                if a and marked(a):
                    px["hit"] = a
            except Exception:
                pass
            time.sleep(1)
    threading.Thread(target=watch, daemon=True).start()

    def save():
        cur.update(i=h_i, amt=amt, gas=tr.gas)
        try:
            with open(cfg["cur"], "w", encoding="utf-8") as f:
                json.dump(cur, f)
        except OSError:
            pass

    def buyer_back():  # the buyer passed the coin on: what is left of its gas goes back to A
        if h is not buyer_w:
            return
        def run():
            catch_back(buyer_w, buyer_n)
            BUSY.discard(buyer_i)
        threading.Thread(target=run, daemon=True).start()

    why = ""
    while True:
        age, held, b = time.time() - t0, time.time() - t_buy, px["b"]
        if px["hit"] == h.address:
            return h, h_i, name, amt, f"застряли на {name}", hops, f"сторож пометил держателя {name}"
        why = (f"время: {age / 60:.1f} мин" if age >= cfg["exit"]
               else f"тест: {held:.0f} с передач" if cfg.get("hop_hold") and held >= cfg["hop_hold"]
               else f"пул упал {b:.2f} от пика {px['peak']:.2f}" if b < px["peak"] * cfg["drop"]
               else "нажат «Стоп»" if not S["chain"]
               else "сеть выключена" if not net_on(net) else "")
        if why:
            break
        nphase(net, "Держу и перекидываю", f"{sym} на {name}, {age / 60:.1f} мин, пул {b:.2f} BNB ({(b / bnb0) ** 2 * 100 - 100:+.1f}% цены), передач {hops}")
        if time.time() - t_hold < CHAIN_FAST_EVERY:
            time.sleep(0.1)
            continue
        if nxt is None:
            try:
                nxt_i, nxt = nh()
            except Exception:
                nxt_i, nxt = None, None
            if nxt is None:
                why = "кончились кошельки"
                break
        mark_used(token, f"W{nxt_i}")
        try:
            hb2 = h.send(token, tok.encode_abi("transfer", args=[nxt.address, amt]), gas=HOP_LIMIT, gas_price=chain_price())
        except Exception as e:
            say(f"сеть {net}: передача {name} → W{nxt_i} не отправлена ({str(e)[:60]}) — повторяю")
            try:
                if h_i is not None and w3.eth.get_balance(h.address) < HOP_LIMIT * chain_price() * 1.05:
                    fund(h_i, int(HOP_LIMIT * chain_price() * 1.5), tr)
            except Exception:
                pass
            time.sleep(0.3)
            continue
        t_sent = time.time()
        rc2 = tr.tx(h, hb2) or tr.tx(h, hb2, 40)
        ok = bool(rc2 and rc2["status"] == 1)
        hop_timing(sym, hops + 1, name, f"W{nxt_i}", (t_sent - t_hold) if hops else 0.0, time.time() - t_sent, ok)
        if not ok:
            return h, h_i, name, amt, f"застряли на {name}", hops, "сторож пометил до передачи" if rc2 else "передача не подтвердилась за 20 с"
        buyer_back()
        h, h_i, name, hops, t_hold = nxt, nxt_i, f"W{nxt_i}", hops + 1, time.time()
        px["addr"] = h.address
        amt = received(rc2, token, h.address) or retry(lambda: tok.functions.balanceOf(h.address).call())
        save()
        try:
            nxt_i, nxt = nh()
        except Exception:
            nxt_i, nxt = None, None
    nphase(net, "Продаю", f"{sym}: {why}")
    s = seller
    if s.get("w") is not None and h is not s["w"]:  # the approved seller takes the coin and sells at once
        mark_used(token, f"W{s['i']}")
        try:
            hs = h.send(token, tok.encode_abi("transfer", args=[s["w"].address, amt]), gas=HOP_LIMIT, gas_price=chain_price())
            rc3 = tr.tx(h, hs) or tr.tx(h, hs, 40)
        except Exception as e:
            say(f"сеть {net}: передача продавцу W{s['i']} не отправлена: {str(e)[:60]}")
            rc3 = None
        if rc3 and rc3["status"] == 1:
            buyer_back()
            h, h_i, name, hops = s["w"], s["i"], f"W{s['i']}", hops + 1
            amt = received(rc3, token, h.address) or retry(lambda: tok.functions.balanceOf(h.address).call())
            save()
            return h, h_i, name, amt, why, hops, ""
    # no seller ready: the holder itself gets the sell gas and approves (it never approved this token)
    if h_i is not None:
        fund(h_i, seller_gas(), tr)
    h.gas_price = A.gas_price
    rc4 = tr.tx(h, h.approve(token))
    if not rc4 or rc4["status"] != 1:
        tr.tx(h, h.approve(token))
    return h, h_i, name, amt, why, hops, ""


def hold_coin(pair, token, t0, net, resume=None):
    cfg = NET_CFG[net]
    tok = w3.eth.contract(token, abi=TOK_ABI)
    try:
        sym = tok.functions.symbol().call()[:20]
    except Exception:
        sym = token[:8]
    pool = B.Pool(pair, token, sym, 0, 0)
    if resume:
        i, stake, bnb, hb = resume["i"], resume["stake"], resume["bnb"], resume["hb"]
        w, wn, tr = wallet(i), f"W{i}", Trade()
        tr.gas = resume["gas"]
        price = max(A.gas_price, int(SIG_GWEI * 1e9))
        w.gas_price = price
        common = resume["common"]
        try:
            amt = tok.functions.balanceOf(w.address).call()
        except Exception:
            amt = resume["amt"]
        if not amt:  # sold before the restart, or never bought
            os.remove(cfg["cur"])
            BUSY.discard(i)
            return say(f"сеть {net}: на W{i} монеты {sym} нет — восстанавливать нечего")
        kit = hop_kit(token, tr) if cfg.get("hop") else None
    else:
        prev = pool.r_bnb / 1e18
        time.sleep(3)  # growing: one more look
        pool.refresh()
        bnb = pool.r_bnb / 1e18
        common = dict(file=cfg["file"], time=time.strftime("%Y-%m-%d %H:%M:%S"), token=token, symbol=sym, kind=cfg["pool_kind"], attempt=sig2_state(net)[0] + 1, source="удержание",
                      pool_bnb=round(bnb, 3), signal_age_s=round(time.time() - t0, 1))
        if bnb < cfg["min_bnb"] or bnb < prev * 0.999:
            say(f"сеть {net}: {sym}: пул {bnb:.2f} BNB на {time.time() - t0:.0f} с — не растёт или мал, пропускаю")
            return
        A.refresh_gas_price()
        price = max(A.gas_price, int(SIG_GWEI * 1e9))
        stake = net_stake(cfg)
        S[f"sig{net}_stake"] = stake
        wei = Web3.to_wei(stake, "ether")
        i = take_wallet(iter(plan_ids()), token)
        if i is None:
            return say(f"сеть {net}: {sym}: нет свободного кошелька")
        w, wn, tr = wallet(i), f"W{i}", Trade()
        w.gas_price = price
    try:
        if not resume:
            nphase(net, "Покупаю", f"{sym}: готовлю {wn}, ставка {stake} BNB")
            kit = None
            if cfg.get("hop"):  # the buyer only buys and hands on: no approve; the next holder and the seller are made ready before the buy
                need = wei + int(B.CFG["gas_limit_buy"] * price * 1.2) + int(HOP_LIMIT * chain_price() * 1.5)
                # 13.11 review: A must hold the gas of the whole chain; run dry, the feed stops and a holder waits for a top-up
                # (up to FEED_WAIT 5 s + a confirmation) next to a guard that marks at ~7 s. MP: 0.000272 BNB for 2 min with the setup.
                mins = min(cfg["exit"] - (time.time() - t0), cfg.get("hop_hold") or 1e9) / 60
                gas_all = HOP7_GAS_MIN * mins + HOP7_GAS_FIXED
                have = w3.eth.get_balance(A.address) / 1e18
                if have < stake + gas_all:
                    BUSY.discard(i)
                    return say(f"сеть {net}: {sym}: на A {have:.5f} BNB, а на сделку нужно ~{stake + gas_all:.5f} (ставка + газ {mins:.0f} мин перекидывания) — пропускаю")
                kit = hop_kit(token, tr)
                if kit["nxt"][1] is None:
                    kit["stop"] = True
                    return say(f"сеть {net}: {sym}: нет кошелька для первой передачи — пропускаю")
            else:
                need = wei + int((B.CFG["gas_limit_buy"] + 100_000 + B.CFG["gas_limit_sell"] + 21_000) * price * 1.2)
            if not fund(i, need, tr):
                if kit:
                    kit["stop"] = True
                return say(f"сеть {net}: {sym}: {wn} не получил BNB")
            if not kit:
                rc = tr.tx(w, w.approve(token))
                if not rc or rc["status"] != 1:
                    return say(f"сеть {net}: {sym}: approve с {wn} не прошёл")
            pool.refresh()
            min_out = int(pool.quote_buy(wei) * 0.97)  # no hurry here: a price guard instead of a race
            mark_used(token, wn)
            S[f"sig{net}_run"] = S.get(f"sig{net}_run", 0) + 1
            nphase(net, "Покупаю", f"{sym}: покупка на {time.time() - t0:.0f} с, пул {pool.r_bnb / 1e18:.2f} BNB")
            hb = w.buy(token, wei, min_out)
            rc = tr.tx(w, hb) or tr.tx(w, hb, 40)
            amt = received(rc, token, w.address)
            if not amt:
                try:
                    amt = tok.functions.balanceOf(w.address).call()
                except Exception:
                    amt = 0
            if not amt:
                if kit:
                    kit["stop"] = True
                buy_failed(net, sym, wn, rc)
                sig_note(sig_row, **common, land_block=tx_block(hb), gas_bnb=f"{tr.gas:.8f}", note="покупка не прошла")
                return
            buy_ok(net)
            try:
                with open(cfg["cur"], "w", encoding="utf-8") as f:
                    json.dump(dict(pair=pair, token=token, t0=t0, i=i, sym=sym, amt=amt, stake=stake, gas=tr.gas, bnb=bnb, hb=hb, common=common, t_buy=time.time()), f)
            except OSError:
                pass
        t_buy = (resume or {}).get("t_buy") or time.time()
        peak, why, errs, hops, stuck = 0.0, "", 0, 0, ""
        if kit:  # 13.11: the network 7 hands the coin on every ~1 s until the exit, then the coin goes to the approved seller
            try:
                w, i, wn, amt, why, hops, stuck = hold_hop(kit, tok, token, sym, pool, w, i, wn, amt, t0, t_buy, bnb, cfg, net, tr,
                                                           dict(pair=pair, token=token, t0=t0, sym=sym, stake=stake, bnb=bnb, hb=hb, common=common, t_buy=t_buy))
            finally:
                kit["stop"] = True
        while not kit:  # hold: the pool is read every 2 s; a node error must not leave the coin unsold
            try:
                pool.refresh()
                errs = 0
            except Exception:
                errs += 1
                if time.time() - t0 < cfg["exit"] and errs < 30:
                    time.sleep(2)
                    continue
            b = pool.r_bnb / 1e18
            peak = max(peak, b)
            age = time.time() - t0
            if age >= cfg["exit"]:
                why = f"время: {age / 60:.1f} мин"
            elif b < peak * cfg["drop"]:
                why = f"пул упал {b:.2f} от пика {peak:.2f}"
            elif not S["chain"]:
                why = "нажат «Стоп»"
            elif not net_on(net):
                why = "сеть выключена"
            if why:
                break
            nphase(net, "Держу", f"{sym}: {age / 60:.1f} мин, пул {b:.2f} BNB ({(b / bnb) ** 2 * 100 - 100:+.1f}% цены), продажа на {cfg['exit'] / 60:.1f} мин")
            time.sleep(2)
        nphase(net, "Продаю", f"{sym}: {why}")
        got, err, hs, sent = 0.0, stuck, None, []
        for _ in range(0 if stuck else 5):
            try:
                hs = w.sell(token, amt, 0, gas_price=price)
                sent.append(hs)
                rc = tr.tx(w, hs) or tr.tx(w, hs, 40)
            except Exception as e:
                err, rc = f"продажа не отправлена: {str(e)[:50]}", None
            if rc and rc["status"] == 1:
                got = B.bnb_received(rc)
                break
            err = err or "продажа отклонена"
            time.sleep(0.5)
        for h in sent if not got else ():
            late = wait(w, h, 3)
            if late and late["status"] == 1:
                got, hs = B.bnb_received(late), h
                break
        held = time.time() - t_buy
        if got:
            catch_back(w, wn)
            tr.gas += 21_000 * chain_price() / 1e18
        pnl = got - stake - tr.gas
        sig_note(sig_row, **common, land_block=tx_block(hb), sell_block=tx_block(hs) if hs else "", hold_s=round(held, 1), got_bnb=f"{got:.8f}", gas_bnb=f"{tr.gas:.8f}",
                 pnl_bnb=f"{pnl:.8f}", pnl_pct=round(pnl / stake * 100, 2), note=why if got else err)
        with REC_LOCK:
            before = S["run_pnl"]
            try:
                record(token, sym, stake, hops, held, f"сеть {net}, сигнал (удержание" + (f", {hops} передач" if kit else "") + f"): {why}", got, tr.gas, err if not got else "")  # "сеть N, сигнал" = the panel's signal tables
            except Exception as e:
                S["run_pnl"] = before + pnl
                say(f"сеть {net}: запись сделки не удалась: {str(e)[:80]}; итог {pnl:+.6f} BNB")
        try:
            os.remove(cfg["cur"])  # the trade is written down: nothing to resume
        except OSError:
            pass
        if not got:
            S[f"sig{net}_off"] = True
            msg = f"ТОРГОВЛЯ НА СЕТИ {net} ОСТАНОВЛЕНА: продажа {sym} не прошла ({err}), монеты на {wn}. Остальные сети продолжают"
            say(msg)
            tg("🧪 " + msg)
            w = None  # the coin stays on the wallet: it stays reserved
    finally:
        if w is not None:
            catch_back(w, wn)
            BUSY.discard(i)
        k = locals().get("kit")
        if k:  # the chain's reserved wallets are free again, except the one left holding a coin
            k["stop"] = True
            for x in k.get("taken", []):
                if w is not None or x != i:
                    BUSY.discard(x)


def signal_test():
    if not CLOCK["on"]:
        CLOCK["on"] = True
        threading.Thread(target=clock_loop, daemon=True).start()
    start_second()
    for n in NET_CFG:
        S[f"sig{n}_run"] = 0
    S["sig_stop"] = False
    S["sig_now"] = {str(k): dict(phase="Тест по сигналу", detail="ищу монету") for k in [1] + sorted(NET_CFG)}
    S["sig_stake"], S["sig_max_loss"] = sig_stake(), round(retry(lambda: w3.eth.get_balance(A.address)) / 1e18 * SIG_LOSS_PCT, 6)
    say(f"ТЕСТ ПО СИГНАЛУ: без ограничения попыток, ставка ${SIG_STAKE_USD:g} на сети 1, сеть 2 ${NET_CFG[2]['stake_usd']:g}, сети 3 и 4 по ${NET_CFG[3]['stake_usd']:g}, сеть 6 (удержание 3553) ${NET_CFG[6]['stake_usd']:g}, сеть 7 (удержание монет сетей 3/4 до 27 мин) ${NET_CFG[7]['stake_usd']:g}, добавленные по $1, без ограничения сделок, сейчас {S['sig_stake']} BNB, платёж от {SIG_REAL_SHARE * 100:.0f}% пула, стоп при падении на {S['sig_max_loss']} BNB от лучшего итога запуска")
    global SIG_MP
    done, S["run_pnl"], S["sig_peak"], tried, mp = 0, 0.0, 0.0, set(), None
    A.resync()  # send_speed.py may have used A: the cached nonce is old
    S["sig_done"], S["sig_n"] = 0, SIG_N
    if SIG_MEMPOOL:
        if SIG_MP is None:
            SIG_MP = SwitchMempool(keep=20)
            SIG_MP.start()
        SIG_MP_REF["mp"] = SIG_MP
        mp = SIG_MP
    t_nets = [threading.Thread(target=hold_loop, args=(n,), daemon=True) if NET_CFG[n].get("kind") == "hold" else threading.Thread(target=second_loop, args=(mp, n), daemon=True)
              for n in list(NET_CFG)]
    for t in t_nets:
        t.start()
    NET_THREADS[:] = t_nets
    while S["chain"] and done < SIG_N and sig_alive():
        if not net_on(1):
            nphase(1, "Выключена", "торговля этой сети выключена в панели")
            time.sleep(1)
            continue
        nphase(1, "Тест по сигналу", "ищу монету")
        found = catch_live()
        if not found or found[1].lower() in tried or time.time() - found[2] > SIG_OLD_COIN:  # 05.11: the csv is ~14 s behind, wait_launch would look only past the moment it starts and miss a coin in between
            time.sleep(1)
            continue
        tried.add(found[1].lower())
        first, creator = sig_first(*found)
        kind = coin_type(first) if first is not None else None
        if not creator:
            say(f"тест по сигналу: {found[1][:10]}: создатель не найден, попробую снова")
            tried.discard(found[1].lower())
            time.sleep(5)
            continue
        try:  # a long run: a node error on one coin must not end the test
            n, stuck = signal_coin(*found, done, kind, creator, mp)
        except Exception as e:
            say(f"тест по сигналу: ошибка на {found[1][:10]}: {str(e)[:100]}. Пробую снова")
            done = S["sig_done"]
            tried.discard(found[1].lower())
            time.sleep(5)
            continue
        done += n
        if stuck:
            say("ТОРГОВЛЯ ОСТАНОВЛЕНА: кошелёк сети 1 заблокирован, ставка потеряна")
            break
        if not net_on(1):
            tried.discard(found[1].lower())
    S["sig_stop"] = True
    if S["chain"]:  # ended by itself (the loss limit, a lost wallet of the network 1), not by «Стоп»: a restart must not start it again
        sig_keep(False)
    for t in list(NET_THREADS):  # the other networks finish their attempts: no coin may be left unsold
        t.join(120)
    S["sig_now"] = {}
    if not sig_alive():
        lt = loss_total() * (S.get("bnb_usd") or SIG_BNB_FALLBACK)
        tf = test_failed()
        msg = (f"ТОРГОВЛЯ ОСТАНОВЛЕНА: убыток с {LOSS_SINCE[:16]} дошёл до {lt:+.2f} $ (лимит −{LOSS_LIMIT_USD:g} $)" if lt <= -LOSS_LIMIT_USD
               else "ТОРГОВЛЯ ОСТАНОВЛЕНА. " + tf if tf
               else f"ТЕСТ ПО СИГНАЛУ ОСТАНОВЛЕН: итог запуска упал на {S['sig_max_loss']} BNB от лучшего")
        say(msg)
        tg("🛑 " + msg)
    res = f"ИТОГ ТЕСТА ПО СИГНАЛУ: попыток {done}, результат {S['run_pnl']:+.6f} BNB ({S['run_pnl'] * S['bnb_usd']:+.2f} $), подробно signal_test.csv"
    say(res)
    tg("🧪 " + res)


# 13.11 (bot_quote_check.py, 151 bot buys of the networks 3/4): the paid bot takes its price ~2 blocks after the payment, tolerance 0.01 %.
# Our buy landing before that does not hurt it; landing after it cancels its buy. Our buy now carries a router deadline = the payment
# block's second + SIG_DEADLINE_S: a late buy reverts (only its gas, ~$0.006). History: +1 s kept 131 of 151, bot passed 81, +1.1 % on average
# (no deadline +0.7 %). The payment block's second is estimated from the local time we saw the payment and the chain clock offset (CLOCK).
SIG_DEADLINE_S = 1
# 19.11: nets 1/2 bots now quote earlier (their buy after ours reverts 9/9 even at +4..+6 blocks, ours lands +2). There the buy must land
# in the payment block's own second (≈ +1 block); later ones revert for gas only. Other nets keep SIG_DEADLINE_S.
SIG_DEADLINE_NET = {1: 0, 2: 0}
CLOCK = {"off": None, "samples": deque(maxlen=15), "on": False}


def clock_loop():
    """Offset of the chain clock to ours: block time (ms, BSC keeps them in mixHash) minus the moment we got the block; the largest
    (= least delayed) of the recent samples."""
    last = None
    while True:
        try:
            b = w3.eth.get_block("latest")
            t = time.time()
            if b["number"] != last:
                last = b["number"]
                x = int.from_bytes(bytes(b.get("mixHash") or b"")[-8:], "big") if b.get("mixHash") else 1000
                ms = b["timestamp"] * 1000 + (x if x < 1000 else 0)
                CLOCK["samples"].append(ms / 1000 - t)
                CLOCK["off"] = max(CLOCK["samples"])
        except Exception:
            pass
        time.sleep(7)


def sig_deadline(sig, net=None):
    """The router deadline for a signal buy: the payment block's second + SIG_DEADLINE_S.
    13.11 LAPTOP: the estimate from our clock was 1 s late, the buy landed at +4 blocks and passed. Now the block's own time:
    a payment read in a block carries it; a mempool payment carries the newest block's second (it lands in the next block,
    at that second or the next one, so the deadline is never looser). The clock estimate is only the last resort."""
    if sig.get("ts"):
        return int(sig["ts"]) + SIG_DEADLINE_NET.get(net, SIG_DEADLINE_S)
    off = CLOCK["off"] if CLOCK["off"] is not None else 0.0
    return int(sig["t"] + off) - 1 + SIG_DEADLINE_NET.get(net, SIG_DEADLINE_S)


def buy_until(w, token, wei, deadline):
    data = B.router.encode_abi("swapExactETHForTokensSupportingFeeOnTransferTokens", args=[0, [B.WBNB, token], w.address, deadline])
    return w.send(B.ROUTER, data, value=wei, gas=B.CFG["gas_limit_buy"])


# 19.11 (user: "do not give up on the networks 1/2, test with a tiny stake"). Their bots now quote at the END OF THE PAYMENT BLOCK (0.001 %):
# a buy between the payment and the bot kills the bot's buy, a buy in or BEFORE the payment block does not. The creator pays on a 5.00 s
# clock (phase of its own per coin, window ~+-0.5 s), and payments of >= 3 % of the pool come in streaks (next big after a big one: 82 %),
# most often 5-6 ticks later, then 9-11. So after a big payment we buy BEFORE the expected next ticks and sell after the bot's buy
# (or at once if no payment came / it came before us). Test: $1, PRETICK_MAX attempts, every attempt in pretick_test.csv.
PRETICK_ON = {1: True, 2: False}  # 23.11: net 2 negative in the offline check (tick_offline.py)
PRETICK_USD, PRETICK_MAX, PRETICK_SHARE = 1.0, 40, 0.03
PRETICK_K = (6, 10)  # 23.11 tick_offline.py: net 1 ticks 6+10, window +1: plus 7 of 7 days (+0.5..+1.9%/attempt, hit ~13%)
PRETICK_LEAD = 1.3  # s: our send before the expected payment (send->land ~0.9 s): we want to land ~0.4 s before it
PRETICK_WAIT_MS, PRETICK_BOT_BLOCKS, PRETICK_CAP_BLOCKS = 1200, 8, 9
# 19.11 (user: «не сдаваться»): the clock buy goes as a ONE-tx bundle with maxBlockNumber = the predicted payment block - MARGIN.
# The builders never put it later than that block, so it can never land after the payment (which breaks the nets 1/2 bots);
# if it does not make it in time it simply does not exist: no buy, no gas. Sent BUNDLE_LEAD s before the cap block's time.
PRETICK_BUNDLE, PRETICK_BUNDLE_MARGIN, PRETICK_BUNDLE_LEAD = True, 1, 1.0  # 23.11: 1.5 -> 1.0 s: land nearer the cap (shorter hold before the guard); a late bundle just does not exist
PRETICK_BLOCK_MS = 450.0  # fallback block interval when the coin's payments span too few blocks
if PRETICK_BUNDLE:
    PRETICK_LEAD = PRETICK_BUNDLE_MARGIN * PRETICK_BLOCK_MS / 1000 + PRETICK_BUNDLE_LEAD  # the main loop picks ticks by the real send moment
PRETICK_FILE = os.path.join(HERE, "pretick_test.csv")
PRETICK_COLS = ["time", "net", "token", "symbol", "tick", "anchor_block", "target_ms", "send_ms", "land_block", "land_ms", "land_vs_target_ms",
                "pay_block", "pay_ms", "pay_vs_target_ms", "share_pct", "order", "bot_block", "sell_block", "hold_s", "stake", "got_bnb", "gas_bnb",
                "pnl_bnb", "pnl_pct", "pnl_before_gas_pct", "note", "mode", "pred_block", "cap_block"]


def pretick_done():
    try:
        return sum(1 for _ in csv.DictReader(open(PRETICK_FILE, encoding="utf-8-sig"), delimiter=";"))
    except Exception:
        return 0


def blk_ms(n):
    b = retry(lambda: w3.eth.get_block(n))
    x = int.from_bytes(bytes(b.get("mixHash") or b"")[-8:], "big") if b.get("mixHash") else 0
    return b["timestamp"] * 1000 + (x if x < 1000 else 0), b


def chain_now_ms():
    return (time.time() + (CLOCK["off"] or 0.0)) * 1000


def pretick_row(**r):
    new = not os.path.exists(PRETICK_FILE)
    with open(PRETICK_FILE, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=PRETICK_COLS, delimiter=";")
        if new:
            w.writeheader()
        w.writerow({c: r.get(c, "") for c in PRETICK_COLS})
    S["pretick"] = pretick_stats()


def pretick_stats():
    """19.11: the panel's card «Пакеты и до тика»."""
    try:
        rows = list(csv.DictReader(open(PRETICK_FILE, encoding="utf-8-sig"), delimiter=";"))
    except Exception:
        rows = []
    bg = [float(r["pnl_before_gas_pct"]) for r in rows if r.get("pnl_before_gas_pct")]
    return dict(n=len(rows), max=PRETICK_MAX, on=bool(PRETICK_ON.get(1) or PRETICK_ON.get(2)) and len(rows) < PRETICK_MAX,
                before=sum(1 for r in rows if r.get("order") in ("до платежа", "в блоке платежа")), bot=sum(1 for r in rows if r.get("bot_block")),
                bundles=sum(1 for r in rows if r.get("tick") == "пакет" or r.get("mode") == "пакет"), bg=round(sum(bg) / len(bg), 2) if bg else None,
                pnl=round(sum(float(r.get("pnl_bnb") or 0) for r in rows), 8),
                last=[dict(time=r.get("time", ""), sym=r.get("symbol", ""), net=r.get("net", ""), tick=r.get("tick", ""), land=r.get("land_vs_target_ms", ""),
                           order=r.get("order", ""), bot=bool(r.get("bot_block")), bg=r.get("pnl_before_gas_pct", ""), pnl=r.get("pnl_bnb", ""))
                      for r in rows[-5:]][::-1])


S["pretick"] = pretick_stats()


def pretick_summary(final=False):
    try:
        rows = list(csv.DictReader(open(PRETICK_FILE, encoding="utf-8-sig"), delimiter=";"))
        pn = sum(float(r["pnl_bnb"] or 0) for r in rows)
        bg = [float(r["pnl_before_gas_pct"]) for r in rows if r["pnl_before_gas_pct"]]
        bot = sum(1 for r in rows if r["bot_block"])
        before = sum(1 for r in rows if r["order"] in ("до платежа", "в блоке платежа"))
        msg = (f"{'ИТОГ ТЕСТА' if final else 'Тест'} «до тика»: {len(rows)} попыток, платёж после нашей покупки {before}, бот купил {bot}, "
               f"до газа в среднем {sum(bg) / len(bg) if bg else 0:+.1f}%, итог {pn:+.6f} BNB ({pn * S['bnb_usd']:+.2f} $)"
               + (". Сети 1/2 вернулись к обычному режиму" if final else ""))
        say(msg)
        tg("🧪 " + msg)
    except Exception as e:
        say("до тика: итог: " + str(e)[:80])


def pretick_coin(pair, token, t_seen, kind, creator, mp, net):
    """Returns (attempts, stuck) like signal_coin."""
    tag = f"сеть {net}, "
    cfg = NET_CFG.get(net, {})
    guard = Web3.to_checksum_address(cfg["guard"]) if net != 1 and cfg.get("guard") else GUARD
    tok = w3.eth.contract(token, abi=TOK_ABI)
    try:
        sym = tok.functions.symbol().call()[:20]
    except Exception:
        sym = token[:8]
    say(f"ДО ТИКА: {tag}{sym} {token}: жду крупный платёж создателя (от {PRETICK_SHARE:.0%} пула), потом покупаю заранее перед тиками {PRETICK_K}")
    A.refresh_gas_price()
    price = max(A.gas_price, int(SIG_GWEI * 1e9))
    pool, watch = B.Pool(pair, token, sym, 0, 0), SigWatch(creator, mp)
    pays, pend, bq = [], deque(), deque()  # pays: dict(block, ms, share, to, value) sorted by block; pend: mempool payments waiting for their block; bq: for a bundle
    ready, stop = deque(), {"v": False}
    ids = iter(plan_ids())
    n, peak, last_pool, last_pay = 0, 0.0, 0.0, {"t": time.time()}
    stake = round(min(PRETICK_USD / (S.get("bnb_usd") or SIG_BNB_FALLBACK), SIG_STAKE_CAP), 6)
    wei = Web3.to_wei(stake, "ether")
    # 23.11: the clock buy is signed at SIG_BUY_GWEI (0.3), not at `price` (0.06): the old sum left the wallet short, builders refused «insufficient funds for gas * price + value» (CCJ 16:01, 16:06)
    need = wei + int((B.CFG["gas_limit_buy"] * max(price, int(SIG_BUY_GWEI * 1e9)) + (100_000 + B.CFG["gas_limit_sell"] + 21_000) * price) * 1.2)

    def add_pay(block, s):
        try:
            ms, _ = blk_ms(block)
        except Exception:
            return
        share = s["value"] / (pool.r_bnb / 1e18) if pool.r_bnb else 0
        pays.append(dict(block=block, ms=ms, share=share, to=s["to"], value=s["value"]))
        pays.sort(key=lambda p: p["block"])

    def pump():
        while watch.signals:
            s = watch.signals.popleft()
            if s["value"] < SIG_MIN_BNB or s["value"] > SIG_MAX_BNB:
                continue
            last_pay["t"] = time.time()
            if s["block"]:
                add_pay(s["block"], s)
            else:
                pend.append(s)
                if BUNDLE_ON and s.get("raw"):
                    bq.append(s)
        for _ in range(len(pend)):
            s = pend.popleft()
            b = tx_block(s["h"]) if time.time() - s["t"] > 1.0 else ""
            if b:
                add_pay(int(b), s)
            elif time.time() - s["t"] < 20:
                pend.append(s)

    def preparer():
        while not stop["v"]:
            try:
                if len(ready) < 2 and pays and pays[-1]["share"] >= PRETICK_SHARE:
                    i = take_wallet(ids, token)
                    if i is None:
                        time.sleep(1)
                        continue
                    tr, w = Trade(), wallet(i)
                    w.gas_price = price
                    if fund(i, need, tr):
                        rc = tr.tx(w, w.approve(token))
                        if rc and rc["status"] == 1:
                            ready.append((i, w, tr))
                            continue
                    catch_back(w, f"W{i}")
                    BUSY.discard(i)
                time.sleep(0.2)
            except Exception as e:
                say(f"до тика: подготовка кошелька: {str(e)[:80]}")
                time.sleep(1)

    def targets():
        """Chain ms of the next ticks after the last big payment, phase = median offset of the coin's recent payments."""
        if not pays or pays[-1]["share"] < PRETICK_SHARE:
            return []
        last = pays[-1]
        rec = [((p["ms"] - last["ms"]) % 5000 + 2500) % 5000 - 2500 for p in pays[-8:]]
        d = st.median(rec)
        return [(k, last["ms"] + k * 5000 + d) for k in PRETICK_K]

    def block_ms():
        """The chain's ms per block from the coin's own payments (block + ms known), else PRETICK_BLOCK_MS."""
        if len(pays) >= 2 and pays[-1]["block"] - pays[0]["block"] >= 20:
            v = (pays[-1]["ms"] - pays[0]["ms"]) / (pays[-1]["block"] - pays[0]["block"])
            if 300 <= v <= 1000:
                return v
        return PRETICK_BLOCK_MS

    def attempt(k, T, anchor, bsig=None):
        i, w, tr = ready.popleft()
        wn = f"W{i}"
        mark_used(token, wn)
        pb = cap = None
        if bsig is None and PRETICK_BUNDLE and anchor:  # 19.11: the clock buy as a bundle capped below the payment block
            bt_ = block_ms()
            pb = anchor["block"] + round((T - anchor["ms"]) / bt_)
            cap = pb - PRETICK_BUNDLE_MARGIN
            cap_ms = anchor["ms"] + (cap - anchor["block"]) * bt_
            while chain_now_ms() < cap_ms - PRETICK_BUNDLE_LEAD * 1000:
                time.sleep(0.002)
            if (head_now() or 0) >= cap:
                ready.appendleft((i, w, tr))
                say(f"до тика: {sym}: тик {k} — потолок блок {cap} уже прошёл, пропуск")
                return None, 0.0, 0.0
            nphase(net, "Покупаю", f"{sym}: до тика {k} пакетом, не позже блока {cap}, {wn}")
            send_ms = chain_now_ms()
            hb, raw = sign_buy(w, token, wei, int(time.time()) + 30, max(price, int(SIG_BUY_GWEI * 1e9)))
            ans = bundle_send([raw], cap)
            S["bundle_last"] = "; ".join(f"{u.split('/')[2].split('.')[0]}: {'принят' if 'result' in (v or {}) else str((v or {}).get('error', {}).get('message', ''))[:40]}"
                                         for u, v in ans.items()) or "нет ответа"
            rc = tr.tx(w, hb, max(1.0, (cap_ms - chain_now_ms()) / 1000 + 2.0))
            if rc:
                with w.lock:
                    w.nonce += 1
                BUNDLE_STATS["landed"] += 1
            S["bundle"] = dict(BUNDLE_STATS)
            if not rc:  # make sure: the cap block must be past and our tx absent before the wallet is reused
                t0 = time.time()
                while (head_now() or 0) <= cap + 1 and time.time() - t0 < 4:
                    time.sleep(0.05)
                if tx_block(hb):
                    rc = tr.tx(w, hb, 10)
                    if rc:
                        with w.lock:
                            w.nonce += 1
                        BUNDLE_STATS["landed"] += 1
                        S["bundle"] = dict(BUNDLE_STATS)
            if not rc and tx_block(hb):  # landed but no receipt: never leave the coins unsold
                rc = {"status": 1, "logs": []}
                say(f"до тика: {sym}: пакет попал, квитанции нет — продаю по балансу")
            if not rc:
                ready.appendleft((i, w, tr))
                say(f"до тика: {sym}: тик {k}, пакет не попал до блока {cap} — покупки нет, газ не потрачен ({S['bundle_last']})")
                return None, 0.0, 0.0
        elif bsig is None:
            while chain_now_ms() < T - PRETICK_LEAD * 1000:
                time.sleep(0.002)
            nphase(net, "Покупаю", f"{sym}: до тика {k}, {wn}")
            send_ms = chain_now_ms()
            hb = buy_until(w, token, wei, int(time.time()) + 30)
            rc = tr.tx(w, hb) or tr.tx(w, hb, 40)
        else:  # 19.11: a bundle with the creator's payment: in its block or nothing
            nphase(net, "Покупаю", f"{sym}: пакетом с платежом, {wn}")
            send_ms = chain_now_ms()
            hb, rc = bundle_buy(w, tr, token, wei, int(time.time()) + 30, max(price, int(SIG_BUY_GWEI * 1e9)), bsig["raw"])
            if not rc:
                ready.appendleft((i, w, tr))
                say(f"до тика: {sym}: пакет не попал в блок — покупки нет, газ не потрачен")
                return None, 0.0, 0.0
        amt = received(rc, token, w.address) if rc and rc["status"] == 1 else 0
        if rc and rc["status"] == 1 and not amt and cap:
            try:
                amt = retry(lambda: tok.functions.balanceOf(w.address).call())
            except Exception:
                pass
        row = dict(time=time.strftime("%Y-%m-%d %H:%M:%S"), net=net, token=token, symbol=sym, tick=k if bsig is None else "пакет", anchor_block=anchor["block"] if anchor else "", target_ms=int(T),
                   send_ms=int(send_ms), stake=stake, mode="пакет" if (cap or bsig is not None) else "обычная", pred_block=pb or "", cap_block=cap or "")
        if not amt:
            lb = tx_block(hb)
            catch_back(w, wn)
            BUSY.discard(i)
            row.update(land_block=lb, gas_bnb=f"{tr.gas:.8f}", pnl_bnb=f"{-tr.gas:.8f}", note="покупка не прошла")
            return row, -tr.gas, 0.0
        lb = rc.get("blockNumber") or tx_block(hb)  # 19.11: bot.parse_raw_receipt gives no blockNumber (KeyError cost 7 attempts)
        try:
            lb = int(lb)
            land_ms, _ = blk_ms(lb)
        except Exception:
            lb, land_ms = head_now(), chain_now_ms()  # unknown: sell at once below (the cap counts from now)
        row.update(land_block=lb, land_ms=int(land_ms), land_vs_target_ms=int(land_ms - T))
        t_land = time.time()
        pay, bot_block = None, ""
        try:  # 19.11: whatever fails while waiting, the coins are sold below
            while True:  # the payment of this tick, then the bot's buy; never longer than PRETICK_CAP_BLOCKS after our buy
                pump()
                head = head_now() or lb
                if pay is None:
                    pay = next((p for p in pays if p["ms"] > T - 2500), None)
                if pay is not None:
                    if pay["block"] < lb:
                        break  # it came before us: our buy is after the bot's quote, the bot fails
                    b = watch.bought.get(pay["to"], 0)
                    if b >= pay["block"]:
                        bot_block = b
                        break
                    if head >= pay["block"] + PRETICK_BOT_BLOCKS:
                        break
                elif chain_now_ms() > T + PRETICK_WAIT_MS:
                    break
                if head >= lb + PRETICK_CAP_BLOCKS:
                    break
                time.sleep(0.01)
        except Exception as e:
            say(f"до тика: {sym}: сбой при ожидании ({str(e)[:60]}), продаю")
        nphase(net, "Продаю", f"{sym}: до тика {k}, {'бот купил' if bot_block else 'платежа нет' if pay is None else 'без бота'}")
        got, why, hs, sent = 0.0, "", None, []
        for _ in range(5):
            try:
                hs = w.sell(token, amt, 0, gas_price=price)
                sent.append(hs)
                rs = tr.tx(w, hs) or tr.tx(w, hs, 40)
            except Exception as e:
                why, rs = f"продажа не отправлена: {str(e)[:50]}", None
            if rs and rs["status"] == 1:
                got = B.bnb_received(rs)
                break
            why = why or "продажа отклонена (метка сторожа?)"
            time.sleep(0.3)
        for h in sent if not got else ():
            late = wait(w, h, 3)
            if late and late["status"] == 1:
                got, hs = B.bnb_received(late), h
                break
        held = time.time() - t_land
        if not got:
            try:
                why += "; метка сторожа есть" if tok.functions.allowance(guard, w.address).call() > 0 else "; метки нет, монеты на " + wn
            except Exception:
                pass
        if got:
            catch_back(w, wn)
            tr.gas += 21_000 * chain_price() / 1e18
            BUSY.discard(i)
        pnl = got - stake - tr.gas
        order = "" if pay is None else ("в блоке платежа" if pay["block"] == lb else "до платежа" if pay["block"] > lb else "после платежа")
        row.update(pay_block=pay["block"] if pay else "", pay_ms=pay["ms"] if pay else "", pay_vs_target_ms=int(pay["ms"] - T) if pay else "",
                   share_pct=round(pay["share"] * 100, 1) if pay else "", order=order, bot_block=bot_block, sell_block=tx_block(hs) if hs else "",
                   hold_s=round(held, 2), got_bnb=f"{got:.8f}", gas_bnb=f"{tr.gas:.8f}", pnl_bnb=f"{pnl:.8f}", pnl_pct=round(pnl / stake * 100, 1),
                   pnl_before_gas_pct=round((got - stake) / stake * 100, 1) if got else "", note=why if not got else "")
        with REC_LOCK:
            try:
                record(token, sym, stake, 0, held, tag + ((f"до тика {k}{' пакетом' if cap else ''}: ") if bsig is None else "пакет с платежом: ") + (order or "платежа нет") + (", бот купил" if bot_block else ""), got, tr.gas,
                       why if not got else "")
            except Exception as e:
                say(f"до тика: запись сделки не удалась: {str(e)[:80]}")
        return row, pnl, got

    def pt_stuck(n_):
        """23.11: a stuck $1 test coin (guard mark) turns off only this net; nets 3/4 keep trading."""
        set_net_off(net, True)
        msg = f"до тика: {sym}: монеты застряли (метка сторожа?) — сеть {net} выключена, остальные сети торгуют"
        say(msg)
        tg("⚠️ " + msg)
        return n_, False

    threading.Thread(target=preparer, daemon=True).start()
    tried = set()
    try:
        while S["chain"] and net_on(net) and sig_alive() and pretick_done() < PRETICK_MAX:
            pump()
            if time.time() - last_pool > 3:
                pool.refresh()
                peak, last_pool = max(peak, pool.r_bnb / 1e18), time.time()
                if pool.r_bnb / 1e18 < max(0.5 * peak, SIG_DEAD_BNB):
                    say(f"до тика: {tag}{sym}: пул упал, монету закончил")
                    break
                if net == 1:
                    newest = catch_live()
                    if newest and newest[1].lower() != token.lower() and newest[2] > t_seen + 5:
                        say(f"до тика: вышла новая монета, {sym} закончил")
                        break
                elif time.time() - last_pay["t"] > SIG2_QUIET and any(c["t"] > t_seen and c["bnb"] and c.get("net", 2) == net for c in list(NET2_COINS.values())):
                    say(f"до тика: {tag}{sym}: {SIG2_QUIET} с без платежей, есть монета новее, перехожу")
                    break
            while bq and time.time() - bq[0]["t"] > 0.35:  # too old: its block is already being built
                bq.popleft()
            if bq and ready:
                s0 = bq.popleft()
                if pool.r_bnb and s0["value"] / (pool.r_bnb / 1e18) >= PRETICK_SHARE:
                    row, pnl, got = attempt(0, chain_now_ms(), None, bsig=s0)
                    if row is not None:
                        n += 1
                        sig_note(pretick_row, **row)
                        say(f"ПАКЕТ {sym}: платёж {row.get('pay_vs_target_ms') or 'нет'} мс, {row.get('order') or 'платежа нет'}, бот {'купил' if row.get('bot_block') else 'нет'}, "
                            f"итог {pnl:+.8f} BNB ({row.get('pnl_before_gas_pct', '?')}% до газа); пакетов попало {BUNDLE_STATS['landed']} из {BUNDLE_STATS['sent']}")
                        if not got and str(row.get("note", "")).startswith("продажа"):
                            return pt_stuck(n)
                    continue
            now = chain_now_ms()
            tg_ = targets()
            nxt = next(((k, T) for k, T in tg_ if T - PRETICK_LEAD * 1000 > now + 150 and (pays[-1]["block"], k) not in tried), None)
            if nxt is None or not ready or nxt[1] - PRETICK_LEAD * 1000 - now > 1500:
                det = (f"{sym}: жду крупный платёж" if not tg_ else f"{sym}: следующий тик через {(nxt[1] - now) / 1000:.1f} с" if nxt else f"{sym}: тики прошли, жду платёж")
                nphase(net, "До тика", det + f", готово кошельков {len(ready)}, попыток {pretick_done()} из {PRETICK_MAX}")
                time.sleep(0.02)
                continue
            k, T = nxt
            anchor = pays[-1]
            tried.add((anchor["block"], k))
            row, pnl, got = attempt(k, T, anchor)
            if row is None:
                continue
            n += 1
            sig_note(pretick_row, **row)
            say(f"ДО ТИКА {sym} тик {k}: посадка {row.get('land_vs_target_ms', '?')} мс от цели, платёж {row.get('pay_vs_target_ms') or 'нет'} мс, "
                f"{row.get('order') or 'платежа нет'}, бот {'купил' if row.get('bot_block') else 'нет'}, итог {pnl:+.8f} BNB ({row.get('pnl_before_gas_pct', '?')}% до газа)")
            if not got and str(row.get("note", "")).startswith("продажа"):
                return pt_stuck(n)
            if pretick_done() % 10 == 0:
                pretick_summary()
        if pretick_done() >= PRETICK_MAX and (PRETICK_ON[1] or PRETICK_ON[2]):
            PRETICK_ON[1] = PRETICK_ON[2] = False
            pretick_summary(final=True)
            set_net_off(net, True)  # 23.11: the normal mode of nets 1/2 loses since 19.11 (the bot quotes at the payment block): off until the user decides
        return n, False
    finally:
        stop["v"] = True
        watch.close()
        time.sleep(0.5)
        while ready:
            i, w, tr = ready.popleft()
            catch_back(w, f"W{i}")
            BUSY.discard(i)


def signal_coin(pair, token, t_seen, done, kind, creator, mp, net2=False, net_id=2):
    if PRETICK_ON.get(net_id if net2 else 1) and pretick_done() < PRETICK_MAX and not bt():
        return pretick_coin(pair, token, t_seen, kind, creator, mp, net_id if net2 else 1)
    net = net_id if net2 else 1
    tag = f"сеть {net}, " if net2 else ""
    cfg = NET_CFG.get(net, {})
    skip_file, row_file = (cfg["skip"], cfg["file"]) if net2 else (SKIP_FILE, SIG_FILE)
    tok = w3.eth.contract(token, abi=TOK_ABI)
    try:
        sym = tok.functions.symbol().call()[:20]
    except Exception:
        sym = token[:8]
    say(f"тест по сигналу: {tag}{sym} {token}, тип {kind} BNB, " + (f"создатель {creator[:10]}" if isinstance(creator, str) else f"плательщик {', '.join(sorted(c[:10] for c in creator))}"))
    A.refresh_gas_price()
    price = max(A.gas_price, int(SIG_GWEI * 1e9))
    buy_price = max(price, int(SIG_BUY_GWEI * 1e9))
    bots = None if net2 else FastBots(pair, token, t_seen)  # 06.11: only a number on the panel; a coin of the second network: its own count below
    pool, watch = B.Pool(pair, token, sym, 0, 0), SigWatch(creator, mp)
    if net2:
        S[f"sig{net}_token"] = token.lower()
    n, w, wn, tr, peak, stake, wei = 0, None, "", None, 0.0, 0.0, 0
    paid_n, paid_at = 0, time.time()
    mp0 = mp.count if mp else 0
    ids = iter(plan_ids())
    last_pool = 0.0
    try:
        while S["chain"] and net_on(net) and not (net2 and S.get("sig_stop")) and n < (cfg["max"] - done if net2 else SIG_PER_COIN) and done + n < SIG_N and sig_alive():
            if time.time() - last_pool > 3:
                pool.refresh()
                peak, last_pool = max(peak, pool.r_bnb / 1e18), time.time()
                if pool.r_bnb / 1e18 < max(0.5 * peak, SIG_DEAD_BNB):
                    say(f"тест по сигналу: {tag}{sym}: пул упал, эту монету закончил")
                    break
                if net2:
                    if sum(watch.src.values()) != paid_n:
                        paid_n, paid_at = sum(watch.src.values()), time.time()
                    if time.time() - paid_at > (SIG5_QUIET if NET_CFG.get(net, {}).get("kind") == "payer" else SIG2_QUIET) and any(c["t"] > t_seen and c["bnb"] and c.get("net", 2) == net for c in list(NET2_COINS.values())):
                        say(f"тест по сигналу: {tag}{sym}: {SIG2_QUIET} с без платежей, есть монета новее, перехожу")
                        break
                newest = None if net2 else catch_live()
                if newest and newest[1].lower() != token.lower() and newest[2] > t_seen + 5:  # the csv is ~14 s behind: an older row is not a new coin
                    say(f"тест по сигналу: вышла новая монета, {sym} закончил")
                    break
            if net2 and w is None and not any(watch.src.values()):  # most coins of this network die in ~35 s without a payment: no gas on them
                nphase(net, "Тест по сигналу", f"{sym}: жду первый платёж создателя")
                time.sleep(0.05)
                continue
            if w is None:
                i = take_wallet(ids, token)
                if i is None:
                    break
                wid, tr, w, wn = i, Trade(), wallet(i), f"W{i}"
                w.gas_price = price
                stake = net_stake(cfg) if net2 else sig_stake()  # 06.11 (user): networks 1 and 2 the same; 3 and 4 a small test stake
                S[f"sig{net}_stake" if net2 else "sig_stake"] = stake
                wei = Web3.to_wei(stake, "ether")
                nphase(net, "Тест по сигналу", f"{sym}: готовлю {wn}, ставка {stake} BNB")
                need = wei + int((B.CFG["gas_limit_buy"] + 100_000 + B.CFG["gas_limit_sell"] + 21_000) * price * 1.2) + int(B.CFG["gas_limit_buy"] * max(0, buy_price - price) * 1.2)
                if not fund(i, need, tr):
                    say(f"тест по сигналу: {wn} не получил BNB, эту монету закончил")
                    break
                rc = tr.tx(w, w.approve(token))
                if not rc or rc["status"] != 1:
                    say(f"тест по сигналу: approve с {wn} не прошёл, эту монету закончил")
                    break
                while watch.signals:  # what came during the preparation is too old to buy on
                    old = watch.signals.popleft()
                    if SIG_MIN_BNB <= old["value"] <= SIG_MAX_BNB:
                        sig_skip(old, token, sym, kind, pool.r_bnb / 1e18, "шла подготовка кошелька", skip_file)
            if not watch.signals:
                nphase(net, "Тест по сигналу", f"{sym}: жду платёж создателя боту, попыток на монете {n}" + (f", покупок ботов {bots.count}" if bots else ""))
                time.sleep(0.005)
                continue
            sig = watch.signals.popleft()
            age = time.time() - sig["t"]
            share = sig["value"] / (pool.r_bnb / 1e18) if pool.r_bnb else 0
            if sig["value"] < SIG_MIN_BNB or sig["value"] > SIG_MAX_BNB:
                continue
            if age > SIG_MAX_AGE:
                sig_skip(sig, token, sym, kind, pool.r_bnb / 1e18, "шла другая попытка", skip_file)
                continue
            bt_any = bool(bt()) and sig["src"] == "мемпул" and bool(sig.get("raw"))  # 19.11 (user): the bundle test takes a payment of any size
            if not bt_any and share < (max(cfg["share"], (0.005 + sig_gas_part(stake)) / 2 * SIG_MARGIN) if net2 and cfg.get("share") else sig_need(stake)):
                sig_skip(sig, token, sym, kind, pool.r_bnb / 1e18, "мелкий платёж", skip_file)
                continue
            if sig["block"] and head_now() - sig["block"] >= SIG_LATE_BLOCKS:
                sig_skip(sig, token, sym, kind, pool.r_bnb / 1e18, "поздно увидели", skip_file)
                continue
            if bt() and not (BUNDLE_ON and sig["src"] == "мемпул" and sig.get("raw")):
                sig_skip(sig, token, sym, kind, pool.r_bnb / 1e18, "тест пакетов: платёж виден только в блоке", skip_file)
                BT_SEEN["block"] = BT_SEEN.get("block", 0) + 1
                continue
            n += 1
            S[f"sig{net}_done" if net2 else "sig_done"] = done + n
            if net2:
                S[f"sig{net}_run"] = S.get(f"sig{net}_run", 0) + 1
            mark_used(token, wn)
            nphase(net, "Покупаю", f"{sym}: попытка {done + n}, ставка {stake}, платёж {sig['value']:.3f} BNB боту {sig['to'][:10]}")
            send_head, t_send = head_now(), time.time()
            w.gas_price = buy_price  # 08.11: only the buy is paid more, to land in the block right after the payment
            dl = sig_deadline(sig, net)
            # 19.11: a bundle only where an ordinary buy breaks the bot (nets 1/2: it quotes at the payment block) or in the bundle test;
            # nets 3/4 keep the ordinary buy, it lands +1 block and earned before
            if BUNDLE_ON and sig["src"] == "мемпул" and sig.get("raw") and (bt() or net in (1, 2)):
                w.gas_price = price
                hb, rc = bundle_buy(w, tr, token, wei, dl, buy_price, sig["raw"])
                if not rc:  # the bundle did not land: no buy, no gas; the wallet stays ready for the next payment
                    n -= 1
                    sig_skip(sig, token, sym, kind, pool.r_bnb / 1e18, "пакет не попал в блок", skip_file)
                    if bt():
                        pb = tx_block(sig["h"])
                        bt_row(time=time.strftime("%Y-%m-%d %H:%M:%S"), net=net, token=token, symbol=sym, pay_hash=sig["h"], pay_bnb=round(sig["value"], 6),
                               pool_bnb=round(pool.r_bnb / 1e18, 3), share_pct=round(share * 100, 2), seen_age_ms=int(age * 1000), send_head=send_head,
                               result="не попал", builders=S.get("bundle_last", ""), pay_block=pb, stake=stake, gas_bnb="0")
                        bt_log(f"сеть {net} {sym}: пакет НЕ попал (платёж {sig['value']:.4f} BNB = {share * 100:.1f}% пула, увидели за {age * 1000:.0f} мс до отправки, "
                               f"голова {send_head}, платёж в блоке {pb or '?'}; строители: {S.get('bundle_last', '')})")
                    continue
            else:
                try:
                    hb = buy_until(w, token, wei, dl)
                finally:
                    w.gas_price = price
                rc = tr.tx(w, hb) or tr.tx(w, hb, 40)  # 20 s without a receipt is not a failed buy: the coin could still arrive
            amt = received(rc, token, w.address)
            for _ in range(5):
                if amt:
                    break
                try:
                    amt = tok.functions.balanceOf(w.address).call()
                except Exception:
                    pass
                if amt or (rc and rc["status"] != 1):
                    break
                time.sleep(1)
            common = dict(file=row_file, time=time.strftime("%Y-%m-%d %H:%M:%S"), token=token, symbol=sym, kind=kind, attempt=done + n, source=sig["src"], pay_block=sig["block"] or "",
                          bnb_paid=round(sig["value"], 5), pool_bnb=round(pool.r_bnb / 1e18, 3), share_pct=round(share * 100, 1), signal_age_s=round(age, 3), send_head=send_head)
            if not amt:
                lb, late = tx_block(hb), False
                try:  # reverted in a block past the deadline: a late buy cancelled on purpose, not a ban on buying
                    late = bool(lb) and rc is not None and rc["status"] != 1 and w3.eth.get_block(lb)["timestamp"] > dl
                except Exception:
                    pass
                if not late:
                    buy_failed(net, sym, wn, rc)
                else:
                    say(f"тест по сигналу: {tag}{sym}: покупка опоздала (блок {lb}, платёж {sig['block'] or 'в мемпуле'}) — отменена сроком, потерян только газ")
                sig_note(sig_row, **common, land_block=lb, gas_bnb=f"{tr.gas:.8f}", note="опоздали — отменена сроком" if late else "покупка не прошла")
                catch_back(w, wn)
                BUSY.discard(wid)
                w = None
                continue
            buy_ok(net)
            t_land = time.time()
            # 24.11: the guards of nets 3/4 now mark 4 blocks after a buy (IGV 23.11; 19.11 it was 14-28). History 14-17.11: the bot at
            # +2/+3 blocks after us gave +2.5..+3.6 %, at +0/+1 -0.6 %. So on these nets the sale goes as soon as the bot bought or
            # SIG_FAST_HOLD blocks after our landing (it lands ~+2..+3, before the mark at +4), not after SIG_HOLD 4 blocks / 2 s.
            fast = SIG_FAST_HOLD.get(net) if net2 else None
            limit, since = head_now() + (fast if fast is not None else SIG_HOLD), sig["block"] or send_head
            bot_seen = lambda: watch.bought.get(sig["to"], 0) >= since
            while not bot_seen() and head_now() < limit and time.time() - t_land < (SIG_FAST_HOLD_S if fast is not None else SIG_HOLD_S):
                time.sleep(0.01)
            bot_block = watch.bought[sig["to"]] if bot_seen() else ""
            nphase(net, "Продаю", f"{sym}: {'бот купил' if bot_block else 'бот не купил'}, продаю с {wn}")
            got, why, hs, sent = 0.0, "", None, []
            for _ in range(5):
                try:
                    hs = w.sell(token, amt, 0, gas_price=price)
                    sent.append(hs)
                    rc = tr.tx(w, hs) or tr.tx(w, hs, 40)
                except Exception as e:
                    why, rc = f"продажа не отправлена: {str(e)[:50]}", None
                if rc and rc["status"] == 1:
                    got = B.bnb_received(rc)
                    break
                why = why or "продажа отклонена (метка сторожа?)"
                time.sleep(0.3)
            held = time.time() - t_land
            for h in sent if not got else ():  # an earlier sale may have landed late, and the next one failed only because of it
                late = wait(w, h, 3)
                if late and late["status"] == 1:
                    got, hs = B.bnb_received(late), h
                    break
            if not got:  # the gas stays on the wallet: without a mark the coins can still be sold from it
                try:
                    why += "; метка сторожа есть" if tok.functions.allowance(Web3.to_checksum_address(cfg["guard"]) if net2 and cfg.get("guard") else GUARD, w.address).call() > 0 else "; метки сторожа нет, монеты на кошельке " + wn
                except Exception:
                    pass
            sell_block, land = tx_block(hs) if hs else "", tx_block(hb)
            if got:
                catch_back(w, wn)
                tr.gas += 21_000 * chain_price() / 1e18
            pnl = got - stake - tr.gas
            sig_note(sig_row, **common, land_block=land, bot_buy_block=bot_block, before_bot=int(land < bot_block) if land and bot_block else "", sell_block=sell_block, hold_s=round(held, 2), got_bnb=f"{got:.8f}",
                    gas_bnb=f"{tr.gas:.8f}", pnl_bnb=f"{pnl:.8f}", pnl_pct=round(pnl / stake * 100, 1), note="" if got else why)
            if bt() and sig["src"] == "мемпул" and sig.get("raw"):
                pb = tx_block(sig["h"])
                same = "в блоке платежа" if pb and land and int(pb) == int(land) else f"платёж {pb}, мы {land}"
                bg = round((got - stake) / stake * 100, 1) if got else ""
                bt_row(time=time.strftime("%Y-%m-%d %H:%M:%S"), net=net, token=token, symbol=sym, pay_hash=sig["h"], pay_bnb=round(sig["value"], 6),
                       pool_bnb=round(pool.r_bnb / 1e18, 3), share_pct=round(share * 100, 2), seen_age_ms=int(age * 1000), send_head=send_head, result="попал",
                       builders=S.get("bundle_last", ""), pay_block=pb, land_block=land, bot_block=bot_block, hold_s=round(held, 2), stake=stake,
                       got_bnb=f"{got:.8f}", gas_bnb=f"{tr.gas:.8f}", pnl_bnb=f"{pnl:.8f}", pnl_before_gas_pct=bg, note=same if got else why)
                bt_log(f"сеть {net} {sym}: пакет ПОПАЛ ({same}), бот {'купил в ' + str(bot_block) if bot_block else 'не купил'}, "
                       f"{'продали: ' + format(bg, '+') + '% до газа' if got else 'НЕ ПРОДАЛИ: ' + why}, газ {tr.gas:.8f} BNB")
            with REC_LOCK:
                before = S["run_pnl"]
                try:
                    record(token, sym, stake, 0, held, tag + ("сигнал: перед ботом" if land and bot_block and land < bot_block else "сигнал: мимо" if not bot_block else "сигнал: не раньше бота"), got, tr.gas, why if not got else "")
                except Exception as e:  # the result counts even if the file is locked
                    S["run_pnl"] = before + pnl
                    say(f"тест по сигналу: запись сделки не удалась: {str(e)[:80]}; итог {pnl:+.6f} BNB")
            if not got:
                say(f"тест по сигналу: {tag}{sym}: продажа не прошла: {why}")
            w = None
            if not got:  # the coin stays on the wallet: it stays reserved
                return n, True
            BUSY.discard(wid)
        return n, False
    finally:
        say(f"тест по сигналу: {tag}{sym}: платежи создателя: заранее в мемпуле {watch.src['мемпул']}, только в блоке {watch.src['блок']}; транзакций в потоке мемпула за монету {(mp.count - mp0) if mp else 0}")
        watch.close()
        if net2:
            S[f"sig{net}_token"] = ""
        if bots:
            bots.stop = True
        if w is not None:
            catch_back(w, wn)
            BUSY.discard(wid)


LATE_HOLD, LATE_AFTER, LATE_EVERY, LATE_WAIT = 120, 120, 1.0, 240
LATE_FILE = os.path.join(HERE, "late_test.csv")


def late_test():
    """05.11 (user: "enter after the bots start buying"): on the next 1 BNB guard coin, after the first bot buy, a fresh
    wallet buys dust and the dust moves between wallets for LATE_HOLD s, nothing is sold. Do the bots go on buying?"""
    say("ТЕСТ ПОЗДНЕГО ВХОДА: жду новую монету на 1 BNB со сторожем")
    while S["chain"]:
        phase("Тест позднего входа", "жду новую монету на 1 BNB")
        found = wait_launch("chain")
        if not found:
            break
        pair, token, t_seen = found
        first = start_bnb(pair, token, t_seen)
        if first is not None and coin_type(first) == 1:
            return late_run(pair, token, t_seen)
        say(f"тест позднего входа: {token[:10]} не на 1 BNB ({first}), жду другую")
    say("тест позднего входа отменён")


def late_run(pair, token, t_seen):
    tok = w3.eth.contract(token, abi=TOK_ABI)
    try:
        sym = tok.functions.symbol().call()[:20]
    except Exception:
        sym = token[:8]
    tr, stop = Trade(), {"v": False}
    marked = lambda a: retry(lambda: tok.functions.allowance(GUARD, a).call()) > 0
    bots = BotCounter(pair, token, t_seen)
    A.refresh_gas_price()
    next_holder = make_next_holder(token, marked, tr)
    buyer_i = next_holder.fresh()
    h, name = wallet(buyer_i), f"W{buyer_i}"
    if buyer_i is None or not fund(buyer_i, int(DECOY_BNB * 1e18) + int(DECOY_GAS * chain_price() * 1.3) + fast_need(), tr):
        bots.stop = True
        return say("тест позднего входа: покупатель не получил BNB — отменён")
    h.gas_price = A.gas_price
    threading.Thread(target=next_holder.feed, args=(lambda: stop["v"] or not S["chain"],), daemon=True).start()
    say(f"тест позднего входа: {sym} {token}, жду первую покупку бота (до {LATE_WAIT} с)")
    try:
        while bots.count < 1 and time.time() - t_seen < LATE_WAIT:
            if not S["chain"]:
                return say("тест позднего входа отменён до покупки")
            phase("Тест позднего входа", f"{sym}: жду первую покупку бота, {time.time() - t_seen:.0f} с")
            time.sleep(0.3)
        if bots.count < 1:
            return say(f"тест позднего входа: {sym}: за {LATE_WAIT} с боты не купили, пыль не покупал")
        mark_used(token, name)
        data = B.router.encode_abi("swapExactETHForTokensSupportingFeeOnTransferTokens",
                                   args=[0, [B.WBNB, token], h.address, int(time.time()) + 60])
        t_buy, before = time.time(), bots.count
        rc = tr.tx(h, h.send(B.ROUTER, data, value=int(DECOY_BNB * 1e18), gas=DECOY_GAS))
        if not rc or rc["status"] != 1:
            return say("тест позднего входа: покупка пыли не прошла")
        tr.gas += DECOY_BNB
        amt = received(rc, token, h.address) or retry(lambda: tok.functions.balanceOf(h.address).call())
        hops, why = 0, ""
        say(f"тест позднего входа: {name} купил пыль {sym} на {t_buy - t_seen:.0f}-й с, передачи {LATE_HOLD} с")
        while S["chain"] and time.time() - t_buy < LATE_HOLD:
            phase("Тест позднего входа", f"{sym}: передач {hops}, покупок ботов после входа {bots.count - before}, "
                                         f"{time.time() - t_buy:.0f} из {LATE_HOLD} с")
            time.sleep(LATE_EVERY)
            nxt_i, nxt = next_holder()
            if nxt is None:
                why = "кончились кошельки"
                break
            mark_used(token, f"W{nxt_i}")
            hb = h.send(token, tok.encode_abi("transfer", args=[nxt.address, amt]), gas=HOP_LIMIT, gas_price=chain_price())
            rc = tr.tx(h, hb) or tr.tx(h, hb, 40)
            if not rc or rc["status"] != 1:
                why = f"передача с {name} не прошла (метка сторожа?)"
                break
            h, name, hops = nxt, f"W{nxt_i}", hops + 1
            amt = received(rc, token, h.address) or retry(lambda: tok.functions.balanceOf(h.address).call())
        t_end, during = time.time(), bots.count - before
        stop["v"] = True
        while S["chain"] and time.time() - t_end < LATE_AFTER:
            phase("Тест позднего входа", f"{sym}: передачи закончены, смотрю покупки ботов ещё "
                                         f"{LATE_AFTER - (time.time() - t_end):.0f} с")
            time.sleep(1)
        after = bots.count - before - during
        a0, a1, a2 = t_buy - t_seen, t_end - t_seen, time.time() - t_seen
        usual = usual2 = None
        try:
            cache = json.load(open(os.path.join(HERE, "gas_options_cache.json"), encoding="utf-8"))
            done = {r["token"].lower() for r in csv.DictReader(open(TRADES, encoding="utf-8-sig"), delimiter=";")}
            src = [c for t, c in cache.items() if c.get("kind") == 1 and c.get("buys") and t.lower() not in done]
            count = lambda c, x0, x1: sum(x0 < (b - c["t0"]) * SPB < x1 for b in c["buys"])
            usual = st.median(count(c, a0, a1) for c in src)
            usual2 = st.median(count(c, a1, a2) for c in src)
        except Exception as e:
            say("тест позднего входа: нет сравнения с архивом: " + str(e)[:60])
        new = not os.path.exists(LATE_FILE)
        with open(LATE_FILE, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            if new:
                w.writerow(["time", "token", "symbol", "buy_age_s", "hold_s", "hops", "bots_before", "bots_during", "usual_during",
                            "bots_after", "usual_after", "gas_bnb", "note"])
            w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), token, sym, round(a0, 1), round(t_end - t_buy, 1), hops, before, during,
                        usual, after, usual2, f"{tr.gas:.8f}", why])
        res = (f"ИТОГ ТЕСТА ПОЗДНЕГО ВХОДА {sym}: вход на {a0:.0f}-й с, передач {hops}. Покупок ботов при передачах: {during} "
               f"(обычно {usual}); после передач за {LATE_AFTER} с: {after} (обычно {usual2}); потрачено {tr.gas:.6f} BNB"
               + (f". {why}" if why else ""))
        say(res)
        tg("🧪 " + res)
    finally:
        stop["v"] = True
        bots.stop = True


def chain_run():
    if S["chain"] == "LATE":
        return late_test()
    if S["chain"] == "CATCH":
        return catch_test()
    if S["chain"] == "SIGNAL":
        return signal_test()
    if isinstance(S["chain"], str):
        tk = Web3.to_checksum_address(S["chain"])
        return chain_test(B.FACTORY.functions.getPair(tk, B.WBNB).call(), tk, time.time() - SET["entry"])
    phase("Тест цепочки", "жду новую монету со сторожем")
    say("ТЕСТ ЦЕПОЧКИ: жду новую монету со сторожем")
    found = wait_launch("chain")
    if found:
        return chain_test(*found)
    say("тест цепочки отменён")


def chain_test(pair, token, t_seen):
    tok = w3.eth.contract(token, abi=TOK_ABI)
    try:
        sym = tok.functions.symbol().call()[:20]
    except Exception:
        sym = token[:8]
    marked = lambda a: retry(lambda: tok.functions.allowance(GUARD, a).call()) > 0
    say(f"тест цепочки: {sym} {token}")
    n_hold = int(CHAIN_HOLD / CHAIN_EVERY) + 2
    ids = list(itertools.islice((i for i in range(1, 96) if not used(token, f"W{i}")
                                 and not marked(wallet(i).address)), n_hold))
    A.refresh_gas_price()
    price = A.gas_price
    need = int((CHAIN_K + 1) * HOP_LIMIT * price * 1.3) + int(DECOY_BNB * 1e18)  # the first holder also buys
    for k, i in enumerate(ids):
        if not S["chain"]:
            return say("тест цепочки отменён")
        phase("Тест цепочки", f"{sym}: газ на W{i} ({k + 1} из {len(ids)})")
        if not fund(i, need):
            return say(f"тест цепочки: W{i} не получил BNB — отменён")
    while time.time() - t_seen < SET["entry"]:
        if not S["chain"]:
            return say("тест цепочки отменён")
        phase("Тест цепочки", f"{sym}: покупка через {SET['entry'] - (time.time() - t_seen):.0f} с")
        time.sleep(0.3)
    holders = [wallet(i) for i in ids]
    names = [f"W{i}" for i in ids]
    for nm in names:
        mark_used(token, nm)
    h = holders[0]
    h.gas_price = price
    data = B.router.encode_abi("swapExactETHForTokensSupportingFeeOnTransferTokens",
                               args=[0, [B.WBNB, token], h.address, int(time.time()) + 60])
    rc = wait(h, h.send(B.ROUTER, data, value=int(DECOY_BNB * 1e18), gas=DECOY_GAS))
    if not rc or rc["status"] != 1:
        return say("тест цепочки: покупка не прошла")
    # 27.10 15:16 ABI: the buyer itself has nobody ahead in the guard's queue (marked 13 s after the buy, before the
    # first cycle at 20 s): the first crumbs + hand-over go right after the buy
    t0 = time.time()
    t_hold = t0 - CHAIN_EVERY
    amt = received(rc, token, h.address) or retry(lambda: tok.functions.balanceOf(h.address).call())
    log_rows, crumbs, c, why = [], [], 0, ""
    say(f"тест цепочки (экономный): {names[0]} купил {sym}; первые {LEAN_WARM} цикла по {CHAIN_K} крошек, дальше по "
        f"{LEAN_K}, передача каждые {CHAIN_EVERY} с")
    while S["chain"]:
        held = time.time() - t0
        if marked(h.address):  # the guard got the holder before it moved on: the scheme failed here
            why = f"сторож пометил держателя {names[c]} через {time.time() - t_hold:.0f} с"
            break
        if held >= CHAIN_HOLD:
            break
        if time.time() - t_hold < CHAIN_EVERY:
            phase("Тест цепочки", f"{sym} на {names[c]}, {held:.0f} из {CHAIN_HOLD} с, перевод через "
                                   f"{CHAIN_EVERY - (time.time() - t_hold):.0f} с")
            time.sleep(0.3)
            continue
        if c + 1 >= len(holders):
            why = "кончились кошельки"
            break
        nxt = holders[c + 1]
        nxt.gas_price = price
        crumb = max(1, amt // CHAIN_CRUMB)
        sent = []
        k = crumbs_for(c + 1, True)
        for _ in range(k):  # fresh addresses nobody holds the key of: the dust there is gone
            to = w3.eth.account.create().address
            sent.append(dict(addr=to, cycle=c + 1, h=h.send(token, tok.encode_abi("transfer", args=[to, crumb]),
                                                            gas=HOP_LIMIT, gas_price=price)))
        crumbs += sent
        hb = h.send(token, tok.encode_abi("transfer", args=[nxt.address, amt - crumb * k]), gas=HOP_LIMIT,
                    gas_price=price)
        rc = wait(h, hb)
        ok = bool(rc and rc["status"] == 1)
        try:
            blk = w3.eth.get_transaction(hb)["blockNumber"]
            cblk = [w3.eth.get_transaction(x["h"])["blockNumber"] for x in sent]
        except Exception:
            blk, cblk = None, []
        for x, b in zip(sent, cblk):
            x["block"] = b
        log_rows.append(dict(cycle=c + 1, frm=names[c], to=names[c + 1], block=blk, crumbs=cblk, ok=ok))
        say(f"тест цепочки: цикл {c + 1}: {names[c]} → {names[c + 1]} в блоке {blk}, крошки в {cblk}, "
            f"{'ok' if ok else 'НЕ ПРОШЁЛ'}")
        if not ok:
            why = f"перевод {names[c]} → {names[c + 1]} не прошёл"
            break
        c, h, t_hold = c + 1, nxt, time.time()
        amt = received(rc, token, h.address) or retry(lambda: tok.functions.balanceOf(h.address).call())
    phase("Тест цепочки", f"{sym}: сверяю метки")
    time.sleep(8)  # guard_gas() has written the exact mark blocks by now
    try:
        exact = {m["wallet"].lower(): int(m["block"]) for m in
                 csv.DictReader(open(MARKS, encoding="utf-8-sig"), delimiter=";")
                 if m["method"] == "095ea7b3" and m["token"].lower() == token.lower()}
    except Exception:
        exact = {}
    can_sell = False
    try:  # the last holder: could it still move the tokens into the pool (= sell)?
        w3.eth.call({"from": h.address, "to": token, "data": tok.encode_abi("transfer", args=[pair, amt])})
        can_sell = True
    except Exception:
        pass
    new = not os.path.exists(CHAIN_FILE)
    with open(CHAIN_FILE, "a", newline="", encoding="utf-8-sig") as f:
        wr = csv.writer(f, delimiter=";")
        if new:
            wr.writerow(["time", "token", "symbol", "kind", "cycle", "who", "address", "got_block", "mark_block"])
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        for k in range(c + 1):
            wr.writerow([stamp, token, sym, "держатель", k, names[k], holders[k].address,
                         log_rows[k - 1]["block"] if k else "", exact.get(holders[k].address.lower(), "")])
        for x in crumbs:
            wr.writerow([stamp, token, sym, "крошка", x["cycle"], "", x["addr"], x.get("block", ""),
                         exact.get(x["addr"].lower(), "")])
    crumbs_marked = sum(1 for x in crumbs if x["addr"].lower() in exact)
    held_marked = sum(1 for k in range(c) if holders[k].address.lower() in exact)
    res = (f"ИТОГ ТЕСТА ЦЕПОЧКИ {sym}: {time.time() - t0:.0f} с, циклов {c}, последний держатель {names[c]} — "
           + ("ЧИСТЫЙ, продать мог бы" if can_sell else "ПРОДАТЬ НЕ МОЖЕТ") + (f" ({why})" if why else "")
           + f". Крошек помечено {crumbs_marked} из {len(crumbs)}; прежних держателей помечено {held_marked} из {c}"
           + " (уже пустыми)")
    say(res)
    tg("🧪 " + res)


def worker():
    while True:
        try:
            if S.get("recover"):
                S["recover"] = False
                recover_trade()
                continue
            if S["chain"]:
                try:
                    chain_run()
                finally:  # 05.11: an error used to leave the flag set, and the test (real money) started again
                    S["chain"] = False
                phase("Остановлено", "тест цепочки закончен")
                continue
            if S["decoy"]:
                decoy_run()
                S["decoy"] = False
                phase("Остановлено", "тест приманок закончен")
                continue
            if S["manual"]:  # a coin chosen in the panel: one trade, also when the automatic run is off
                tk = Web3.to_checksum_address(S["manual"])
                A.resync()
                bal = w3.eth.get_balance(A.address) / 1e18
                if stake_now(bal) < MIN_STAKE or bal < stake_now(bal) + 0.00015:
                    say("мало BNB на кошельке A — ручная сделка отменена")
                elif prefund():
                    if trade(B.FACTORY.functions.getPair(tk, B.WBNB).call(), tk, time.time() - SET["entry"]) \
                            and S["running"]:
                        S["done"] += 1
                S["manual"] = ""
                if not S["running"]:
                    phase("Остановлено", "ручная сделка закончена")
                continue
            if not S["running"]:
                time.sleep(0.5)
                continue
            why = ("сделано сделок: %d" % S["done"] if SET["trades"] and S["done"] >= SET["trades"]
                   else "нажат стоп" if S["stop_after"]
                   else "убыток за запуск больше $%g" % SET["max_loss_usd"]
                   if -S["run_pnl"] * S["bnb_usd"] >= SET["max_loss_usd"]
                   else "застряли %d сделки подряд" % S["stuck_streak"] if S["stuck_streak"] >= SET["max_stuck"] else "")
            if why:
                S.update(running=False, stop_after=False)
                stop_auto()
                phase("Остановлено", why)
                say("ОСТАНОВКА: " + why)
                tg(f"⏹ Остановка: {why}. Итог запуска {S['run_pnl'] * S['bnb_usd']:+.2f} $")
                continue
            if S.get("sweeping") and time.time() - S["sweeping"] < 1800:  # 04.11 (a crashed sweep must not block forever): no prefund while the gas comes back to A
                time.sleep(1)
                continue
            A.resync()
            bal = w3.eth.get_balance(A.address) / 1e18
            if stake_now(bal) < MIN_STAKE or bal < stake_now(bal) + 0.00015:
                if SET["auto"]:  # 27.10 (user): "work without me" starts by itself once A is topped up
                    if S["phase"] != "Жду пополнения кошелька A":
                        say(f"мало BNB на A ({bal:.5f}) — жду пополнения, начну сам")
                        tg(f"⏳ Мало BNB на кошельке A ({bal:.5f}). Жду пополнения — начну сам.\n{A.address}")
                    phase("Жду пополнения кошелька A", f"на A {bal:.5f} BNB, нужно от {MIN_STAKE + GAS_RESERVE:.4f}")
                    time.sleep(10)
                    continue
                S["running"] = False
                phase("Остановлено", "мало BNB на кошельке A")
                tg(f"⏹ Остановка: мало BNB на кошельке A ({bal:.5f})")
                continue
            if S["phase"] == "Жду пополнения кошелька A":
                say(f"кошелёк A пополнен ({bal:.5f} BNB) — начинаю")
                tg(f"▶️ Кошелёк A пополнен ({bal:.5f} BNB) — начинаю работу")
            since = w3.eth.block_number
            if not prefund():
                S["running"] = False
                phase("Остановлено", S["detail"] or "не удалось подготовить кошельки")
                continue
            phase("Жду монету со сторожем", "ищу новые пулы PancakeSwap")
            found = next_guard_launch(since)
            if found and trade(*found):
                S["done"] += 1
        except Exception as e:
            say("ошибка: " + str(e)[:200])
            traceback.print_exc()
            time.sleep(3)


def stop_auto():
    """An automatic stop must not be undone by a restart: the run resumes only when started again by hand."""
    if SET["auto"]:
        save_settings({"auto": False})
    sig_keep(False)


SIG_ON_FILE = os.path.join(HERE, "signal_on.json")  # 10.11 (user): the signal trading survives a restart (watchdog, reboot, the restart button)


def sig_keep(on):
    """On when the signal trading is started; off on «Стоп» and on an automatic stop (the loss limit, a lost wallet of the network 1)."""
    try:
        if on:
            with open(SIG_ON_FILE, "w", encoding="utf-8") as f:
                json.dump({"on": True, "since": time.strftime("%Y-%m-%d %H:%M:%S")}, f)
        elif os.path.exists(SIG_ON_FILE):
            os.remove(SIG_ON_FILE)
    except OSError as e:
        say("автозапуск торговли: " + str(e)[:80])


def sig_kept():
    try:
        return bool(json.load(open(SIG_ON_FILE, encoding="utf-8")).get("on"))
    except (OSError, ValueError):
        return False


def is_guard(token):
    """A node error must not turn a guard coin into "no guard": three tries before giving up."""
    for k in range(3):
        try:
            return w3.eth.get_storage_at(token, 6).hex()[-40:].lower() == B.GUARD[2:]
        except Exception:
            time.sleep(0.3 * (k + 1))
    return False


ACTIVE_FILE = os.path.join(HERE, "active_coins.json")  # the coin list survives a restart (27.10, user)


def save_active():
    try:
        with open(ACTIVE_FILE, "w", encoding="utf-8") as f:
            json.dump(ACTIVE, f, ensure_ascii=False)
    except Exception:
        pass


def seed_active(head):
    """At start: the guard coins of the last hour from active_coins.json and guard_launches.csv, before any scanning."""
    try:
        ACTIVE.update(json.load(open(ACTIVE_FILE, encoding="utf-8")))
    except Exception:
        pass
    now = time.time()
    for v in list(LAUNCHES.values()):
        if now - v["time"] > 3600 or v["pair"] in ACTIVE or not v.get("pair"):
            continue
        try:
            is0 = w3.eth.contract(v["pair"], abi=B.PAIR_ABI).functions.token0().call().lower() == v["token"].lower()
        except Exception:
            continue
        ACTIVE[v["pair"]] = dict(token=v["token"], sym=v["sym"] or v["token"][:8],
                                 block=head - int((now - v["time"]) / SPB), bnb=None, price0=None, price=None,
                                 a_marked=False, is0=is0)


def refresh_active(head):
    for pair, c in list(ACTIVE.items()):
        try:
            if (head - c["block"]) * SPB > 3600:
                del ACTIVE[pair]
                continue
            r0, r1, _ = w3.eth.contract(pair, abi=B.PAIR_ABI).functions.getReserves().call()
            rt, rb = (r0, r1) if c["is0"] else (r1, r0)
            c["bnb"], c["price"] = rb / 1e18, (rb / rt if rt else 0)
            c["price0"] = c["price0"] or c["price"]
            c["a_marked"] = w3.eth.contract(c["token"], abi=TOK_ABI).functions.allowance(GUARD, A.address).call() > 0
        except Exception:
            pass
    save_active()


def history_backfill(a, b):
    """Guard launches in blocks a..b (older than the coin list's hour): only their times, for the schedule."""
    head = w3.eth.block_number
    while a < b:
        to = min(b, a + 1000)
        try:
            logs = w3.eth.get_logs({"address": B.FACTORY.address, "topics": [B.PAIR_CREATED_TOPIC],
                                    "fromBlock": a + 1, "toBlock": to})
        except Exception:
            time.sleep(3)
            continue
        pairs = [(l, r) for l, r in ((l, B.parse_pair_created(l)) for l in logs) if r]
        with ThreadPoolExecutor(8) as ex:
            flags = list(ex.map(lambda lr: is_guard(lr[1][1]), pairs))
        for (l, (pair, token)), g in zip(pairs, flags):
            if g and token.lower() not in LAUNCHES:
                bn = l["blockNumber"] if isinstance(l["blockNumber"], int) else int(l["blockNumber"], 16)
                try:
                    sym = w3.eth.contract(token, abi=TOK_ABI).functions.symbol().call()[:20]
                except Exception:
                    sym = token[:8]
                add_launch(time.time() - (head - bn) * SPB, token, sym, pair)
        a = to
    S["history_ready"] = True


def scanner():
    """Guard coins of the last hour that still trade, for the panel list and for active_age."""
    head = w3.eth.block_number
    # the last hour first, so the panel's coin list is back within ~30 s of a restart; hours 2-6 (only for the schedule)
    # are read by history_backfill() meanwhile
    last = head - int(3600 / SPB)
    seed_active(head)
    refresh_active(head)
    threading.Thread(target=history_backfill, args=(head - int(6 * 3600 / SPB), last), daemon=True).start()
    while True:
        try:
            head = w3.eth.block_number
            while last < head:
                to = min(head, last + 1000)
                logs = w3.eth.get_logs({"address": B.FACTORY.address, "topics": [B.PAIR_CREATED_TOPIC],
                                        "fromBlock": last + 1, "toBlock": to})
                pairs = [(l, r) for l, r in ((l, B.parse_pair_created(l)) for l in logs) if r and r[0] not in ACTIVE]
                with ThreadPoolExecutor(8) as ex:  # one storage read per pair: in parallel, or the 6 h look-back takes ages
                    flags = list(ex.map(lambda lr: is_guard(lr[1][1]), pairs))
                for (l, (pair, token)), g in zip(pairs, flags):
                    if not g:
                        continue
                    try:
                        sym = w3.eth.contract(token, abi=TOK_ABI).functions.symbol().call()[:20]
                    except Exception:
                        sym = token[:8]
                    bn = l["blockNumber"]
                    bn = bn if isinstance(bn, int) else int(bn, 16)
                    add_launch(time.time() - (head - bn) * SPB, token, sym, pair)
                    if (head - bn) * SPB > 3600:
                        continue
                    ACTIVE[pair] = dict(token=token, sym=sym, block=bn, bnb=None,
                                        price0=None, price=None, a_marked=False,
                                        is0=w3.eth.contract(pair, abi=B.PAIR_ABI).functions.token0().call().lower()
                                        == token.lower())
                last = to
            S["scan_ready"] = True
        except Exception:
            time.sleep(5)
            continue
        refresh_active(head)
        time.sleep(15)


def active_rows():
    head = w3.eth.block_number
    out = []
    for pair, c in sorted(ACTIVE.items(), key=lambda kv: -kv[1]["block"]):
        if c["bnb"] is None or c["bnb"] < 1:
            continue
        out.append(dict(pair=pair, token=c["token"], sym=c["sym"], age_min=round((head - c["block"]) * SPB / 60, 1),
                        bnb=round(c["bnb"], 2), change=round((c["price"] / c["price0"] - 1) * 100, 1) if c["price0"] else 0,
                        a_marked=c["a_marked"], traded=used(c["token"], "A"),
                        used_wallets=len(USED.get(c["token"].lower(), ())), net=1))
    now = time.time()
    for pair, c in sorted(list(NET2_COINS.items()), key=lambda kv: -kv[1]["t"]):
        if c["bnb"] is not None:
            out.append(dict(pair=pair, token=c["token"], sym=c["sym"], age_min=round((now - c["t"]) / 60, 1), bnb=round(c["bnb"], 2),
                            change=round((c["price"] / c["price0"] - 1) * 100, 1) if c["price0"] else 0, a_marked=False, traded=False, used_wallets=len(USED.get(c["token"].lower(), ())), net=c.get("net", 2),
                            holding=c["token"].lower() == S.get(f"sig{c.get('net', 2)}_token")))
    return out


def load_launches():
    """Guard launch times: the 50.0 series from hist.py plus what the scanner has seen (guard_launches.csv)."""
    out = {}
    src = os.path.join(HERE, "hist_launches.jsonl")
    if os.path.exists(src):
        with open(src, encoding="utf-8") as f:
            for l in f:
                if '"50.0"' not in l:
                    continue
                try:
                    r = json.loads(l)
                except ValueError:
                    continue
                if r.get("series") == "50.0":
                    out[r["token"].lower()] = dict(time=r["time"], token=r["token"], sym="", pair=r["pair"])
    if os.path.exists(LAUNCH_FILE):
        with open(LAUNCH_FILE, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f, delimiter=";"):
                out[r["token"].lower()] = dict(time=float(r["ts"]), token=r["token"], sym=r["symbol"], pair=r["pair"])
    return out


LAUNCHES = load_launches()


def add_launch(t, token, sym, pair):
    if token.lower() in LAUNCHES and LAUNCHES[token.lower()]["sym"]:
        return
    LAUNCHES[token.lower()] = dict(time=t, token=token, sym=sym, pair=pair)
    new = not os.path.exists(LAUNCH_FILE)
    with open(LAUNCH_FILE, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        if new:
            w.writerow(["time", "ts", "token", "symbol", "pair"])
        w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)), round(t), token, sym, pair])
    # was it announced by the cash desk? the latest unmatched desk payment within 15 min before the pool
    ev = next((e for e in reversed(CASH_EVENTS) if not e["sym"] and 0 <= t - e["time"] <= 900), None)
    if ev and time.time() - t < 600:
        ev["sym"], ev["lead"] = sym, round(t - ev["time"])
        cash_row("launch", ev["cash"], ev["to"], ev["bnb"], f"{sym} {token}, пул через {ev['lead']} с после кассы")
        say(f"запуск {sym} — касса предупредила за {ev['lead']} с")


DRAIN_MIN = {1: (7.6, 23), 10: (15.5, 39), 25: (51, 60)}  # minutes after the launch the creator dumped (27.10)
FINAL_SILENCE = 214  # s from the bots' last buy to the dump (211-235 on 26 coins)


def net_watch():
    """27.10 (user: "a timer for the launches of the coins I need"): the network runs one guard coin at a time and
    launches the next ~1 min after the dump. For the current coin: its type (start pool), age, the bots' buys against
    BOT_MIN; once they are done the dump is FINAL_SILENCE s after the last buy, so the next launch is predictable to
    the minute. S["net"] feeds the panel's timer card."""
    cur, counter, pool, peak = None, None, None, 0.0
    while True:
        try:
            last = max(LAUNCHES.values(), key=lambda r: r["time"]) if LAUNCHES else None
            if last and (cur is None or last["token"].lower() != cur["token"].lower()):
                if counter:
                    counter.stop = True
                cur = dict(last)
                first = start_bnb(cur["pair"], cur["token"], cur["time"])
                cur["kind"] = coin_type(first) if first is not None else None
                counter = BotCounter(cur["pair"], cur["token"], cur["time"]) if time.time() - cur["time"] < 4000 else None
                pool, peak = B.Pool(cur["pair"], Web3.to_checksum_address(cur["token"]), cur["sym"], 0, 0), 0.0
            if cur is None:
                time.sleep(5)
                continue
            pool.refresh()
            bnb = pool.r_bnb / 1e18
            peak = max(peak, bnb)
            age = time.time() - cur["time"]
            kind = cur["kind"]
            need = BOT_MIN.get(kind) if kind else None
            count = counter.count if counter else None
            silence = counter.silence() if counter else 0.0
            dumped = peak > 0 and bnb < 0.2 * peak
            if dumped:
                stage, eta = "слита — следующая монета в течение ~1–2 мин (касса заплатит за ~30 с)", (0, 120)
            elif need and count is not None and count >= need and silence >= 20:
                left = max(0, FINAL_SILENCE - silence)
                stage, eta = (f"боты закончили покупать {silence:.0f} с назад — слив через ~{left:.0f} с",
                              (left + 40, left + 90))
            elif kind:
                lo, hi = DRAIN_MIN[kind]
                stage = f"боты покупают ({count if count is not None else '?'} из ~{need}); слив обычно на {lo:g}–{hi:g} мин"
                eta = (max(0, lo * 60 - age) + 60, max(0, hi * 60 - age) + 90)
            else:
                stage, eta = "тип не определён", None
            take = {1: True, 10: True, 25: not SET["skip25"]}.get(kind)
            S["net"] = dict(sym=cur["sym"], kind=kind, age=round(age), pool=round(bnb, 2), peak=round(peak, 2),
                            count=count, need=need, silence=round(silence), stage=stage,
                            next_from=round(time.time() + eta[0]) if eta else None,
                            next_to=round(time.time() + eta[1]) if eta else None, ours=bool(take),
                            trading=S["sym"] == cur["sym"] and S["phase"] == "Держу и перекидываю")
        except Exception:
            pass
        time.sleep(5)


def cash_row(kind, cash, to, bnb, note=""):
    new = not os.path.exists(CASH_FILE)
    with open(CASH_FILE, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        if new:
            w.writerow(["time", "kind", "cash", "to", "bnb", "note"])
        w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), kind, cash, to, f"{bnb:.4f}", note])


def cash_watch():
    """Polls the desks' nonces every 1.5 s; on a new transaction reads the new blocks for it. A payment of >= 5 BNB
    to a fresh wallet is the creator of the next guard launch: alert in the panel and have the hop wallets funded."""
    nonce = {c: w3.eth.get_transaction_count(c) for c in CASH}
    last = w3.eth.block_number
    while True:
        time.sleep(1.5)
        try:
            head = w3.eth.block_number
            if head <= last:
                continue
            moved = [c for c in CASH if w3.eth.get_transaction_count(c, head) > nonce[c]]
            if moved:
                for b in range(last + 1, head + 1):
                    for tx in w3.eth.get_block(b, full_transactions=True).transactions:
                        if tx["from"] in moved and tx["to"]:
                            bnb = tx["value"] / 1e18
                            cash_row("pay", tx["from"], tx["to"], bnb, f"блок {b}")
                            if bnb >= 5:
                                if tx["from"] == CASH[0]:  # the forecast and the "coin soon" alert are about the first network
                                    CASH_EVENTS.append(dict(time=time.time(), cash=tx["from"], to=tx["to"], bnb=round(bnb, 2),
                                                            sym="", lead=None))
                                    say(f"СЕТЬ 1, КАССА {tx['from'][:10]} → {tx['to'][:10]}: {bnb:.2f} BNB — монета сети 1 выйдет в ближайшие минуты")
                                else:
                                    say(f"сеть 2, касса {tx['from'][:10]} → {tx['to'][:10]}: {bnb:.2f} BNB — монета сети 2, большинство живут ~35 с")
                for c in moved:
                    nonce[c] = w3.eth.get_transaction_count(c, head)
            last = head
        except Exception:
            time.sleep(3)


def guard_gas():
    """Polls the guard's nonce every 3 s; on a new transaction reads the new blocks (at most the last 40) for its gas
    price (hop_price() pays twice that) and its block (the timer's phase for tick_mode)."""
    nonce, last = w3.eth.get_transaction_count(GUARD), w3.eth.block_number
    while True:
        time.sleep(3)
        try:
            head = w3.eth.block_number
            n = w3.eth.get_transaction_count(GUARD, head)
            if n > nonce:
                for b in range(max(last + 1, head - 40), head + 1):
                    blk = w3.eth.get_block(b, full_transactions=True)
                    for tx in blk.transactions:
                        if tx["from"] == GUARD:
                            inp = bytes(tx["input"]).hex()
                            row = [b, blk.timestamp, tx["nonce"], tx["transactionIndex"], tx["to"], "0x" + inp[32:72],
                                   inp[:8], tx["gasPrice"] / 1e9]
                            with open(MARKS, "a", newline="", encoding="utf-8-sig") as f:
                                csv.writer(f, delimiter=";").writerow(row)
                            if inp[:8] == "095ea7b3":  # only approve() is a mark (13:04 a 2f54bf6e call moved the phase)
                                tick_check(b, row[5], row[4])
                                set_tick(b, blk.timestamp, row[5])
                                TICK["stale"] = False
                            elif not TICK["stale"]:
                                # 27.10 13:04 the guard sent 2f54bf6e calls and then marked 5 blocks off the old phase
                                # (NOKc stuck): it was restarted. The phase is not trusted until its next mark.
                                TICK["stale"] = True
                                say("сторож сделал не-метку (возможно, перезапуск) — фаза таймера под сомнением "
                                    "до его следующей метки, перекиды пока каждые N с")
                            g = tx["gasPrice"] / 1e9
                            if abs(g - GUARD_GAS["gwei"]) > 1e-6:
                                say(f"сторож сменил цену газа: {GUARD_GAS['gwei']:g} → {g:g} gwei")
                            GUARD_GAS.update(gwei=g, block=b, seen=time.time())
                nonce = n
            last = head
            S.update(guard_gwei=GUARD_GAS["gwei"], hop_gwei_now=round(hop_price() / 1e9, 4), tick=tick_text(),
                     plan=plan_rows())
        except Exception:
            time.sleep(5)


def plan_rows():
    """What the chain trade would do with the stake now, by coin type (the main tab's "Правила" card)."""
    try:
        stake = stake_now()
    except Exception:
        return []
    out = []
    for kind, drain in ((1, "8–18"), (10, "15–39"), (25, "51–60")):  # 27.10: the 10 BNB coins drained later by evening
        m, edge = best_hold(kind, stake, SET["entry"] / 60)
        out.append(dict(kind=kind, drain=drain, sell_min="по ботам" if SET["bot_exit"] and m is not None else m,
                        edge=round(edge),
                        take=bool(SET["hold_auto"] and m is not None and edge >= MIN_EDGE
                                  and (kind > 1 or SET["min_pool"] <= 2) and not (kind == 25 and SET["skip25"]))))
    return out


def forecast():
    ts = sorted(v["time"] for v in LAUNCHES.values())
    now = time.time()
    if len(ts) < 3:
        return None
    gaps = [(b - a) / 60 for a, b in zip(ts, ts[1:]) if b - a < 3 * 3600][-60:]  # a long hole = we were not watching
    med = st.median(gaps) if gaps else 52.0
    last = ts[-1]
    alert = next((e for e in reversed(CASH_EVENTS) if not e["sym"] and now - e["time"] < 900), None)
    recent = sorted(LAUNCHES.values(), key=lambda v: v["time"])[-10:]
    leads = {e["sym"]: e["lead"] for e in CASH_EVENTS if e["sym"]}
    rows = []
    for i, v in enumerate(recent):
        prev = recent[i - 1]["time"] if i else None
        rows.append(dict(time=time.strftime("%d.%m %H:%M", time.localtime(v["time"])), sym=v["sym"] or v["token"][:8],
                         gap=round((v["time"] - prev) / 60) if prev and v["time"] - prev < 3 * 3600 else None,
                         lead=leads.get(v["sym"])))
    today = time.strftime("%Y-%m-%d")
    return dict(med=round(med, 1), short_pct=round(100 * sum(g < 18 for g in gaps) / len(gaps)) if gaps else None,
                last_ago=round((now - last) / 60, 1), next_at=time.strftime("%H:%M", time.localtime(last + med * 60)),
                next_in=round((last + med * 60 - now) / 60, 1), overdue=now - last > 90 * 60,
                today=sum(1 for t in ts if time.strftime("%Y-%m-%d", time.localtime(t)) == today),
                alert=dict(ago=round(now - alert["time"]), bnb=alert["bnb"], to=alert["to"]) if alert else None,
                recent=rows[::-1], n=len(ts), warming=not S.get("scan_ready"))


def balance_loop():
    n = 0
    while True:
        try:
            S["balance"] = w3.eth.get_balance(A.address) / 1e18
            S["next_stake"] = stake_now(S["balance"])
            if n % 60 == 0:
                S["bnb_usd"] = float(B.http.get("https://api.binance.com/api/v3/ticker/price",
                                                params={"symbol": "BNBUSDT"}, timeout=5).json()["price"])
        except Exception:
            pass
        n += 1
        time.sleep(5)


def sweep_gas():
    """27.10 (user): the BNB left on the hop wallets (gas reserves, leftovers) back to A. A wallet with less than
    SWEEP_MIN is skipped; each transfer costs 21000 gas at the network price."""
    was = (S["phase"], S["detail"])
    S["sweeping"] = time.time()  # 04.11: the worker waits meanwhile (it started prefund as soon as A got the minimum)
    phase("Собираю газ на A", "проверяю кошельки")
    say("СБОР ГАЗА НА A: начинаю")
    price = A.gas_price
    moved, n = 0.0, 0
    # 28.10: the wallets prefund() filled for the next trade keep their gas (swept at 21:07 while waiting for a coin:
    # the next chain would have topped up every holder on the way, holding the coin meanwhile)
    keep = set(PREP) if S["running"] and SET["chain_mode"] else set()  # the wallets the next chain will use
    for i in sorted(KEYS):
        if S["phase"] in ("Жду момент входа", "Покупаю", "Держу и перекидываю", "Продаю",
                          "Ставлю приманки") or S["manual"]:  # a trade began
            say("сбор газа прерван: началась сделка")
            break
        if i in keep:
            continue
        try:
            addr = Web3.to_checksum_address(WALLET_ADDRS[i]) if i in WALLET_ADDRS else wallet(i).address
            bal = w3.eth.get_balance(addr)
            if bal / 1e18 < SWEEP_MIN:
                continue
            w = wallet(i)
            w.gas_price = price
            value = bal - 21_000 * price
            rc = wait(w, w.send(A.address, b"", value=value, gas=21_000, gas_price=price))
            if rc and rc["status"] == 1:
                moved += value / 1e18
                n += 1
                phase("Собираю газ на A", f"W{i}: +{value / 1e18:.6f} BNB, всего {moved:.6f} с {n} кошельков")
        except Exception as e:
            say(f"сбор газа: W{i} — {str(e)[:60]}")
    S["sweeping"] = 0
    say(f"СБОР ГАЗА НА A: собрано {moved:.6f} BNB (≈ ${moved * S['bnb_usd']:.2f}) с {n} кошельков")
    if S["phase"] == "Собираю газ на A":  # otherwise the worker has moved on: its phase stays
        phase(*was) if S["running"] else phase("Остановлено", f"газ собран: {moved:.6f} BNB")


WALLET_ADDRS = {}
DEAL_JOB = []
SPEED_JOB = []  # 05.11: analysis\\send_speed.py started from the panel; it uses wallet A, so nothing else may run with it
SPEED_OUT = os.path.join(HERE, "send_speed.txt")


def wallets_loop():
    """Hop wallets for the panel: address and BNB (gas) balance. 27.10: up to 1000 wallets — read every 5 min, the
    panel gets the total and the wallets that hold something (plus W1-W20)."""
    addrs = WALLET_ADDRS
    for line in open(os.path.join(ROOT, "hop_wallets.txt"), encoding="utf-8"):
        p = line.strip().split(";")
        if len(p) == 2 and p[0].isdigit():
            addrs[int(p[0])] = p[1]
    while True:
        out, total = [], 0.0
        for i, a in sorted(addrs.items()):
            while S.get("phase") in ("Покупаю", "Держу и перекидываю", "Продаю"):  # 05.11: no 1000 reads during a trade
                time.sleep(2)
            try:
                bnb = w3.eth.get_balance(Web3.to_checksum_address(a)) / 1e18
            except Exception:
                bnb = None
            total += bnb or 0.0
            if i <= 20 or (bnb or 0) >= SWEEP_MIN:
                out.append(dict(n=i, address=a, bnb=bnb))
        S["wallets"], S["wallets_total"], S["wallets_n"] = out, round(total, 6), len(addrs)
        time.sleep(300)


def restore_run():
    """After a restart in auto mode: done / run_pnl / stuck_streak from the trades made since the run started."""
    try:
        start = json.load(open(RUN_FILE, encoding="utf-8"))["start"]
    except Exception:
        return
    for r in trades_rows():
        if time.mktime(time.strptime(r["time"], "%Y-%m-%d %H:%M:%S")) < start:
            continue
        S["run_pnl"] += float(r["pnl_bnb"])
        S["stuck_streak"] = S["stuck_streak"] + 1 if float(r["got_bnb"]) == 0 else 0
        if r["reason"] not in ("покупка не прошла", "отменено"):
            S["done"] += 1


TRADES_CACHE = {"key": None, "rows": []}


def trades_rows():
    if not os.path.exists(TRADES):
        return []
    st_ = os.stat(TRADES)
    key = (st_.st_mtime_ns, st_.st_size)
    if TRADES_CACHE["key"] != key:
        with open(TRADES, encoding="utf-8-sig") as f:
            TRADES_CACHE["rows"] = list(csv.DictReader(f, delimiter=";"))  # all of them: the panel builds the totals and the chart
        TRADES_CACHE["key"] = key
    return TRADES_CACHE["rows"]


# 17.11 (user): profit goes to the user's hardware wallet (Trezor). The address is set only from this PC (127.0.0.1): a phone in the
# home network may send, but never change where the money goes. A keeps keep_usd for the stakes and gas.
VAULT_FILE = os.path.join(HERE, "vault.json")


def vault_load():
    try:
        v = json.load(open(VAULT_FILE, encoding="utf-8"))
        S["vault"] = dict(addr=v.get("addr") or "", keep_usd=float(v.get("keep_usd") or 60))
    except (OSError, ValueError):
        S["vault"] = dict(addr="", keep_usd=60.0)


def vault_amount(usd=None):
    """BNB that would go to the vault now: A's balance minus keep_usd and the transfer's gas.
    17.11 (user): or exactly `usd` dollars, as long as A keeps 0.002 BNB for gas after it (the keep_usd rule is then the user's call)."""
    bal = retry(lambda: w3.eth.get_balance(A.address))
    gas = 21_000 * max(A.gas_price, int(0.1e9)) * 2
    if usd:
        want = int(float(usd) / (S.get("bnb_usd") or SIG_BNB_FALLBACK) * 1e18)
        return bal, (want if want <= bal - gas - int(0.002e18) else 0)
    keep = int(S["vault"]["keep_usd"] / (S.get("bnb_usd") or SIG_BNB_FALLBACK) * 1e18)
    return bal, max(0, bal - keep - gas)


vault_load()

# 18.11 (user: «конвертировать из кошелька А и перевести туда… пропиши туда этот кошелёк Соланы»): BNB from A -> SOL on the Solana
# bot's wallet through the Relay bridge (api.relay.link, one deposit tx on BSC, SOL arrives in seconds). Pressed only by the user in the
# panel; the address is changed only from this PC; every quote is checked (one deposit step on BSC from A for exactly our amount, SOL to
# our address, at most 10 % lost on fees) before anything is sent.
SOLB_FILE = os.path.join(HERE, "sol_bridge.json")
SOLB_DEFAULT = "CqMsFPRFuSqCuxuK79kpw7cFm5ud8MoWBnqT27VfDx6J"  # sol_trader.py wallet (D:\bot\sol_wallet.env)
RELAY, RELAY_SOL = "https://api.relay.link", 792703809
SOLB_MIN_USD, SOLB_MAX_USD = 1.0, 50.0
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def sol_addr_ok(a):
    if not (32 <= len(a) <= 44) or any(c not in _B58 for c in a):
        return False
    n = 0
    for c in a:
        n = n * 58 + _B58.index(c)
    return len(b"\0" * (len(a) - len(a.lstrip("1"))) + n.to_bytes((n.bit_length() + 7) // 8, "big")) == 32


def solb_load():
    try:
        S["solb"] = dict(addr=json.load(open(SOLB_FILE, encoding="utf-8")).get("addr") or SOLB_DEFAULT)
    except (OSError, ValueError):
        S["solb"] = dict(addr=SOLB_DEFAULT)


def relay_quote(amt):
    """Relay quote for `amt` wei BNB from A to SOL on S["solb"]["addr"]; raises on anything unexpected."""
    to_sol = S["solb"]["addr"]
    body = {"user": A.address, "recipient": to_sol, "originChainId": 56, "destinationChainId": RELAY_SOL,
            "originCurrency": "0x0000000000000000000000000000000000000000", "destinationCurrency": "11111111111111111111111111111111",
            "amount": str(int(amt)), "tradeType": "EXACT_INPUT"}
    r = json.loads(urllib.request.urlopen(urllib.request.Request(RELAY + "/quote/v2", data=json.dumps(body).encode(),
                                                                 headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}), timeout=20).read())
    steps = r.get("steps") or []
    if len(steps) != 1 or steps[0].get("kind") != "transaction" or len(steps[0].get("items") or []) != 1:
        raise RuntimeError(f"неожиданный ответ моста: шагов {len(steps)}")
    d = steps[0]["items"][0]["data"]
    if int(d.get("chainId", 0)) != 56 or int(d.get("value", 0)) != int(amt) or str(d.get("from", "")).lower() != A.address.lower():
        raise RuntimeError("мост вернул чужую сумму, сеть или отправителя")
    det = r.get("details") or {}
    out = det.get("currencyOut") or {}
    cur = out.get("currency") or {}
    if int(cur.get("chainId", 0)) != RELAY_SOL or cur.get("address") != "11111111111111111111111111111111":
        raise RuntimeError("мост меняет не на SOL в сети Solana")
    if det.get("recipient") and det["recipient"] != to_sol:
        raise RuntimeError("получатель в ответе моста не наш")
    usd_in = float((det.get("currencyIn") or {}).get("amountUsd") or 0)
    usd_out = float(out.get("amountUsd") or 0)
    if usd_in and usd_out < usd_in * 0.9:
        raise RuntimeError(f"потеря на мосту больше 10%: ${usd_in:.2f} → ${usd_out:.2f}")
    chk = steps[0]["items"][0].get("check") or {}
    return dict(to=Web3.to_checksum_address(d["to"]), data=d.get("data") or "0x", value=int(d["value"]), sol=float(out.get("amountFormatted") or 0),
                usd_in=usd_in, usd_out=usd_out, fee_usd=round(usd_in - usd_out, 3) if usd_in else None, rid=r.get("requestId"),
                check=chk.get("endpoint"), secs=det.get("timeEstimate"))


def solb_amount(usd):
    """wei BNB for `usd` dollars, if A keeps 0.002 BNB for gas after it (0 = not possible)"""
    bal = retry(lambda: w3.eth.get_balance(A.address))
    want = int(float(usd) / (S.get("bnb_usd") or SIG_BNB_FALLBACK) * 1e18)
    return bal, (want if want <= bal - int(0.0025e18) else 0)


def solb_watch(q, h):
    """after the deposit: Relay's status until SOL arrives (at most 3 min)"""
    if not q.get("check"):
        return
    t = time.time()
    while time.time() - t < 180:
        time.sleep(5)
        try:
            st = json.loads(urllib.request.urlopen(urllib.request.Request(RELAY + q["check"], headers={"User-Agent": "Mozilla/5.0"}), timeout=15).read())
        except Exception:
            continue
        s = str(st.get("status", ""))
        if s in ("success", "failure", "refund", "refunded"):
            msg = (f"SOLANA: мост завершил перевод — {q['sol']:.4f} SOL на {S['solb']['addr']}" if s == "success"
                   else f"SOLANA: мост вернул ответ «{s}» по переводу {h} — проверьте кошелёк A")
            say(msg)
            tg(("✅ " if s == "success" else "⚠️ ") + msg)
            return
    say(f"SOLANA: мост не подтвердил перевод за 3 мин (tx {h}) — проверьте кошелёк Solana {S['solb']['addr']}")


solb_load()


def sol_view():
    """18.11: the Solana bot's state (sol_status.json, written every 3 s), its real trades (sol_trades.csv, mode LIVE) and the end of its log."""
    st, trades, log = {}, [], []
    try:
        st = json.load(open(os.path.join(HERE, "sol_status.json"), encoding="utf-8"))
        st["age_s"] = round(time.time() - float(st.get("t") or 0), 1)
    except (OSError, ValueError):
        pass
    try:
        with open(os.path.join(HERE, "sol_trades.csv"), encoding="utf-8-sig") as f:
            trades = [r for r in csv.DictReader(f, delimiter=";") if r.get("mode") == "LIVE"]
    except OSError:
        pass
    try:
        with open(os.path.join(HERE, "sol_trader.log"), encoding="utf-8", errors="replace") as f:
            log = [l.rstrip("\n") for l in deque(f, 60)]
    except OSError:
        pass
    try:
        settings = json.load(open(SOL_SETTINGS, encoding="utf-8"))
    except (OSError, ValueError):
        settings = {}
    since = settings.get("since") or ""
    return dict(status=st, trades=trades[-200:], log=log, settings=settings, running=not os.path.exists(SOL_STOP),
                since=since)


# 19.11 (user: Start/Stop for the Solana bot in its tab, must survive restarts; carry the useful BSC features over):
# the switch is the file sol_stop.flag (the bot checks it before every new trade, the watchdog/autostart never deletes it), settings go to
# sol_settings.json (the bot restarts itself on change), every finished LIVE trade and a stop by the loss limit go to Telegram.
SOL_STOP = os.path.join(HERE, "sol_stop.flag")
SOL_SETTINGS = os.path.join(HERE, "sol_settings.json")
SOL_LIMITS = dict(stake=(0.003, 0.5), loss_usd=(0.5, 200.0), tp=(1.0, 500.0), hold=(30.0, 1800.0), ten_idle=(5.0, 600.0), ten_wait=(10.0, 900.0))


def sol_tg_loop():
    seen, stop_said = None, False
    while True:
        try:
            with open(os.path.join(HERE, "sol_trades.csv"), encoding="utf-8-sig") as f:
                rows = [r for r in csv.DictReader(f, delimiter=";") if r.get("mode") == "LIVE"]
            if seen is None:
                seen = len(rows)
            for r in rows[seen:]:
                px = None
                try:
                    px = float(json.load(open(os.path.join(HERE, "sol_status.json"), encoding="utf-8")).get("sol_usd") or 0) or None
                except (OSError, ValueError):
                    pass
                p = r.get("pnl_sol")
                if p not in (None, ""):
                    p = float(p)
                    tg(f"◎ Solana: {'✅' if p >= 0 else '❌'} {r.get('mint', '')[:8]} {p:+.6f} SOL" + (f" (${p * px:+.2f})" if px else "")
                       + f", {r.get('exit_reason') or ''} за {r.get('t_exit_s') or '?'} с")
                elif r.get("note"):
                    tg(f"◎ Solana: {r.get('mint', '')[:8]} — {r['note'][:150]}")
            seen = len(rows)
            st = json.load(open(os.path.join(HERE, "sol_status.json"), encoding="utf-8"))
            if st.get("stopped") and not stop_said and st.get("mode") == "LIVE" and st.get("pnl", 0) <= st.get("loss", -1e9):
                tg(f"⛔ Solana: торговля остановлена — итог {st['pnl']:+.5f} SOL дошёл до лимита {st['loss']} SOL")
                stop_said = True
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(5)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def may_control(self):
        ip = ipaddress.ip_address(self.client_address[0])
        return ip.is_loopback or bool(LAN_IP) and ip.is_private

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode())

    def do_GET(self):
        if self.path == "/":
            with open(os.path.join(HERE, "hop_trader.html"), "rb") as f:
                return self.send(200, f.read(), "text/html; charset=utf-8")
        if self.path == "/api/ping":
            return self.send(200, {"ok": True})
        if self.path == "/api/active":
            return self.send(200, active_rows())
        if self.path == "/api/send_speed":
            try:
                with open(SPEED_OUT, encoding="utf-8") as f:
                    text = f.read()
            except OSError:
                text = ""
            return self.send(200, dict(running=bool(SPEED_JOB and SPEED_JOB[0].poll() is None), text=text))
        if self.path == "/api/finder":  # 07.11: net_finder.py writes it every 30 s
            try:
                with open(os.path.join(HERE, "net_finder.json"), encoding="utf-8") as f:
                    d = json.load(f)
            except (OSError, ValueError):
                d = dict(families=[], at=None)
            d["ours"] = NET_KEYS
            return self.send(200, d)
        if self.path == "/api/deal_charts":  # 05.11: analysis\\after_exit.py writes it
            try:
                with open(os.path.join(HERE, "deal_charts.json"), "rb") as f:
                    return self.send(200, f.read())
            except OSError:
                return self.send(200, {"deals": []})
        if self.path == "/api/sol":  # 18.11 (user): the Solana bot (sol_trader.py, its own process) in this panel: read-only, from its files
            return self.send(200, sol_view())
        if self.path == "/api/state":
            return self.send(200, dict(state=S, settings=SET, log=list(LOG)[-40:], trades=trades_rows(),
                                       wallet=A.address, forecast=forecast(),
                                       local=self.may_control()))
        self.send(404, {"error": "not found"})

    def do_POST(self):
        if not self.may_control():
            return self.send(403, {"error": "управление только из домашней сети"})
        if "application/json" not in (self.headers.get("Content-Type") or ""):  # a plain form from another site
            return self.send(415, {"error": "нужен JSON"})
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self.send(400, {"error": "неверный JSON"})
        if self.path == "/api/deal_charts":  # read-only analysis in its own process: never touches a trade
            import subprocess
            if DEAL_JOB and DEAL_JOB[0].poll() is None:
                return self.send(200, {"ok": True, "busy": True})
            DEAL_JOB[:] = [subprocess.Popen([sys.executable, "-X", "utf8", os.path.join(HERE, "after_exit.py")], cwd=ROOT,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                            creationflags=subprocess.CREATE_NO_WINDOW)]
            return self.send(200, {"ok": True})
        speed_on = bool(SPEED_JOB and SPEED_JOB[0].poll() is None)
        if self.path == "/api/send_speed":  # a few real transfers from A to itself: only while nothing else uses A
            import subprocess
            if speed_on:
                return self.send(200, {"ok": True, "busy": True})
            if S["running"] or S["manual"] or S["decoy"] or S["chain"]:
                return self.send(409, {"error": "сейчас идёт работа — сначала «Стоп»"})
            out = open(SPEED_OUT, "w", encoding="utf-8")
            SPEED_JOB[:] = [subprocess.Popen([sys.executable, "-u", "-X", "utf8", os.path.join(HERE, "send_speed.py")], cwd=ROOT, stdout=out, stderr=subprocess.STDOUT,
                                             creationflags=subprocess.CREATE_NO_WINDOW)]
            say("нажат «Замер доставки»")
            return self.send(200, {"ok": True})
        if speed_on and self.path in ("/api/signal", "/api/start", "/api/catch", "/api/late", "/api/trade", "/api/sweep", "/api/decoy", "/api/chain"):
            return self.send(409, {"error": "идёт замер доставки, подождите минуту"})
        if self.path == "/api/auto":  # the "work without me" switch: on = save the form and start (or wait for money)
            on = bool(body.get("on"))
            if not on:
                save_settings({"auto": False})
                say("«Работать без меня» выключено (текущая работа продолжается до «Стоп»)")
                return self.send(200, {"ok": True})
            save_settings(dict(body.get("settings") or {}, auto=True))
            if S["running"]:
                say("«Работать без меня» включено")
                return self.send(200, {"ok": True})
            body = dict(SET)  # start the run below with the settings just saved
            self.path = "/api/start"
        if self.path == "/api/start":
            if S["running"]:
                return self.send(409, {"error": "уже работает"})
            if S["decoy"] or S["chain"]:
                return self.send(409, {"error": "идёт тест — дождитесь итога или нажмите «Стоп»"})
            save_settings(body)
            S.update(running=True, done=0, stop_after=False, sell_now=False, run_pnl=0.0, stuck_streak=0)
            SKIP.clear()
            with open(RUN_FILE, "w", encoding="utf-8") as f:
                json.dump({"start": time.time()}, f)
            lim = f"{SET['trades']}" if SET["trades"] else "без ограничения"
            stake_txt = stake_text()
            say(f"СТАРТ (сделок: {lim}, стоп при убытке ${SET['max_loss_usd']} или {SET['max_stuck']} застрявших подряд"
                f"{', продолжать после перезапуска' if SET['auto'] else ''}): ставка {stake_txt}, цель +{SET['tp']}%, стоп −{SET['sl']}%, держать ≤{SET['hold']} с, "
                f"{tick_text()}, газ перекидов {'сторож×2: ' if SET['hop_dyn'] else ''}{hop_price() / 1e9:g} gwei, вход через {SET['entry']} с")
        elif self.path == "/api/decoy":
            if S["running"] or S["manual"] or S["decoy"] or S["chain"]:
                return self.send(409, {"error": "сейчас идёт работа — сначала «Стоп»"})
            tk = str(body.get("token") or "").strip()
            if tk:
                try:
                    tk = Web3.to_checksum_address(tk)
                except Exception:
                    return self.send(400, {"error": "неверный адрес монеты"})
                if not is_guard(tk):
                    return self.send(400, {"error": "в этой монете нет сторожа 0xbd418ce6"})
                if int(B.FACTORY.functions.getPair(tk, B.WBNB).call(), 16) == 0:
                    return self.send(400, {"error": "у монеты нет пула с BNB"})
            S["decoy"] = tk or True
            say("нажат «Тест приманок»" + (f" для {tk}" if tk else ""))
        elif self.path == "/api/sendtest":
            if S.get("sendtest_on"):
                return self.send(409, {"error": "тест узлов уже идёт"})
            n = max(5, min(50, int(body.get("n") or 20)))
            urls = [str(u) for u in (body.get("urls") or []) if str(u).startswith("https://")][:8] or None

            def _run():
                S["sendtest_on"], S["sendtest_stop"] = True, False
                try:
                    send_test(n, urls)
                except Exception as e:
                    say("тест скорости узлов: " + str(e)[:100])
                finally:
                    S["sendtest_on"] = False
            threading.Thread(target=_run, daemon=True).start()
            return self.send(200, {"ok": True})
        elif self.path == "/api/net_off":
            try:
                net = int(body.get("net"))
            except (TypeError, ValueError):
                return self.send(400, {"error": "неверный номер сети"})
            if net != 1 and net not in NET_CFG:
                return self.send(400, {"error": f"сети {net} нет"})
            set_net_off(net, bool(body.get("off")))
            return self.send(200, {"ok": True})
        elif self.path == "/api/net_auto":
            on = bool(body.get("on"))
            with open(NET_AUTO_FILE, "w", encoding="utf-8") as f:
                json.dump({"on": on}, f)
            S["net_auto"] = on
            say("автодобавление сетей " + ("включено" if on else "выключено"))
            return self.send(200, {"ok": True})
        elif self.path == "/api/net_add":
            k = str(body.get("key") or "")
            try:
                with open(os.path.join(HERE, "net_finder.json"), encoding="utf-8") as f:
                    fam = next((x for x in json.load(f)["families"] if x["key"] == k), None)
            except (OSError, ValueError):
                fam = None
            if not fam:
                return self.send(400, {"error": "такой сети нет в списке службы поиска"})
            n, new = add_net(fam)
            if n is None:
                return self.send(400, {"error": "это код наших сетей без своего сторожа: такая семья заняла бы их монеты"})
            return self.send(200, {"ok": True, "net": n, "new": new})
        elif self.path == "/api/signal":
            if S["running"] or S["manual"] or S["decoy"] or S["chain"]:
                return self.send(409, {"error": "сейчас идёт работа — сначала «Стоп»"})
            S["chain"] = "SIGNAL"
            sig_keep(True)
            say("нажат «Тест по сигналу»: торговля продолжится и после перезапуска, пока не нажат «Стоп»")
        elif self.path == "/api/catch":
            if S["running"] or S["manual"] or S["decoy"] or S["chain"]:
                return self.send(409, {"error": "сейчас идёт работа — сначала «Стоп»"})
            S["chain"] = "CATCH"
            say("нажат «Тест ловли покупки»")
        elif self.path == "/api/late":
            if S["running"] or S["manual"] or S["decoy"] or S["chain"]:
                return self.send(409, {"error": "сейчас идёт работа — сначала «Стоп»"})
            S["chain"] = "LATE"
            say("нажат «Тест позднего входа»")
        elif self.path == "/api/chain":
            if S["running"] or S["manual"] or S["decoy"] or S["chain"]:
                return self.send(409, {"error": "сейчас идёт работа — сначала «Стоп»"})
            tk = str(body.get("token") or "").strip()
            if tk:
                try:
                    tk = Web3.to_checksum_address(tk)
                except Exception:
                    return self.send(400, {"error": "неверный адрес монеты"})
                if not is_guard(tk):
                    return self.send(400, {"error": "в этой монете нет сторожа 0xbd418ce6"})
            S["chain"] = tk or True
            say("нажат «Тест цепочки»" + (f" для {tk}" if tk else ""))
        elif self.path == "/api/vault_set":
            if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                return self.send(403, {"error": "адрес сейфа меняется только с этого компьютера"})
            addr = str(body.get("addr") or "").strip()
            if addr and not Web3.is_address(addr):
                return self.send(400, {"error": "неверный адрес"})
            if addr and addr.lower() in {A.address.lower()} | {x.lower() for x in our_addrs()}:
                return self.send(400, {"error": "это адрес самого бота, нужен адрес Trezor"})
            try:
                keep = max(0.0, float(body.get("keep_usd", S["vault"]["keep_usd"])))
            except (TypeError, ValueError):
                return self.send(400, {"error": "неверная сумма"})
            S["vault"] = dict(addr=Web3.to_checksum_address(addr) if addr else "", keep_usd=keep)
            with open(VAULT_FILE, "w", encoding="utf-8") as f:
                json.dump(S["vault"], f)
            say(f"сейф: адрес {S['vault']['addr'] or 'не задан'}, на A остаётся ${keep:g}")
        elif self.path == "/api/vault_send":
            to = S["vault"]["addr"]
            if not to:
                return self.send(400, {"error": "сначала впишите адрес Trezor в «Настройках»"})
            usd = body.get("usd")
            try:
                usd = float(usd) if usd not in (None, "") else None
                if usd is not None and usd <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                return self.send(400, {"error": "неверная сумма"})
            try:
                bal, amt = vault_amount(usd)
            except Exception as e:
                return self.send(503, {"error": "узел не ответил: " + str(e)[:60]})
            if amt < int(0.0001e18):
                return self.send(400, {"error": (f"на кошельке бота {bal / 1e18:.4f} BNB — ${usd:g} отправить нельзя (нужно оставить 0.002 BNB на газ)" if usd
                                                 else f"сверх ${S['vault']['keep_usd']:g} на A нечего отправлять (на A {bal / 1e18:.4f} BNB)")})
            if body.get("bnb") is not None and abs(float(body["bnb"]) - amt / 1e18) > 0.01:
                return self.send(409, {"error": "баланс изменился, откройте окно ещё раз"})
            try:
                h = A.send(to, b"", value=amt, gas=21_000, gas_price=max(A.gas_price, int(0.1e9)))
                rc = wait(A, h)
            except Exception as e:
                return self.send(500, {"error": "перевод не отправлен: " + str(e)[:80]})
            if not rc or rc["status"] != 1:
                return self.send(500, {"error": "перевод не прошёл"})
            msg = f"ОТПРАВЛЕНО В СЕЙФ {amt / 1e18:.6f} BNB (≈${amt / 1e18 * (S.get('bnb_usd') or 0):.2f}) на {to}, tx {h}"
            say(msg)
            tg("🔐 " + msg)
            return self.send(200, {"ok": True, "tx": h, "bnb": amt / 1e18})
        elif self.path == "/api/sol_run":  # 19.11: Solana bot start / stop (file switch, survives restarts)
            on = bool(body.get("on"))
            try:
                if on and os.path.exists(SOL_STOP):
                    os.remove(SOL_STOP)
                elif not on and not os.path.exists(SOL_STOP):
                    open(SOL_STOP, "w").close()
            except OSError as e:
                return self.send(500, {"error": str(e)[:80]})
            say("Solana: торговля " + ("ВКЛЮЧЕНА" if on else "ОСТАНОВЛЕНА (новые сделки не начинаются, открытая доводится до продажи)"))
            tg("◎ Solana: торговля " + ("включена ▶️" if on else "остановлена ⏸"))
            return self.send(200, {"ok": True, "running": on})
        elif self.path == "/api/sol_settings":  # 19.11: settings of the Solana bot from the panel
            try:
                cur = json.load(open(SOL_SETTINGS, encoding="utf-8"))
            except (OSError, ValueError):
                cur = {}
            new = dict(cur)
            try:
                for k, (lo, hi) in SOL_LIMITS.items():
                    if body.get(k) in (None, ""):
                        continue
                    v = float(body[k])
                    if not lo <= v <= hi:
                        return self.send(400, {"error": f"{k}: от {lo:g} до {hi:g}"})
                    new[k] = v
                if "family_only" in body:
                    new["family_only"] = bool(body["family_only"])
            except (TypeError, ValueError):
                return self.send(400, {"error": "неверное число"})
            if "loss_usd" in new:
                try:
                    px = float(json.load(open(os.path.join(HERE, "sol_status.json"), encoding="utf-8")).get("sol_usd") or 0)
                except (OSError, ValueError):
                    px = 0
                if not px:
                    return self.send(503, {"error": "нет курса SOL — лимит в $ сейчас не пересчитать"})
                new["loss"] = -round(new["loss_usd"] / px, 4)
            new["max"] = new.get("max", 100000)
            if body.get("reset_count"):  # start the count of trades / result again from now (the old result is kept in the table)
                new["since"] = time.strftime("%Y-%m-%d %H:%M", time.gmtime())
            with open(SOL_SETTINGS + ".tmp", "w", encoding="utf-8") as f:
                json.dump(new, f, ensure_ascii=False)
            os.replace(SOL_SETTINGS + ".tmp", SOL_SETTINGS)
            say(f"Solana: настройки {new}")
            tg(f"◎ Solana: настройки — ставка {new.get('stake')} SOL, лимит убытка ${new.get('loss_usd', '?')}, цель +{new.get('tp')}%")
            return self.send(200, {"ok": True, "settings": new})
        elif self.path == "/api/sol_set":
            if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                return self.send(403, {"error": "адрес Solana меняется только с этого компьютера"})
            addr = str(body.get("addr") or "").strip()
            if not sol_addr_ok(addr):
                return self.send(400, {"error": "неверный адрес Solana"})
            S["solb"] = dict(addr=addr)
            with open(SOLB_FILE, "w", encoding="utf-8") as f:
                json.dump(S["solb"], f)
            say(f"кошелёк Solana для перевода: {addr}")
        elif self.path in ("/api/sol_preview", "/api/sol_send"):
            try:
                usd = float(body.get("usd"))
                if not SOLB_MIN_USD <= usd <= SOLB_MAX_USD:
                    raise ValueError
            except (TypeError, ValueError):
                return self.send(400, {"error": f"сумма от ${SOLB_MIN_USD:g} до ${SOLB_MAX_USD:g}"})
            try:
                bal, amt = solb_amount(usd)
            except Exception as e:
                return self.send(503, {"error": "узел не ответил: " + str(e)[:60]})
            if not amt:
                return self.send(400, {"error": f"на кошельке бота {bal / 1e18:.4f} BNB — ${usd:g} отправить нельзя (нужно оставить 0.0025 BNB на газ)"})
            try:
                q = relay_quote(amt)
            except Exception as e:
                return self.send(502, {"error": "мост: " + str(e)[:120]})
            if self.path == "/api/sol_preview":
                return self.send(200, {"bal": bal / 1e18, "bnb": amt / 1e18, "usd": usd, "sol": q["sol"], "fee_usd": q["fee_usd"], "secs": q["secs"],
                                       "addr": S["solb"]["addr"]})
            if body.get("sol") is not None and float(body["sol"]) > 0 and q["sol"] < float(body["sol"]) * 0.97:
                return self.send(409, {"error": f"курс ухудшился: теперь {q['sol']:.4f} SOL, откройте окно ещё раз"})
            try:
                data = bytes.fromhex(q["data"][2:])
                try:
                    gas = int(w3.eth.estimate_gas({"from": A.address, "to": q["to"], "value": q["value"], "data": data}) * 1.3)
                except Exception:
                    gas = 150_000
                h = A.send(q["to"], data, value=q["value"], gas=gas, gas_price=max(A.gas_price, int(0.1e9)))
                rc = wait(A, h)
            except Exception as e:
                return self.send(500, {"error": "перевод не отправлен: " + str(e)[:80]})
            if not rc or rc["status"] != 1:
                return self.send(500, {"error": "перевод не прошёл"})
            msg = f"ПЕРЕВОД НА SOLANA: {amt / 1e18:.6f} BNB (≈${usd:g}) → ≈{q['sol']:.4f} SOL на {S['solb']['addr']} через мост Relay, tx {h}"
            say(msg)
            tg("🌉 " + msg)
            threading.Thread(target=solb_watch, args=(q, h), daemon=True).start()
            return self.send(200, {"ok": True, "tx": h, "sol": q["sol"]})
        elif self.path == "/api/vault_preview":
            try:
                usd = float(body["usd"]) if body.get("usd") not in (None, "") else None
                bal, amt = vault_amount(usd if usd and usd > 0 else None)
            except Exception as e:
                return self.send(503, {"error": "узел не ответил: " + str(e)[:60]})
            return self.send(200, {"bal": bal / 1e18, "bnb": amt / 1e18, "usd": amt / 1e18 * (S.get("bnb_usd") or 0), "addr": S["vault"]["addr"],
                                   "keep_usd": S["vault"]["keep_usd"]})
        elif self.path == "/api/sweep":
            if S["phase"] in ("Держу и перекидываю", "Продаю", "Покупаю", "Жду момент входа", "Ставлю приманки", "Готовлю кошельки",
                              "Собираю газ на A", "Ставлю приманки") or S["manual"] or S["chain"]:
                return self.send(409, {"error": "сейчас идёт сделка или подготовка — попробуйте позже"})
            threading.Thread(target=sweep_gas, daemon=True).start()
            say("нажат «Собрать газ на A»")
        elif self.path == "/api/stop":
            S["sendtest_stop"] = True
            if S["chain"]:
                S["chain"] = False
            if S["decoy"]:
                S["decoy"] = False
            if S["phase"] in ("Держу и перекидываю", "Продаю"):
                S["stop_after"] = True
                say("стоп после текущей сделки")
            else:
                S.update(running=False, stop_after=True)
                say("СТОП")
            stop_auto()
        elif self.path == "/api/trade":
            try:
                tk = Web3.to_checksum_address(str(body.get("token", "")).strip())
            except Exception:
                return self.send(400, {"error": "неверный адрес монеты"})
            if S["manual"] or S["decoy"] or S["chain"] or S["phase"] in ("Держу и перекидываю", "Продаю", "Покупаю", "Жду момент входа", "Ставлю приманки",
                                              "Готовлю продавца"):
                return self.send(409, {"error": "сейчас идёт другая сделка"})
            if w3.eth.get_storage_at(tk, 6).hex()[-40:].lower() != B.GUARD[2:]:
                return self.send(400, {"error": "в этой монете нет сторожа 0xbd418ce6 — перекидывание не нужно"})
            if used(tk, "A") and not SET["chain_mode"]:
                return self.send(400, {"error": "кошелёк A уже покупал эту монету — повторно её брать нельзя"})
            if int(B.FACTORY.functions.getPair(tk, B.WBNB).call(), 16) == 0:
                return self.send(400, {"error": "у монеты нет пула с BNB"})
            if isinstance(body.get("settings"), dict):  # the form as it is now, not the last "Старт"
                save_settings({k: v for k, v in body["settings"].items() if k != "auto"})
            S["manual"] = tk
            S["stop_after"] = False  # an old "Стоп" must not cancel the coin chosen now
            say(f"ручная сделка: {tk}")
        elif self.path == "/api/extend":
            if S["phase"] != "Держу и перекидываю" or not SET["chain_mode"]:
                return self.send(409, {"error": "сейчас нет сделки-цепочки"})
            S["hold_now"] += 300
            say(f"+5 мин к сделке {S['sym']}: держу до {S['hold_now'] / 60:.1f} мин")
        elif self.path == "/api/sell":
            S["sell_now"] = True
            say("нажато «Продать сейчас»")
        else:
            return self.send(404, {"error": "not found"})
        self.send(200, {"ok": True})


if __name__ == "__main__":
    B.single_instance("Перекидыватель", LOCK_PORT)
    B.tg.enabled = bool(B.tg.token)  # the hop trader trades for real whatever mode config.json says
    B.tg.prefix = ""
    if B.tg.enabled:
        B.tg.start()
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=balance_loop, daemon=True).start()
    threading.Thread(target=wallets_loop, daemon=True).start()
    threading.Thread(target=scanner, daemon=True).start()
    threading.Thread(target=cash_watch, daemon=True).start()
    threading.Thread(target=net_watch, daemon=True).start()
    threading.Thread(target=auto_add_loop, daemon=True).start()
    threading.Thread(target=sol_tg_loop, daemon=True).start()  # 19.11: Solana trades -> Telegram
    start_second()
    load_marks()
    threading.Thread(target=guard_gas, daemon=True).start()
    for nm, nd in HEAD_NODES.items():
        threading.Thread(target=head_poll, args=(nm, nd), daemon=True).start()
    S["recover"] = os.path.exists(CUR_FILE)  # a chain trade was going on when the process stopped
    if sig_kept() and not S["recover"]:  # 10.11 (user): the signal trading (and its mempool stream) comes back by itself after a restart
        S["chain"] = "SIGNAL"
        say("автозапуск: продолжаю торговлю по сигналу после перезапуска")
        tg("▶️ Перезапуск: торговля по сигналу продолжается сама")
    elif SET["auto"]:  # unattended mode survives a restart (watchdog, reboot)
        restore_run()
        S["running"] = True
        say("автозапуск: продолжаю работу после перезапуска")
    say(f"панель: http://127.0.0.1:{PORT}" + (f", в сети: http://{LAN_IP}:{PORT} (с управлением)" if LAN_IP else "") + f"  (кошелёк A {A.address}, кошельков для перекидывания: {len(KEYS)})")
    ThreadingHTTPServer(("0.0.0.0" if LAN_IP else "127.0.0.1", PORT), H).serve_forever()
