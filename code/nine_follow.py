"""18.11 (user: follow the nine wallets' transactions; enter before them, exit after their buying). PAPER ONLY — sends nothing, needs no key.
Helius websockets (HELIUS_KEY of D:\\bot\\.env, never printed): logsSubscribe on the nine wallets and on pool creators (the 5 known + any address
that sends the nine >= 0.5 SOL). Signals:
  CREATE — a watched creator creates a PumpSwap pool  -> paper entry right away (the nine usually enter 6-50 s later)
  NINE   — first transaction of the nine in a pool     -> paper entry (comparison)
Pool reserves are read every second (getMultipleAccounts of the pool vaults) for open positions; entry/exit prices from the pool formula
(x*y=k, 0.3% fee each side, stake STAKE SOL, price impact of our own stake included). Rug = pool SOL falls below 50% of its maximum since entry.
Exits (each entry is evaluated with every exit rule, one CSV row per pair):
  after_first_nine_10s  10 s after the first nine trade in the pool
  after_nine_10tx       after the 10th nine transaction in the pool
  nine_idle_60          the nine made no trade in this pool for 60 s (their session is over)
  tp10_or_idle60        +10 % or nine_idle_60, whichever first
  hold_180              180 s after entry
  (no nine trade in 300 s after a CREATE entry -> exit "no_nine_300")
Files: analysis/nine_follow_trades.csv, nine_follow_events.csv, nine_follow.log, nine_follow_status.json. Report: python nine_follow.py report
Lock port 47669 (watchdog)."""
import asyncio, base64, collections, csv, datetime as dt, json, os, socket, sys, threading, time, urllib.request

A = os.path.dirname(os.path.abspath(__file__))
if sys.stdout:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
AMM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
WSOL = "So11111111111111111111111111111111111111112"
STAKE = 0.1
FEE = 0.003
TRADES = os.path.join(A, "nine_follow_trades.csv")
EVENTS = os.path.join(A, "nine_follow_events.csv")
LOG = os.path.join(A, "nine_follow.log")
STATUS = os.path.join(A, "nine_follow_status.json")
ACTIVITY = os.path.join(A, "ten_activity.json")  # read by sol_trader.py (signal mode)
NINE = ["2RCjTNMYfivvCHpp9CtxQhBB1kzQG8fptq4nYa3EB3tP", "5KJEmUWT474k49ZVZrteFYicZbT22sTvvNRRNk75hBpn", "6k64Wxj9LAYJ2zZ6YY1MKtmpeuBW3bcVqyRigYX1nU2x",
        "7bL6uz3PYJSsb6ndZHhnRheaaxkHBqhRDr513tswFNnA", "9YHUi5cV3gjY1hCZNJw54rXCSf2cfpRVSt1TzeSCLzBD", "AHXA5vwTqp9iNKLQk1NtodpLZdirxmDfEiJMeGLaqnqc",
        "B4ExgwFBJaG2DSKMHgrVwNGHd7ckbAzUhY6c7yT4AQsE", "ByrLc8xWYdfwBfzXRBFmoHEE3DNqCbyFD6YptddLvepE", "GN1gj63Dzgvdqa79m32Rvt7NjSP36wTTsuJz4fHtX7mF",
        "GH74zHdNZWcF9zRzw3KoNG8GNgUvtzokNwkSFMeWTvi6"]  # 19.11 (user): 10th wallet, same buy->sell-min-out pattern (task 12)
N = set(NINE)
CREATORS = {"Czg8kh3EYpqgLMUdrV3X3iiBaUdgP7aZYRMYm8xU2BdL", "6e7XUsDbJyxMdDe8zEHHMgWxkC73YAe428mJRSV65sSC", "29iqxoNvRDdx8G5peK3FbDVVt4mayaKKAesvn7m6R9ko",
            "CPC8iEE1PripNpd7VjofSHENYnoKpFnjmZFDtE8m7QT2", "89coAVTUZetQ4K6Ct4nsqtLtMCaR4X2mWStdBsmdECur", "GLEBhVYdFuP6Gh4Q7Qan66gYeSc7AjcFYC8gM7aMN6wd",
            "8vB8UUGxTDhZFEW9BRspXdaqNNauYgrLL5GZwbynw9XL"}
EXITS = ("after_first_nine_10s", "after_nine_10tx", "nine_idle_60", "tp10_or_idle60", "hold_180")
iso = lambda u: dt.datetime.fromtimestamp(u, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
LK = threading.Lock()


def log(*a):
    line = time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a)
    with LK:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    try:
        print(line, flush=True)
    except Exception:
        pass


def append(path, row):
    new = not os.path.exists(path)
    with LK:
        with open(path, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(row.keys()), delimiter=";")
            if new:
                w.writeheader()
            w.writerow(row)


