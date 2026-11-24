"""BSC meme-coin bot (PancakeSwap V2) with one strategy: "manual launch".

A brand-new PancakeSwap pool into which the creator put >= manual_min_bnb by hand is bought in its first seconds, but
only after hard filters (manual_filter): not a bait copy of a known name, not the typical 17.6 BNB bait amount, GoPlus
must already know the token (waits up to manual_goplus_wait_seconds), no red flags, and our own simulated buy+sell
(trade_sim) must pass with tax <= max_tax_pct. Hidden source code is allowed only when that simulation passed.
Not bought either: pools holding >= manual_max_pool_supply_pct of the whole supply (the 10 BNB rug family).
The buy waits manual_entry_delay_seconds after the money appeared in the pool and needs >= 90% of that BNB still there:
26.10 a rug family pulled ~14 s after launch, while good launches stay flat for the first 60-90 s.
After that it also waits (up to manual_entry_max_wait_seconds) until the price is manual_entry_min_rise_pct above the
launch price, i.e. other people are buying: 26.10 a trap family kept the pool flat and pulled it the very block our
buy landed (福气, MEGA, 属猴风水...), and a flat pool never shows anything before that.
Exit as a ladder: manual_tp1_part_pct of the tokens at +manual_tp1_pct (profit taken before a typical pull at
+50..+110%), the rest at +manual_take_profit_pct / -manual_stop_loss_pct / manual_max_hold_seconds; everything at
once when the creator mints new tokens, and at once with a higher gas price when a liquidity pull from our pool
shows up in the public mempool (RugGuard): the pull is a plain router removeLiquidity call seen ~0.3 s before its block.

Threads:
  * Feed       - WebSocket: new blocks, new pools (PairCreated), Sync and mint events of the pools we hold.
  * HttpPoller - the same over HTTP while the WebSocket is silent.
  * KnownNames - symbols already trading on Binance lists: a new pool under such a name is a bait copy.
  * Main       - buys what passed the filters, follows positions and sells.
Live transactions are signed locally with a cached nonce and gas price and broadcast to several RPCs at once.
mode = "paper" simulates trades on real pool reserves with a realistic delay; mode = "live" trades real BNB.
Run `python bot.py selftest` (live) to make one tiny buy+sell of CAKE and measure real timings.
"""
import csv, datetime as dt, json, os, queue, sys, threading, time, traceback
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import leaders
from websockets.sync.client import connect
from eth_abi import decode, encode
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
if "--paper" in sys.argv:  # force a paper run without touching config.json
    CFG["mode"] = "paper"
if "--no-loss-limit" in sys.argv and CFG["mode"] == "paper":  # paper data collection must not stop after a bad streak
    CFG["daily_loss_limit_bnb"] = 1e9
    CFG["auto_money"] = False  # otherwise the auto calculation would set a limit again
if "--stake" in sys.argv:  # paper runs keep their own stake, whatever the live wallet uses
    CFG["stake_bnb"] = float(sys.argv[sys.argv.index("--stake") + 1])
if "--copy" in sys.argv:  # copy the leader wallets (LeaderCopier); off in live since 26.10 21:35
    CFG["copy_leaders_on"] = True
if "--rug-window" in sys.argv:  # demo only for now: stay out of the time window recent pulls happened in
    CFG["adaptive_rug_window"] = True
if "--lan" in sys.argv:  # 27.10 (user): panel visible in the LAN at --lan <ip>; buttons stay local-only
    CFG["web_host"] = "0.0.0.0"
    CFG["web_lan_ip"] = sys.argv[sys.argv.index("--lan") + 1]
if "--port" in sys.argv:  # e.g. a second (paper) copy next to the live bot
    CFG["web_port"] = int(sys.argv[sys.argv.index("--port") + 1])
# 26.10 21:20 (user): late entry like the wallets that made money in journal_swaps - buy at ~150 s, once the pull
# window is over, hold 90 s, TP +100% / SL -30% (journal replay: 322 launches, +16.9% per trade, ~1% zeroed, +14% in
# the evening when TP +200% from 30 s went to -4%). The pool may lose up to 30% of its BNB while we wait.
CFG.update({"manual_tp1_part_pct": 0, "manual_take_profit_pct": 100, "manual_stop_loss_pct": 30,
            "manual_entry_delay_seconds": 150, "manual_entry_min_rise_pct": 0, "manual_max_hold_seconds": 90,
            "manual_min_pool_left": 0.7})
if "--series" in sys.argv:  # 27.10 (user): buy only launches whose creator was paid a series' fixed amount by its
    # cash desk; history 19, 20, 26.10: series 50.0 never pulled, 30 s entry / TP +200% = +31/+41/+45% a trade
    CFG["series_only"] = sys.argv[sys.argv.index("--series") + 1].split(",")
    CFG.update({"manual_take_profit_pct": 100, "manual_stop_loss_pct": 50, "manual_entry_delay_seconds": 30,
                "manual_max_hold_seconds": 300,
                # 26.10 all 13 launches of 50.0 were cut by these two (100% of supply in the pool, copied name); the
                # +31..+45% history is on all of them. The buy+sell simulation and GoPlus stay
                "manual_allow_copies": True, "manual_max_pool_supply_pct": 101, "exit_on_creator_tx": False,
                # hold per series, history 19, 20, 26.10 (30 s entry, TP +200%): 300 s was best for 50.0 and 55.02 on
                # every day; 17.02 at 90 s +80% with 7 of 122 zeroed vs 300 s +99% with 20 (26.10: +49% vs +51%)
                "series_hold": {"50.0": 300, "17.02": 90, "55.02": 300},
                # take-profit per series, same history: 50.0 TP100 +37% (=TP200), 17.02 TP150 +80% (TP100 +76%),
                # 55.02 TP200 +124% (TP100 +88%), the best on every day
                "series_tp": {"50.0": 100, "17.02": 150, "55.02": 200},
                # 27.10 HELL/KRON passed the one-call buy+sell simulation but refused every real sell: in live a tiny
                # real buy first, then a dry-run sell from our wallet; a token we cannot sell is forgotten
                "sell_probe": True, "sell_probe_bnb": 0.00001, "sell_probe_wait": 20,
                # 27.10 test: does the guard 0xbd418ce6 also blacklist a wallet that got the tokens by a plain transfer?
                "hold_test_max": 0,  # 27.10 05:00 WRDc answered it: B is marked too; off (user: no real-money trading)
                # 27.10 (user): one real trade on a guard token of 50.0: buy with A, tokens to wallet B, sell from B
                "hold_trade": True,
                # 27.10 (user): live trades ONLY that one A->B trade, ordinary buys are off (demo/lab not affected)
                "hold_only": True})

STATE_FILE = os.path.join(HERE, f"state_{CFG['mode']}.json")  # paper and live never share positions
TRADES_FILE = os.path.join(HERE, "trades.csv")

WBNB = Web3.to_checksum_address("0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c")
ROUTER = Web3.to_checksum_address("0x10ED43C718714eb63d5aA57B78B54704E256024E")
CAKE = Web3.to_checksum_address("0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82")
BINANCE_RANK = "https://web3.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/market/token/pulse/unified/rank/list"


def topic(sig):
    return "0x" + Web3.keccak(text=sig).hex().removeprefix("0x")


SWAP_TOPIC = topic("Swap(address,uint256,uint256,uint256,uint256,address)")
SYNC_TOPIC = topic("Sync(uint112,uint112)")
TRANSFER_TOPIC = topic("Transfer(address,address,uint256)")
WITHDRAWAL_TOPIC = topic("Withdrawal(address,uint256)")
GAS_BUY, GAS_SELL, GAS_APPROVE = 160_000, 160_000, 50_000  # typical usage, for paper-mode cost

ROUTER_ABI = [
    {"name": "swapExactETHForTokensSupportingFeeOnTransferTokens", "type": "function", "stateMutability": "payable",
     "inputs": [{"name": "amountOutMin", "type": "uint256"}, {"name": "path", "type": "address[]"},
                {"name": "to", "type": "address"}, {"name": "deadline", "type": "uint256"}], "outputs": []},
    {"name": "swapExactTokensForETHSupportingFeeOnTransferTokens", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "amountIn", "type": "uint256"}, {"name": "amountOutMin", "type": "uint256"},
                {"name": "path", "type": "address[]"}, {"name": "to", "type": "address"},
                {"name": "deadline", "type": "uint256"}], "outputs": []},
]
PAIR_ABI = [
    {"name": "getReserves", "type": "function", "stateMutability": "view", "inputs": [],
     "outputs": [{"type": "uint112"}, {"type": "uint112"}, {"type": "uint32"}]},
    {"name": "token0", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "address"}]},
]
ERC20_ABI = [
    {"name": "approve", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "s", "type": "address"}, {"name": "v", "type": "uint256"}], "outputs": [{"name": "", "type": "bool"}]},
    {"name": "totalSupply", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "uint256"}]},
]
ZERO_TOPIC = "0x" + "0" * 64
FACTORY_ABI = [{"name": "getPair", "type": "function", "stateMutability": "view",
                "inputs": [{"type": "address"}, {"type": "address"}], "outputs": [{"type": "address"}]}]

RATE_LIMIT_WORDS = ("limit", "rate", "too many", "archive", "token", "forbidden", "unauthorized", "upgrade")


class FallbackHTTPProvider(Web3.HTTPProvider):
    """Reads go to the first node that answers; a node that refuses (403/429, rate or archive limit, timeout) is
    skipped and the next one is tried. Free BSC nodes ban an IP for a while after bursts — one node must not stop
    the bot. eth_getLogs only goes to nodes that serve logs (Binance dataseed nodes refuse them)."""

    def __init__(self, urls, logs_urls, **kw):
        super().__init__(urls[0], **kw)
        self.urls, self.logs_urls, self.good = list(urls), set(logs_urls), 0
        self.sess = requests.Session()

    def make_request(self, method, params):
        body = self.encode_rpc_request(method, params)
        order = self.urls[self.good:] + self.urls[:self.good]
        last = None
        for url in order:
            if method == "eth_getLogs" and url not in self.logs_urls:
                continue
            try:
                r = self.sess.post(url, data=body, headers={"Content-Type": "application/json"}, timeout=10)
                if r.status_code in (403, 429) or r.status_code >= 500:
                    raise RuntimeError(f"HTTP {r.status_code}")
                resp = r.json()
                err = (resp.get("error") or {}).get("message", "") if isinstance(resp, dict) else ""
                if err and any(w in err.lower() for w in RATE_LIMIT_WORDS):
                    raise RuntimeError(err[:60])
                self.good = self.urls.index(url)
                return resp
            except Exception as e:  # node refused or is down: next one
                last = e
        raise RuntimeError(f"все узлы отказали для {method}: {last}")


w3 = Web3(FallbackHTTPProvider(CFG.get("rpc_urls") or [CFG["rpc_url"]], CFG.get("logs_rpc_urls") or [CFG["rpc_url"]],
                               request_kwargs={"timeout": 10}))
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
router = w3.eth.contract(ROUTER, abi=ROUTER_ABI)
FACTORY = w3.eth.contract(Web3.to_checksum_address("0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73"), abi=FACTORY_ABI)
ERC20 = w3.eth.contract(abi=ERC20_ABI)
LIVE = CFG["mode"] == "live"
http = requests.Session()


LOG_BUF = deque(maxlen=300)  # recent log lines for the web panel
_console = queue.Queue()


def _console_writer():
    """Console output on its own thread: on Windows a click in the console (QuickEdit selection) blocks every print(),
    and that must never freeze trading."""
    while True:
        line = _console.get()
        try:
            print(line, flush=True)
        except Exception:
            pass


threading.Thread(target=_console_writer, daemon=True).start()
_logfile = queue.Queue()
LOG_DIR = os.path.join(HERE, "logs")


def _logfile_writer():
    """Every log line also goes to logs/<script>_<mode>_<date>.log: the panel keeps only the last 300 lines in memory,
    and they are gone after a restart. Own thread, so neither a slow disk nor a frozen console stops the other."""
    script = os.path.splitext(os.path.basename(sys.argv[0]))[0]
    while True:
        line = _logfile.get()
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            name = f"{script}_{CFG['mode']}_{dt.date.today():%Y%m%d}.log"
            with open(os.path.join(LOG_DIR, name), "a", encoding="utf-8") as f:
                f.write(f"{dt.date.today():%Y-%m-%d} {line}\n")
        except Exception:
            pass


threading.Thread(target=_logfile_writer, daemon=True).start()


def _quiet_ws_keepalive(args):
    """websockets' keepalive thread raises on a ping timeout; our loops already reconnect, so log one line instead."""
    if args.exc_type.__module__.startswith("websockets"):
        log(f"WebSocket: таймаут связи с узлом ({args.exc_type.__name__}), переподключение")
        return
    threading.__excepthook__(args)


threading.excepthook = _quiet_ws_keepalive


class Telegram(threading.Thread):
    """Notifications to Telegram. The bot token is read from 'api telegram.txt' (or TELEGRAM_BOT_TOKEN in .env) and is
    never logged. The recipient chat is taken from config 'telegram_chat_id'; while it is empty, the first private chat
    that sends /start to the bot is saved as the recipient. Messages go out from a queue so trading never waits."""

    def __init__(self):
        super().__init__(daemon=True)
        self.q = queue.Queue()
        self.token = self._token()
        self.enabled = bool(self.token) and (CFG["mode"] == "live" or CFG.get("telegram_notify_paper"))
        self.prefix = "" if CFG["mode"] == "live" else "[PAPER] "

    @staticmethod
    def _token():
        import re
        for path in (os.path.join(HERE, "api telegram.txt"), os.path.join(HERE, ".env")):
            if os.path.exists(path):
                found = re.findall(r"\d{6,12}:[A-Za-z0-9_-]{30,}", open(path, encoding="utf-8", errors="replace").read())
                if found:
                    return found[0]
        return ""

    def send(self, text):
        if self.enabled:
            self.q.put(self.prefix + text)

    def _api(self, method, **params):
        return http.post(f"https://api.telegram.org/bot{self.token}/{method}", json=params, timeout=15).json()

    def _find_chat(self):
        """Remember the first private chat that wrote /start to the bot."""
        try:
            for u in self._api("getUpdates", timeout=0).get("result", []):
                m = u.get("message") or {}
                c = m.get("chat") or {}
                if c.get("type") == "private" and str(m.get("text", "")).startswith("/start"):
                    CFG["telegram_chat_id"] = c["id"]
                    path = os.path.join(HERE, "config.json")
                    with open(path, encoding="utf-8") as f:
                        disk = json.load(f)
                    disk["telegram_chat_id"] = c["id"]
                    with open(path + ".tmp", "w", encoding="utf-8") as f:
                        json.dump(disk, f, indent=2, ensure_ascii=False)
                    os.replace(path + ".tmp", path)
                    log(f"Telegram: чат {c.get('first_name', '')} подключён, уведомления будут приходить туда")
                    self.q.put("✅ Уведомления мем-бота подключены. Сюда будут приходить покупки, продажи, итоги дня и тревоги.")
                    return True
        except Exception:
            pass
        return False

    def run(self):
        if not self.enabled:
            return
        while True:
            if not CFG.get("telegram_chat_id"):
                if not self._find_chat():
                    time.sleep(10)
                    continue
            text = self.q.get()
            for attempt in range(3):
                try:
                    r = self._api("sendMessage", chat_id=CFG["telegram_chat_id"], text=text[:4000],
                                  disable_web_page_preview=True)
                    if r.get("ok"):
                        break
                    if r.get("error_code") == 429:
                        time.sleep(int((r.get("parameters") or {}).get("retry_after", 3)))
                        continue
                    log(f"Telegram: не отправлено ({str(r.get('description'))[:60]})")
                    break
                except Exception:
                    time.sleep(3)
            time.sleep(1.1)  # Telegram allows about one message per second to a chat


