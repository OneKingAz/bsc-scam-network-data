"""Signal journal for the "manual launch" strategy: NO money and NO private key.

Every new PancakeSwap pool funded by hand with >= manual_min_bnb is run through the bot's own filters
(bot.manual_filter) and gets a virtual 0.002 BNB position, filled after the same delay a real tx has. The journal
records what it would be worth after 10 s, 30 s, 1, 2 and 5 minutes net of PancakeSwap fee, token tax and gas, and the
result of the bot's exit rules (take-profit / stop-loss / timeout). "safe" = 1 means the launch passed every filter,
i.e. the bot would have bought it; the rest show what the filters saved us from.

  manual       - a new name
  manual_bait  - a copy of a name that already trades (always skipped by the bot)

When the BNB of a followed pool suddenly drops by 80%+ (a liquidity pull or a dump), rug_mempool.csv gets a row:
was that transaction seen in the public mempool before its block, how many ms earlier, who sent it, at what gas
price, and what the virtual position was worth just before. This tells whether the bot can sell ahead of a pull.

Run:   python journal.py          report: http://127.0.0.1:8770      rows: journal_signals.csv
"""
import csv, datetime as dt, json, os, threading, time, traceback

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
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from web3 import Web3

import bot as B

OUT = os.path.join(B.HERE, "journal_signals.csv")
PORT = 8770
STAKE = 0.002
HORIZONS = [10, 30, 60, 120, 300]
TRACK_S = 300
COOLDOWN_S = 300  # one signal per strategy per token per 5 minutes
# 27.10: a pull after the 5-minute position (KIMCHI emptied at 16 min) was never seen, so a series looked "never pulled".
# After the position ends the pool's BNB is still read every LATE_EVERY_S s, up to LATE_WATCH_S after the signal.
LATE_WATCH_S, LATE_EVERY_S = 3600, 15
STRATS = {"manual": "Ручной запуск", "manual_bait": "Ручной запуск-приманка"}  # older rows of removed strategies stay in the CSV
HEADER = (["time", "strategy", "symbol", "token", "pair", "safe", "features"] + [f"r{h}s_pct" for h in HORIZONS]
          + ["max_pct", "min_pct", "bot_rules_pct", "path"])
RUGS = os.path.join(B.HERE, "rug_mempool.csv")
SWAPS = os.path.join(B.HERE, "journal_swaps.csv")  # every buy/sell in a followed pool: who trades these launches
SWAP_HEADER = ["signal_time", "symbol", "token", "pair", "creator", "block", "t_rel_s", "wallet", "wallet_source", "side",
               "bnb", "tx", "outcome_max_pct", "outcome_min_pct", "outcome_bot_pct"]
RUG_HEADER = ["time", "symbol", "token", "pair", "tx", "bnb_before", "bnb_after", "seen_in_mempool", "lead_ms", "from",
              "to", "selector", "gas_gwei", "position_pct", "held_s", "source", "first_warning_ms", "first_warning"]


def upgrade_csv():
    """Older files had no 'path' column: rewrite the header once so new rows line up."""
    if not os.path.exists(OUT):
        return
    with open(OUT, encoding="utf-8-sig") as f:
        rows = list(csv.reader(f, delimiter=";"))
    if rows and "path" not in rows[0]:
        with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(HEADER)
            w.writerows(r + [""] for r in rows[1:])


log = B.log