# ---------------- report ----------------
if len(sys.argv) > 1 and sys.argv[1] == "report":
    rows = list(csv.DictReader(open(TRADES, encoding="utf-8-sig"), delimiter=";")) if os.path.exists(TRADES) else []
    g = collections.defaultdict(list)
    for r in rows:
        g[(r["entry"], r["exit_rule"])].append(r)
    print(f"сделок (бумага): {len(rows)}, монет: {len({r['mint'] for r in rows})}")
    for k in sorted(g):
        v = g[k]
        p = [float(r["pnl_pct"]) for r in v]
        rug = sum(1 for r in v if r["rugged"] == "1")
        print(f"{k[0]:7} {k[1]:22} n={len(v):3}  в плюсе {sum(1 for x in p if x > 0):3}  среднее {sum(p) / len(p):+7.2f}%  "
              f"медиана {sorted(p)[len(p) // 2]:+7.2f}%  худшая {min(p):+7.1f}%  изъятий {rug}")
    sys.exit(0)

K = [l.split("=", 1)[1].strip().strip('"\'') for l in open(r"D:\bot\.env", encoding="utf-8-sig") if l.strip().startswith("HELIUS_KEY=")][0]
HTTP = "https://mainnet.helius-rpc.com/?api-key=" + K
WSS = "wss://mainnet.helius-rpc.com/?api-key=" + K
# single instance
_LOCK = socket.socket()
try:
    _LOCK.bind(("127.0.0.1", 47669))
    _LOCK.listen(1)
except OSError:
    log("уже запущен (порт 47669 занят) — выхожу")
    sys.exit(0)


def rpc(m, p, tries=4):
    for k in range(tries):
        try:
            r = json.loads(urllib.request.urlopen(urllib.request.Request(HTTP, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": m, "params": p}).encode(),
                                                                         headers={"Content-Type": "application/json"}), timeout=20).read())
            if r.get("result") is not None:
                return r["result"]
        except Exception:
            pass
        time.sleep(0.4 * (k + 1))
    return None


def b58enc(b):
    A58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    n = int.from_bytes(b, "big")
    s = ""
    while n:
        n, r = divmod(n, 58)
        s = A58[r] + s
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + s


POOLS = {}  # pool -> dict(mint, wvault, cvault, reversed, creator, created, nine_first, nine_last, nine_n, max_w)
POS = []  # open paper positions
LOCKP = threading.Lock()
STATE = dict(started=time.time(), nine_tx=0, creates=0, funding=0, closed=0, current_funder="", last_event="")


def pool_info(pool):
    if pool in POOLS:
        return POOLS[pool]
    v = rpc("getAccountInfo", [pool, {"encoding": "base64"}])
    if not v or not v.get("value") or v["value"]["owner"] != AMM:
        return None
    d = base64.b64decode(v["value"]["data"][0])
    if len(d) < 243:
        return None
    base, quote, bv, qv, creator = b58enc(d[43:75]), b58enc(d[75:107]), b58enc(d[139:171]), b58enc(d[171:203]), b58enc(d[11:43])
    if WSOL not in (base, quote):
        return None
    rev = base == WSOL
    info = dict(pool=pool, mint=quote if rev else base, wvault=bv if rev else qv, cvault=qv if rev else bv, creator=creator, created=None,
                nine_first=None, nine_last=None, nine_n=0)
    POOLS[pool] = info
    return info


def reserves(pools):
    keys = []
    for p in pools:
        keys += [p["wvault"], p["cvault"]]
    res = {}
    for i in range(0, len(keys), 100):
        v = rpc("getMultipleAccounts", [keys[i:i + 100], {"encoding": "jsonParsed"}]) or {}
        for k, a in zip(keys[i:i + 100], v.get("value") or []):
            try:
                res[k] = int(a["data"]["parsed"]["info"]["tokenAmount"]["amount"])
            except Exception:
                pass
    return {p["pool"]: (res.get(p["wvault"]), res.get(p["cvault"])) for p in pools}


def buy_coins(w, c, sol):
    x = int(sol * 1e9)
    return c * x / (w + x) * (1 - FEE)


def sell_sol(w, c, coins):
    q = coins * (1 - FEE)
    return w * q / (c + q) / 1e9


def open_position(info, entry, why):
    r = reserves([info]).get(info["pool"])
    if not r or not r[0] or not r[1]:
        log("нет резервов для", info["pool"][:8])
        return
    w, c = r
    coins = buy_coins(w, c, STAKE)
    with LOCKP:
        if any(p["pool"] == info["pool"] and p["entry"] == entry for p in POS):
            return
        POS.append(dict(pool=info["pool"], mint=info["mint"], creator=info["creator"], entry=entry, why=why, t0=time.time(), w0=w, coins=coins,
                        max_w=w, open_rules=set(EXITS), nine_at_entry=info["nine_n"]))
    log(f"БУМАГА вход {entry} {info['mint'][:8]} пул {w / 1e9:.2f} SOL ({why})")


