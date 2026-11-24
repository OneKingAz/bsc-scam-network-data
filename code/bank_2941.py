"""07.11 (user: does family 2941 pay?). Read-only. The wallet 0x950f0340 pays a bot the sum of its buy 3-6 blocks before the buy.
What would we get buying on that payment and selling right after the bot's buy? Paper, by the pool's own Sync prices:
entry at the end of block pay+2 (seen in the block) or pay+1 (seen in the mempool), exit at the end of the bot's buy block,
minus 0.5 % swap fees and the gas at a $30 stake. One NodeReal call (the payer's transfers), the rest through a public node.
Run: python bank_2941.py [hours]"""
import collections
import statistics as st
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"D:\bot\analysis")
import nr
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 6
PAYER = "0x950f0340b133b276be5855622f34149b79a3a9f3"
w3 = Web3(Web3.HTTPProvider("https://rpc.48.club", request_kwargs={"timeout": 15}))
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
T = lambda s: "0x" + Web3.keccak(text=s).hex().replace("0x", "")
SWAP, SYNC = T("Swap(address,uint256,uint256,uint256,uint256,address)"), T("Sync(uint112,uint112)")
WBNB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
STAKE_BNB, GAS = 0.043, 0.00002
head = w3.eth.block_number
lo = head - int(HOURS * 3600 / 0.45)
r = nr.call("nr_getAssetTransfers", [dict(category=["external"], fromAddress=PAYER, fromBlock=hex(lo), toBlock=hex(head), maxCount="0x3e8", excludeZeroValue=True, order="desc")])
if r.get("error"):
    sys.exit("NodeReal: " + str(r["error"])[:150])
pays = [(int(t["blockNum"], 16), t["to"].lower(), int(t["value"], 16) / 1e18) for t in (r.get("result") or {}).get("transfers", [])]
print(f"платежей кошелька за {HOURS:g} ч: {len(pays)}", flush=True)
token0, syncs = {}, {}


def pair_info(pair):
    if pair not in token0:
        c = w3.eth.contract(pair, abi=[{"name": "token0", "inputs": [], "outputs": [{"type": "address"}], "stateMutability": "view", "type": "function"}])
        token0[pair] = c.functions.token0().call().lower() == WBNB
    return token0[pair]


def price_at(pair, block):
    """BNB per token at the end of `block` from the pool's Syncs (cached per pair in 2000-block pieces)."""
    piece = block // 2000
    key = (pair, piece)
    if key not in syncs:
        logs = w3.eth.get_logs({"address": pair, "fromBlock": piece * 2000, "toBlock": piece * 2000 + 1999, "topics": [SYNC]})
        wb0 = pair_info(pair)
        syncs[key] = [(l["blockNumber"], int.from_bytes(bytes(l["data"])[:32], "big"), int.from_bytes(bytes(l["data"])[32:64], "big")) for l in logs]
        syncs[key] = [(b, (r0 if wb0 else r1) / 1e18, (r1 if wb0 else r0)) for b, r0, r1 in syncs[key]]
    s = [x for x in syncs[key] if x[0] <= block]
    if not s:
        return None, None
    b, bnb, tok = s[-1]
    return (bnb / tok if tok else None), bnb


rows = []
for i, (pb, buyer, val) in enumerate(pays):
    try:
        logs = w3.eth.get_logs({"fromBlock": pb + 1, "toBlock": pb + 12, "topics": [SWAP, None, "0x" + "0" * 24 + buyer[2:]]})
    except Exception as e:
        continue
    buy = None
    for l in logs:
        pair = Web3.to_checksum_address(l["address"])
        d = bytes(l["data"])
        try:
            wb0 = pair_info(pair)
        except Exception:
            continue
        bnb_in = int.from_bytes(d[0:32] if wb0 else d[32:64], "big") / 1e18
        if bnb_in > 0:
            buy = (l["blockNumber"], pair, bnb_in)
            break
    if not buy:
        rows.append(dict(lag=None))
        continue
    bb, pair, bnb_in = buy
    try:
        p_pay, pool = price_at(pair, pb)
        out = dict(lag=bb - pb, pair=pair, share=bnb_in / pool * 100 if pool else None, ratio=val / bnb_in)
        p_exit, _ = price_at(pair, bb)
        for name, land in (("блок", pb + 2), ("мемпул", pb + 1)):
            p_in, _ = price_at(pair, land)
            out[name] = ((p_exit / p_in - 1) * 100 - 0.5 - GAS / STAKE_BNB * 100) if land < bb and p_in and p_exit else None
        rows.append(out)
    except Exception:
        rows.append(dict(lag=None))
    if i % 50 == 0:
        print(f"  {i}/{len(pays)}", flush=True)
ok = [x for x in rows if x.get("lag") is not None]
print(f"\nс найденной покупкой: {len(ok)} из {len(pays)}; лаг платёж→покупка: {collections.Counter(x['lag'] for x in ok).most_common(6)}")
print(f"монет (пулов): {len({x['pair'] for x in ok})}; доля покупки от пула медиана {st.median(x['share'] for x in ok if x['share']):.2f}%; платёж/покупка медиана {st.median(x['ratio'] for x in ok):.4f}")
for name in ("блок", "мемпул"):
    v = [x[name] for x in ok if x.get(name) is not None]
    if not v:
        print(name, "нет попыток")
        continue
    srt = sorted(v)
    print(f"{name}: попыток {len(v)}, в плюсе {sum(a > 0 for a in v)}, среднее {st.mean(v):+.2f}%, медиана {st.median(v):+.2f}%, худшие {[round(a, 1) for a in srt[:3]]}, лучшие {[round(a, 1) for a in srt[-3:]]}")
    for th in (0.5, 1, 2):
        u = [x[name] for x in ok if x.get(name) is not None and x["share"] and x["share"] >= th]
        if u:
            print(f"   только покупки от {th}% пула: {len(u)}, среднее {st.mean(u):+.2f}%, в плюсе {sum(a > 0 for a in u)}")
print("готово")