tg = Telegram()


def disable_quick_edit():
    """Turn off QuickEdit for this console window only, so a stray click does not pause output."""
    if os.name != "nt":
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        mode = ctypes.c_uint32()
        if k32.GetConsoleMode(h, ctypes.byref(mode)):
            k32.SetConsoleMode(h, (mode.value & ~0x0040) | 0x0080)  # clear ENABLE_QUICK_EDIT_MODE, keep EXTENDED_FLAGS
    except Exception:
        pass


def log(msg):
    line = f"{dt.datetime.now():%H:%M:%S.%f}"[:12] + " " + msg
    LOG_BUF.append(line)
    _console.put(line)
    _logfile.put(line)


LP_ABI = [{"name": "totalSupply", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "uint256"}]},
          {"name": "balanceOf", "type": "function", "stateMutability": "view", "inputs": [{"type": "address"}],
           "outputs": [{"type": "uint256"}]}]
BURN_ADDRS = ["0x000000000000000000000000000000000000dEaD", "0x0000000000000000000000000000000000000000"]


RUG_FILE = os.path.join(HERE, "rug_mempool.csv")  # pulls seen by the journal: time, pool BNB, held_s = pool age at the pull
_rug_cache = {"at": 0.0, "rows": []}


def rug_window(bnb, look_min=60, need=5):
    """Pool ages (from_s, to_s) at which pools of a similar size were pulled within the last look_min minutes, or None.
    26.10: scammers pulled at ~20 s by day and at ~66-75 s (big pools) in the evening, right at our entry. Journal replay:
    waiting past this window gave +85% per trade vs +73% overall, and +26% vs -4% after 17:30."""
    now = time.time()
    if now - _rug_cache["at"] > 30:
        rows = []
        try:
            with open(RUG_FILE, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f, delimiter=";"):
                    try:
                        t = dt.datetime.strptime(r["time"][:19], "%Y-%m-%d %H:%M:%S").timestamp()
                        rows.append((t, float(r["bnb_before"] or 0), float(r["held_s"])))
                    except (ValueError, KeyError, TypeError):
                        pass
        except OSError:
            pass
        _rug_cache.update(at=now, rows=rows)
    hs = sorted(h for t, b, h in _rug_cache["rows"] if 0 < now - t <= look_min * 60 and 0.7 * bnb <= b <= 1.4 * bnb)
    if len(hs) < need:
        return None
    return hs[0] - 5, hs[int(len(hs) * 0.9) - 1 if len(hs) >= 10 else -1] + 10


def lp_burned_pct(pair):
    """Share of the pool's LP tokens sent to a dead address. Whoever holds the rest can pull the liquidity at any moment
    (example: ROCK — 80 BNB removed 2 blocks after our buy)."""
    lp = w3.eth.contract(pair, abi=LP_ABI)
    total = lp.functions.totalSupply().call()
    if not total:
        return 0.0
    burned = sum(lp.functions.balanceOf(Web3.to_checksum_address(a)).call() for a in BURN_ADDRS)
    return burned / total * 100


def load_private_key():
    """Key is read from .env (BSC_PRIVATE_KEY=...) written by the user; it is never printed or logged."""
    path = os.path.join(HERE, ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            if line.strip().startswith("BSC_PRIVATE_KEY="):
                return line.split("=", 1)[1].strip()
    return os.environ.get("BSC_PRIVATE_KEY", "")


def amount_out(amount_in, r_in, r_out):
    """PancakeSwap V2 constant-product formula with the 0.25% LP fee."""
    if amount_in <= 0 or r_in <= 0 or r_out <= 0:
        return 0
    a = amount_in * 9975
    return a * r_out // (r_in * 10000 + a)


def addr_from_topic(t):
    return Web3.to_checksum_address("0x" + (t.hex() if isinstance(t, bytes) else t)[-40:])


# ---------- fast transaction sender ----------

class Sender:
    """Signs locally with cached nonce/gas price and broadcasts to several RPCs in parallel: one network round trip."""

    def __init__(self, key):
        self.acct = w3.eth.account.from_key(key)
        self.address = self.acct.address
        self.pool = ThreadPoolExecutor(max_workers=len(CFG["send_rpc_urls"]) + 2)
        self.gas_price_at = 0.0
        self._last_receipt_poll = {}
        self.lock = threading.Lock()  # the sell probe sends from another thread than the main loop
        self.resync()

    def resync(self):
        self.nonce = w3.eth.get_transaction_count(self.address, "pending")
        self.refresh_gas_price(force=True)

    def refresh_gas_price(self, force=False):
        if force or time.time() - self.gas_price_at > 30:
            self.gas_price = network_gas_price()
            self.gas_price_at = time.time()

    def _post(self, url, raw):
        r = http.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "eth_sendRawTransaction", "params": [raw]}, timeout=5)
        d = r.json()
        if "error" in d and "already known" not in d["error"].get("message", ""):
            raise RuntimeError(d["error"].get("message"))
        return url

    def send(self, to, data, value=0, gas=None, gas_price=None):
        with self.lock:
            return self._send(to, data, value, gas, gas_price)

    def _send(self, to, data, value=0, gas=None, gas_price=None):
        tx = {"chainId": 56, "nonce": self.nonce, "to": to, "value": value, "data": data,
              "gas": gas or CFG["gas_limit_buy"], "gasPrice": gas_price or self.gas_price}
        signed = self.acct.sign_transaction(tx)
        raw = "0x" + signed.raw_transaction.hex().removeprefix("0x")
        futures = [self.pool.submit(self._post, u, raw) for u in CFG["send_rpc_urls"]]
        errors = []
        for f in as_completed(futures):
            try:
                f.result()
                self.nonce += 1
                return "0x" + signed.hash.hex().removeprefix("0x")
            except Exception as e:
                errors.append(str(e)[:80])
        self.resync()
        raise RuntimeError("ни один узел не принял транзакцию: " + "; ".join(errors))

    def buy(self, token, bnb_wei, min_out):
        data = router.encode_abi("swapExactETHForTokensSupportingFeeOnTransferTokens",
                                 args=[min_out, [WBNB, token], self.address, int(time.time()) + 60])
        return self.send(ROUTER, data, value=bnb_wei, gas=CFG["gas_limit_buy"])

    def approve(self, token):
        data = ERC20.encode_abi("approve", args=[ROUTER, 2**256 - 1])
        return self.send(token, data, gas=100_000)

    def sell(self, token, amount, min_out, gas_price=None):
        data = router.encode_abi("swapExactTokensForETHSupportingFeeOnTransferTokens",
                                 args=[amount, min_out, [token, WBNB], self.address, int(time.time()) + 60])
        # taxed tokens swap their collected fee inside transfer(): a sell can burn ~400k+ gas; only used gas is paid
        return self.send(ROUTER, data, gas=CFG["gas_limit_sell"], gas_price=gas_price)

    def receipt(self, h):
        """Polled at most every 150 ms per tx. publicnode refuses receipts without a paid token,
        so receipt_rpc_urls are asked in order until one answers."""
        now = time.time()
        if now - self._last_receipt_poll.get(h, 0) < 0.15:
            return None
        self._last_receipt_poll[h] = now
        for u in CFG["receipt_rpc_urls"]:
            try:
                d = http.post(u, json={"jsonrpc": "2.0", "id": 1, "method": "eth_getTransactionReceipt", "params": [h]},
                              timeout=3).json()
                if "error" in d:
                    continue
                return parse_raw_receipt(d["result"]) if d.get("result") else None
            except Exception:
                continue
        return None


def parse_raw_receipt(r):
    """Raw JSON-RPC receipt -> the fields the bot uses, in the same shape web3 returns."""
    return {"status": int(r["status"], 16), "gasUsed": int(r["gasUsed"], 16),
            "effectiveGasPrice": int(r.get("effectiveGasPrice") or "0x0", 16),
            "logs": [{"address": l["address"], "topics": [bytes.fromhex(t[2:]) for t in l["topics"]],
                      "data": bytes.fromhex(l["data"][2:])} for l in r["logs"]]}


def network_gas_price():
    """Lowest gas price suggested by the nodes, kept within [min_gas_price_gwei, max_gas_price_gwei]. Nodes disagree a lot:
    48.club suggests 1 gwei while BSC blocks accept 0.05 gwei — paying its suggestion cost 20x more gas per trade."""
    seen = []
    for url in dict.fromkeys(CFG.get("rpc_urls", []) + CFG.get("send_rpc_urls", [])):
        try:
            v = int(http.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "eth_gasPrice", "params": []}, timeout=4).json()["result"], 16)
            if v > 0:
                seen.append(v)
        except Exception:
            continue
    lo = Web3.to_wei(CFG["min_gas_price_gwei"], "gwei")
    hi = Web3.to_wei(CFG.get("max_gas_price_gwei", 0.3), "gwei")
    return min(hi, max(lo, min(seen))) if seen else lo


_gas_cache = {"v": 0, "at": 0.0}


def cached_gas_price():
    """For paper/journal cost estimates: the same price a live trade would pay, refreshed once a minute."""
    if time.time() - _gas_cache["at"] > 60:
        _gas_cache["v"], _gas_cache["at"] = network_gas_price(), time.time()
    return _gas_cache["v"]


def tokens_received(rc, token, me):
    total = 0
    for l in rc["logs"]:
        if l["address"].lower() == token.lower() and l["topics"] and \
                "0x" + l["topics"][0].hex().removeprefix("0x") == TRANSFER_TOPIC and addr_from_topic(l["topics"][2]) == me:
            total += int.from_bytes(bytes(l["data"]), "big")
    return total


def bnb_received(rc):
    for l in rc["logs"]:
        if l["address"].lower() == WBNB.lower() and l["topics"] and \
                "0x" + l["topics"][0].hex().removeprefix("0x") == WITHDRAWAL_TOPIC \
                and addr_from_topic(l["topics"][1]) == ROUTER:
            return int.from_bytes(bytes(l["data"]), "big") / 1e18
    return 0.0


def gas_cost(rc):
    return rc["gasUsed"] * rc["effectiveGasPrice"] / 1e18


# ---------- watched pools ----------

class Pool:
    def __init__(self, pair, token, symbol, btax, stax):
        self.pair, self.token, self.symbol = pair, token, symbol
        self.btax, self.stax = btax, stax
        c = w3.eth.contract(pair, abi=PAIR_ABI)
        self.token_is0 = c.functions.token0().call().lower() == token.lower()
        self.refresh()

    def refresh(self):
        r0, r1, _ = w3.eth.contract(self.pair, abi=PAIR_ABI).functions.getReserves().call()
        self.set_reserves(r0, r1)

    def set_reserves(self, r0, r1):
        self.r_tok, self.r_bnb = (r0, r1) if self.token_is0 else (r1, r0)

    def quote_buy(self, bnb_wei):
        return int(amount_out(bnb_wei, self.r_bnb, self.r_tok) * (1 - self.btax / 100))

    def quote_sell(self, tokens):
        return amount_out(int(tokens * (1 - self.stax / 100)), self.r_tok, self.r_bnb) / 1e18


# ---------- state & journal ----------

def load_state():
    if os.path.exists(STATE_FILE):
        st = json.load(open(STATE_FILE, encoding="utf-8"))
        if st.get("version") == 3:
            return st
    return {"version": 3, "positions": {}, "day": "", "day_pnl_bnb": 0.0, "last_trade": {}, "blacklist": [],
            "approved": [], "paper_balance_bnb": 0.1}


