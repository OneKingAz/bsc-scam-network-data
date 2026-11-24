"""How does the guard wallet (slot 6 of the HELL template) pick whom to blacklist? For every journal token whose slot 6 is
the guard: every address that received tokens (from the pool = a buy, or from another wallet = a transfer), whether the
guard marked it (Approval with owner = guard), how fast, and who the address is (tx count: network bots have ~29k)."""
import csv, os, sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hist as H
from web3 import Web3

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
GUARD = "bd418ce6a83995971de9f1cf9f61f345e91aeadd"
T = "0x" + Web3.keccak(text="Transfer(address,address,uint256)").hex().removeprefix("0x")
AP = "0x8c5be1e5ebec7d5bd14f714f4f5ec7c46ab3db174da78c3f62f10b71e9aeeaa0"
FACTORY = "0xca143ce32fe78f1f7019d7d551a6402fc5350c73"
WBNB = "bb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
ME = "fafd8a4e4a494da281ebfdd6f09f9a4345f90cdd"

top = int(H.rpc("eth_blockNumber", []), 16)
seen, toks = set(), []
for x in csv.DictReader(open(os.path.join(ROOT, "journal_signals.csv"), encoding="utf-8-sig"), delimiter=";"):
    t = x["token"].lower()
    if x["time"] >= "2026-10-26 18:00" and t not in seen:
        seen.add(t)
        toks.append((x["symbol"], t))
nonce_cache = {}


def nonce(a):
    if a not in nonce_cache:
        nonce_cache[a] = int(H.rpc("eth_getTransactionCount", ["0x" + a, "latest"]), 16)
    return nonce_cache[a]


rows = []
for sym, tok in toks:
    if H.rpc("eth_getStorageAt", [tok, "0x6", "latest"])[-40:] != GUARD:
        continue
    pair = H.rpc("eth_call", [{"to": FACTORY, "data": "0xe6a43905" + tok[2:].rjust(64, "0") + WBNB.rjust(64, "0")}, "latest"])[-40:]
    tr, mk = [], {}
    for a in range(top - 40000, top, 5000):
        b = min(a + 4999, top)
        tr += H.rpc("eth_getLogs", [{"address": tok, "topics": [T], "fromBlock": hex(a), "toBlock": hex(b)}])
        for l in H.rpc("eth_getLogs", [{"address": tok, "topics": [AP, "0x" + GUARD.rjust(64, "0")], "fromBlock": hex(a), "toBlock": hex(b)}]):
            mk.setdefault(l["topics"][2][-40:], int(l["blockNumber"], 16))
    first = {}
    for l in tr:
        frm, to = l["topics"][1][-40:], l["topics"][2][-40:]
        if to in (pair, "0" * 40) or to in first:
            continue
        first[to] = (int(l["blockNumber"], 16), "покупка из пула" if frm == pair else ("от создателя/минт" if int(frm, 16) == 0 else "перевод от кошелька"))
    for a, (blk, how) in first.items():
        n = nonce(a)
        who = "МЫ" if a == ME else ("бот сети" if n > 5000 else "посторонний")
        rows.append(dict(sym=sym, addr=a, how=how, who=who, nonce=n, marked=a in mk, delay=(mk[a] - blk) if a in mk else None))
    print(f"{sym}: получателей {len(first)}, помечено {len(mk)}", flush=True)

print("\nкто | как получил | всего | помечено | задержка, блоков")
g = defaultdict(list)
for r in rows:
    g[(r["who"], r["how"])].append(r)
for (who, how), v in sorted(g.items()):
    d = sorted(r["delay"] for r in v if r["delay"] is not None)
    print(f"{who:12} | {how:20} | {len(v):4} | {sum(r['marked'] for r in v):3} | {d}")
print("\nпомеченные:")
for r in rows:
    if r["marked"]:
        print(f"  {r['sym']:10} 0x{r['addr'][:10]} {r['who']:11} {r['how']:20} транзакций {r['nonce']:6} через {r['delay']} блоков")
print("\nнепомеченные посторонние:")
for r in rows:
    if not r["marked"] and r["who"] != "бот сети":
        print(f"  {r['sym']:10} 0x{r['addr'][:10]} {r['who']:11} {r['how']:20} транзакций {r['nonce']}")