def close(pos, rule, w, c, rugged=False):
    sol = sell_sol(w, c, pos["coins"]) if w and c else 0.0
    pnl = (sol - STAKE) / STAKE * 100
    info = POOLS.get(pos["pool"], {})
    append(TRADES, dict(time_utc=iso(time.time()), mint=pos["mint"], pool=pos["pool"], creator=pos["creator"], current_funder=STATE["current_funder"],
                        entry=pos["entry"], why=pos["why"], exit_rule=rule, entry_utc=iso(pos["t0"]), hold_s=round(time.time() - pos["t0"], 1),
                        pool_sol_entry=round(pos["w0"] / 1e9, 4), pool_sol_exit=round((w or 0) / 1e9, 4), nine_tx_in_pool=info.get("nine_n", 0),
                        nine_first_after_entry_s=round(info["nine_first"] - pos["t0"], 1) if info.get("nine_first") else "",
                        stake=STAKE, sol_back=round(sol, 6), pnl_pct=round(pnl, 3), rugged=int(rugged)))
    STATE["closed"] += 1
    log(f"БУМАГА выход {pos['entry']}/{rule} {pos['mint'][:8]} {pnl:+.1f}%{' ИЗЪЯТИЕ' if rugged else ''}")


def price_loop():
    while True:
        time.sleep(1.0)
        with LOCKP:
            act = list(POS)
        if not act:
            continue
        infos = {p["pool"]: POOLS[p["pool"]] for p in act if p["pool"] in POOLS}
        res = reserves(list(infos.values()))
        now = time.time()
        for p in act:
            w, c = res.get(p["pool"], (None, None))
            if not w or not c:
                continue
            p["max_w"] = max(p["max_w"], w)
            info = infos[p["pool"]]
            val = sell_sol(w, c, p["coins"])
            rug = w < 0.5 * p["max_w"]
            for rule in list(p["open_rules"]):
                done = rug
                if not done:
                    nf, nl, nn = info["nine_first"], info["nine_last"], info["nine_n"]
                    if rule == "after_first_nine_10s":
                        done = nf is not None and now >= nf + 10
                    elif rule == "after_nine_10tx":
                        done = nn - p["nine_at_entry"] >= 10
                    elif rule == "nine_idle_60":
                        done = nl is not None and now - nl >= 60
                    elif rule == "tp10_or_idle60":
                        done = val >= STAKE * 1.10 or (nl is not None and now - nl >= 60)
                    elif rule == "hold_180":
                        done = now - p["t0"] >= 180
                    if not done and p["entry"] == "CREATE" and info["nine_first"] is None and now - p["t0"] >= 300:
                        rule_name = "no_nine_300"
                        close(p, rule_name, w, c)
                        p["open_rules"].clear()
                        break
                if done:
                    close(p, rule, w, c, rugged=rug)
                    p["open_rules"].discard(rule)
        with LOCKP:
            POS[:] = [p for p in POS if p["open_rules"]]