def save_state(st):
    tmp = STATE_FILE + ".tmp"
    json.dump(st, open(tmp, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)


def record_trade(pos, net_bnb, reason):
    new = not os.path.exists(TRADES_FILE)
    pnl = net_bnb - pos["stake_bnb"]
    with open(TRADES_FILE, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        if new:
            w.writerow(["mode", "symbol", "token", "opened", "closed", "seconds", "stake_bnb", "exit_bnb",
                        "pnl_bnb", "pnl_pct", "reason", "signal_to_buy_ms", "exit_to_sell_ms"])
        opened = dt.datetime.fromtimestamp(pos["opened_at"])
        w.writerow([CFG["mode"], pos["symbol"], pos["token"], f"{opened:%Y-%m-%d %H:%M:%S}",
                    f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}", round(time.time() - pos["opened_at"]),
                    pos["stake_bnb"], round(net_bnb, 8), round(pnl, 8), round(pnl / pos["stake_bnb"] * 100, 2), reason,
                    pos.get("buy_ms", ""), pos.get("sell_ms", "")])
    return pnl


# ---------- names, security, the manual-launch filter ----------

pair_cache = {}  # token -> PancakeSwap V2 pair with WBNB (or None)


def v2_pair(token):
    if token not in pair_cache:
        p = FACTORY.functions.getPair(token, WBNB).call()
        pair_cache[token] = None if int(p, 16) == 0 else Web3.to_checksum_address(p)
    return pair_cache[token]


KNOWN_NAMES = set()  # symbols of tokens already trading on Binance lists
NAMES_FILE = os.path.join(HERE, "manual_names.txt")  # "symbol<TAB>token" of every manual launch seen by live, paper and journal
NAMES_LOCK = threading.Lock()


def name_seen_before(sym, token):
    """True if another token already launched under this symbol; the launch is written to disk at once,
    so the live bot, the paper bot and the journal share one list that survives restarts."""
    key = " ".join(sym.strip().lower().split())  # no tabs/newlines: they would break the file format
    token = token.lower()
    with NAMES_LOCK:
        try:
            lines = open(NAMES_FILE, encoding="utf-8").read().splitlines()
        except FileNotFoundError:
            lines = []
        tokens = {t for n, _, t in (l.partition("\t") for l in lines) if n == key}
        if token not in tokens:
            with open(NAMES_FILE, "a", encoding="utf-8") as f:
                f.write(f"{key}\t{token}\n")
    return bool(tokens - {token})


_known_names_started = False


def start_known_names():
    """Every 20 s one of the Binance Wallet lists (rising, newest, trending memes — the same data as
    web3.binance.com/markets) adds its symbols to KNOWN_NAMES. It is the website's internal API, not an official one."""
    global _known_names_started
    if _known_names_started:
        return
    _known_names_started = True

    def loop():
        i = 0
        while True:
            rank_type = CFG["binance_rank_types"][i % len(CFG["binance_rank_types"])]
            i += 1
            try:
                r = http.post(BINANCE_RANK, json={"rankType": rank_type, "period": 30, "chainId": "56", "tabId": 100,
                                                  "memeTrending": True, "size": 100},
                              headers={"Content-Type": "application/json", "clienttype": "web", "User-Agent": "Mozilla/5.0"},
                              timeout=10)
                for t in (r.json().get("data") or {}).get("tokens") or []:
                    KNOWN_NAMES.add(" ".join(str(t.get("symbol", "")).strip().lower().split()))
            except Exception:
                pass  # names are an extra check; the shared names file works without them
            time.sleep(20)

    threading.Thread(target=loop, daemon=True).start()


RED_FLAGS = {  # GoPlus field -> why it is dangerous
    "is_honeypot": "honeypot: продать нельзя",
    "cannot_buy": "покупка заблокирована",
    "cannot_sell_all": "нельзя продать всё",
    "owner_change_balance": "владелец может менять балансы",
    "hidden_owner": "скрытый владелец",
    "transfer_pausable": "владелец может остановить переводы",
    "is_blacklisted": "есть чёрный список кошельков",
    "slippage_modifiable": "налог можно поднять в любой момент",
    "is_mintable": "можно допечатать токены",
    "trading_cooldown": "ограничение на частоту продаж",
    "is_proxy": "код контракта можно подменить",
    "selfdestruct": "контракт может самоуничтожиться",
}


GOPLUS_PENDING = set()  # bought before GoPlus knew the token: asked again while the position is open


def security_check(token, pair):
    """Returns (ok, buy_tax, sell_tax, reason). If the check itself is unavailable the token is skipped."""
    try:
        d = http.get(f"https://api.gopluslabs.io/api/v1/token_security/56?contract_addresses={token}", timeout=10).json()
        g = (d.get("result") or {}).get(token.lower())
    except Exception as e:
        return False, 0, 0, f"проверка GoPlus недоступна ({e})"
    if not g:
        return False, 0, 0, "GoPlus ещё не знает токен"
    for field, why in RED_FLAGS.items():
        if g.get(field) == "1":
            return False, 0, 0, why
    if g.get("is_open_source") != "1":
        return False, 0, 0, "код контракта скрыт"
    bt = float(g.get("buy_tax") or 0) * 100
    stx = float(g.get("sell_tax") or 0) * 100
    # honeypot.is actually simulates a buy+sell; it lags on fresh pairs, so it only adds info when it knows the pair
    try:
        h = http.get("https://api.honeypot.is/v2/IsHoneypot",
                     params={"address": token, "pair": pair, "chainID": 56}, timeout=8).json()
        if h.get("simulationSuccess"):
            if h.get("honeypotResult", {}).get("isHoneypot"):
                return False, 0, 0, "honeypot: продать нельзя (симуляция)"
            sim = h["simulationResult"]
            bt, stx = max(bt, float(sim.get("buyTax", 0))), max(stx, float(sim.get("sellTax", 0)))
    except Exception:
        pass
    if max(bt, stx) > CFG["max_tax_pct"]:
        return False, bt, stx, f"налог {bt:.1f}%/{stx:.1f}%"
    return True, bt, stx, "ok"


TRADE_SIM_FILE = os.path.join(HERE, "sim", "TradeSim.runtime.hex")  # compiled sim/TradeSim.sol
TRADE_SIM_CODE = "0x" + open(TRADE_SIM_FILE).read().strip() if os.path.exists(TRADE_SIM_FILE) else ""
TRADE_SIM_ADDR = "0x00000000000000000000000000000000005e7a11"  # made-up addresses: they only exist inside the eth_call
TRADE_SIM_FROM = "0x00000000000000000000000000000000000f1234"
TRADE_SIM_RUN = Web3.keccak(text="run(address,address,address,address)")[:4]


def trade_sim(token, pair, stake_bnb):
    """Our own honeypot/tax check, ~0.15 s and independent of GoPlus: an eth_call with state override places
    sim/TradeSim.sol at a made-up address, buys the token for stake_bnb and sells it all back in one simulated call.
    Nothing is sent to the network. Returns (ok, buy_tax, sell_tax, reason)."""
    if not TRADE_SIM_CODE:
        return False, 0, 0, "нет файла симуляции sim/TradeSim.runtime.hex"
    data = "0x" + (TRADE_SIM_RUN + encode(["address"] * 4, [ROUTER, token, WBNB, pair])).hex()
    params = [{"from": TRADE_SIM_FROM, "to": TRADE_SIM_ADDR, "value": hex(Web3.to_wei(stake_bnb, "ether")), "data": data,
               "gas": hex(5_000_000)},
              "latest", {TRADE_SIM_ADDR: {"code": TRADE_SIM_CODE}, TRADE_SIM_FROM: {"balance": hex(10**21)}}]
    err = None
    for url in CFG.get("rpc_urls") or [CFG["rpc_url"]]:
        try:
            r = http.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "eth_call", "params": params}, timeout=5).json()
        except Exception as e:
            err = str(e)[:60]
            continue
        if "result" not in r:
            err = str((r.get("error") or {}).get("message", r))[:60]
            continue
        buy_ok, sell_ok, exp_t, got_t, exp_b, got_b, _, _ = decode(["bool", "bool"] + ["uint256"] * 6,
                                                                  bytes.fromhex(r["result"][2:]))
        if not buy_ok:
            return False, 0, 0, "симуляция: купить нельзя"
        if not sell_ok:
            return False, 0, 0, "симуляция: продать нельзя (honeypot)"
        bt = max(0.0, (1 - got_t / exp_t) * 100) if exp_t else 100.0
        stx = max(0.0, (1 - got_b / exp_b) * 100) if exp_b else 100.0
        if max(bt, stx) > CFG["max_tax_pct"]:
            return False, bt, stx, f"симуляция: налог {bt:.1f}%/{stx:.1f}%"
        return True, bt, stx, "ok"
    return False, 0, 0, f"симуляция недоступна ({err})"


def manual_filter(pair, token, sym, r_bnb, r_tok):
    """All filters of the manual-launch strategy, shared by the bot and the journal.
    Returns (ok, buy_tax, sell_tax, reason, bait); bait = the symbol already belongs to another token."""
    bait = name_seen_before(sym, token) or " ".join(sym.strip().lower().split()) in KNOWN_NAMES
    # 26.10 journal: copies that pass the other filters were up ~+78% (17.6 BNB, n=104) and ~+117% (other sizes, n=21)
    # back off 26.10 19:20: since ~18 h copies are pulled at 26-35 s, right at our 30 s entry (lab tests a copy scalp instead)
    if bait and not CFG.get("manual_allow_copies", False):
        return False, 0, 0, "имя уже торгуется (копия-приманка)", True
    lo, hi = CFG.get("manual_bait_bnb_range", [17.5, 17.8])
    if not bait and lo <= r_bnb / 1e18 <= hi:  # 17.6 BNB non-copies were drained within seconds: +17%, 5 of 11 zeroed
        return False, 0, 0, f"в пуле {r_bnb / 1e18:.2f} BNB, типичная сумма приманки", False
    # journal 25-26.10: every pool that got the whole supply (SkillNet, MCPX, MemoryAI... ~10 BNB) was pulled within
    # seconds, while launches with ~20% of the supply in the pool were up on average
    try:
        supply = w3.eth.contract(token, abi=ERC20_ABI).functions.totalSupply().call()
    except Exception:
        supply = 0
    share = r_tok / supply * 100 if supply else 100
    if share >= CFG.get("manual_max_pool_supply_pct", 99):
        return False, 0, 0, f"в пул положено {share:.0f}% выпуска — так делают мошенники", bait
    sim_ok, sim_bt, sim_st, sim_why = trade_sim(token, pair, CFG["stake_bnb"])
    if not sim_ok:
        return False, sim_bt, sim_st, sim_why, bait
    # GoPlus learns a fresh token within seconds; buying before that let unchecked tokens through (BNC纳斯)
    for _ in range(int(CFG.get("manual_goplus_wait_seconds", 15))):
        ok, btax, stax, why = security_check(token, pair)
        if not why.startswith(("GoPlus ещё", "проверка")):
            break
        time.sleep(1)
    if why == "код контракта скрыт" and CFG.get("manual_allow_hidden_code", True):
        # the code cannot be read, but the simulated buy+sell above went through with a small tax
        return True, sim_bt, sim_st, "ok: код скрыт, симуляция покупки и продажи прошла", bait
    if why.startswith(("GoPlus ещё", "проверка")) and CFG.get("manual_goplus_after_buy", False):
        # GoPlus lags on fresh tokens (Tie 26.10: +110% skipped; journal: 9 such launches +78%, none zeroed).
        # The simulated buy+sell passed, so buy now; goplus_watch asks again and sells on a red flag
        GOPLUS_PENDING.add(token.lower())
        return True, sim_bt, sim_st, "ok: GoPlus ещё не знает токен, проверю после покупки", bait
    if not ok:
        return False, btax, stax, why, bait
    return True, max(btax, sim_bt), max(stax, sim_st), "ok", bait


# ---------- WebSocket feed ----------

PAIR_CREATED_TOPIC = topic("PairCreated(address,address,address,uint256)")
SYMBOL_ABI = [{"name": "symbol", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "string"}]}]


def parse_pair_created(l):
    """PairCreated log (WebSocket or HTTP) -> (pair, token) for WBNB pairs, else None."""
    t0, t1 = addr_from_topic(l["topics"][1]), addr_from_topic(l["topics"][2])
    if WBNB not in (t0, t1):
        return None
    data = l["data"] if isinstance(l["data"], str) else "0x" + bytes(l["data"]).hex()
    return Web3.to_checksum_address("0x" + data[2:][24:64]), (t1 if t0 == WBNB else t0)


class ManualLaunchWatcher:
    """Strategy 'manual launch': a brand-new PancakeSwap pool funded by hand with >= manual_min_bnb.
    Backtest (12 h, 182 launches) and the journal: early buyers were up on average within 2-5 minutes, but ~20% of
    pools were pulled to zero in that time, so only launches that pass manual_filter are bought."""

    def __init__(self, bot):
        self.bot = bot

    def check(self, pair, token):
        ms = self.bot.manual_stats
        # liquidity often comes a few seconds after the pair; the journal (whose data the rules come from) looks for
        # 20 s, and with a 4 s window the bot never saw the profitable launches (属虎联盟, WOJ 26.10)
        for delay in (0.4, 1.0, 3.0, 8.0, 15.0):
            time.sleep(delay)
            try:
                c = w3.eth.contract(pair, abi=PAIR_ABI)
                r0, r1, _ = c.functions.getReserves().call()
                is0 = c.functions.token0().call().lower() == token.lower()
            except Exception:
                continue
            r_tok, r_bnb = (r0, r1) if is0 else (r1, r0)
            if r_bnb >= CFG["manual_min_bnb"] * 10**18:
                break
        else:
            return
        ms["funded"] += 1
        copier = getattr(self.bot, "copier", None)
        if copier:  # prepare the copy checks while the launch filters below run
            threading.Thread(target=copier.prepare, args=(pair, token), daemon=True).start()
        t_seen = time.time()
        try:
            sym = w3.eth.contract(token, abi=SYMBOL_ABI).functions.symbol().call()[:20]
        except Exception:
            sym = token[-4:]
        creator = launch_creator(pair)
        # any launch whose token keeps the guard wallet in slot 6 (HELL, GOOGLc template), whatever its series
        try:
            guard = w3.eth.get_storage_at(token, 6).hex()[-40:].lower() == GUARD[2:]
        except Exception:
            guard = False
        if guard:  # a trap for any outside buyer: never bought with wallet A (only the wallet-B test)
            if LIVE and HOLD_ADDRESS and getattr(self.bot, "sender", None) and hold_trade_left():
                threading.Thread(target=hold_trade, args=(self.bot, pair, token, sym, creator, t_seen), daemon=True).start()
            elif LIVE and HOLD_ADDRESS and getattr(self.bot, "sender", None) and HOLD_TEST["done"] < CFG.get("hold_test_max", 0):
                HOLD_TEST["done"] += 1
                threading.Thread(target=hold_test, args=(self.bot, pair, token, sym), daemon=True).start()
            ms["skipped"] += 1
            ms["last_skip"] = f"{sym}: токен со сторожем 0xbd418ce6"
            log(f"  ручной запуск {sym}: пропуск — в токене сторож {GUARD[:10]} (блокирует посторонних покупателей)")
            return
        if LIVE and CFG.get("hold_only"):
            ms["skipped"] += 1
            ms["last_skip"] = f"{sym}: без сторожа"
            log(f"  ручной запуск {sym}: пропуск — live берёт только токены со сторожем (сделка A→B)")
            return
        if CFG.get("series_only"):  # cheap, so before the filters below (GoPlus may wait up to 15 s)
            ser = launch_series(creator)
            if ser not in CFG["series_only"]:
                ms["skipped"] += 1
                ms["last_skip"] = f"{sym}: серия {ser}"
                log(f"  ручной запуск {sym}: пропуск — серия кассы {ser}, беру только {','.join(CFG['series_only'])}")
                return
            # history 19-21.10 + 26-27.10: pulls of 55.02 and 40.02 come in streaks - after a launch pulled within 60 s
            # the next one of the series was too in 96% (after a normal one: 3%). Every launch of the series is followed
            # for 60 s, and the series is skipped while its last launch was pulled early
            threading.Thread(target=series_fate, args=(ser, pair, is0, r_bnb, sym), daemon=True).start()
            if SERIES_EARLY.get(ser):
                ms["skipped"] += 1
                ms["last_skip"] = f"{sym}: серия {ser} в полосе ранних изъятий"
                log(f"  ручной запуск {sym}: пропуск — серия {ser} сейчас в полосе ранних изъятий ({SERIES_EARLY[ser]})")
                return
            log(f"  ручной запуск {sym}: серия кассы {ser} — подходит, проверяю фильтры")
        ok, btax, stax, why, _ = manual_filter(pair, token, sym, r_bnb, r_tok)
        if not ok:
            ms["skipped"] += 1
            ms["last_skip"] = f"{sym}: {why}"
            log(f"  ручной запуск {sym}: пропуск — {why}")
            return
        try:
            pool = Pool(pair, token, sym, btax, stax)
        except Exception:
            return
        pool.creator = creator
        pool.series = ser if CFG.get("series_only") else ""
        # journal 26.10: entering 10-30 s later turned -26% into +35% on the night's launches (the pulls came first)
        wait = CFG.get("manual_entry_delay_seconds", 30) - (time.time() - t_seen)
        if wait > 0:
            log(f"  ручной запуск {sym}: фильтры пройдены, жду {wait:.0f} с — пул должен выжить")
            time.sleep(wait)
            try:
                pool.refresh()
            except Exception:
                return
            if pool.r_bnb < r_bnb * CFG.get("manual_min_pool_left", 0.9):
                ms["skipped"] += 1
                ms["last_skip"] = f"{sym}: деньги ушли из пула за время ожидания"
                log(f"  ручной запуск {sym}: пропуск — за время ожидания из пула ушло "
                    f"{(1 - pool.r_bnb / r_bnb) * 100:.0f}% BNB")
                return
        if CFG.get("adaptive_rug_window"):
            w = rug_window(r_bnb / 1e18)
            age = time.time() - t_seen
            if w and w[1] > age:
                if w[1] > CFG.get("manual_entry_max_wait_seconds", 240):
                    ms["skipped"] += 1
                    ms["last_skip"] = f"{sym}: окно изъятий слишком длинное"
                    log(f"  ручной запуск {sym}: пропуск — похожие пулы изымали до {w[1] - 10:.0f} с")
                    return
                log(f"  ручной запуск {sym}: похожие пулы за час изымали на {w[0] + 5:.0f}–{w[1] - 10:.0f} с — жду до {w[1]:.0f} с")
                time.sleep(w[1] - age)
                try:
                    pool.refresh()
                except Exception:
                    return
                if pool.r_bnb < r_bnb * CFG.get("manual_min_pool_left", 0.9):
                    ms["skipped"] += 1
                    ms["last_skip"] = f"{sym}: пул опустошили в окне изъятий"
                    log(f"  ручной запуск {sym}: пропуск — пул опустошили в окне изъятий (обход сработал)")
                    return
        # trap pools stay flat until the first buyer arrives and are pulled in that very block: buy only a rising pool
        rise = CFG.get("manual_entry_min_rise_pct", 0)
        if rise:
            price0 = r_bnb / r_tok if r_tok else 0
            deadline = t_seen + CFG.get("manual_entry_max_wait_seconds", 240)
            log(f"  ручной запуск {sym}: жду, пока цена вырастет на {rise:g}% (другие начнут покупать)")
            while True:
                try:
                    pool.refresh()
                except Exception:
                    pass
                if pool.r_bnb < r_bnb * 0.5:
                    ms["skipped"] += 1
                    ms["last_skip"] = f"{sym}: пул опустошили, пока ждал роста"
                    log(f"  ручной запуск {sym}: пропуск — пул опустошили, пока ждал роста цены")
                    return
                if price0 and pool.r_tok and pool.r_bnb / pool.r_tok >= price0 * (1 + rise / 100):
                    break
                if time.time() > deadline:
                    ms["skipped"] += 1
                    ms["last_skip"] = f"{sym}: цена не выросла"
                    log(f"  ручной запуск {sym}: пропуск — за {CFG.get('manual_entry_max_wait_seconds', 240)} с цена не выросла на {rise:g}%")
                    return
                time.sleep(1)
        # off by default: 26.10 SATO had the same approval as MEGA and went +247%; the journal collects data on it
        approved = lp_approved_to(pair, pool.creator) if CFG.get("manual_skip_lp_approved", False) else ""
        if approved:
            ms["skipped"] += 1
            ms["last_skip"] = f"{sym}: создатель разрешил забрать ликвидность {approved}"
            log(f"  ручной запуск {sym}: пропуск — создатель уже разрешил забрать ликвидность пула {approved}")
            return
        if LIVE and CFG.get("sell_probe") and getattr(self.bot, "sender", None):
            if not self.bot.can_open():  # paused / limit / no room: the probe would spend real BNB for nothing
                ms["skipped"] += 1
                log(f"  ручной запуск {sym}: пропуск — пауза/лимит, пробу не делаю")
                return
            ok, why = self.bot.sell_probe(pool)
            if not ok:
                self.bot.st["blacklist"].append(pool.token)
                ms["skipped"] += 1
                ms["last_skip"] = f"{sym}: {why}"
                log(f"  ручной запуск {sym}: пропуск — {why}, забываю монету")
                return
            log(f"  ручной запуск {sym}: {why}")
            try:  # the probe took ~20 s: the pool must still be there
                pool.refresh()
            except Exception:
                return
            if pool.r_bnb < r_bnb * CFG.get("manual_min_pool_left", 0.9):
                ms["skipped"] += 1
                ms["last_skip"] = f"{sym}: деньги ушли из пула за время пробы"
                log(f"  ручной запуск {sym}: пропуск — за время пробы из пула ушло {(1 - pool.r_bnb / r_bnb) * 100:.0f}% BNB")
                return
        ms["passed"] += 1
        log(f"РУЧНОЙ ЗАПУСК {sym}: в пуле {pool.r_bnb / 1e18:.1f} BNB, фильтры и {time.time() - t_seen:.0f} с ожидания пройдены")
        self.bot.manual_queue.put((pool, time.time()))


