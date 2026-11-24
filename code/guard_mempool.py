"""27.10: does the guard 0xbd418ce6 (blacklists every new holder of its tokens with approve(holder, MAX)) send its
transactions through the public mempool, and how long before their block do we see them? If yes, a bot could sell
the moment the mark appears, with a higher gas price, before the mark lands. Free: no transactions are sent.

Run: python analysis\\guard_mempool.py [minutes]   -> analysis\\guard_mempool.csv, a summary line every minute.
Mined guard transactions are counted by its nonce; those never seen pending went around the public mempool."""
import csv, os, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bot as B

GUARD = B.GUARD
GUARD_CS = B.Web3.to_checksum_address(GUARD)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "guard_mempool.csv")
minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 120
pending = {}      # hash -> (seen time, to, selector, gas gwei, approve target)
block_seen = {}   # block number -> when we first saw it
lock = threading.Lock()


def on_tx(tx):
    if (tx.get("from") or "").lower() != GUARD:
        return
    data = tx.get("input") or ""
    target = "0x" + data[34:74] if data.startswith("0x095ea7b3") else ""
    with lock:
        pending[tx["hash"].lower()] = (time.time(), (tx.get("to") or "").lower(), data[:10],
                                       int(tx.get("gasPrice") or "0x0", 16) / 1e9, target)


def blocks():
    last = B.w3.eth.block_number
    while True:
        try:
            n = B.w3.eth.block_number
            for b in range(last + 1, n + 1):
                block_seen.setdefault(b, time.time())
            last = max(last, n)
        except Exception:
            pass
        time.sleep(0.1)


mp = B.Mempool()
mp.watchers.append(on_tx)
mp.start()
threading.Thread(target=blocks, daemon=True).start()
nonce0 = B.w3.eth.get_transaction_count(GUARD_CS)
t_end, seen_n, leads, next_sum = time.time() + minutes * 60, 0, [], time.time() + 60
new = not os.path.exists(OUT)
with open(OUT, "a", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f, delimiter=";")
    if new:
        w.writerow(["seen", "tx", "to", "method", "gas_gwei", "approve_to", "block", "lead_ms"])
    while time.time() < t_end:
        time.sleep(1)
        with lock:
            items = list(pending.items())
        for h, (ts, to, sel, gas, target) in items:
            try:
                rc = B.w3.eth.get_transaction_receipt(h)
            except Exception:
                if time.time() - ts > 120:
                    with lock:
                        pending.pop(h, None)
                continue
            bs = block_seen.get(rc["blockNumber"])
            lead = round((bs - ts) * 1000) if bs else ""
            w.writerow([time.strftime("%H:%M:%S", time.localtime(ts)), h, to, sel, gas, target, rc["blockNumber"], lead])
            f.flush()
            seen_n += 1
            if lead != "":
                leads.append(lead)
            with lock:
                pending.pop(h, None)
        if time.time() > next_sum:
            next_sum += 60
            mined = B.w3.eth.get_transaction_count(GUARD_CS) - nonce0
            leads.sort()
            med = leads[len(leads) // 2] if leads else "-"
            print(f"{time.strftime('%H:%M:%S')} сторож: в блоках {mined}, видно в мемпуле {seen_n}, "
                  f"опережение медиана {med} мс, мин {leads[0] if leads else '-'} мс", flush=True)
