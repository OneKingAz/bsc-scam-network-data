"""18.11 (user: «делай наблюдатель»). Solana farm watcher, NO MONEY, nothing is sent.
Live via Helius websockets (HELIUS_KEY of D:\\bot\\.env, never printed):
  - logsSubscribe on the farm's treasury / sponsor and every known creator (from sol_pools.csv + new ones the sponsor/treasury pays >= 50 SOL);
  - a creator's CreatePool -> getTransaction (1 request) -> pool, mint, the pool's SOL vault and coin vault;
  - accountSubscribe on both vaults for POOL_WATCH_S seconds -> pool SOL and price after every change, by local receive time.
Paper trade per pool: we learn of the pool at the moment the CreatePool notification arrives (t=0 = that moment); a buy sent at
t + D (D = 1/3/5/10 s, + SEND_LAG for landing) gets the pool state of that moment (with our 1 SOL impact, fee 0.3 % each way);
sell at the first state with pool SOL >= T (88/89/90). A creator Withdraw (pool SOL drops > 20 % at once) before T = loss.
Out: analysis/sol_watch.csv (one row per pool), analysis/sol_watch.log, analysis/sol_watch_paths.jsonl (path of each pool).
Usage: D:\\bot\\.venv\\Scripts\\python.exe -X utf8 analysis\\sol_watch.py"""
import asyncio
import csv
import json
import os
import sys
import time
import urllib.request

import websockets

if sys.stdout:
    sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
WSOL = "So11111111111111111111111111111111111111112"
TREASURY = "B7StC42h126Hmtkw4GoRMcaHA5ZuKawfa9fCSTV6UMSj"
SPONSOR = "3azG8rmtPnNUz77a6UqBKt1ykpk3JQpLLNTaoXW31b34"
NEW_CREATOR_SOL = 20.0     # a transfer of at least this from treasury/sponsor (or to the treasury) = a creator
POOL_WATCH_S = 600         # follow each pool this long
STAKE, FEE, SEND_LAG = 1.0, 0.003, 0.8
DELAYS, TARGETS = (1, 3, 5, 10), (88, 89, 90)
KEY = None
for _l in open(r"D:\bot\.env", encoding="utf-8-sig"):
    if _l.strip().startswith("HELIUS_KEY="):
        KEY = _l.split("=", 1)[1].strip().strip('"\'')
HTTP = "https://mainnet.helius-rpc.com/?api-key=" + KEY
WSS = "wss://mainnet.helius-rpc.com/?api-key=" + KEY
LOG = os.path.join(HERE, "sol_watch.log")
OUT = os.path.join(HERE, "sol_watch.csv")
PATHS = os.path.join(HERE, "sol_watch_paths.jsonl")
CREATORS_FILE = os.path.join(HERE, "sol_watch_creators.json")


