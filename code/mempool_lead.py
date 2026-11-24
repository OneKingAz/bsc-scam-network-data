"""Idea 2 (05.11), no money, no keys: is a bot buy on the network's coins visible in the public mempool early enough to go
in front of it? Every buy in a live guard pair: seen in the mempool? how long before its block reached us, at what gas, how
deep in the block. Out: analysis\\mempool_lead.csv. It exits with a summary after N buys or MAX_H hours (to wake whoever waits).
Run: python analysis\\mempool_lead.py [N]"""
import contextlib, csv, io, os, statistics as st, sys, threading, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
N = int(sys.argv[1]) if len(sys.argv) > 1 else 60
sys.argv = sys.argv[:1]
MAX_H = 6
with contextlib.redirect_stdout(io.StringIO()):
    import bot as B
from web3 import Web3

w3 = B.w3
OUT = os.path.join(HERE, "mempool_lead.csv")
SWAP = "0x" + Web3.keccak(text="Swap(address,uint256,uint256,uint256,uint256,address)").hex().replace("0x", "")
WBNB = int(B.WBNB, 16)
mp = B.Mempool()
mp.start()
heads, seen_logs, rows = {}, set(), []
t_start = time.time()


def head_loop():
    while True:
        try:
            n = w3.eth.block_number
            if n not in heads:
                heads[n] = time.time()
        except Exception:
            pass
        time.sleep(0.1)


threading.Thread(target=head_loop, daemon=True).start()


def live_pairs():
    now, out = time.time(), {}
    with open(os.path.join(HERE, "guard_launches.csv"), encoding="utf-8-sig") as f:
        for r in csv.DictReader(f, delimiter=";"):
            if now - int(r["ts"]) < 65 * 60:
                out[Web3.to_checksum_address(r["pair"])] = r
    return out


last = w3.eth.block_number
print("замер запущен", flush=True)
while len(rows) < N and time.time() - t_start < MAX_H * 3600:
    time.sleep(1)
    try:
        pairs = live_pairs()
        head = w3.eth.block_number
        if not pairs or head <= last:
            continue
        logs = w3.eth.get_logs({"address": list(pairs), "fromBlock": last + 1, "toBlock": head, "topics": [SWAP]})
        last = head
    except Exception:
        continue
    for l in logs:
        h = l["transactionHash"].hex()
        h = (h if h.startswith("0x") else "0x" + h).lower()
        key = (h, l["logIndex"])
        if key in seen_logs:
            continue
        seen_logs.add(key)
        r = pairs[l["address"]]
        is0 = WBNB < int(r["token"], 16)
        d = bytes(l["data"])
        bnb_in = int.from_bytes(d[0:32] if is0 else d[32:64], "big") / 1e18
        if bnb_in <= 0.0001:
            continue
        t_block = heads.get(l["blockNumber"])
        got = mp.lookup(h)
        lead = round(t_block - got[0], 3) if got and t_block else None
        row = [time.strftime("%H:%M:%S"), r["symbol"], l["blockNumber"], l["transactionIndex"], round(bnb_in, 3), int(bool(got)), lead,
               got[4] if got else "", got[2] if got else "", int(mp.healthy()), h]
        new = not os.path.exists(OUT)
        with open(OUT, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            if new:
                w.writerow(["time", "symbol", "block", "tx_index", "bnb", "in_mempool", "lead_s", "gas_gwei", "to", "mempool_ok", "tx"])
            w.writerow(row)
        rows.append(row)
ok = [r for r in rows if r[9]]
leads = sorted(r[6] for r in ok if r[6] is not None)
print(f"покупок {len(rows)}, при живом мемпуле {len(ok)}, видно в мемпуле {sum(r[5] for r in ok)}, до блока (lead>0) {sum(x > 0 for x in leads)}")
if leads:
    print(f"запас, с: мин {leads[0]}, 25% {leads[len(leads) // 4]}, медиана {st.median(leads)}, 75% {leads[len(leads) * 3 // 4]}, макс {leads[-1]}; ≥0.5 с: {sum(x >= .5 for x in leads)}, ≥1 с: {sum(x >= 1 for x in leads)}")
print("газ видимых, gwei:", sorted(set(r[7] for r in ok if r[5]))[:10], "| позиция в блоке, медиана:", st.median(r[3] for r in rows) if rows else None)
