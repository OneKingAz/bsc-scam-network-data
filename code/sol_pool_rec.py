"""18.11 (user: «закономерность покупок ботов… нумеровать семьи… предварительные покупки»). Records EVERY new «reversed» PumpSwap pool
(base mint = WSOL, the farms' kind) for a later study of families and bot buys. Free public Solana node only (no Helius credits):
  programSubscribe on PumpSwap with filters dataSize 301 + base mint WSOL at offset 43 -> a pool we have not seen;
  getSignaturesForAddress (public HTTP) -> younger than 60 s = new;
  then accountSubscribe on its SOL vault and coin vault for 15 min -> pool SOL and coins after every change (local time).
Out: analysis/sol_pool_rec.jsonl (one line per pool: pool, mint, creator (pool data offset 11), first seen, path [[t, sol, coins]]).
Single instance: port 47668 (listening). Usage: D:\\bot\\.venv\\Scripts\\python.exe -X utf8 analysis\\sol_pool_rec.py"""
import asyncio, base64, json, os, socket, sys, threading, time, urllib.request

import websockets

if sys.stdout:
    sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
WSS, HTTP = "wss://api.mainnet-beta.solana.com", "https://api.mainnet-beta.solana.com"
AMM, WSOL = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA", "So11111111111111111111111111111111111111112"
OUT, LOG = os.path.join(HERE, "sol_pool_rec.jsonl"), os.path.join(HERE, "sol_pool_rec.log")
FOLLOW_S, MAX_AGE = 900, 60
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def say(*a):
    s = time.strftime("%d.%m %H:%M:%S ") + " ".join(str(x) for x in a)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(s + "\n")


def b58(b):
    n = int.from_bytes(b, "big")
    s = ""
    while n:
        n, r = divmod(n, 58)
        s = B58[r] + s
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + s


def http(m, p):
    for k in range(4):
        try:
            r = json.loads(urllib.request.urlopen(urllib.request.Request(HTTP, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": m, "params": p}).encode(),
                                                                         headers={"Content-Type": "application/json"}), timeout=10).read())
            if r.get("result") is not None:
                return r["result"]
        except Exception:
            pass
        time.sleep(0.7 * (k + 1))


SEEN, POOLS, VAULT = set(), {}, {}   # pool -> record; vault -> (pool, "sol"/"tok")
NEW = []                              # pools waiting for their vault subscriptions (filled by threads)
LOCK = threading.Lock()


def check(pool, data, t_seen):
    try:
        sigs = []
        for k in range(6):
            sigs = http("getSignaturesForAddress", [pool, {"limit": 30, "commitment": "confirmed"}]) or []
            if sigs:
                break
            time.sleep(1)
        if not sigs or len(sigs) >= 30:
            return
        bt = min((s.get("blockTime") or 0) for s in sigs)
        if not bt or time.time() - bt > MAX_AGE:
            return
        rec = dict(pool=pool, mint=b58(data[75:107]), creator=b58(data[11:43]), sol_vault=b58(data[139:171]), tok_vault=b58(data[171:203]),
                   created=bt, seen=t_seen, path=[], sol=None, tok=None)
        with LOCK:
            NEW.append(rec)
        say(f"новый пул {pool} создатель {rec['creator']} возраст {time.time() - bt:.0f} с")
    except Exception as e:
        say(f"проверка {pool[:8]}: {e}")


def flush(rec):
    rec.pop("sol", None), rec.pop("tok", None)
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


async def main():
    loop = asyncio.get_running_loop()
    nid = [10]
    while True:
        subs, subkey = {}, {}
        try:
            async with websockets.connect(WSS, ping_interval=20, ping_timeout=30, max_size=2 ** 24) as ws:
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "programSubscribe", "params": [AMM, {
                    "encoding": "base64", "commitment": "processed", "filters": [{"dataSize": 301}, {"memcmp": {"offset": 43, "bytes": WSOL}}]}]}))
                subs[1] = ("prog", None)
                for v in list(VAULT):  # after a reconnect
                    nid[0] += 1
                    subs[nid[0]] = ("acct", v)
                    await ws.send(json.dumps({"jsonrpc": "2.0", "id": nid[0], "method": "accountSubscribe", "params": [v, {"encoding": "jsonParsed", "commitment": "processed"}]}))
                say("подключён")
                last = time.time()
                while True:
                    with LOCK:
                        new, NEW[:] = list(NEW), []
                    for rec in new:
                        POOLS[rec["pool"]] = rec
                        for v, side in ((rec["sol_vault"], "sol"), (rec["tok_vault"], "tok")):
                            VAULT[v] = (rec["pool"], side)
                            nid[0] += 1
                            subs[nid[0]] = ("acct", v)
                            await ws.send(json.dumps({"jsonrpc": "2.0", "id": nid[0], "method": "accountSubscribe", "params": [v, {"encoding": "jsonParsed", "commitment": "processed"}]}))
                    try:
                        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=1))
                    except asyncio.TimeoutError:
                        msg = {}
                    if "id" in msg and "result" in msg and msg["id"] in subs:
                        subkey[msg["result"]] = subs[msg["id"]]
                    elif msg.get("method") == "programNotification":
                        v = msg["params"]["result"]["value"]
                        pool = v["pubkey"]
                        if pool not in SEEN:
                            SEEN.add(pool)
                            loop.run_in_executor(None, check, pool, base64.b64decode(v["account"]["data"][0]), time.time())
                    elif msg.get("method") == "accountNotification":
                        k = subkey.get(msg["params"]["subscription"])
                        if k and k[1] in VAULT:
                            pool, side = VAULT[k[1]]
                            rec = POOLS.get(pool)
                            try:
                                amt = float(msg["params"]["result"]["value"]["data"]["parsed"]["info"]["tokenAmount"]["uiAmount"] or 0)
                            except Exception:
                                amt = None
                            if rec and amt is not None:
                                rec[side] = amt
                                if rec["sol"] is not None and rec["tok"] is not None:
                                    rec["path"].append([round(time.time() - rec["created"], 2), rec["sol"], rec["tok"]])
                    if time.time() - last > 10:
                        last = time.time()
                        for pool, rec in list(POOLS.items()):
                            if time.time() - rec["created"] > FOLLOW_S:
                                flush(rec)
                                POOLS.pop(pool)
                                for v in (rec["sol_vault"], rec["tok_vault"]):
                                    VAULT.pop(v, None)
                                    sid = next((s for s, kk in subkey.items() if kk == ("acct", v)), None)
                                    if sid is not None:
                                        nid[0] += 1
                                        await ws.send(json.dumps({"jsonrpc": "2.0", "id": nid[0], "method": "accountUnsubscribe", "params": [sid]}))
        except Exception as e:
            say(f"обрыв: {type(e).__name__} {str(e)[:80]}")
            await asyncio.sleep(5)


if __name__ == "__main__":
    _lock = socket.socket()
    try:
        _lock.bind(("127.0.0.1", 47668))
        _lock.listen(1)
    except OSError:
        sys.exit(0)
    say("СТАРТ записи пулов")
    asyncio.run(main())
