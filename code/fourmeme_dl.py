"""20.11 (user: idea 2 — launchpads whose token code the creator does not write). Downloads every event of the four.meme
manager on BSC (TokenManager2 0x5c952063...) for the last N hours into analysis/fourmeme_logs.jsonl (resumes from the file).
No money, read only. Usage: python fourmeme_dl.py [hours=24]"""
import json, os, sys, time, requests
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "fourmeme_logs.jsonl")
MGR = "0x5c952063c7fc8610ffdb798152d69f0b9550762b"
NODES = ["https://rpc-bsc.48.club", "https://bsc-dataseed1.binance.org", "https://bsc-rpc.publicnode.com"]
S = requests.Session()


def rpc(m, p):
    for k in range(6):
        u = NODES[k % len(NODES)]
        try:
            r = S.post(u, json={"jsonrpc": "2.0", "id": 1, "method": m, "params": p}, timeout=30).json()
            if "result" in r and r["result"] is not None:
                return r["result"]
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError(m)


hours = float(sys.argv[1]) if len(sys.argv) > 1 else 24
head = int(rpc("eth_blockNumber", []), 16)
start = head - int(hours * 3600 / 0.45)
done = start
if os.path.exists(OUT):  # resume: the last block written
    with open(OUT, encoding="utf-8") as f:
        for line in f:
            pass
        try:
            done = max(start, int(json.loads(line)["blockNumber"], 16) + 1)
        except Exception:
            pass
ts_cache = {}
print(f"блоки {done}..{head} ({(head - done) * 0.45 / 3600:.1f} ч)")
n = 0
with open(OUT, "a", encoding="utf-8") as f:
    for a in range(done, head + 1, 200):
        lg = rpc("eth_getLogs", [{"address": MGR, "fromBlock": hex(a), "toBlock": hex(min(a + 199, head))}])
        for l in lg:
            f.write(json.dumps({k: l[k] for k in ("blockNumber", "transactionHash", "logIndex", "topics", "data")}) + "\n")
            n += 1
        if (a - done) % 20000 < 200:
            f.flush()
            print(f"  {a}  событий {n}", flush=True)
print("готово, событий", n)