class Feed(threading.Thread):
    """Pushes new blocks, new pools and Swap/Sync/mint events of watched pools; resubscribes when the watchlist changes."""

    def __init__(self, bot):
        super().__init__(daemon=True)
        self.bot = bot
        self.last_msg = 0.0
        self.head = 0

    def healthy(self, max_silence=3):
        return time.time() - self.last_msg < max_silence

    def run(self):
        while True:
            try:
                self.session()
            except Exception as e:
                log(f"WebSocket: обрыв ({str(e)[:60]}), переподключаюсь")
            time.sleep(1)

    def session(self):
        with connect(CFG["ws_url"], open_timeout=10, max_size=None, ping_interval=20) as ws:
            req_id = 0

            def call(method, params):
                nonlocal req_id
                req_id += 1
                ws.send(json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}))
                return req_id

            call("eth_subscribe", ["newHeads"])
            call("eth_subscribe", ["logs", {"address": FACTORY.address, "topics": [PAIR_CREATED_TOPIC]}])  # new pools
            subscribed, logs_req, logs_sub = None, None, None
            mint_req, mint_sub = None, None
            for p in list(self.bot.pools.values()):  # reserves may have moved while we were disconnected
                try:
                    p.refresh()
                except Exception:
                    pass
            log("WebSocket: подключён, новые блоки приходят в реальном времени")
            self.last_msg = time.time()
            last_pair = time.time()
            while True:
                pairs = sorted(self.bot.pools)
                if pairs != subscribed:
                    for sub in (logs_sub, mint_sub):
                        if sub:
                            call("eth_unsubscribe", [sub])
                    logs_sub = mint_sub = None
                    logs_req = call("eth_subscribe", ["logs", {"address": pairs, "topics": [[SWAP_TOPIC, SYNC_TOPIC]]}]) if pairs else None
                    tokens = sorted({p.token for p in list(self.bot.pools.values())})
                    mint_req = call("eth_subscribe", ["logs", {"address": tokens, "topics": [TRANSFER_TOPIC, ZERO_TOPIC]}]) if tokens else None
                    subscribed = pairs
                    # events between the old and the new subscription are lost: read the reserves again
                    threading.Thread(target=lambda ps: [p.refresh() for p in ps], args=(list(self.bot.pools.values()),),
                                     daemon=True).start()
                try:
                    msg = json.loads(ws.recv(timeout=0.5))
                except TimeoutError:
                    if time.time() - self.last_msg > 15:  # a block every ~0.45 s: 15 s of silence = dead subscription
                        raise RuntimeError("новые блоки не приходят 15 с")
                    continue
                self.last_msg = time.time()
                # 26.10 19:56: after a WebSocket hiccup the node silently dropped the PairCreated subscription while blocks
                # kept coming, and every bot was blind to new pools for 25 min (BSC creates several pairs a minute)
                if time.time() - last_pair > 300:
                    raise RuntimeError("новые пулы не приходят 5 мин — подписка потеряна")
                if msg.get("id") == logs_req and "result" in msg:
                    logs_sub = msg["result"]
                    continue
                if msg.get("id") == mint_req and "result" in msg:
                    mint_sub = msg["result"]
                    continue
                if msg.get("method") != "eth_subscription":
                    continue
                res = (msg.get("params") or {}).get("result")
                if not isinstance(res, dict):  # odd notification from the node: ignore instead of dropping the socket
                    continue
                if "number" in res and "logsBloom" in res:  # new block header
                    self.head = int(res["number"], 16)
                    self.bot.on_block(self.head)
                elif "topics" in res and not res.get("removed"):
                    if res["topics"][0] == PAIR_CREATED_TOPIC:
                        last_pair = time.time()
                        self.bot.on_pair_created(res)
                    else:
                        self.bot.on_log(res)


class ReserveRefresher(threading.Thread):
    """Safety net under the WebSocket: every `every` s the reserves of the followed pools are read again over HTTP.
    26.10 the journal missed the Sync events of a pull (ResearchAI) and kept a dead pool at +21%; a missed event must
    never leave a position valued on stale reserves. owner.on_reserves(pool, bnb_before), if defined, sees each read."""

    def __init__(self, owner, every=2.0):
        super().__init__(daemon=True)
        self.owner, self.every = owner, every

    def run(self):
        while True:
            time.sleep(self.every)
            for pair, pool in list(self.owner.pools.items()):
                try:
                    r0, r1, _ = w3.eth.contract(pair, abi=PAIR_ABI).functions.getReserves().call()
                except Exception:
                    continue
                with self.owner.lock:
                    before = pool.r_bnb
                    pool.set_reserves(r0, r1)
                if hasattr(self.owner, "on_reserves"):
                    self.owner.on_reserves(pool, before)
            self.owner.wake.set()


class HttpPoller(threading.Thread):
    """Backup channel. Every second it checks the WebSocket; if it has been silent for 3 s, events of the last blocks
    (watched pools + new pools) are fetched over HTTP from whichever node answers. Duplicates are dropped by the
    owner (tx hash + log index), so both channels may deliver the same event safely."""

    def __init__(self, owner, feed):
        super().__init__(daemon=True)
        self.owner, self.feed = owner, feed
        self.last_block, self.active = None, False

    def run(self):
        while True:
            time.sleep(1)
            if self.feed.healthy(3):
                if self.active:
                    log("HTTP-резерв: WebSocket снова работает, резерв выключен")
                self.active, self.last_block = False, None
                continue
            try:
                head = w3.eth.block_number
                frm = head - 3 if self.last_block is None else self.last_block + 1
                if head < frm:
                    continue
                frm = max(frm, head - 40)
                addrs = list(self.owner.pools) + [FACTORY.address]
                resp = w3.provider.make_request("eth_getLogs", [{"fromBlock": hex(frm), "toBlock": hex(head), "address": addrs,
                                                                 "topics": [[SWAP_TOPIC, SYNC_TOPIC, PAIR_CREATED_TOPIC]]}])
                for l in resp.get("result") or []:
                    if l.get("removed"):
                        continue
                    if l["topics"][0] == PAIR_CREATED_TOPIC:
                        self.owner.on_pair_created(l)
                    else:
                        self.owner.on_log(l)
                self.owner.on_block(head)
                self.last_block = head
                if not self.active:
                    self.active = True
                    log("HTTP-резерв: WebSocket молчит — беру блоки и сделки через HTTP каждую секунду")
            except Exception as e:
                log(f"HTTP-резерв: ошибка {str(e)[:70]}")
                time.sleep(2)


class Mempool(threading.Thread):
    """Pending transactions of the public BSC mempool (~180 a second through publicnode), kept for `keep` seconds:
    hash -> (time seen, from, to, method selector, gas price in gwei). Lets us see a liquidity pull before its block."""

    def __init__(self, keep=180):
        super().__init__(daemon=True)
        self.keep, self.seen, self.order = keep, {}, deque()
        self.count, self.last_msg = 0, 0.0
        self.watchers = []  # callables(tx dict), called for every pending transaction; must be fast

    def healthy(self):
        return time.time() - self.last_msg < 10

    def lookup(self, tx_hash):
        return self.seen.get(str(tx_hash).lower())

    def run(self):
        while True:
            try:
                with connect(CFG["ws_url"], open_timeout=10, max_size=None, ping_interval=20) as ws:
                    ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_subscribe",
                                        "params": ["newPendingTransactions", True]}))
                    while True:
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
                                        (tx.get("input") or "")[:10], int(tx.get("gasPrice") or "0x0", 16) / 1e9)
                        self.order.append((now, h))
                        for w in self.watchers:
                            try:
                                w(tx)
                            except Exception:
                                pass
                        while self.order and now - self.order[0][0] > self.keep:
                            self.seen.pop(self.order.popleft()[1], None)
            except Exception as e:
                log(f"Мемпул: обрыв ({str(e)[:60]}), переподключаюсь")
                time.sleep(2)


# PancakeSwap V2 router: removeLiquidity, removeLiquidityETH, ...SupportingFeeOnTransferTokens and the *WithPermit forms
REMOVE_LIQUIDITY = {"0xbaa2abde", "0x02751cec", "0xaf2979eb", "0x2195995c", "0xded9382a", "0x5b0d5984"}
PAIR_BURN = "0x89afcb44"  # pair.burn(to), a pull without the router


NODEREAL_KEY = ""
if os.path.exists(os.path.join(HERE, ".env")):
    for _line in open(os.path.join(HERE, ".env"), encoding="utf-8-sig"):  # only this line is used here
        if _line.strip().startswith("NODEREAL_KEY="):
            NODEREAL_KEY = _line.split("=", 1)[1].strip().strip("\"'")
SERIES = [17.02, 40.02, 55.02, 12.29, 58.01, 17.84, 50.0, 9.58]


def launch_series(creator):
    """The scam network's cash desk pays each new creator a fixed amount ~93 blocks before the pool: 17.02, 40.02,
    55.02, 50.0... BNB, one amount per series. Last payment >= 1 BNB in 3000 blocks (NodeReal); "?" when unknown."""
    if not NODEREAL_KEY or not creator:
        return "?"
    try:
        b0 = w3.eth.block_number
        body = {"jsonrpc": "2.0", "id": 1, "method": "nr_getAssetTransfers",
                "params": [{"category": ["external"], "toAddress": creator.lower(), "fromBlock": hex(b0 - 3000),
                            "toBlock": hex(b0), "maxCount": "0x32", "excludeZeroValue": True}]}
        r = requests.post("https://bsc-mainnet.nodereal.io/v1/" + NODEREAL_KEY, json=body, timeout=10).json()
        tr = [t for t in (r.get("result") or {}).get("transfers", []) if int(t["value"], 16) >= 10**18]
        if not tr:
            return "нет кассы"
        bnb = int(max(tr, key=lambda t: int(t["blockNum"], 16))["value"], 16) / 1e18
        return next((str(x) for x in SERIES if abs(bnb - x) < 0.03), f"прочие {bnb:.2f}")
    except Exception:
        return "?"


SERIES_EARLY = {}  # series -> name of its last launch pulled within 60 s (empty once a launch outlives that)


def series_fate(ser, pair, is0, r_bnb0, sym):
    """Watches a launch of a series for 60 s: pulled (BNB -80%) -> the series is in an early-pull streak; survived ->
    the streak is over."""
    c = w3.eth.contract(pair, abi=PAIR_ABI)
    t_end = time.time() + 60
    while time.time() < t_end:
        time.sleep(2)
        try:
            r0, r1, _ = c.functions.getReserves().call()
        except Exception:
            continue
        if (r0 if not is0 else r1) < r_bnb0 * 0.2:
            if not SERIES_EARLY.get(ser):
                log(f"СЕРИЯ {ser}: {sym} изъят в первые 60 с — пропускаю серию, пока её запуск не проживёт 60 с")
            SERIES_EARLY[ser] = sym
            return
    if SERIES_EARLY.get(ser):
        log(f"СЕРИЯ {ser}: {sym} прожил 60 с — снова беру серию")
    SERIES_EARLY[ser] = ""


SELL_FAIL_FILE = os.path.join(HERE, "analysis", "sell_fail.csv")
TRANSFER_ABI = [{"name": "transfer", "type": "function", "stateMutability": "nonpayable",
                 "inputs": [{"type": "address"}, {"type": "uint256"}], "outputs": [{"type": "bool"}]}]


def sell_diagnose(pool, pos, me, amount):
    """27.10 HELL: only OUR wallet could not move the token (blacklist); KRON: nobody could sell into the pool
    (honeypot). Free eth_calls tell which, logged with a BscScan link and kept in analysis/sell_fail.csv."""
    t = w3.eth.contract(pool.token, abi=TRANSFER_ABI)
    other = Web3.to_checksum_address("0x1111111111111111111111111111111111111111")

    def ok(fn, frm):
        try:
            fn.call({"from": frm})
            return "да", ""
        except Exception as e:
            return "нет", str(e)[:120]
    move, why1 = ok(t.functions.transfer(other, 1), me)
    to_pool, why2 = ok(t.functions.transfer(pool.pair, amount), me)
    if move == "нет":
        verdict = "наш кошелёк в чёрном списке токена: нельзя ни продать, ни перевести"
    elif to_pool == "нет":
        verdict = "токен не даёт продавать в пул (honeypot), перевод на другой адрес проходит"
    else:
        verdict = "перевод в пул проходит, отказывает только роутер — возможно, временный запрет или налог"
    link = f"https://bscscan.com/token/{pool.token}?a={me}"
    log(f"РАЗБОР ПРОДАЖИ {pos['symbol']}: {verdict}. {link}")
    try:
        new = not os.path.exists(SELL_FAIL_FILE)
        with open(SELL_FAIL_FILE, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            if new:
                w.writerow(["time", "symbol", "token", "series", "verdict", "can_move", "to_pool", "reason", "bscscan"])
            w.writerow([dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), pos["symbol"], pool.token,
                        getattr(pool, "series", ""), verdict, move, to_pool, (why1 or why2)[:120], link])
    except Exception:
        pass