def say(*a):
    s = time.strftime("%d.%m %H:%M:%S ") + " ".join(str(x) for x in a)
    if sys.stdout:
        print(s, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(s + "\n")


def rpc(method, params, tries=5):
    for k in range(tries):
        try:
            r = json.loads(urllib.request.urlopen(urllib.request.Request(HTTP, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
                                                                         headers={"Content-Type": "application/json"}), timeout=30).read())
            if r.get("result") is not None:
                return r["result"]
        except Exception:
            pass
        time.sleep(0.4 * (k + 1))
    return None


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


def save_creators():
    json.dump(sorted(CREATORS), open(CREATORS_FILE, "w", encoding="utf-8"), indent=0)


class Pool:
    def __init__(self, creator, pool, mint, sol_vault, tok_vault, slot, block_time, t0):
        self.creator, self.pool, self.mint, self.sv, self.tv = creator, pool, mint, sol_vault, tok_vault
        self.slot, self.block_time, self.t0 = slot, block_time, t0
        self.sol = self.tok = None
        self.path = []          # (t since detect, sol, tok)
        self.withdraw = None    # (t, sol before, sol after)
        self.done = False

    def update(self, sol=None, tok=None):
        t = time.time() - self.t0
        prev = self.sol
        if sol is not None:
            self.sol = sol
        if tok is not None:
            self.tok = tok
        if self.sol is None or self.tok is None:
            return
        if prev and self.sol < prev * 0.8 and self.withdraw is None and t > 2:
            self.withdraw = (round(t, 2), prev, self.sol)
            say(f"ВЫВОД ЛИКВИДНОСТИ {self.pool[:8]} на {t:.1f} с: {prev:.2f} → {self.sol:.2f} SOL")
        self.path.append((round(t, 3), self.sol, self.tok))

    def state_at(self, t):
        st = None
        for p in self.path:
            if p[0] <= t:
                st = p
            else:
                break
        return st

    def sim(self, d, T):
        e = self.state_at(d + SEND_LAG)
        if not e:
            return None
        _, s, k = e
        got = k * STAKE * (1 - FEE) / (s + STAKE * (1 - FEE))
        for t, sol, tok in self.path:
            if t <= d + SEND_LAG:
                continue
            if self.withdraw and t >= self.withdraw[0]:
                return (-1.0, t, "вывод")
            if sol >= T:
                sol2, tok2 = sol + STAKE, tok - got
                return ((sol2 * got * (1 - FEE) / (tok2 + got * (1 - FEE))) / STAKE - 1, t, "цель")
        return (None, None, "не дошёл")

    def row(self):
        r = dict(time=time.strftime("%d.%m %H:%M:%S", time.localtime(self.t0)), creator=self.creator, pool=self.pool, mint=self.mint,
                 lag_detect_s=round(self.t0 - self.block_time, 1) if self.block_time else "",
                 sol_first=round(self.path[0][1], 2) if self.path else "", sol_max=round(max((p[1] for p in self.path), default=0), 2),
                 changes=len(self.path), withdraw_s=self.withdraw[0] if self.withdraw else "",
                 withdraw_sol=round(self.withdraw[1], 2) if self.withdraw else "")
        for T in TARGETS:
            r[f"t_to_{T}"] = next((p[0] for p in self.path if p[1] >= T), "")
        for d in DELAYS:
            e = self.state_at(d + SEND_LAG)
            r[f"sol_at_{d}s"] = round(e[1], 2) if e else ""
            for T in TARGETS:
                x = self.sim(d, T)
                r[f"pnl_{d}s_{T}"] = "" if not x or x[0] is None else round(x[0] * 100, 2)
        return r


POOLS = {}        # pool -> Pool
VAULT = {}        # vault account -> (pool, "sol"/"tok")


def parse_create(sig, creator):
    t = rpc("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1, "commitment": "confirmed"}])
    if not t or not t.get("meta") or t["meta"].get("err"):
        return None
    ks = [k["pubkey"] if isinstance(k, dict) else k for k in t["transaction"]["message"]["accountKeys"]]
    own = {}
    for b in t["meta"].get("postTokenBalances") or []:
        own.setdefault(b.get("owner"), {})[b["mint"]] = (ks[b["accountIndex"]], float(b["uiTokenAmount"].get("uiAmount") or 0))
    for o, mm in own.items():
        if o != creator and WSOL in mm and len(mm) == 2:
            mint = [m for m in mm if m != WSOL][0]
            return dict(pool=o, mint=mint, sv=mm[WSOL][0], sol=mm[WSOL][1], tv=mm[mint][0], tok=mm[mint][1], slot=t["slot"], bt=t.get("blockTime"))
    return None


def payee_of(sig, payer):
    """a plain SOL transfer of >= NEW_CREATOR_SOL from payer -> the receiver"""
    t = rpc("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 1, "commitment": "confirmed"}])
    if not t or not t.get("meta") or t["meta"].get("err"):
        return []
    out = []
    for ins in t["transaction"]["message"].get("instructions") or []:
        p = ins.get("parsed") if isinstance(ins, dict) else None
        if isinstance(p, dict) and p.get("type") == "transfer" and ins.get("program") == "system":
            i = p.get("info") or {}
            if int(i.get("lamports", 0)) < NEW_CREATOR_SOL * 1e9:
                continue
            if i.get("source") == payer:
                out.append((i["destination"], int(i["lamports"]) / 1e9))
            elif payer == TREASURY and i.get("destination") == TREASURY:  # creators send their 35 SOL back to the treasury
                out.append((i["source"], int(i["lamports"]) / 1e9))
    return out


def plain_transfer(logs):
    """only the System program (and compute budget) ran = a plain SOL transfer"""
    progs = {l.split()[1] for l in logs if l.startswith("Program ") and " invoke [" in l}
    return bool(progs) and progs <= {"11111111111111111111111111111111", "ComputeBudget111111111111111111111111111111"}


def flush(p):
    r = p.row()
    new = not os.path.exists(OUT)
    with open(OUT, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(r.keys()), delimiter=";")
        if new:
            w.writeheader()
        w.writerow(r)
    with open(PATHS, "a", encoding="utf-8") as f:
        f.write(json.dumps(dict(pool=p.pool, creator=p.creator, t0=p.t0, block_time=p.block_time, withdraw=p.withdraw, path=p.path)) + "\n")
    say(f"ИТОГ {p.pool[:8]}: старт {r['sol_first']} SOL, макс {r['sol_max']}, до 90 за {r['t_to_90'] or '-'} с, "
        f"вывод {r['withdraw_s'] or 'нет'}; вход +1с→90: {r['pnl_1s_90']}%, +3с: {r['pnl_3s_90']}%, +5с: {r['pnl_5s_90']}%, +10с: {r['pnl_10s_90']}%")


async def main():
    nid = [100]
    subs = {}      # request id -> ("logs", addr) / ("acct", vault)
    subkey = {}    # subscription id -> same
    while True:
        try:
            async with websockets.connect(WSS, ping_interval=20, ping_timeout=20, max_size=2 ** 24) as ws:
                say(f"подключён; слежу за казной, спонсором и {len(CREATORS)} создателями; активных пулов {len(POOLS)}")
                subs.clear()
                subkey.clear()

                async def sub_logs(a):
                    nid[0] += 1
                    subs[nid[0]] = ("logs", a)
                    await ws.send(json.dumps({"jsonrpc": "2.0", "id": nid[0], "method": "logsSubscribe", "params": [{"mentions": [a]}, {"commitment": "processed"}]}))

                async def sub_acct(v):
                    nid[0] += 1
                    subs[nid[0]] = ("acct", v)
                    await ws.send(json.dumps({"jsonrpc": "2.0", "id": nid[0], "method": "accountSubscribe", "params": [v, {"encoding": "jsonParsed", "commitment": "processed"}]}))

                for a in [TREASURY, SPONSOR] + sorted(CREATORS):
                    await sub_logs(a)
                for v in list(VAULT):
                    await sub_acct(v)
                loop = asyncio.get_running_loop()
                last_tick = time.time()
                while True:
                    try:
                        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                    except asyncio.TimeoutError:
                        msg = None
                    now = time.time()
                    if msg and "id" in msg and "result" in msg and msg["id"] in subs:
                        subkey[msg["result"]] = subs[msg["id"]]
                    elif msg and msg.get("method") == "logsNotification":
                        v = msg["params"]["result"]["value"]
                        kind = subkey.get(msg["params"]["subscription"])
                        if not kind or v.get("err"):
                            continue
                        a, logs, sig = kind[1], " ".join(v.get("logs") or []), v["signature"]
                        if a in CREATORS and "CreatePool" in logs:
                            t0 = now
                            info = await loop.run_in_executor(None, parse_create, sig, a)
                            if info and info["pool"] not in POOLS:
                                p = Pool(a, info["pool"], info["mint"], info["sv"], info["tv"], info["slot"], info["bt"], t0)
                                POOLS[p.pool] = p
                                VAULT[p.sv], VAULT[p.tv] = (p.pool, "sol"), (p.pool, "tok")
                                p.update(info["sol"], info["tok"])
                                await sub_acct(p.sv)
                                await sub_acct(p.tv)
                                lag = f"{t0 - info['bt']:.1f}" if info["bt"] else "?"
                                say(f"НОВЫЙ ПУЛ {p.pool} монета {p.mint} создатель {a[:8]}; в пуле {info['sol']:.2f} SOL; узнали через {lag} с после блока")
                        elif a in (TREASURY, SPONSOR) and plain_transfer(v.get("logs") or []):
                            for dst, sol in await loop.run_in_executor(None, payee_of, sig, a):
                                if dst not in CREATORS and dst not in (TREASURY, SPONSOR):
                                    CREATORS.add(dst)
                                    save_creators()
                                    await sub_logs(dst)
                                    say(f"НОВЫЙ СОЗДАТЕЛЬ? {dst}: перевод {sol:.1f} SOL с {'казной' if a == TREASURY else 'спонсором'} — слежу")
                    elif msg and msg.get("method") == "accountNotification":
                        kind = subkey.get(msg["params"]["subscription"])
                        if not kind:
                            continue
                        v = kind[1]
                        if v not in VAULT:
                            continue
                        pool, side = VAULT[v]
                        p = POOLS.get(pool)
                        try:
                            amt = float(msg["params"]["result"]["value"]["data"]["parsed"]["info"]["tokenAmount"]["uiAmount"] or 0)
                        except Exception:
                            continue
                        if p and not p.done:
                            p.update(sol=amt) if side == "sol" else p.update(tok=amt)
                    if now - last_tick > 5:
                        last_tick = now
                        for p in list(POOLS.values()):
                            if not p.done and now - p.t0 > POOL_WATCH_S:
                                p.done = True
                                flush(p)
                                for v in (p.sv, p.tv):
                                    VAULT.pop(v, None)
                                    sid = next((s for s, k in subkey.items() if k == ("acct", v)), None)
                                    if sid is not None:
                                        nid[0] += 1
                                        await ws.send(json.dumps({"jsonrpc": "2.0", "id": nid[0], "method": "accountUnsubscribe", "params": [sid]}))
                                POOLS.pop(p.pool, None)
        except Exception as e:
            say(f"обрыв связи: {type(e).__name__} {str(e)[:120]}; переподключаюсь через 5 с")
            await asyncio.sleep(5)


if __name__ == "__main__":
    import socket
    _LOCK = socket.socket()  # 18.11 (user: autostart): one copy; the watchdog sees it alive by this listening port
    try:
        _LOCK.bind(("127.0.0.1", 47667))
        _LOCK.listen(1)
    except OSError:
        sys.exit(0)
    say(f"СТАРТ наблюдателя Solana (без денег). Создателей в списке: {len(CREATORS)}")
    asyncio.run(main())
