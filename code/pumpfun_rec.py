"""21.11 (user: idea 2 — launchpads whose token code the creator does not write): RECORDER of pump.fun (Solana) bonding-curve events.
Read only, no money, no key: the public node's logsSubscribe on the pump.fun program. Every Anchor event in the logs is decoded:
  create  — a new coin (mint, creator, name, symbol)
  trade   — buy / sell on the curve (SOL, tokens, user, virtual and real reserves)
  complete— the curve is full: the coin leaves for the AMM (graduation)
Rows go to analysis/pumpfun_events.csv (append; one file for any number of runs). Lock port 47671 (a second copy exits).
Usage: python pumpfun_rec.py [hours=12]   (Ctrl+C stops)"""
import base64, csv, hashlib, json, os, socket, struct, sys, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "pumpfun_events.csv")
LOG = os.path.join(HERE, "pumpfun_rec.log")
PROG = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
WS = "wss://api.mainnet-beta.solana.com"
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58(b):
    n = int.from_bytes(b, "big")
    s = ""
    while n:
        n, r = divmod(n, 58)
        s = B58[r] + s
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + s


def disc(name):
    return hashlib.sha256(f"event:{name}".encode()).digest()[:8]


D_TRADE, D_CREATE, D_COMPLETE = disc("TradeEvent"), disc("CreateEvent"), disc("CompleteEvent")


def say(m):
    line = time.strftime("%Y-%m-%d %H:%M:%S") + "  " + m
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def rd_str(b, o):
    n = struct.unpack_from("<I", b, o)[0]
    return b[o + 4:o + 4 + n].decode("utf-8", "replace"), o + 4 + n


def decode(raw):
    d, b = raw[:8], raw[8:]
    if d == D_TRADE and len(b) >= 129:
        mint = b58(b[0:32])
        sol, tok = struct.unpack_from("<QQ", b, 32)
        is_buy = b[48] == 1
        user = b58(b[49:81])
        ts, vsol, vtok, rsol, rtok = struct.unpack_from("<qQQQQ", b, 81)
        return ["trade", mint, sol, tok, int(is_buy), user, ts, vsol, vtok, rsol, rtok, "", ""]
    if d == D_CREATE:
        name, o = rd_str(b, 0)
        sym, o = rd_str(b, o)
        _, o = rd_str(b, o)  # uri
        mint, curve, user = b58(b[o:o + 32]), b58(b[o + 32:o + 64]), b58(b[o + 64:o + 96])
        return ["create", mint, 0, 0, 0, user, 0, 0, 0, 0, 0, name[:30].replace(";", ","), sym[:16].replace(";", ",")]
    if d == D_COMPLETE and len(b) >= 104:
        user, mint = b58(b[0:32]), b58(b[32:64])
        ts = struct.unpack_from("<q", b, 96)[0]
        return ["complete", mint, 0, 0, 0, user, ts, 0, 0, 0, 0, "", ""]
    return None


COLS = ["t", "slot", "sig", "kind", "mint", "sol", "tokens", "is_buy", "user", "ts", "vsol", "vtok", "rsol", "rtok", "name", "symbol"]


def main():
    lock = socket.socket()
    try:
        lock.bind(("127.0.0.1", 47671))
        lock.listen(1)
    except OSError:
        print("запись pump.fun уже работает — выхожу")
        return
    hours = float(sys.argv[1]) if len(sys.argv) > 1 else 12
    t_end = time.time() + hours * 3600
    from websockets.sync.client import connect
    new = not os.path.exists(OUT)
    f = open(OUT, "a", newline="", encoding="utf-8")
    w = csv.writer(f, delimiter=";")
    if new:
        w.writerow(COLS)
    stat = {"trade": 0, "create": 0, "complete": 0, "tx": 0}
    last = time.time()
    say(f"=== ЗАПИСЬ PUMP.FUN на {hours} ч → {OUT} ===")
    while time.time() < t_end:
        try:
            with connect(WS, open_timeout=15, max_size=None, ping_interval=20) as ws:
                ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "logsSubscribe",
                                    "params": [{"mentions": [PROG]}, {"commitment": "confirmed"}]}))
                say("подключён к публичному узлу Solana")
                while time.time() < t_end:
                    try:
                        msg = json.loads(ws.recv(timeout=30))
                    except TimeoutError:
                        raise RuntimeError("узел молчит 30 с")
                    v = ((msg.get("params") or {}).get("result") or {}).get("value")
                    if not v or v.get("err"):
                        continue
                    slot = msg["params"]["result"]["context"]["slot"]
                    stat["tx"] += 1
                    for line in v.get("logs") or []:
                        if not line.startswith("Program data: "):
                            continue
                        try:
                            row = decode(base64.b64decode(line[14:]))
                        except Exception:
                            row = None
                        if row:
                            stat[row[0]] += 1
                            w.writerow([round(time.time(), 2), slot, v["signature"]] + row)
                    if time.time() - last > 60:
                        f.flush()
                        say(f"  транзакций {stat['tx']}, сделок {stat['trade']}, новых монет {stat['create']}, выходов с кривой {stat['complete']}")
                        last = time.time()
        except Exception as e:
            say(f"обрыв: {str(e)[:80]} — переподключаюсь")
            time.sleep(3)
    f.close()
    say(f"=== КОНЕЦ: {stat} ===")


if __name__ == "__main__":
    main()