GUARD = "0xbd418ce6a83995971de9f1cf9f61f345e91aeadd"  # the network wallet that blacklists outside buyers (slot 6)
HOLD_TEST = {"done": 0}
HOLD_TEST_FILE = os.path.join(HERE, "analysis", "hold_test.csv")


def _hold_address():
    """Address of wallet B from .env BSC_HOLD_KEY; only the address is kept, the key is never logged."""
    path = os.path.join(HERE, ".env")
    try:
        for line in open(path, encoding="utf-8"):
            if line.strip().startswith("BSC_HOLD_KEY="):
                return w3.eth.account.from_key(line.split("=", 1)[1].strip()).address
    except Exception:
        pass
    return ""


HOLD_ADDRESS = _hold_address()


def hold_test(bot, pair, token, sym):
    """Tiny buy with wallet A, plain transfer to wallet B at once, then after 90 s a free check: can B still move
    the tokens into the pool (= sell) while A is blacklisted? Result in the log and analysis/hold_test.csv."""
    s = bot.sender
    t = w3.eth.contract(token, abi=TRANSFER_ABI)
    bal = lambda a: w3.eth.contract(token, abi=LP_ABI).functions.balanceOf(a).call()

    def wait(h):
        t_end = time.time() + 15
        while time.time() < t_end:
            time.sleep(0.2)
            rc = s.receipt(h)
            if rc:
                return rc
        return None

    def can_move(frm, amount):
        try:
            t.functions.transfer(pair, amount).call({"from": frm})
            return "да"
        except Exception:
            return "нет"
    res = ""
    try:
        rc = wait(s.buy(token, Web3.to_wei(CFG.get("sell_probe_bnb", 0.00001), "ether"), 0))
        got = bal(s.address)
        if not rc or rc["status"] != 1 or not got:
            res = "тестовая покупка не прошла"
        else:
            t_buy = time.time()
            rc2 = wait(s.send(token, t.encode_abi("transfer", args=[HOLD_ADDRESS, got]), gas=150_000))
            sec = time.time() - t_buy
            if not rc2 or rc2["status"] != 1:
                res = f"перевод на B не прошёл ({sec:.0f} с после покупки)"
            else:
                log(f"ТЕСТ B {sym}: токены переведены на B через {sec:.1f} с после покупки, жду 90 с")
                time.sleep(90)
                hb = bal(HOLD_ADDRESS)
                res = (f"через 90 с: B может продать = {can_move(HOLD_ADDRESS, hb)}, A может = {can_move(s.address, 1)}, "
                       f"перевод на B через {sec:.1f} с")
    except Exception as e:
        res = f"ошибка теста ({str(e)[:80]})"
    log(f"ТЕСТ B {sym}: {res}. https://bscscan.com/token/{token}?a={HOLD_ADDRESS}")
    try:
        new = not os.path.exists(HOLD_TEST_FILE)
        with open(HOLD_TEST_FILE, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            if new:
                w.writerow(["time", "symbol", "token", "result"])
            w.writerow([dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), sym, token, res])
    except Exception:
        pass


HOLD_TRADE_FILE = os.path.join(HERE, "analysis", "hold_trade.csv")
HOLD_TRADE = {"busy": False}


def hold_trade_left():
    """27.10 (user): ONE real trade A->B. Done once the file has a row (survives restarts)."""
    if HOLD_TRADE["busy"] or not CFG.get("hold_trade"):
        return False
    try:
        return len(open(HOLD_TRADE_FILE, encoding="utf-8-sig").read().strip().splitlines()) < 2
    except Exception:
        return True