def status_loop():
    while True:
        try:
            with LOCKP:
                op = [dict(mint=p["mint"][:8], entry=p["entry"], age=round(time.time() - p["t0"]), rules=sorted(p["open_rules"])) for p in POS]
            json.dump(dict(STATE, open=op, updated=iso(time.time()), creators=sorted(CREATORS)), open(STATUS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            # 19.11 (user: live bot on these signals): per-pool activity of the ten for sol_trader.py, written atomically every second
            now = time.time()
            act = {p: dict(creator=i["creator"], mint=i["mint"], created=i["created"], first=i["nine_first"], last=i["nine_last"], n=i["nine_n"])
                   for p, i in list(POOLS.items()) if (i["nine_last"] or i["created"] or 0) > now - 3600}
            tmp = ACTIVITY + ".tmp"
            json.dump(dict(t=now, creators=sorted(CREATORS), current_funder=STATE["current_funder"], pools=act), open(tmp, "w", encoding="utf-8"))
            os.replace(tmp, ACTIVITY)
        except Exception:
            pass
        time.sleep(1)


def handle(sig, who):
    t = None
    for k in range(6):
        t = rpc("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1, "commitment": "confirmed"}], tries=1)
        if t:
            break
        time.sleep(0.5 + k * 0.5)
    if not t or (t.get("meta") or {}).get("err"):
        return
    msg = t["transaction"]["message"]
    keys = [k["pubkey"] for k in msg["accountKeys"]]
    payer = keys[0]
    logs = " ".join(t["meta"].get("logMessages") or [])
    allins = list(msg["instructions"]) + [x for g in t["meta"].get("innerInstructions") or [] for x in g["instructions"]]
    # funding: creator -> nine
    for ins in allins:
        p = ins.get("parsed")
        if isinstance(p, dict) and ins.get("program") == "system" and p.get("type") == "transfer":
            a, b, v = p["info"]["source"], p["info"]["destination"], int(p["info"]["lamports"]) / 1e9
            if b in N and a not in N and v >= 0.5:
                STATE["funding"] += 1
                if a != STATE["current_funder"]:
                    log(f"ПОПОЛНЕНИЕ девяти от {a[:8]} ({v} SOL) — смена финансирующего")
                    append(EVENTS, dict(time_utc=iso(time.time()), event="funding", address=a, sol=v, signature=sig))
                STATE["current_funder"] = a
                if a not in CREATORS:
                    CREATORS.add(a)
                    NEW_SUBS.append(a)
                    log("новый адрес для слежения (пополняет девять):", a)
    # pool creation by a watched creator
    if payer in CREATORS and ("CreatePool" in logs or "create_pool" in logs):
        for ins in allins:
            if ins.get("programId") == AMM and len(ins.get("accounts", [])) >= 10:
                info = pool_info(ins["accounts"][0])
                if info:
                    info["created"] = t.get("blockTime") or time.time()
                    STATE["creates"] += 1
                    append(EVENTS, dict(time_utc=iso(time.time()), event="create", address=payer, sol="", signature=sig))
                    log(f"СОЗДАН ПУЛ {info['pool'][:8]} монета {info['mint'][:8]} создатель {payer[:8]} (смена: {STATE['current_funder'][:8]})")
                    open_position(info, "CREATE", f"создатель {payer[:8]}")
                    break
    # nine trade
    if payer in N:
        for ins in msg["instructions"]:
            if ins.get("programId") == AMM and len(ins.get("accounts", [])) >= 10:
                info = pool_info(ins["accounts"][0])
                if not info:
                    return
                now = time.time()
                first = info["nine_first"] is None
                if first:
                    info["nine_first"] = now
                    append(EVENTS, dict(time_utc=iso(now), event="nine_first", address=info["pool"], sol="", signature=sig))
                    log(f"ДЕВЯТЬ вошли в {info['mint'][:8]} (пул {info['pool'][:8]}, создатель {info['creator'][:8]})")
                info["nine_last"] = now
                info["nine_n"] += 1
                STATE["nine_tx"] += 1
                if first:
                    open_position(info, "NINE", "первая сделка девяти")
                break


NEW_SUBS = []


async def ws_main():
    import websockets
    while True:
        try:
            async with websockets.connect(WSS, ping_interval=20, ping_timeout=20, max_size=2 ** 22) as ws:
                subs, idmap = list(NINE) + sorted(CREATORS), {}
                for i, a in enumerate(subs, 1):  # "processed": we hear the tx ~0.4-1 s earlier than "confirmed"
                    await ws.send(json.dumps({"jsonrpc": "2.0", "id": i, "method": "logsSubscribe", "params": [{"mentions": [a]}, {"commitment": "processed"}]}))
                    idmap[i] = a
                subid = {}
                log(f"подключено: слежу за {len(NINE)} кошельками и {len(CREATORS)} создателями")
                seen = collections.OrderedDict()
                nxt = len(subs) + 1
                while True:
                    while NEW_SUBS:
                        a = NEW_SUBS.pop()
                        await ws.send(json.dumps({"jsonrpc": "2.0", "id": nxt, "method": "logsSubscribe", "params": [{"mentions": [a]}, {"commitment": "processed"}]}))
                        idmap[nxt] = a
                        nxt += 1
                    try:  # 19.11: silence of the wallets is normal; the connection itself is checked by the websocket pings
                        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
                    except asyncio.TimeoutError:
                        continue
                    if "id" in msg and "result" in msg:
                        subid[msg["result"]] = idmap.get(msg["id"])
                        continue
                    if msg.get("method") != "logsNotification":
                        continue
                    v = msg["params"]["result"]["value"]
                    if v.get("err"):
                        continue
                    sig = v["signature"]
                    if sig in seen:
                        continue
                    seen[sig] = 1
                    if len(seen) > 5000:
                        seen.popitem(last=False)
                    who = subid.get(msg["params"]["subscription"], "")
                    threading.Thread(target=handle, args=(sig, who), daemon=True).start()
        except Exception as e:
            log("связь потеряна:", type(e).__name__, str(e)[:120], "— переподключаюсь через 5 с")
            await asyncio.sleep(5)


log("СТАРТ nine_follow (бумага, без денег), ставка", STAKE, "SOL")
threading.Thread(target=price_loop, daemon=True).start()
threading.Thread(target=status_loop, daemon=True).start()
asyncio.run(ws_main())