class Journal:
    def __init__(self):
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.pools = {}                    # pair -> B.Pool of an open virtual position (read by B.Feed)
        self.trades = []                   # open virtual positions
        self.last_signal = {}              # (strategy, token) -> time
        self.last_block = B.w3.eth.block_number
        self.last_block_at = time.time()
        self.gas_price = B.cached_gas_price()
        self.counts = defaultdict(int)
        self.started = time.time()
        self.dedupe = B.LogDedupe()
        # "is it alive" data for the report page: times are time.time() of the last event of each kind
        self.health = {"loop": time.time(), "events": 0, "event_at": 0.0, "new_pools": 0, "pool_at": 0.0,
                       "signal_at": 0.0, "writes": 0, "write_at": 0.0, "errors": 0, "error_at": 0.0, "error": ""}
        self.feed = None
        self.mempool = B.Mempool()
        self.rugs = []                     # recent rows of rug_mempool.csv for the report page
        self.rugged = set()                # pairs already recorded as pulled
        self.swaps = {}                    # pair -> {(tx, log index): swap dict} while a virtual position follows it
        self.swaps_lock = threading.Lock()  # write_swaps threads append to one file
        self.warnings = {}                 # pair -> (time, why) of the first threat the bot's RugGuard would act on
        self.late = {}                     # pair -> [pool, t_entry, max BNB, until]: pulls after the 5-minute position
        upgrade_csv()

    # ---- feed callbacks (B.Feed thread) ----
    def on_block(self, n):
        self.last_block = max(self.last_block, n)
        now = time.time()
        self.last_block_at = now
        self.wake.set()

    def on_pair_created(self, l):
        """New PancakeSwap pool from the Feed socket or the HTTP backup: a possible manual launch."""
        if not self.dedupe.first_time(l):
            return
        self.health["new_pools"] += 1
        self.health["pool_at"] = time.time()
        r = B.parse_pair_created(l)
        if r:
            bn = l.get("blockNumber")
            bn = int(bn, 16) if isinstance(bn, str) else bn
            threading.Thread(target=self.check_manual, args=r + (bn,), daemon=True).start()

    def on_log(self, l):
        if not self.dedupe.first_time(l):  # the same event can arrive from both channels
            return
        self.health["events"] += 1
        self.health["event_at"] = time.time()
        t0 = l["topics"][0] if isinstance(l["topics"][0], str) else "0x" + l["topics"][0].hex().removeprefix("0x")
        if t0 not in (B.SWAP_TOPIC, B.SYNC_TOPIC):
            return
        pair = Web3.to_checksum_address(l["address"])
        with self.lock:
            pool = self.pools.get(pair)
            if not pool:
                return
            data = bytes.fromhex(l["data"][2:]) if isinstance(l["data"], str) else bytes(l["data"])
            words = [int.from_bytes(data[i:i + 32], "big") for i in range(0, len(data), 32)]
            if t0 == B.SYNC_TOPIC:
                before = pool.r_bnb
                pool.set_reserves(words[0], words[1])
                if before > 10**18 and pool.r_bnb < before * 0.2:
                    self.rug_seen(pool, str(l.get("transactionHash") or ""), before)
            else:
                self.record_swap(pool, l, words)
        self.wake.set()

    def record_swap(self, pool, l, words, t=None):
        """Swap(sender, a0in, a1in, a0out, a1out, to): who bought or sold how much BNB. The wallet is the tx sender
        from the mempool when we saw it (the real person), else the Swap recipient (`to`, often the same wallet)."""
        a0in, a1in, a0out, a1out = words[:4]
        bnb_in, bnb_out = (a1in, a1out) if pool.token_is0 else (a0in, a0out)
        h = str(l.get("transactionHash") or "")
        h = h if h.startswith("0x") else "0x" + h
        seen = self.mempool.lookup(h)
        wallet, source = (seen[1], "mempool") if seen else (B.addr_from_topic(l["topics"][2]).lower(), "swap_to")
        blk = l.get("blockNumber")
        blk = int(blk, 16) if isinstance(blk, str) else int(blk or 0)
        key = (h, str(l.get("logIndex")))
        self.swaps.setdefault(pool.pair, {})[key] = {
            "block": blk, "t": t if t is not None else time.time(), "wallet": wallet, "source": source,
            "side": "buy" if bnb_in > 0 else "sell", "bnb": round((bnb_in or bnb_out) / 1e18, 6), "tx": h}

    def backfill_swaps(self, pool):
        """Swaps before our signal (the first buyers) come from the logs of the last ~60 blocks."""
        try:
            head = B.w3.eth.block_number
            res = B.w3.provider.make_request("eth_getLogs", [{"fromBlock": hex(head - 60), "toBlock": hex(head),
                                                              "address": [pool.pair], "topics": [B.SWAP_TOPIC]}])
            for l in res.get("result") or []:
                data = bytes.fromhex(l["data"][2:])
                words = [int.from_bytes(data[i:i + 32], "big") for i in range(0, len(data), 32)]
                with self.lock:
                    if (l["transactionHash"], str(l.get("logIndex"))) not in self.swaps.get(pool.pair, {}):
                        self.record_swap(pool, l, words, t=0.0)  # time unknown: block number tells the order
        except Exception as e:
            log(f"Журнал: история сделок {pool.symbol} недоступна ({str(e)[:60]})")

    def watch_threats(self, tx):
        """The bot's RugGuard rules on the pools we follow: when would the bot have sold?"""
        for tr in list(self.trades):
            p = tr["pool"]
            if p.pair in self.warnings or not tr["entered"]:
                continue
            why = B.rug_signal(tx, p.token.lower(), p.pair.lower(), getattr(p, "creator", ""))
            if why:
                self.warnings[p.pair] = (time.time(), why)

    def on_reserves(self, pool, before):
        """ReserveRefresher: a pull whose Sync event the subscription missed is still recorded (tx found later)."""
        if before > 10**18 and pool.r_bnb < before * 0.2:
            self.rug_seen(pool, "", before)

    def rug_seen(self, pool, tx, before):
        if pool.pair in self.rugged:
            return
        self.rugged.add(pool.pair)
        threading.Thread(target=self.record_rug, args=(pool, tx, before, pool.r_bnb, time.time()), daemon=True).start()

    def find_drop_tx(self, pool, back=20):
        """The transaction that emptied the pool, from the Sync events of the last blocks."""
        head = B.w3.eth.block_number
        resp = B.w3.provider.make_request("eth_getLogs", [{"fromBlock": hex(head - back), "toBlock": hex(head),
                                                           "address": [pool.pair], "topics": [B.SYNC_TOPIC]}])
        prev = None
        for l in resp.get("result") or []:
            d = l["data"][2:]
            a, b = int(d[:64], 16), int(d[64:128], 16)
            bnb = b if pool.token_is0 else a
            if prev is not None and bnb < prev * 0.2:
                return l["transactionHash"]
            prev = bnb
        return ""

    def watch_late(self):
        """After the 5-minute position: one reserves read per pool every LATE_EVERY_S s for up to an hour. A pool that
        lost 80%+ of its best BNB is written to rug_mempool.csv with source "late" (held_s = time since the entry)."""
        while True:
            time.sleep(LATE_EVERY_S)
            now = time.time()
            for pair, w in list(self.late.items()):
                pool, t_entry, best, until = w
                if now > until or pair in self.rugged:
                    self.late.pop(pair, None)
                    continue
                try:
                    r0, r1, _ = B.w3.eth.contract(pair, abi=B.PAIR_ABI).functions.getReserves().call()
                except Exception:
                    continue
                bnb = r1 if pool.token_is0 else r0
                w[2] = max(best, bnb)
                if w[2] > 10**18 and bnb < w[2] * 0.2:
                    self.rugged.add(pair)
                    self.late.pop(pair, None)
                    pool.set_reserves(r0, r1)
                    try:
                        h = self.find_drop_tx(pool, back=int(LATE_EVERY_S / 0.45) + 20)
                    except Exception:
                        h = ""
                    threading.Thread(target=self.record_rug, args=(pool, h, w[2], bnb, now, "late", now - t_entry),
                                     daemon=True).start()

    def record_rug(self, pool, h, before, after, t_log, source=None, held=None):
        """The pool lost 80%+ of its BNB in one transaction: was it visible in the mempool before the block?
        source "ws" = noticed from the Sync event right after the block (lead_ms is honest); "refresh" = noticed up to
        2 s later by ReserveRefresher, so its lead_ms is too big."""
        source = source or ("ws" if h else "refresh")
        if not h:
            try:
                h = self.find_drop_tx(pool)
            except Exception:
                h = ""
        h = h if not h or h.startswith("0x") else "0x" + h
        seen = self.mempool.lookup(h)
        tr = next((t for t in self.trades if t["pool"].pair == pool.pair), None)
        last = tr["path"][-1] if tr and tr.get("path") else (round(held, 1) if held is not None else None, None)
        row = [dt.datetime.fromtimestamp(t_log).strftime("%Y-%m-%d %H:%M:%S"), pool.symbol, pool.token, pool.pair, h,
               round(before / 1e18, 3), round(after / 1e18, 3), int(bool(seen)),
               round((t_log - seen[0]) * 1000) if seen else "", seen[1] if seen else "", seen[2] if seen else "",
               seen[3] if seen else "", seen[4] if seen else "", last[1], last[0], source,
               round((t_log - self.warnings[pool.pair][0]) * 1000) if pool.pair in self.warnings else "",
               self.warnings.get(pool.pair, ("", ""))[1]]
        new = not os.path.exists(RUGS)
        with open(RUGS, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            if new:
                w.writerow(RUG_HEADER)
            w.writerow(row)
        self.rugs = (self.rugs + [dict(zip(RUG_HEADER, row))])[-30:]
        log(f"ИЗЪЯТИЕ {pool.symbol}: {before / 1e18:.2f} → {after / 1e18:.2f} BNB, "
            + (f"в мемпуле видно за {row[8]} мс до блока (газ {seen[4]:.3f} gwei, функция {seen[3]})" if seen
               else "в публичном мемпуле этой транзакции не было"))

    # ---- signals ----
    def signal(self, strategy, pool, features):
        now = time.time()
        key = (strategy, pool.token)
        if now - self.last_signal.get(key, 0) < COOLDOWN_S:
            return
        self.last_signal[key] = now
        self.counts[strategy] += 1
        self.health["signal_at"] = now
        # the trade first: drain() keeps a pool only while a trade follows it
        self.trades.append({"strategy": strategy, "pool": pool, "t": now, "entered": False, "features": features,
                            "values": {}, "max": None, "min": None, "bot": None})
        with self.lock:
            self.pools.setdefault(pool.pair, pool)
        threading.Thread(target=self.backfill_swaps, args=(pool,), daemon=True).start()
        log(f"СИГНАЛ [{STRATS[strategy]}] {pool.symbol} "
            + ", ".join(f"{k}={v}" for k, v in features.items() if k not in ("liq_usd",)))

    # ---- virtual positions ----
    def gas_bnb(self, units):
        return units * self.gas_price / 1e18

    def track(self):
        now = time.time()
        stake_wei = Web3.to_wei(STAKE, "ether")
        c = B.CFG
        for tr in list(self.trades):
            pool = tr["pool"]
            age = now - tr["t"]
            with self.lock:
                if not tr["entered"]:
                    if age < c["paper_latency_ms"] / 1000:
                        continue
                    tr["tokens"] = pool.quote_buy(stake_wei)
                    tr["entered"] = True
                    tr["t_entry"] = now
                    tr["cost"] = self.gas_bnb(B.GAS_BUY + B.GAS_APPROVE + B.GAS_SELL)
                value = pool.quote_sell(tr["tokens"]) - tr["cost"]
            pct = (value / STAKE - 1) * 100
            tr["max"] = pct if tr["max"] is None else max(tr["max"], pct)
            tr["min"] = pct if tr["min"] is None else min(tr["min"], pct)
            held = now - tr["t_entry"]
            path = tr.setdefault("path", [])
            if not path or held - path[-1][0] >= 1 or abs(pct - path[-1][1]) >= 5:  # ~1 point/s, plus every sharp move
                path.append((round(held, 1), round(pct, 1)))
            for h in HORIZONS:
                if h not in tr["values"] and held >= h:
                    tr["values"][h] = round(pct, 2)
            if tr["bot"] is None:  # the bot's exit rules on this path
                gross = pct + tr["cost"] / STAKE * 100  # bot decides on the quote, gas is paid anyway
                if gross >= c["manual_take_profit_pct"] or gross <= -c["manual_stop_loss_pct"] \
                        or held >= c["manual_max_hold_seconds"]:
                    tr["bot"] = round(pct, 2)
            if held >= TRACK_S:
                if tr["bot"] is None:
                    tr["bot"] = round(pct, 2)
                self.finish(tr)
                self.trades.remove(tr)

    def finish(self, tr):
        new = not os.path.exists(OUT)
        with open(OUT, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            if new:
                w.writerow(HEADER)
            p = tr["pool"]
            w.writerow([dt.datetime.fromtimestamp(tr["t"]).strftime("%Y-%m-%d %H:%M:%S"), tr["strategy"], p.symbol, p.token,
                        p.pair, int(getattr(p, "safe", True)), json.dumps(tr["features"], ensure_ascii=False)]
                       + [tr["values"].get(h, "") for h in HORIZONS]
                       + [round(tr["max"], 2), round(tr["min"], 2), tr["bot"], json.dumps(tr.get("path", []))])
        self.health["writes"] += 1
        self.health["write_at"] = time.time()
        if p.pair not in self.rugged:
            self.late[p.pair] = [p, tr.get("t_entry", tr["t"]), p.r_bnb, tr["t"] + LATE_WATCH_S]
        swaps = sorted(self.swaps.pop(p.pair, {}).values(), key=lambda x: (x["block"], x["t"]))
        if swaps:
            threading.Thread(target=self.write_swaps, args=(tr, p, swaps), daemon=True).start()

    def write_swaps(self, tr, p, swaps):
        """Swaps not seen in the mempool carry the Swap recipient: on a sell that is the router, on a bot buy its
        contract. The real trader is the transaction sender: asked from the node, at most ~10 requests a second."""
        senders = {}
        for x in swaps:
            if x["source"] == "mempool":
                continue
            if x["tx"] not in senders:
                try:
                    senders[x["tx"]] = (B.w3.eth.get_transaction(x["tx"])["from"] or "").lower()
                except Exception:
                    senders[x["tx"]] = ""
                time.sleep(0.1)
            if senders[x["tx"]]:
                x["wallet"], x["source"] = senders[x["tx"]], "tx_from"
        with self.swaps_lock:
            new = not os.path.exists(SWAPS)
            with open(SWAPS, "a", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f, delimiter=";")
                if new:
                    w.writerow(SWAP_HEADER)
                for x in swaps:
                    w.writerow([dt.datetime.fromtimestamp(tr["t"]).strftime("%Y-%m-%d %H:%M:%S"), p.symbol, p.token, p.pair,
                                getattr(p, "creator", ""), x["block"], round(x["t"] - tr["t"], 1) if x["t"] else "",
                                x["wallet"], x["source"], x["side"], x["bnb"], x["tx"],
                                round(tr["max"], 2), round(tr["min"], 2), tr["bot"]])

    # ---- main loop ----
    def drain(self):
        """Pools are kept only while a virtual position follows them."""
        busy = {tr["pool"].pair for tr in self.trades}
        for pair in list(self.pools):
            if pair not in busy:
                with self.lock:
                    del self.pools[pair]

    def run(self):
        feed = self.feed = B.Feed(self)
        feed.start()
        B.HttpPoller(self, feed).start()
        B.ReserveRefresher(self).start()
        threading.Thread(target=self.watch_late, daemon=True).start()
        self.mempool.watchers.append(self.watch_threats)
        self.mempool.start()
        B.start_known_names()
        start_report(self)
        log(f"ЖУРНАЛ СИГНАЛОВ запущен: торговли нет, только наблюдение. Отчёт: http://127.0.0.1:{PORT}")
        last_gas = last_summary = time.time()
        while True:
            self.wake.wait(timeout=0.25)
            self.wake.clear()
            self.health["loop"] = time.time()
            try:
                self.drain()
                self.track()
                if time.time() - last_gas > 60:
                    self.gas_price, last_gas = B.cached_gas_price(), time.time()
                if time.time() - last_summary > 600:
                    last_summary = time.time()
                    log("ИТОГ: " + "; ".join(f"{STRATS[k]} {v['n']} сигн., 2 мин {v['r120']:+.1f}%, по правилам бота {v['bot']:+.1f}%"
                                             for k, v in summary().items() if v["n"]))
            except Exception:
                log("Ошибка журнала:\n" + traceback.format_exc(limit=3))
                self.health.update(errors=self.health["errors"] + 1, error_at=time.time(),
                                   error=traceback.format_exc(limit=1).strip().splitlines()[-1][:120])
                time.sleep(1)

    def check_manual(self, pair, token, block=None):
        """A pool funded by hand with >= manual_min_bnb, run through the bot's own filters."""
        t_found = time.time()
        try:  # when the pool was created: the path starts at the signal, so a backtest must add this age
            t_pool = B.w3.eth.get_block(block)["timestamp"] if block else None
        except Exception:
            t_pool = None
        err = ""
        for delay in (1, 4, 15):
            time.sleep(delay)
            try:
                c = B.w3.eth.contract(pair, abi=B.PAIR_ABI)
                r0, r1, _ = c.functions.getReserves().call()
                is0 = c.functions.token0().call().lower() == token.lower()
                r_tok, r_bnb = (r0, r1) if is0 else (r1, r0)
                if r_bnb < B.CFG["manual_min_bnb"] * 10**18:
                    continue
                try:
                    sym = B.w3.eth.contract(token, abi=B.SYMBOL_ABI).functions.symbol().call()[:20]
                except Exception:
                    sym = token[-4:]
                ok, bt, stx, why, bait = B.manual_filter(pair, token, sym, r_bnb, r_tok)
                sellable = bait_check(pair, token) if bait else None
                supply = B.w3.eth.contract(token, abi=B.ERC20_ABI).functions.totalSupply().call()
                try:
                    lp = B.lp_burned_pct(pair)
                except Exception:
                    lp = 0.0
                pool = B.Pool(pair, token, sym, bt or 0, stx or 0)  # 27.10: tax also when not safe (was 0)
                pool.safe, pool.why = ok, why
                pool.creator = B.launch_creator(pair)
                features = {"bnb_in_pool": round(r_bnb / 1e18, 1), "pool_supply_pct": round(r_tok / supply * 100, 1) if supply else 0,
                            "lp_burned": round(lp), "safe": int(ok), "why": why[:40],
                            "lp_approved": B.lp_approved_to(pair, pool.creator) or "нет"}
                if t_pool:
                    features["age_at_signal_s"] = round(time.time() - t_pool, 1)
                if sellable is not None:  # the bot skips copies at the name check: were they also tradeable?
                    features["bait_ok"], features["bait_why"] = int(sellable[0]), sellable[1][:40]
                self.signal("manual_bait" if bait else "manual", pool, features)
                # the bot checks after its entry delay: record the approval at that moment too
                time.sleep(max(0, B.CFG.get("manual_entry_delay_seconds", 30) - (time.time() - t_found)))
                features["lp_approved_at_entry"] = B.lp_approved_to(pair, pool.creator) or "нет"
                features.update(launch_funder(pool.creator))
                return
            except Exception as e:
                err = str(e)[:80]
                continue
        if err:
            log(f"Журнал: пул {pair} пропущен — проверка падала с ошибкой ({err})")


NODEREAL_KEY = ""
if os.path.exists(os.path.join(B.HERE, ".env")):
    for _line in open(os.path.join(B.HERE, ".env"), encoding="utf-8-sig"):  # only this line is read
        if _line.strip().startswith("NODEREAL_KEY="):
            NODEREAL_KEY = _line.split("=", 1)[1].strip().strip("\"'")
# 26.10 every launch got its BNB from a "cash desk" ~93 blocks before the pool; desks are one-off (a creator later funds
# the next creator), but the amount is fixed per series and each series pulls on its own schedule
SERIES = [17.02, 40.02, 55.02, 12.29, 58.01, 17.84, 50.0, 9.58]


def launch_funder(creator):
    """Who paid the creator >= 1 BNB in the last ~3000 blocks (NodeReal nr_getAssetTransfers, one request):
    funder, funder_bnb, funder_lead_blocks, series ("17.02"... / "прочие"; copies are told by bnb_in_pool 17.6).
    Lead blocks count back from now, i.e. after the entry delay."""
    if not NODEREAL_KEY or not creator:
        return {}
    try:
        b0 = B.w3.eth.block_number
        body = {"jsonrpc": "2.0", "id": 1, "method": "nr_getAssetTransfers",
                "params": [{"category": ["external"], "toAddress": creator.lower(), "fromBlock": hex(b0 - 3000),
                            "toBlock": hex(b0), "maxCount": "0x32", "excludeZeroValue": True}]}
        r = B.requests.post("https://bsc-mainnet.nodereal.io/v1/" + NODEREAL_KEY, json=body, timeout=15).json()
        tr = [t for t in (r.get("result") or {}).get("transfers", []) if int(t["value"], 16) >= 10**18]
        if not tr:
            return {"funder": "", "series": "нет кассы"}
        t = max(tr, key=lambda t: int(t["blockNum"], 16))  # the last big payment before the launch
        bnb = int(t["value"], 16) / 1e18
        series = next((str(s) for s in SERIES if abs(bnb - s) < 0.03), "прочие")
        return {"funder": t["from"].lower(), "funder_bnb": round(bnb, 3), "funder_lead_blocks": b0 - int(t["blockNum"], 16),
                "series": series}
    except Exception as e:
        return {"funder_err": str(e).replace(NODEREAL_KEY, "***")[:60]}


def bait_check(pair, token):
    """The bot skips a copy of a known name before any other check. 26.10 the serial copies (4AGENT every ~10 min,
    7AGENT, FLAPAI) were up ~+100% on the journal's quotes, but the quotes do not show whether the token can really be
    sold. Here a copy gets the bot's remaining safety checks (own buy+sell simulation, GoPlus); returns (ok, why)."""
    try:
        sim_ok, _, _, sim_why = B.trade_sim(token, pair, B.CFG["stake_bnb"])
        if not sim_ok:
            return False, sim_why
        ok, why = False, "GoPlus не ответил"
        for _ in range(int(B.CFG.get("manual_goplus_wait_seconds", 15))):
            ok, _, _, why = B.security_check(token, pair)
            if not why.startswith(("GoPlus ещё", "проверка")):
                break
            time.sleep(1)
        if why == "код контракта скрыт" and B.CFG.get("manual_allow_hidden_code", True):
            return True, "ok: код скрыт, симуляция прошла"
        return ok, "ok" if ok else why
    except Exception as e:
        return False, f"ошибка проверки: {str(e)[:30]}"


# ---------- report ----------

def rows():
    if not os.path.exists(OUT):
        return []
    with open(OUT, encoding="utf-8-sig") as f:
        data = list(csv.DictReader(f, delimiter=";"))
    for r in data:  # the bot's exit rules as they are set now, replayed on each row's saved path
        v = rules_result(r)
        r["bot_rules_pct"] = "" if v is None else v
    return data


def rules_result(r):
    """The bot's exit ladder (part at +tp1, the rest at take-profit / stop-loss / timeout) replayed on the row's saved
    path; each sell is filled one sample (~1 s, the next block) after its trigger."""
    c = B.CFG
    try:
        path = json.loads(r.get("path") or "[]")
    except ValueError:
        path = []
    tp1, part = c.get("manual_tp1_pct", 0), c.get("manual_tp1_part_pct", 0) / 100
    first = None
    delay = c.get("manual_entry_delay_seconds", 0)  # the bot buys this much later, if the pool is still alive
    rise = c.get("manual_entry_min_rise_pct", 0)  # ... and only once the price is this much up (others are buying)
    limit = c.get("manual_entry_max_wait_seconds", 240)
    entry = next(((t, p) for t, p in path if t >= delay and p > -90 and (not rise or p >= rise)), None)
    if entry is not None and rise and entry[0] > limit:
        entry = None
    if rise and path and entry is None:
        return None  # never rose (or was pulled): the bot would not have bought
    if delay and path:
        if entry is None or entry[1] <= -90:
            return None  # pulled while the bot waited: no trade
        base = 1 + entry[1] / 100
        path = [(t - entry[0], round(((1 + p / 100) / base - 1) * 100, 1)) for t, p in path if t >= entry[0]]
    for i, (t, pct) in enumerate(path):
        nxt = path[min(i + 1, len(path) - 1)][1]
        if first is None and part and pct >= tp1 and pct < c["manual_take_profit_pct"]:
            first = nxt
        if pct >= c["manual_take_profit_pct"] or pct <= -c["manual_stop_loss_pct"] or t >= c["manual_max_hold_seconds"]:
            return round(nxt if first is None else part * first + (1 - part) * nxt, 2)
    if path:
        return path[-1][1] if first is None else round(part * first + (1 - part) * path[-1][1], 2)
    return float(r["bot_rules_pct"]) if r.get("bot_rules_pct") not in ("", None) else None


def summary():
    out = {}
    data = rows()
    for k in STRATS:
        rs = [r for r in data if r["strategy"] == k]
        def avg(col):
            v = [float(r[col]) for r in rs if r[col] not in ("", None)]
            return sum(v) / len(v) if v else 0.0
        def win(col):
            v = [float(r[col]) for r in rs if r[col] not in ("", None)]
            return sum(x > 0 for x in v) / len(v) * 100 if v else 0.0
        safe = [r for r in rs if r["safe"] == "1"]
        sv = [float(r["bot_rules_pct"]) for r in safe if r["bot_rules_pct"] not in ("", None)]
        out[k] = {"n": len(rs), **{f"r{h}": avg(f"r{h}s_pct") for h in HORIZONS}, "bot": avg("bot_rules_pct"),
                  "win120": win("r120s_pct"), "winbot": win("bot_rules_pct"), "max": avg("max_pct"), "min": avg("min_pct"),
                  "n_safe": len(safe), "bot_safe": sum(sv) / len(sv) if sv else 0.0}
    return out


PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Журнал сигналов</title><style>
:root{--bg:#0d1117;--panel:#161b22;--line:#2a3140;--text:#e6edf3;--muted:#8b949e;--g:#2ea043;--r:#f85149}
@media (prefers-color-scheme: light){:root{--bg:#f6f8fa;--panel:#fff;--line:#d0d7de;--text:#1f2328;--muted:#656d76}}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,"Segoe UI",sans-serif}
.wrap{max-width:1280px;margin:0 auto;padding:16px}h1{font-size:20px;margin:0 0 4px}.sub{color:var(--muted);margin-bottom:16px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:16px;overflow-x:auto}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}th,td{padding:7px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th:first-child,td:first-child,td.l{text-align:left}th{color:var(--muted);font-size:12px}.p{color:var(--g)}.n{color:var(--r)}
h2{font-size:13px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin:0 0 10px}.note{color:var(--muted);font-size:12px;margin-top:8px}
.hs{display:flex;align-items:center;gap:12px;font-size:18px;font-weight:700;margin-bottom:10px}.dot{width:16px;height:16px;border-radius:50%;background:var(--muted);flex:none}
.dot.ok{background:var(--g);animation:pl 2s infinite}.dot.warn{background:#d29922}.dot.bad{background:var(--r)}
@keyframes pl{0%{box-shadow:0 0 0 0 rgba(46,160,67,.6)}70%{box-shadow:0 0 0 10px rgba(46,160,67,0)}100%{box-shadow:0 0 0 0 rgba(46,160,67,0)}}
.hg{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}.hi{border:1px solid var(--line);border-radius:10px;padding:8px 10px}
.hi .k{color:var(--muted);font-size:12px}.hi .v{font-weight:600;margin-top:2px}.ok-t{color:var(--g)}.warn-t{color:#d29922}.bad-t{color:var(--r)}
</style></head><body><div class="wrap"><h1>Журнал сигналов</h1><div class="sub" id="sub"></div>
<div class="card"><div class="hs"><span class="dot" id="hdot"></span><span id="htext">Проверяю журнал…</span></div>
<div class="hg" id="hgrid"></div><div class="note" id="hnote"></div></div>
<div class="card"><h2>Итоги по стратегиям (виртуальная ставка 0.002 BNB, после комиссий, налога и газа)</h2><table><thead><tr>
<th>Стратегия</th><th>Сигналов</th><th>Ждут итога</th><th>через 10 с</th><th>30 с</th><th>1 мин</th><th>2 мин</th><th>5 мин</th><th>в плюсе через 2 мин</th>
<th>по правилам бота</th><th>в плюсе по правилам</th><th>средний макс.</th><th>средний мин.</th><th>прошли фильтры бота</th><th>по правилам, только прошедшие фильтры</th></tr></thead><tbody id="sum"></tbody></table>
<div class="note">Проценты — средний результат одной сделки. «По правилам бота» — тейк-профит / стоп-лосс / таймаут ручного запуска из настроек, пересчитано по сохранённому пути цены. Главная цифра — последний столбец: так торговал бы бот. В старых строках (до 26.10) «прошли фильтры» означало более мягкую проверку.</div></div>
<div class="card"><h2>Последние сигналы</h2><table><thead><tr><th>Время</th><th>Стратегия</th><th>Токен</th><th>10 с</th><th>30 с</th><th>1 мин</th><th>2 мин</th><th>5 мин</th>
<th>макс.</th><th>мин.</th><th>по правилам бота</th><th style="text-align:left">Признаки</th></tr></thead><tbody id="rows"></tbody></table></div></div>
<script>
const f=x=>x===""||x==null?"—":`<span class="${+x>0?"p":+x<0?"n":""}">${(+x>0?"+":"")+(+x).toFixed(1)}%</span>`;
const e=s=>String(s??"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const ago=s=>s==null?"ещё не было":s<60?`${Math.round(s)} с назад`:s<3600?`${Math.round(s/60)} мин назад`:`${(s/3600).toFixed(1)} ч назад`;
let fails=0;
function health(d){const h=d.health,items=[],bad=[],warn=[];
const add=(k,v,lvl,why)=>{items.push(`<div class="hi"><div class="k">${k}</div><div class="v ${lvl}-t">${v}</div></div>`);if(lvl==="bad")bad.push(why);if(lvl==="warn")warn.push(why)};
add("Программа журнала",h.loop_ago<5?"работает, не зависла":`не отвечает ${Math.round(h.loop_ago)} с`,h.loop_ago<5?"ok":"bad","программа журнала зависла");
add("Блоки BSC",`блок ${d.block}, ${ago(h.block_ago)}`,h.block_ago<15?"ok":h.block_ago<60?"warn":"bad","новые блоки не приходят");
add("Связь (WebSocket)",h.feed_ok?"подключена":"через резерв (HTTP)",h.feed_ok?"ok":"warn","WebSocket молчит, данные идут через резерв");
add("Сделки в отслеживаемых пулах",`${h.events} шт., последняя ${ago(h.event_ago)}`,(h.event_ago!=null&&h.event_ago<120)||d.uptime<120||!d.pools?"ok":"warn","давно нет сделок в отслеживаемых пулах");
add("Новые пулы PancakeSwap",`${h.new_pools} шт., последний ${ago(h.pool_ago)}`,h.pool_ago!=null&&h.pool_ago<600?"ok":"warn","давно не видно новых пулов");
add("Сигналы",`последний ${ago(h.signal_ago)} · ждут итога ${d.open}`,"ok","");
add("Запись в журнал (CSV)",`за этот запуск ${h.writes} строк · файл обновлён ${ago(h.csv_ago)}`,"ok","");
add("Мемпул (ожидающие транзакции)",h.mempool_ok?`идёт, всего ${h.mempool_txs.toLocaleString("ru")}`:"нет связи",h.mempool_ok?"ok":"warn","мемпул не приходит");
add("Ошибки",h.errors?`${h.errors}, последняя ${ago(h.error_ago)}`:"нет",h.errors&&h.error_ago<300?"warn":"ok","недавно были ошибки: "+h.error);
document.getElementById("hgrid").innerHTML=items.join("");
const lvl=bad.length?"bad":warn.length?"warn":"ok";
document.getElementById("hdot").className="dot "+lvl;
document.getElementById("htext").textContent=lvl==="ok"?"Журнал работает, данные идут":lvl==="warn"?"Журнал работает, но есть замечания":"Журнал НЕ работает как надо";
document.getElementById("hnote").textContent=(bad.concat(warn).join("; ")||"Строка в журнал пишется через 5 минут после сигнала, поэтому новые строки появляются не сразу.")+` · Страница обновлена ${new Date().toLocaleTimeString("ru-RU")}`}
async function t(){try{const d=await(await fetch("/api",{cache:"no-store"})).json();fails=0;health(d);
document.getElementById("sub").textContent=`Работает ${Math.round(d.uptime/60)} мин · блок ${d.block} · наблюдаю пулов: ${d.pools} · открытых виртуальных сделок: ${d.open}`;
document.getElementById("sum").innerHTML=Object.entries(d.summary).map(([k,v])=>`<tr><td>${e(d.names[k])}</td><td>${v.n}</td><td>${d.pending[k]||0}</td>
${["r10","r30","r60","r120","r300"].map(h=>`<td>${v.n?f(v[h]):"—"}</td>`).join("")}<td>${v.n?Math.round(v.win120)+"%":"—"}</td><td>${v.n?f(v.bot):"—"}</td>
<td>${v.n?Math.round(v.winbot)+"%":"—"}</td><td>${v.n?f(v.max):"—"}</td><td>${v.n?f(v.min):"—"}</td>
<td>${v.n_safe}</td><td>${v.n_safe?f(v.bot_safe):"—"}</td></tr>`).join("");
document.getElementById("rows").innerHTML=d.rows.map(r=>`<tr><td class="l">${e(r.time.slice(11))}</td><td class="l">${e(d.names[r.strategy])}</td>
<td class="l"><a href="https://dexscreener.com/bsc/${e(r.pair)}" target="_blank" rel="noopener">${e(r.symbol)}</a>${r.safe==="0"?' ⚠':''}</td>
<td>${f(r.r10s_pct)}</td><td>${f(r.r30s_pct)}</td><td>${f(r.r60s_pct)}</td><td>${f(r.r120s_pct)}</td><td>${f(r.r300s_pct)}</td>
<td>${f(r.max_pct)}</td><td>${f(r.min_pct)}</td><td>${f(r.bot_rules_pct)}</td><td class="l" style="color:var(--muted);font-size:12px">${e(r.features)}</td></tr>`).join("");
}catch(x){fails++;document.getElementById("hdot").className="dot bad";
document.getElementById("htext").textContent="Журнал не отвечает — программа выключена или зависла";
document.getElementById("hnote").textContent=`Нет ответа ${fails*5} с. Запустите run_journal.cmd (или start_all.cmd).`;
document.getElementById("sub").textContent="Журнал не отвечает — запущен ли python journal.py?"}}
t();setInterval(t,5000);</script></body></html>"""


def start_report(j):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/api":
                pending = defaultdict(int)
                for tr in list(j.trades):
                    pending[tr["strategy"]] += 1
                now, h = time.time(), j.health
                ago = lambda t: round(now - t, 1) if t else None
                health = {"loop_ago": ago(h["loop"]), "block_ago": ago(j.last_block_at), "feed_ok": bool(j.feed and j.feed.healthy()),
                          "events": h["events"], "event_ago": ago(h["event_at"]), "new_pools": h["new_pools"],
                          "pool_ago": ago(h["pool_at"]), "signal_ago": ago(h["signal_at"]), "writes": h["writes"],
                          "write_ago": ago(h["write_at"]), "csv_ago": ago(os.path.getmtime(OUT)) if os.path.exists(OUT) else None,
                          "errors": h["errors"], "error_ago": ago(h["error_at"]), "error": h["error"],
                          "mempool_ok": j.mempool.healthy(), "mempool_txs": j.mempool.count}
                body = json.dumps({"uptime": time.time() - j.started, "block": j.last_block, "pools": len(j.pools), "health": health,
                                   "open": len(j.trades), "pending": pending, "names": STRATS, "summary": summary(),
                                   "rows": [r for r in rows() if r["strategy"] in STRATS][-150:][::-1]}, ensure_ascii=False).encode()
                ctype = "application/json; charset=utf-8"
            else:
                body, ctype = PAGE.encode(), "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        request_queue_size = 64  # the default 5 fills up when a browser keeps several tabs polling

    srv = Server(("127.0.0.1", PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()


if __name__ == "__main__":
    B.disable_quick_edit()
    B.single_instance("Журнал сигналов", 47652)
    try:
        Journal().run()
    except KeyboardInterrupt:
        log("Журнал остановлен. Готовые сигналы сохранены в journal_signals.csv")
    finally:
        B.flush_console()