def _hold_key():
    try:
        for line in open(os.path.join(HERE, ".env"), encoding="utf-8"):
            if line.strip().startswith("BSC_HOLD_KEY="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""


def _hold_trade_row(sym, token, stage, stake, exit_bnb, res):
    try:
        new = not os.path.exists(HOLD_TRADE_FILE)
        with open(HOLD_TRADE_FILE, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            if new:
                w.writerow(["time", "symbol", "token", "stage", "stake_bnb", "exit_bnb", "result"])
            w.writerow([dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), sym, token, stage, stake, exit_bnb, res])
    except Exception:
        pass


def hold_trade(bot, pair, token, sym, creator, t_seen):
    """27.10 (user): one real trade on a guard token of series 50.0. Wallet B gets gas and approves the router first;
    at 30 s wallet A buys with the stake and at once transfers all tokens to B (the guard marks A ~5-80 s after the
    buy); B sells at TP +100% or after 300 s. Log "СДЕЛКА A→B", analysis/hold_trade.csv."""
    HOLD_TRADE["busy"] = True
    s = bot.sender
    t = w3.eth.contract(token, abi=TRANSFER_ABI)
    bal = lambda a: w3.eth.contract(token, abi=LP_ABI).functions.balanceOf(a).call()
    stake = CFG["stake_bnb"]
    tp, hold = CFG["series_tp"].get("50.0", 100), CFG["series_hold"].get("50.0", 300)

    gas = [0]  # all gas of the trade (A buy + transfer, B approve + sell), counted into the trades.csv pnl

    def wait(snd, h):
        t_end = time.time() + 15
        while time.time() < t_end:
            time.sleep(0.2)
            rc = snd.receipt(h)
            if rc:
                gas[0] += rc["gasUsed"] * (rc["effectiveGasPrice"] or snd.gas_price)
                return rc
        return None
    pos = None
    try:
        # the guard template is series 50.0 only (template_scan 4/4); a NodeReal miss ("?") must not cost the one trade
        log(f"СДЕЛКА A→B {sym}: токен со сторожем, серия кассы {launch_series(creator)}, начинаю")
        if bot.wallet_bnb() < stake + CFG["min_wallet_reserve_bnb"] + 0.0003:
            log(f"СДЕЛКА A→B {sym}: не беру — мало BNB на кошельке A")
            return
        sb = Sender(_hold_key())
        # gas for B's approve + sell, the shortfall sent from A
        need = int((CFG["gas_limit_sell"] + 100_000) * s.gas_price * 1.5)
        have = w3.eth.get_balance(sb.address)
        if have < need:
            rc = wait(s, s.send(sb.address, b"", value=need - have, gas=21_000))
            if not rc or rc["status"] != 1:
                log(f"СДЕЛКА A→B {sym}: не беру — газ на B не дошёл")
                return
        sb.resync()
        rc = wait(sb, sb.approve(token))
        if not rc or rc["status"] != 1:
            log(f"СДЕЛКА A→B {sym}: не беру — approve на B не прошёл")
            return
        time.sleep(max(0.0, CFG["manual_entry_delay_seconds"] - (time.time() - t_seen)))
        pool = Pool(pair, token, sym, 0, 0)
        if pool.r_bnb / 1e18 < CFG.get("manual_min_pool_left", 0.7):
            log(f"СДЕЛКА A→B {sym}: не беру — пул пуст")
            return
        rc = wait(s, s.buy(token, Web3.to_wei(stake, "ether"), 0))
        t_buy = time.time()
        got = bal(s.address)
        if not rc or rc["status"] != 1 or not got:
            log(f"СДЕЛКА A→B {sym}: покупка не прошла")
            return
        pos = {"symbol": sym, "token": token, "stake_bnb": stake, "opened_at": t_buy}
        _hold_trade_row(sym, token, "куплено", stake, "", "")  # from here the one trade is spent, even on a crash
        rc2 = wait(s, s.send(token, t.encode_abi("transfer", args=[sb.address, got]), gas=150_000))
        sec = time.time() - t_buy
        if not rc2 or rc2["status"] != 1:
            res = f"перевод на B не прошёл ({sec:.1f} с после покупки), токены на A"
            log(f"СДЕЛКА A→B {sym}: {res}")
            _hold_trade_row(sym, token, "итог", stake, "", res)
            record_trade(pos, -gas[0] / 1e18, "A→B: " + res)
            return
        hb = bal(sb.address)
        log(f"СДЕЛКА A→B {sym}: куплено на {stake} BNB, токены на B через {sec:.1f} с, держу до +{tp}% или {hold} с")
        why = f"{hold} с"
        while time.time() - t_buy < hold:
            time.sleep(1)
            pool.refresh()
            worth = pool.quote_sell(hb)
            if worth >= stake * (1 + tp / 100):
                why = f"тейк +{tp}%"
                break
        data = router.encode_abi("swapExactTokensForETHSupportingFeeOnTransferTokens",
                                 args=[hb, 0, [token, WBNB], sb.address, int(time.time()) + 600])
        b0, res, exit_bnb = w3.eth.get_balance(sb.address), "", 0.0
        t_end = time.time() + 600  # a sell refused by the token costs nothing: dry-run first, retry for 10 min
        while time.time() < t_end:
            try:
                w3.eth.call({"from": sb.address, "to": ROUTER, "data": data})
            except Exception as e:
                res = f"B не может продать ({str(e)[:60]})"
                time.sleep(1)
                continue
            rc3 = wait(sb, sb.sell(token, hb, 0))
            if rc3 and rc3["status"] == 1:
                exit_bnb = (w3.eth.get_balance(sb.address) - b0 + rc3["gasUsed"] * (rc3["effectiveGasPrice"] or sb.gas_price)) / 1e18
                res = f"продано с B ({why}): {exit_bnb:.6f} BNB, {(exit_bnb / stake - 1) * 100:+.0f}% без газа"
                break
            res = "продажа с B отклонена сетью"
            time.sleep(1)
        log(f"СДЕЛКА A→B {sym}: {res}. https://bscscan.com/token/{token}?a={sb.address}")
        _hold_trade_row(sym, token, "итог", stake, round(exit_bnb, 8), res)
        record_trade(pos, exit_bnb - gas[0] / 1e18, "A→B: " + res)
    except Exception as e:
        log(f"СДЕЛКА A→B {sym}: ошибка ({str(e)[:80]})")
    finally:
        HOLD_TRADE["busy"] = False


def launch_creator(pair):
    """Who funded the pool: the sender of its first liquidity add (Mint) in the last 200 blocks. That wallet holds the
    LP tokens and is the one who pulls the money out."""
    try:
        head = w3.eth.block_number
        res = w3.provider.make_request("eth_getLogs", [{"fromBlock": hex(head - 200), "toBlock": hex(head),
                                                        "address": [pair], "topics": [MINT_TOPIC]}]).get("result") or []
        if res:
            return (w3.eth.get_transaction(res[0]["transactionHash"])["from"] or "").lower()
    except Exception:
        pass
    return ""


MINT_TOPIC = topic("Mint(address,uint256,uint256)")
ALLOWANCE_ABI = [{"name": "allowance", "type": "function", "stateMutability": "view",
                  "inputs": [{"type": "address"}, {"type": "address"}], "outputs": [{"type": "uint256"}]}]


def lp_approved_to(pair, creator):
    """A pull needs the creator's LP tokens: 26.10 the creator of MEGA had approved all of them to the rug contract
    before the pull. Returns who may spend the creator's LP (a rug contract or the router), or ""."""
    if not creator:
        return ""
    lp = w3.eth.contract(pair, abi=ALLOWANCE_ABI)
    for spender in CFG.get("rug_contracts", []) + [ROUTER]:
        try:
            if lp.functions.allowance(Web3.to_checksum_address(creator), Web3.to_checksum_address(spender)).call() > 0:
                return "контракту скамеров" if spender != ROUTER else "роутеру PancakeSwap"
        except Exception:
            continue
    return ""


def rug_signal(tx, token, pair, creator):
    """Why this pending transaction threatens a pool we hold, or "" (lower-case token/pair/creator)."""
    frm = (tx.get("from") or "").lower()
    to = (tx.get("to") or "").lower()
    data = (tx.get("input") or "").lower()
    sel = data[:10]
    if sel in REMOVE_LIQUIDITY and to == ROUTER.lower() and token[2:] in data:
        return "изъятие ликвидности через PancakeSwap"
    if sel == PAIR_BURN and to == pair:
        return "изъятие ликвидности напрямую из пула"
    # 26.10: profitable wallets also BUY through 0x37dc44b6 (BNB attached); a pull through it carries no BNB
    if to in CFG.get("rug_contracts", []) and (pair[2:] in data or token[2:] in data) \
            and not int(tx.get("value") or "0x0", 16):
        return "контракт скамеров трогает наш пул"
    # 26.10 04:00-04:15 the creators pulled through their own contract ~30 s after launch and sent two other
    # transactions in between: any move of the creator while we hold is a warning
    if creator and frm == creator and CFG.get("exit_on_creator_tx", True):
        return "создатель пула отправил транзакцию"
    return ""


class RugGuard:
    """Watches the mempool for a threat to a pool we hold (see rug_signal) and sells everything at once with a gas
    price above the threat's. 26.10 a router pull (ForecastX) was seen 284 ms before its block; the creators' own
    pull contract ~0.03-0.3 s, but their earlier transactions much sooner."""

    def __init__(self, bot):
        self.bot = bot

    def __call__(self, tx):
        for pos in list(self.bot.st["positions"].values()):
            if pos["status"] != "open" or pos.get("rug_alarm"):
                continue
            why = rug_signal(tx, pos["token"].lower(), pos["pair"].lower(), pos.get("creator", ""))
            if why:
                gwei = int(tx.get("gasPrice") or "0x0", 16) / 1e9
                pos["rug_alarm"] = {"tx": tx.get("hash"), "seen": time.time(), "gas_gwei": gwei, "why": why}
                pos["force_exit"] = why
                log(f"!!! {pos['symbol']}: в мемпуле {why} (газ {gwei:.3f} gwei) — продаю всё срочно")
                self.bot.wake.set()


ROUTER_BUYS = {"0x7ff36ab5", "0xb6f9de95", "0xfb3bdb41"}  # swapExactETHForTokens(+FeeOnTransfer), swapETHForExactTokens
NOT_TOKENS = {"0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c", "0x10ed43c718714eb63d5aa57b78b54704e256024e",
              "0xca143ce32fe78f1f7019d7d551a6402fc5350c73", "0x000000000000000000000000000000000000dead"}


def tx_addresses(tx):
    """Every address-shaped 32-byte word of the call data (token candidates): leaders buy through PancakeSwap, through
    0x37dc44b6 and through 0xa3e0e540, whose calls we cannot decode by name, but the token address is in the data."""
    data, me, out = (tx.get("input") or "")[10:].lower(), (tx.get("from") or "").lower(), []
    for i in range(0, len(data) - 63, 64):
        w = data[i:i + 64]
        if w[:24] == "0" * 24 and w[24:] != "0" * 40:
            a = "0x" + w[24:]
            if a not in NOT_TOKENS and a != me and a not in out:
                out.append(a)
    return out


class LeaderCopier:
    """Copies the wallets that made money on recent launches (leaders.top, rebuilt every minute from journal_swaps.csv):
    a leader's pending transaction with BNB attached is a buy -> we buy the same token right away (no launch filters,
    only the simulated buy+sell); a leader's transaction without BNB that names a token we copied is a sell -> we sell.
    Our own exits (TP / SL / hold) still apply. Journal replay 26.10: +9..11% per copied trade, but ~15% zeroed."""

    def __init__(self, bot):
        # 26.10 21:29 live: one worker thread and ~8 RPC calls per buy made the copy 16-24 s late, and both copies
        # (GINNAN, BX) bought right into the ~26-35 s pull. Now: parallel workers, no creator lookup, cached pairs,
        # and a copy later than copy_max_lag_s after the leader's transaction is skipped.
        self.bot, self.leaders, self.pairs, self.ready = bot, {}, {}, {}
        self.workers = ThreadPoolExecutor(max_workers=8)
        self.stats = {"seen_buys": 0, "copied": 0, "skipped": 0, "late": 0, "lag_ms": [], "last": ""}
        threading.Thread(target=self.refresh, daemon=True).start()

    def refresh(self):
        while True:
            try:
                self.leaders = {x["wallet"]: x for x in leaders.top(CFG.get("copy_leaders_count", 20))}
            except Exception as e:
                log(f"Лидеры: не удалось обновить список ({str(e)[:60]})")
            time.sleep(60)

    def __call__(self, tx):  # mempool thread: must be fast
        frm = (tx.get("from") or "").lower()
        if frm not in self.leaders:
            return
        buy = int(tx.get("value") or "0x0", 16) > 0 or (tx.get("input") or "")[:10] in ROUTER_BUYS
        gwei = int(tx.get("gasPrice") or "0x0", 16) / 1e9
        self.workers.submit(self.handle, "buy" if buy else "sell", frm, tx_addresses(tx), time.time(), gwei)

    def handle(self, kind, leader, toks, t, gwei=0.0):
        try:
            if kind == "sell":
                for pos in list(self.bot.st["positions"].values()):
                    if pos.get("copy_of") == leader and pos["token"].lower() in toks and pos["status"] == "open" \
                            and not pos.get("force_exit"):
                        pos["force_exit"] = f"лидер {leader[:8]} продаёт"
                        pos["follow_gas_gwei"] = gwei  # sell at the leader's gas price: same block, right behind him
                        log(f"КОПИЯ: лидер {leader[:8]} продаёт {pos['symbol']} — продаю тоже")
                        self.bot.wake.set()
                return
            self.stats["seen_buys"] += 1
            for tok in toks[:3]:
                if self.copy(leader, tok, t):
                    break
        except Exception as e:
            log(f"КОПИЯ: ошибка {str(e)[:80]}")

    def late(self, t, sym):
        lag = time.time() - t
        if lag <= CFG.get("copy_max_lag_s", 3):
            return False
        self.stats["late"] += 1
        self.stats["last"] = f"{sym}: опоздал на {lag:.1f} с"
        log(f"  КОПИЯ → {sym}: пропуск — опоздал на {lag:.1f} с (лидер уже купил)")
        return True

    def prepare(self, pair, token):
        """Called for every new pool with money in it (ManualLaunchWatcher): the slow checks (symbol, pool, simulated
        buy+sell) are done before any leader buys, so at the moment that matters a copy costs one reserves read."""
        try:
            tok = Web3.to_checksum_address(token)
            try:
                sym = w3.eth.contract(tok, abi=SYMBOL_ABI).functions.symbol().call()[:20]
            except Exception:
                sym = token[-4:]
            pool = Pool(pair, tok, sym, 0, 0)
            ok, pool.btax, pool.stax, why = trade_sim(tok, pair, CFG["stake_bnb"])
            now = time.time()
            self.ready[token.lower()] = {"at": now, "pool": pool, "ok": ok, "why": why}
            for k in [k for k, v in list(self.ready.items()) if now - v["at"] > 900]:
                self.ready.pop(k, None)
        except Exception:
            pass

    def copy(self, leader, token, t):
        r = self.ready.get(token)
        if r and time.time() - r["at"] < CFG.get("copy_ready_s", 600):
            pool, ok, why = r["pool"], r["ok"], r["why"]
            self.stats["prepared"] = self.stats.get("prepared", 0) + 1
            try:
                pool.refresh()  # reserves of now, not of the moment the pool was prepared
            except Exception:
                pass
        else:  # a pool the launch watcher did not see: all checks now (slower)
            pair = self.pairs.get(token)
            if pair is None:
                pair = self.pairs[token] = FACTORY.functions.getPair(Web3.to_checksum_address(token), WBNB).call()
            if int(pair, 16) == 0:
                return False  # not a token with a BNB pool
            tok = Web3.to_checksum_address(token)
            pool = Pool(Web3.to_checksum_address(pair), tok, token[-4:], 0, 0)
            ok, pool.btax, pool.stax, why = trade_sim(tok, pool.pair, CFG["stake_bnb"])  # an unsellable token is a sure -100%
        sym = pool.symbol
        if pool.token in self.bot.st["blacklist"] or self.bot.position_for(pool.pair) or pool.pair in self.bot.signals:
            return True
        if not ok or pool.r_bnb < CFG.get("copy_min_bnb", 3) * 10**18:
            self.stats["skipped"] += 1
            self.stats["last"] = f"{sym}: {why if not ok else f'в пуле {pool.r_bnb / 1e18:.1f} BNB'}"
            log(f"  КОПИЯ {leader[:8]} → {sym}: пропуск — {self.stats['last'].split(': ', 1)[1]}")
            return True
        if self.late(t, sym):
            return True
        pool.creator = ""  # the creator lookup took seconds; the copy follows the leader's exit instead
        pool.copy_of = leader
        lag = round((time.time() - t) * 1000)
        self.stats["copied"] += 1
        self.stats["lag_ms"] = (self.stats["lag_ms"] + [lag])[-20:]
        self.stats["last"] = f"{sym} за {leader[:8]}"
        log(f"КОПИЯ: лидер {leader[:8]} покупает {sym} ({pool.r_bnb / 1e18:.1f} BNB в пуле) — повторяю, отставание {lag} мс")
        self.bot.manual_queue.put((pool, t))
        self.bot.wake.set()
        return True

class LogDedupe:
    """Remembers recent (tx hash, log index) pairs so an event delivered by both channels is handled once."""

    def __init__(self, size=50000):
        self.q, self.s = deque(), set()
        self.size = size

    def first_time(self, l):
        key = (str(l.get("transactionHash")), str(l.get("logIndex")))
        if key in self.s:
            return False
        self.s.add(key)
        self.q.append(key)
        if len(self.q) > self.size:
            self.s.discard(self.q.popleft())
        return True


# ---------- bot ----------

class Bot:
    def __init__(self, sender=None):
        self.st = load_state()
        self.sender = sender
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.pools = {}                 # pair -> Pool of a pending buy or an open position
        self.manual_queue = queue.Queue()  # (Pool, time found) from ManualLaunchWatcher
        self.signal_meta = {}              # pair -> exit settings for the pending entry
        self.minted = queue.Queue()     # token addresses that just minted new supply (from Feed)
        self.signals = {}               # pair -> signal time (paper: executed after paper_latency_ms)
        self.last_block = w3.eth.block_number
        self.last_block_at = time.time()
        self.last_stats = time.time()
        self.started_at = time.time()
        self.stats = {"blocks": 0, "swaps": 0, "signals": 0}
        # new PancakeSwap pools with BNB / of them funded with >= manual_min_bnb / skipped by the filters / bought
        self.manual_stats = {"new_pools": 0, "funded": 0, "skipped": 0, "passed": 0, "last_skip": ""}
        self.last_heartbeat, self.heartbeat_seen = time.time(), (0, 0)
        self.dedupe = LogDedupe()
        self.manual_watcher = ManualLaunchWatcher(self)
        for pos in self.st["positions"].values():
            try:
                self.pools[pos["pair"]] = Pool(pos["pair"], pos["token"], pos["symbol"], 0, pos["sell_tax"])
            except Exception as e:
                log(f"не удалось подключить пул открытой позиции {pos['symbol']}: {e}")

    # --- feed callbacks (Feed thread) ---
    def on_block(self, n):
        if n > self.last_block:
            self.stats["blocks"] += 1
        self.last_block = max(self.last_block, n)
        now = time.time()
        self.last_block_at = now
        self.wake.set()

    def on_pair_created(self, l):
        """New PancakeSwap pool (from the WebSocket or the HTTP backup): a possible manual launch."""
        if not self.dedupe.first_time(l):
            return
        r = parse_pair_created(l)
        if r:
            self.manual_stats["new_pools"] += 1
            threading.Thread(target=self.manual_watcher.check, args=r, daemon=True).start()

    def on_log(self, l):
        if not self.dedupe.first_time(l):  # the same event can come from both channels
            return
        pair = Web3.to_checksum_address(l["address"])
        t0 = l["topics"][0] if isinstance(l["topics"][0], str) else "0x" + l["topics"][0].hex().removeprefix("0x")
        if t0 == TRANSFER_TOPIC:  # new tokens minted for a watched token: dilution/rug, get out
            self.minted.put(pair)
            self.wake.set()
            return
        with self.lock:
            pool = self.pools.get(pair)
            if not pool:
                return
            data = bytes.fromhex(l["data"][2:]) if isinstance(l["data"], str) else bytes(l["data"])
            words = [int.from_bytes(data[i:i + 32], "big") for i in range(0, len(data), 32)]
            if t0 == SYNC_TOPIC:
                pool.set_reserves(words[0], words[1])
            elif t0 == SWAP_TOPIC:
                self.stats["swaps"] += 1
        self.wake.set()

    # --- helpers ---
    def wallet_bnb(self):
        return w3.eth.get_balance(self.sender.address) / 1e18 if LIVE else self.st["paper_balance_bnb"]

    def usd(self, bnb):
        p = getattr(self, "bnb_usd", None)
        return f" (${bnb * p:+.2f})" if p else ""

    def roll_day(self):
        """At midnight: send yesterday's result and start the day's loss counter from zero (also while paused)."""
        today = dt.date.today().isoformat()
        if self.st["day"] != today:
            if self.st["day"]:
                self.day_summary(self.st["day"], self.st["day_pnl_bnb"])
            self.st["day"], self.st["day_pnl_bnb"] = today, 0.0
            self.limit_notified = False
            save_state(self.st)

    def loss_limit_hit(self):
        """The daily loss limit can be switched off from the panel (daily_loss_limit_on = 0)."""
        return CFG.get("daily_loss_limit_on", True) and self.st["day_pnl_bnb"] <= -CFG["daily_loss_limit_bnb"]

    def can_open(self):
        self.roll_day()
        if self.loss_limit_hit():
            if not getattr(self, "limit_notified", False):
                self.limit_notified = True
                tg.send(f"⛔ Дневной лимит убытка исчерпан: {self.st['day_pnl_bnb']:+.5f} BNB{self.usd(self.st['day_pnl_bnb'])}. "
                        f"Новых покупок до завтра не будет.")
            return False
        if self.st.get("paused"):
            return False
        return len(self.st["positions"]) + len(self.signals) < CFG["max_open_positions"]

    def position_for(self, pair):
        return next((p for p in self.st["positions"].values() if p["pair"] == pair), None)

    def drain_events(self):
        """Tokens whose creator minted new supply go to the blacklist and their position is sold at once;
        pools that are neither pending nor held are dropped from the watchlist."""
        while not self.minted.empty():
            token = self.minted.get()
            if token in self.st["blacklist"]:
                continue
            self.st["blacklist"].append(token)
            for pair, pool in list(self.pools.items()):
                if pool.token == token:
                    log(f"!!! {pool.symbol}: допечатаны новые токены — в чёрный список"
                        + (", выхожу из позиции" if self.position_for(pair) else ""))
                    if self.position_for(pair):
                        tg.send(f"⚠️ {pool.symbol}: создатель допечатал токены — срочно продаю позицию")
                    self.signals.pop(pair, None)
                    pos = self.position_for(pair)
                    if pos and pos["status"] == "open":
                        pos["force_exit"] = "допечатка токенов"
            save_state(self.st)
        for pair in list(self.pools):
            if not self.position_for(pair) and pair not in self.signals:
                with self.lock:
                    del self.pools[pair]

    # --- entries ---
    @staticmethod
    def exit_settings():
        # "sell_slip": a fresh token's sell tax is usually still unknown, so a sell needs the same wide slippage
        return {"strategy": "manual", "tp": CFG["manual_take_profit_pct"], "sl": CFG["manual_stop_loss_pct"],
                "hold": CFG["manual_max_hold_seconds"], "slippage": CFG["manual_slippage_pct"],
                "sell_slip": CFG["manual_slippage_pct"], "tp1": CFG.get("manual_tp1_pct", 0),
                "tp1_part": CFG.get("manual_tp1_part_pct", 0), "frac": 1.0, "partial_bnb": 0.0}

    def drain_manual(self):
        while not self.manual_queue.empty():
            pool, t_found = self.manual_queue.get()
            if pool.token in self.st["blacklist"] or self.position_for(pool.pair) or pool.pair in self.signals:
                continue
            if not self.can_open():
                log(f"  ручной запуск {pool.symbol}: пропуск — нет свободного места/лимит/пауза")
                continue
            with self.lock:
                self.pools.setdefault(pool.pair, pool)
            self.stats["signals"] += 1
            self.signals[pool.pair] = t_found
            self.signal_meta[pool.pair] = self.exit_settings()
            hold = CFG.get("series_hold", {}).get(getattr(pool, "series", ""))
            if hold:
                self.signal_meta[pool.pair]["hold"] = hold
            tp = CFG.get("series_tp", {}).get(getattr(pool, "series", ""))
            if tp:
                self.signal_meta[pool.pair]["tp"] = tp
            log(f"СИГНАЛ [{'копия ' + pool.copy_of[:8] if getattr(pool, 'copy_of', '') else 'ручной запуск'}] {pool.symbol}")
            if LIVE:
                self.open_live(pool, t_found)

    def sell_probe(self, pool):
        """Real tiny buy (sell_probe_bnb), approve, then a dry-run sell of what we got FROM OUR WALLET.
        Returns (ok, reason). Costs ~2 small txs of gas; the probe tokens are left on the wallet."""
        s = self.sender
        try:
            h = s.buy(pool.token, Web3.to_wei(CFG.get("sell_probe_bnb", 0.00001), "ether"), 0)
            ah = None
            if pool.token not in self.st["approved"]:
                ah = s.approve(pool.token)
                self.st["approved"].append(pool.token)
        except Exception as e:
            return False, f"пробная покупка не отправлена ({str(e)[:60]})"
        rc, arc, t_end = None, None, time.time() + 10
        while time.time() < t_end and (rc is None or (ah and arc is None)):
            time.sleep(0.2)
            rc = rc or s.receipt(h)
            if ah:
                arc = arc or s.receipt(ah)
        if rc is None or rc["status"] != 1:
            return False, "пробная покупка не прошла"
        if ah and (arc is None or arc["status"] != 1):
            return False, "разрешение на продажу не прошло"
        try:
            held = w3.eth.contract(pool.token, abi=LP_ABI).functions.balanceOf(s.address).call()
        except Exception:
            held = 0
        if not held:
            return False, "после пробной покупки токенов на кошельке нет"
        # 27.10 HELL: the network's wallet blacklisted our address ~11 s AFTER our buy (approve() from its Kalu
        # address), so the dry-run sell is repeated after sell_probe_wait seconds before the real stake goes in
        for attempt in range(2):
            if attempt:
                time.sleep(CFG.get("sell_probe_wait", 20))
            try:
                router.functions.swapExactTokensForETHSupportingFeeOnTransferTokens(
                    held, 0, [pool.token, WBNB], s.address, int(time.time()) + 60).call({"from": s.address})
            except Exception as e:
                when = "сразу" if not attempt else f"через {CFG.get('sell_probe_wait', 20)} с"
                return False, f"пробная продажа с кошелька не проходит {when} ({str(e)[:50]})"
        return True, f"пробная продажа проходит и сразу, и через {CFG.get('sell_probe_wait', 20)} с"

    def open_live(self, pool, t_signal):
        stake_wei = Web3.to_wei(CFG["stake_bnb"], "ether")
        if self.wallet_bnb() < CFG["stake_bnb"] + CFG["min_wallet_reserve_bnb"]:
            log("Мало BNB на кошельке — покупку пропускаю")
            del self.signals[pool.pair]
            return
        meta = self.signal_meta.pop(pool.pair, {})
        min_out = int(pool.quote_buy(stake_wei) * (1 - meta.get("slippage", CFG["manual_slippage_pct"]) / 100))
        try:
            h = self.sender.buy(pool.token, stake_wei, min_out)
            approve_h = None
            if pool.token not in self.st["approved"]:  # approve right behind the buy (next nonce), no waiting
                approve_h = self.sender.approve(pool.token)
                self.st["approved"].append(pool.token)
        except Exception as e:
            log(f"  покупка {pool.symbol} не отправлена: {str(e)[:100]}")
            del self.signals[pool.pair]
            return
        sent_ms = (time.time() - t_signal) * 1000
        self.st["positions"][pool.token] = {
            "token": pool.token, "pair": pool.pair, "symbol": pool.symbol, "status": "buying", "buy_tx": h,
            "approve_tx": approve_h, "amount": "0", "stake_bnb": CFG["stake_bnb"], "peak_bnb": 0.0,
            "creator": getattr(pool, "creator", ""), "copy_of": getattr(pool, "copy_of", ""),
            "opened_at": time.time(), "signal_at": t_signal, "sell_tax": pool.stax, "buy_gas": 0.0, "sell_attempts": 0,
            **{k: v for k, v in meta.items() if k != "slippage"}}
        del self.signals[pool.pair]
        self.st["last_trade"][pool.token] = time.time()
        save_state(self.st)
        log(f"ПОКУПКА отправлена {pool.symbol} на {CFG['stake_bnb']} BNB через {sent_ms:.0f} мс после сигнала, tx {h}")

    def execute_paper_signals(self):
        """Paper mode: a real tx lands ~paper_latency_ms after the signal, so fill at the reserves of that moment."""
        now = time.time()
        for pair, t_signal in list(self.signals.items()):
            if now - t_signal < CFG["paper_latency_ms"] / 1000:
                continue
            del self.signals[pair]
            meta = self.signal_meta.pop(pair, {})
            pool = self.pools.get(pair)
            if not pool:
                continue
            stake = CFG["stake_bnb"]
            try:  # a fresh launch moves every block: fill at the on-chain reserves of now
                pool.refresh()
            except Exception:
                pass
            with self.lock:
                amount = pool.quote_buy(Web3.to_wei(stake, "ether"))
                value = pool.quote_sell(amount)
            gas_price = cached_gas_price()
            self.st["positions"][pool.token] = {
                "token": pool.token, "pair": pair, "symbol": pool.symbol, "status": "open", "amount": str(amount),
                "stake_bnb": stake, "peak_bnb": value, "opened_at": now, "signal_at": t_signal, "sell_tax": pool.stax,
                "creator": getattr(pool, "creator", ""), "copy_of": getattr(pool, "copy_of", ""),
                "buy_gas": (GAS_BUY + GAS_APPROVE) * gas_price / 1e18, "buy_ms": round((now - t_signal) * 1000),
                "sell_attempts": 0, **{k: v for k, v in meta.items() if k != "slippage"}}
            self.st["last_trade"][pool.token] = now
            self.st["paper_balance_bnb"] -= stake
            save_state(self.st)
            log(f"ПОКУПКА {pool.symbol} на {stake} BNB через {(now - t_signal)*1000:.0f} мс после сигнала "
                f"(сразу продать дало бы {value:.6f} BNB)")

    # --- exits ---
    def exit_decision(self, pos, value):
        """value = what the whole original position would be worth now (price move only).
        Returns ("part" | "all", reason) or None. No trailing stop: a fresh launch swings ±20% within seconds."""
        pnl_pct = (value - pos["stake_bnb"]) / pos["stake_bnb"] * 100
        if pos.get("force_exit"):
            return "all", f"{pos['force_exit']} {pnl_pct:+.1f}%"
        held = time.time() - pos["opened_at"]
        if pos.get("tp1") and pos.get("tp1_part") and pos.get("frac", 1.0) == 1.0 and pnl_pct >= pos["tp1"] \
                and pnl_pct < pos.get("tp", CFG["manual_take_profit_pct"]):
            return "part", f"[ручной запуск] продаю {pos['tp1_part']:g}% на {pnl_pct:+.1f}%"
        reason = self.exit_reason(pos, pnl_pct, held)
        return ("all", reason) if reason else None

    def exit_reason(self, pos, pnl_pct, held):
        if pnl_pct >= pos.get("tp", CFG["manual_take_profit_pct"]):
            return f"[ручной запуск] тейк-профит {pnl_pct:+.1f}%"
        if pnl_pct <= -pos.get("sl", CFG["manual_stop_loss_pct"]):
            return f"[ручной запуск] стоп-лосс {pnl_pct:+.1f}%"
        if held >= pos.get("hold", CFG["manual_max_hold_seconds"]):
            return f"[ручной запуск] таймаут {held:.0f}с {pnl_pct:+.1f}%"
        return None

    def goplus_watch(self, token, pos):
        """Asks GoPlus every 2 s while a position bought before GoPlus knew the token is open; a red flag sells it."""
        while self.st["positions"].get(token) is pos and pos["status"] in ("buying", "open"):
            ok, _, _, why = security_check(token, pos["pair"])
            if not why.startswith(("GoPlus ещё", "проверка")):
                if ok or why == "код контракта скрыт":  # hidden code is fine: the simulated sell passed
                    log(f"  {pos['symbol']}: GoPlus проверил после покупки — {why}, держу")
                else:
                    log(f"!!! {pos['symbol']}: GoPlus после покупки — {why}, продаю")
                    pos["force_exit"] = f"GoPlus после покупки: {why}"
                    self.wake.set()
                return
            time.sleep(2)

    def manage_positions(self):
        for token, pos in list(self.st["positions"].items()):
            if token.lower() in GOPLUS_PENDING:
                GOPLUS_PENDING.discard(token.lower())
                threading.Thread(target=self.goplus_watch, args=(token, pos), daemon=True).start()
            if pos["status"] == "stuck":  # frozen by an older version: try to sell again (dry runs first)
                pos["status"] = "open"
                pos.setdefault("force_exit", "повтор зависшей продажи")
            pool = self.pools.get(pos["pair"])
            if not pool:
                continue
            if pos["status"] == "open":
                with self.lock:
                    value = pool.quote_sell(int(pos["amount"])) / pos.get("frac", 1.0)
                pos["peak_bnb"] = max(pos["peak_bnb"], value)
                d = self.exit_decision(pos, value)
                if d:
                    kind, reason = d
                    part = pos.get("tp1_part", 0) / 100 if kind == "part" else 1.0
                    pos.update(sell_kind=kind, sell_amount=str(int(pos["amount"]) * round(part * 100) // 100), sell_reason=reason,
                               exit_at=time.time())
                    if LIVE:
                        if pos.get("sell_wait_until", 0) > time.time():
                            continue
                        self.close_live(pool, pos)
                    else:
                        pos["status"] = "selling"
            elif pos["status"] == "selling" and not LIVE:
                # an urgent sell races the pull for the same block: in paper it fills after 0.3 s at whatever the
                # reserves are then (if the pull landed first, the pool is empty and the demo gets ~0)
                latency = 0.3 if pos.get("rug_alarm") else CFG["paper_latency_ms"] / 1000
                if time.time() - pos["exit_at"] >= latency:
                    pos["sell_ms"] = round((time.time() - pos["exit_at"]) * 1000)
                    self.sold(token, pos, self.paper_sell_bnb(pool, int(pos["sell_amount"])),
                              GAS_SELL * cached_gas_price() / 1e18)

    def paper_sell_bnb(self, pool, amount):
        """Paper fill of a sell. A simulated round trip first: if the token cannot really be sold now (honeypot switched
        on, sells blocked) the paper sell gets nothing, and a higher real sell tax is applied — so the demo does not
        count profits that a real sell would not get."""
        ok, _, stx, why = trade_sim(pool.token, pool.pair, CFG["stake_bnb"])
        if not ok and "продать нельзя" in why:
            log(f"  {pool.symbol}: пробная продажа не проходит — в демо считаю 0 BNB")
            return 0.0
        if ok and stx > pool.stax:
            pool.stax = stx
        try:  # the reserves of the latest block, not what the subscription last brought
            pool.refresh()
        except Exception:
            pass
        with self.lock:
            return pool.quote_sell(amount)

    def sold(self, token, pos, got_bnb, gas):
        """A sell went through: a partial one keeps the rest of the position open, a full one closes it."""
        if pos.get("sell_kind") != "part":
            pos["exit_reason"] = pos["sell_reason"] + (" (часть продана раньше)" if pos.get("frac", 1.0) < 1 else "")
            self.finish(token, pos, got_bnb, gas)
            return
        pos["partial_bnb"] = pos.get("partial_bnb", 0.0) + got_bnb - gas
        pos["amount"] = str(int(pos["amount"]) - int(pos["sell_amount"]))
        pos["frac"] = pos.get("frac", 1.0) * (1 - pos["tp1_part"] / 100)
        pos["status"] = "open"
        save_state(self.st)
        log(f"ПРОДАНО {pos['tp1_part']:g}% {pos['symbol']}: {pos['sell_reason']}, получено {got_bnb:.6f} BNB, остаток держу")
        tg.send(f"💰 {pos['symbol']}: продал {pos['tp1_part']:g}% позиции, получил {got_bnb:.6f} BNB{self.usd(got_bnb)}. "
                f"Остаток держу до +{pos.get('tp', CFG['manual_take_profit_pct']):g}% / стопа / таймаута.")

    def close_live(self, pool, pos):
        amount = int(pos["sell_amount"])
        try:  # never sell more than the wallet holds (黄金 26.10: saved amount above the balance -> TRANSFER_FROM_FAILED)
            held = w3.eth.contract(pool.token, abi=LP_ABI).functions.balanceOf(self.sender.address).call()
            if held == 0 and pos.get("status") == "open":
                pos["zero_seen"] = pos.get("zero_seen", 0) + 1
                if pos["zero_seen"] >= 20:  # ~2 s of checks: the tokens are gone (taken back or burnt by the contract)
                    log(f"{pos['symbol']}: токенов на кошельке нет — закрываю позицию с нулём")
                    pos["exit_reason"] = pos.get("sell_reason", "") + " (токенов на кошельке нет)"
                    self.finish(pool.token, pos, 0.0, 0.0)
                    return
            if 0 < held < amount:
                if not pos.get("amount_fixed"):
                    log(f"{pos['symbol']}: на кошельке {held} токенов, а бот считал {amount} — продаю сколько есть")
                    pos["amount_fixed"] = True
                amount = held
        except Exception:
            pass
        # every retry accepts more slippage (5% -> 15% -> 25% ...): getting out matters more than the last percent
        base = pos.get("sell_slip", CFG["manual_slippage_pct"])
        slip = min(50, base + 10 * pos.get("sell_attempts", 0))
        with self.lock:
            min_out = int(pool.quote_sell(amount) * 1e18 * (1 - slip / 100))
        # an urgent sell races the pull: no time for a dry run, but only on the first try. 26.10 21:52 KRON: the urgent sell
        # kept failing and was resent every ~3 s at the outbid gas price, burning ~0.0003 BNB on a 0.000067 stake.
        # Following a leader's sell is just as urgent (26.10: we sold ~3 s behind him, after his sell had moved the price)
        urgent = pos.get("rug_alarm") and not pos.get("sell_attempts")
        follow = pos.get("follow_gas_gwei") and not pos.get("sell_attempts") and not urgent
        if not urgent and not follow:
            try:
                router.functions.swapExactTokensForETHSupportingFeeOnTransferTokens(
                    amount, min_out, [pool.token, WBNB], self.sender.address, int(time.time()) + 60
                ).call({"from": self.sender.address})
            except Exception as e:
                if "revert" in str(e).lower() or type(e).__name__ == "ContractLogicError":
                    # 黄金 26.10: the token refused sells for ~1 min and 5 reverted sells cost more gas than the stake;
                    # a dry run is free, so retry it every 0.1 s and pay gas only once it goes through
                    if time.time() - pos.get("diag_at", 0) > 600:  # why: our wallet blacklisted / nobody can sell
                        pos["diag_at"] = time.time()
                        threading.Thread(target=sell_diagnose, args=(pool, pos, self.sender.address, amount),
                                         daemon=True).start()
                    if time.time() - pos.get("sell_wait_logged", 0) > 30:
                        log(f"{pos['symbol']}: пробная продажа не проходит ({str(e)[:60]}), газ не трачу, повторяю каждые 0.1 с")
                        pos["sell_wait_logged"] = time.time()
                    pos["status"] = "open"
                    pos["sell_wait_until"] = time.time() + 0.1
                    return
        gas_price = None
        if follow:  # the leader's gas price, never above it: we land in his block, behind his sell, without outbidding him
            gas_price = max(self.sender.gas_price,
                            Web3.to_wei(min(pos["follow_gas_gwei"], CFG.get("rug_max_gas_gwei", 1.0)), "gwei"))
        if urgent:  # outbid the pull so the sell is ordered before it in the same block
            want = max(pos["rug_alarm"]["gas_gwei"] * 1.5, pos["rug_alarm"]["gas_gwei"] + 0.05)
            gas_price = Web3.to_wei(min(want, CFG.get("rug_max_gas_gwei", 1.0)), "gwei")
            gas_price = max(gas_price, self.sender.gas_price)
            # the node wants gas limit x price up front: on a tiny wallet outbid only as far as the balance allows
            try:
                affordable = int(w3.eth.get_balance(self.sender.address) * 0.9 / CFG["gas_limit_sell"])
                gas_price = max(self.sender.gas_price, min(gas_price, affordable))
            except Exception:
                pass
        try:
            pos["sell_tx"] = self.sender.sell(pool.token, amount, min_out, gas_price=gas_price)
            pos["status"] = "selling"
            log(f"ПРОДАЖА отправлена {pos['symbol']} через {(time.time()-pos['exit_at'])*1000:.0f} мс: {pos['sell_reason']}"
                + (f" (попытка {pos['sell_attempts'] + 1}, проскальзывание до {slip:.0f}%)" if pos.get("sell_attempts") else ""))
        except Exception as e:
            log(f"{pos['symbol']}: продажа не отправлена ({str(e)[:100]}), повторю")
        save_state(self.st)

    def finish(self, token, pos, exit_bnb, sell_gas):
        if pos.get("copy_of") and not pos["exit_reason"].startswith("[копия"):
            pos["exit_reason"] = f"[копия {pos['copy_of'][:8]}] {pos['exit_reason']}"
        net = exit_bnb + pos.get("partial_bnb", 0.0) - sell_gas - pos.get("buy_gas", 0)
        pnl = record_trade(pos, net, pos["exit_reason"])
        self.st["day_pnl_bnb"] += pnl
        if not LIVE:
            self.st["paper_balance_bnb"] += net
        del self.st["positions"][token]
        save_state(self.st)
        log(f"ПРОДАЖА {pos['symbol']} через {time.time() - pos['opened_at']:.0f}с: {pos['exit_reason']}, "
            f"итог с газом {pnl:+.6f} BNB ({pnl/pos['stake_bnb']*100:+.1f}%)")
        try:
            bal = self.wallet_bnb()
        except Exception:
            bal = None
        tg.send(f"{'✅' if pnl > 0 else '🔴'} {pos['symbol']}: {pnl/pos['stake_bnb']*100:+.1f}% = {pnl:+.6f} BNB{self.usd(pnl)}\n"
                f"Держал {time.time() - pos['opened_at']:.0f} с, причина: {pos['exit_reason']}\n"
                f"За сегодня: {self.st['day_pnl_bnb']:+.6f} BNB{self.usd(self.st['day_pnl_bnb'])}"
                + (f"\nБаланс: {bal:.6f} BNB{self.usd(bal).replace('+', '')}" if bal is not None else ""))

    def day_summary(self, day, pnl):
        """Day result for Telegram, from the trades journal."""
        n = wins = 0
        if os.path.exists(TRADES_FILE):
            for r in csv.DictReader(open(TRADES_FILE, encoding="utf-8-sig"), delimiter=";"):
                if r["mode"] == CFG["mode"] and r["closed"].startswith(day):
                    n += 1
                    wins += float(r["pnl_bnb"]) > 0
        tg.send(f"📊 Итог дня {day}: сделок {n}, в плюсе {wins}, результат {pnl:+.6f} BNB{self.usd(pnl)}")

    def check_receipts(self):
        """Live: follow sent transactions without blocking the loop."""
        for token, pos in list(self.st["positions"].items()):
            if pos["status"] == "buying":
                rc = self.sender.receipt(pos["buy_tx"])
                if rc is None:
                    if time.time() - pos["opened_at"] > 60:
                        log(f"{pos['symbol']}: покупка не попала в блок за 60с, снимаю")
                        self.sender.resync(); del self.st["positions"][token]; save_state(self.st)
                    continue
                if rc["status"] != 1:
                    log(f"{pos['symbol']}: покупка отклонена сетью (вероятно, цена ушла дальше проскальзывания)")
                    tg.send(f"⚪ {pos['symbol']}: покупка не прошла (цена ушла дальше допуска), потерян только газ")
                    self.st["day_pnl_bnb"] -= gas_cost(rc)
                    self.sender.resync(); del self.st["positions"][token]; save_state(self.st)
                    continue
                got = tokens_received(rc, token, self.sender.address)
                pool = self.pools.get(pos["pair"])
                pos.update(status="open", amount=str(got), buy_gas=gas_cost(rc) + GAS_APPROVE * self.sender.gas_price / 1e18,
                           buy_ms=round((time.time() - pos["signal_at"]) * 1000), opened_at=time.time())
                pos["peak_bnb"] = pool.quote_sell(got) if pool else 0.0
                save_state(self.st)
                log(f"КУПЛЕНО {pos['symbol']}: от сигнала до блока {pos['buy_ms']} мс, сейчас стоит {pos['peak_bnb']:.6f} BNB")
                tg.send(f"🟢 Купил {pos['symbol']} на {pos['stake_bnb']} BNB{self.usd(pos['stake_bnb']).replace('+', '')} (ручной запуск)")
            elif pos["status"] == "selling":
                rc = self.sender.receipt(pos["sell_tx"])
                if rc is None:
                    if time.time() - pos["exit_at"] > 60:
                        log(f"{pos['symbol']}: продажа не попала в блок за 60с, повторю")
                        self.sender.resync(); pos["status"] = "open"
                    continue
                if rc["status"] != 1:
                    pos["sell_attempts"] += 1
                    pos["buy_gas"] += gas_cost(rc)
                    pool = self.pools.get(pos["pair"])
                    try:
                        pool.refresh()
                        worth = pool.quote_sell(int(pos["amount"]))
                    except Exception:
                        worth = None
                    if worth is not None and worth < pos["stake_bnb"] * 0.02:  # the pool was emptied: nothing to sell into
                        log(f"!!! {pos['symbol']}: пул опустошён, токены ничего не стоят — закрываю как убыток, газ больше не трачу")
                        pos["sell_kind"] = "all"
                        pos["sell_reason"] = pos.get("sell_reason", "") + " (пул опустошён)"
                        self.sold(token, pos, 0.0, 0.0)
                        continue
                    if pos["sell_attempts"] >= CFG["max_sell_attempts"]:
                        # used to freeze the position for good (黄金 26.10: sells worked again a minute later);
                        # now only dry runs continue until one passes, so no more gas is burned on reverts
                        pos["status"] = "open"
                        pos["sell_wait_until"] = time.time() + 0.1
                        self.sender.resync()
                        if pos["sell_attempts"] == CFG["max_sell_attempts"]:
                            log(f"!!! {pos['symbol']}: продать не удалось {pos['sell_attempts']} раза — дальше пробую без газа, "
                                f"пока пробная продажа не пройдёт (токен {token})")
                            tg.send(f"⚠️ {pos['symbol']}: продажа отклонена {pos['sell_attempts']} раз — продолжаю пробовать. "
                                    f"https://dexscreener.com/bsc/{token}")
                    else:
                        log(f"{pos['symbol']}: продажа отклонена сетью, попытка {pos['sell_attempts']}, повторю")
                        self.sender.resync(); pos["status"] = "open"
                        if pos["sell_attempts"] == 1:  # maybe the approve never landed
                            try:
                                self.sender.approve(token)
                            except Exception:
                                pass
                    save_state(self.st)
                    continue
                pos["sell_ms"] = round((time.time() - pos["exit_at"]) * 1000)
                self.sold(token, pos, bnb_received(rc), gas_cost(rc))

    def print_stats(self):
        if not os.path.exists(TRADES_FILE):
            log("СТАТИСТИКА: сделок пока нет")
            return
        rows = [r for r in csv.DictReader(open(TRADES_FILE, encoding="utf-8-sig"), delimiter=";") if r["mode"] == CFG["mode"]]
        if rows:
            pnl = sum(float(r["pnl_bnb"]) for r in rows)
            wins = sum(float(r["pnl_bnb"]) > 0 for r in rows)
            log(f"СТАТИСТИКА ({CFG['mode']}): сделок {len(rows)}, в плюс {wins} ({wins/len(rows)*100:.0f}%), "
                f"итог {pnl:+.6f} BNB")

    def auto_money(self, force=False):
        """Money settings from one number: the budget in dollars (0 = the whole wallet). Recomputed every minute,
        so a top-up raises the stakes by itself. Rules: stake 10% of the budget (min `min_stake_bnb`; gas is ~0.000025 BNB a trade,
        max 0.05), as many simultaneous positions as fit (1-3), daily loss limit 25% of the budget (>= one stake)."""
        if not CFG.get("auto_money"):
            self.auto = None
            return
        now = time.time()
        if not force and now - getattr(self, "auto_at", 0) < 60:
            return
        self.auto_at = now
        try:
            if now - getattr(self, "bnb_usd_at", 0) > 300:
                self.bnb_usd = float(http.get("https://api.binance.com/api/v3/ticker/price",
                                              params={"symbol": "BNBUSDT"}, timeout=10).json()["price"])
                self.bnb_usd_at = now
            wallet = self.wallet_bnb()
        except Exception as e:
            log(f"Авторасчёт денег: нет данных ({str(e)[:60]}), оставляю прежние значения")
            return
        reserve = CFG["min_wallet_reserve_bnb"]
        budget = wallet - reserve
        if CFG.get("budget_usd", 0) > 0:
            budget = min(budget, CFG["budget_usd"] / self.bnb_usd)
        budget = max(0.0, budget)
        stake = min(0.05, max(CFG.get("min_stake_bnb", 0.002), budget * 0.10))
        positions = max(1, min(3, int(budget // stake))) if budget >= stake else 1
        limit = max(stake, budget * CFG.get("daily_loss_pct", 25) / 100)
        new = {"stake_bnb": round(stake, 6), "max_open_positions": positions, "daily_loss_limit_bnb": round(limit, 6)}
        changed = any(CFG.get(k) != v for k, v in new.items())
        CFG.update(new)
        self.auto = {**new, "budget_bnb": round(budget, 6), "bnb_usd": self.bnb_usd, "wallet_bnb": round(wallet, 6),
                     "enough": budget >= stake}
        if changed or force:
            log(f"Авторасчёт денег: бюджет {budget:.4f} BNB (${budget * self.bnb_usd:.2f}) → ставка {stake:.4f} BNB "
                f"(${stake * self.bnb_usd:.2f}), позиций {positions}, лимит убытка в день {limit:.4f} BNB "
                f"(${limit * self.bnb_usd:.2f})" + ("" if budget >= stake else " — БЮДЖЕТА НЕ ХВАТАЕТ НА ОДНУ СТАВКУ"))

    def heartbeat(self):
        """One line a minute in the console so it is obvious the bot is alive and what it is doing."""
        ms = self.manual_stats
        new, funded = ms["new_pools"] - self.heartbeat_seen[0], ms["funded"] - self.heartbeat_seen[1]
        self.last_heartbeat, self.heartbeat_seen = time.time(), (ms["new_pools"], ms["funded"])
        log(f"Работаю: блок {self.last_block}, за минуту новых пулов с BNB {new}, из них ручных запусков {funded}; "
            f"всего пропущено фильтрами {ms['skipped']}, прошло фильтры {ms['passed']} — {self.activity()}")

    def activity(self):
        if self.st.get("paused"):
            return "на паузе"
        if self.loss_limit_hit():
            return "дневной лимит убытка исчерпан, жду завтра"
        if self.st["positions"]:
            return "веду позиции: " + ", ".join(p["symbol"] for p in self.st["positions"].values())
        return f"жду новый пул с ≥ {CFG['manual_min_bnb']:g} BNB, который пройдёт фильтры"

    def run(self):
        import web
        feed = self.feed = Feed(self)
        feed.start()
        HttpPoller(self, feed).start()
        ReserveRefresher(self).start()
        if CFG.get("rug_guard", True):
            self.mempool = Mempool()
            self.mempool.watchers.append(RugGuard(self))
            if CFG.get("copy_leaders_on", False):
                self.copier = LeaderCopier(self)
                self.mempool.watchers.append(self.copier)
            self.mempool.start()
        start_known_names()
        log(f"Стратегия: ручной запуск — новый пул с ≥ {CFG['manual_min_bnb']:g} BNB, только после всех фильтров")
        web.start(self, sys.modules[__name__])
        self.auto_money(force=True)
        tg.start()
        bal = self.wallet_bnb()
        tg.send(f"🤖 Бот {'LIVE' if LIVE else 'PAPER'} запущен. {'⏸ На паузе' if self.st.get('paused') else '▶ Торгует'}.\n"
                f"Баланс: {bal:.6f} BNB{self.usd(bal).replace('+', '')}, ставка {CFG['stake_bnb']} BNB, "
                f"лимит убытка в день {CFG['daily_loss_limit_bnb']} BNB")
        while True:
            self.wake.wait(timeout=0.25)
            self.wake.clear()
            try:
                self.roll_day()
                self.auto_money()
                self.drain_events()
                if LIVE:
                    self.check_receipts()
                    self.sender.refresh_gas_price()
                else:
                    self.execute_paper_signals()
                self.manage_positions()
                self.drain_manual()
                if time.time() - self.last_stats >= 300:
                    self.last_stats = time.time(); self.print_stats()
                if time.time() - self.last_heartbeat >= 60:
                    self.heartbeat()
            except KeyboardInterrupt:
                raise
            except Exception:
                log("Ошибка цикла:\n" + traceback.format_exc(limit=3))
                time.sleep(1)


def selftest(sender):
    """Live check of the whole path on CAKE: buy for a tiny amount, approve, sell back. Costs ~1% of 0.0005 BNB + gas."""
    pair = v2_pair(CAKE)
    pool = Pool(pair, CAKE, "CAKE", 0, 0)
    stake = Web3.to_wei(0.0005, "ether")
    bal0 = w3.eth.get_balance(sender.address) / 1e18
    log(f"Самопроверка: кошелёк {sender.address}, баланс {bal0:.6f} BNB, газ {sender.gas_price/1e9:.3f} gwei")
    t = time.time()
    h = sender.buy(CAKE, stake, int(pool.quote_buy(stake) * 0.97))
    log(f"  покупка отправлена за {(time.time()-t)*1000:.0f} мс: {h}")
    ha = sender.approve(CAKE)
    while (rc := sender.receipt(h)) is None:
        time.sleep(0.05)
    got = tokens_received(rc, CAKE, sender.address)
    log(f"  покупка в блоке через {(time.time()-t)*1000:.0f} мс, статус {rc['status']}, получено {got/1e18:.6f} CAKE")
    while sender.receipt(ha) is None:
        time.sleep(0.05)
    pool.refresh()
    t = time.time()
    hs = sender.sell(CAKE, got, int(pool.quote_sell(got) * 1e18 * 0.97))
    while (rs := sender.receipt(hs)) is None:
        time.sleep(0.05)
    log(f"  продажа в блоке через {(time.time()-t)*1000:.0f} мс, статус {rs['status']}, получено {bnb_received(rs):.6f} BNB")
    bal1 = w3.eth.get_balance(sender.address) / 1e18
    log(f"Готово. Баланс {bal1:.6f} BNB, проверка стоила {bal0 - bal1:.6f} BNB (≈ ${(bal0 - bal1) * 744:.3f})")


_instance_socket = None


def single_instance(name, port):
    """Holds a localhost port for the process lifetime: a second copy (autostart + manual start) exits at once.
    Two live bots on one wallet would fight over nonces and could double-buy."""
    global _instance_socket
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        s.listen(1)
    except OSError:
        print(f"{name} уже запущен в другом окне — эта копия закрывается.", flush=True)
        time.sleep(5)
        sys.exit(0)
    _instance_socket = s


def flush_console(timeout=2.0):
    end = time.time() + timeout
    while not _console.empty() and time.time() < end:
        time.sleep(0.05)
    time.sleep(0.05)


def main():
    disable_quick_edit()
    if "selftest" not in sys.argv:
        single_instance("Бот LIVE" if LIVE else "Бот PAPER", 47650 if LIVE else 47651)
    sender = None
    if LIVE or "selftest" in sys.argv:
        key = load_private_key()
        if not key:
            sys.exit("Нужен ключ ОТДЕЛЬНОГО кошелька в файле .env (BSC_PRIVATE_KEY=...)")
        sender = Sender(key)
    if "selftest" in sys.argv:
        selftest(sender)
        return
    if LIVE:
        log(f"РЕЖИМ LIVE — торговля настоящими BNB. Кошелёк {sender.address}, "
            f"баланс {w3.eth.get_balance(sender.address)/1e18:.6f} BNB")
    else:
        log("РЕЖИМ PAPER — торговля понарошку по реальным резервам пулов PancakeSwap")
    Bot(sender).run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Остановлен. Открытые позиции сохранены и продолжат вестись при следующем запуске.")
    finally:
        flush_console()
