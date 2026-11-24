"""Which addresses collect USDT from MANY one-time senders on BSC (exchange sweeps)? 600 recent blocks."""
import collections, sys
from bsc_rpc import *
sys.stdout.reconfigure(encoding="utf-8")
head = int(rpc("eth_blockNumber", []), 16)
snd = collections.defaultdict(set); vol = collections.Counter()
for a in range(head - 600, head, 50):
    for l in rpc("eth_getLogs", [{"address": USDT, "fromBlock": hex(a), "toBlock": hex(a + 49), "topics": [TR]}]):
        to = "0x" + l["topics"][2][-40:]; fr = "0x" + l["topics"][1][-40:]
        snd[to].add(fr); vol[to] += int(l["data"], 16) / 1e18
top = sorted(snd, key=lambda a: -len(snd[a]))[:12]
for a in top: print(a, "отправителей", len(snd[a]), f"USDT {vol[a]:,.0f}")
