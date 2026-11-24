"""13.11 (user, for an outside analyst): every operation of one PancakeSwap V2 pool as a CSV, like BscScan's Txns tab and more.
Read-only, open nodes, no keys. Columns: time (local, to the second), block, position in block, type, wallet, role, BNB, USD, tokens, price,
pool after, gas price, gas used, gas fee, method, nonce, tx; per wallet: its buy number in this pool and its total buys; funding: who sent it BNB
last before its first operation here (creator payments are matched to the buy that follows; others by a binary search of the balance,
possible ~1 h back on the open node).
Usage: python pair_export.py <token> [usd per BNB]. Out: analysis/pair_<SYMBOL>.csv"""
import collections
import csv
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN = Web3.to_checksum_address(sys.argv[1])
USD = float(sys.argv[2]) if len(sys.argv) > 2 else 638.0
W3 = Web3(Web3.HTTPProvider("https://bsc-rpc.publicnode.com", request_kwargs={"timeout": 30}))
W3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
WB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
SW = "0x" + Web3.keccak(text="Swap(address,uint256,uint256,uint256,uint256,address)").hex().replace("0x", "")
SYNC = "0x" + Web3.keccak(text="Sync(uint112,uint112)").hex().replace("0x", "")
METH = {"02751cec": "removeLiquidityETH", "f305d719": "addLiquidityETH", "7ff36ab5": "swapExactETHForTokens", "b6f9de95": "swapExactETHForTokensSupportingFee",
        "791ac947": "swapExactTokensForETHSupportingFee", "18cbafe5": "swapExactTokensForETH", "fb3bdb41": "swapETHForExactTokens"}
OURS = {l.split(";")[1].strip().lower() for l in open(os.path.join(os.path.dirname(HERE), "hop_wallets.txt"), encoding="utf-8") if ";" in l}


def call(f, tries=8):
    for k in range(tries):
        try:
            return f(W3)
        except Exception:
            time.sleep(0.5 + 0.6 * k)
    return None


fac = W3.eth.contract("0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73", abi=[{"name": "getPair", "type": "function", "stateMutability": "view",
                      "inputs": [{"type": "address"}, {"type": "address"}], "outputs": [{"type": "address"}]}])
pair = fac.functions.getPair(TOKEN, WB).call()
wb0 = int(WB, 16) < int(TOKEN, 16)
tok = W3.eth.contract(TOKEN, abi=[{"name": "symbol", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "string"}]},
                                  {"name": "decimals", "type": "function", "stateMutability": "view", "inputs": [], "outputs": [{"type": "uint8"}]}])
sym = call(lambda w: tok.functions.symbol().call()) or TOKEN[:8]
dec = call(lambda w: tok.functions.decimals().call()) or 18
guard = "0x" + call(lambda w: w.eth.get_storage_at(TOKEN, 6)).hex()[-40:]
head = W3.eth.block_number
logs = []
for b in range(head - 12000, head, 2000):  # ~90 min back
    logs += call(lambda w: w.eth.get_logs({"address": pair, "fromBlock": b, "toBlock": min(head, b + 1999), "topics": [[SW, SYNC]]})) or []
logs.sort(key=lambda l: (l["blockNumber"], l["logIndex"]))
if not logs:
    sys.exit("пул не найден за последние ~90 мин")
swaps = {}
for l in logs:
    if l["topics"][0].hex().replace("0x", "") == SW[2:]:
        swaps[l["transactionHash"]] = l
syncs = [l for l in logs if l["topics"][0].hex().replace("0x", "") == SYNC[2:]]
txh = list({l["transactionHash"] for l in syncs})
with ThreadPoolExecutor(8) as ex:
    TX = dict(zip(txh, ex.map(lambda h: call(lambda w: w.eth.get_transaction(h)), txh)))
    RC = dict(zip(txh, ex.map(lambda h: call(lambda w: w.eth.get_transaction_receipt(h)), txh)))
blocks = sorted({l["blockNumber"] for l in syncs})
with ThreadPoolExecutor(8) as ex:
    TS = dict(zip(blocks, ex.map(lambda b: call(lambda w: w.eth.get_block(b))["timestamp"], blocks)))
start = syncs[0]["blockNumber"]
rows, prev = [], None
for l in syncs:
    d = bytes(l["data"])
    r0, r1 = int.from_bytes(d[:32], "big"), int.from_bytes(d[32:64], "big")
    bnb, tkn = (r0, r1) if wb0 else (r1, r0)
    bnb, tkn = bnb / 1e18, tkn / 10 ** dec
    t, rc = TX[l["transactionHash"]], RC[l["transactionHash"]]
    sw = swaps.get(l["transactionHash"])
    tokens = ""
    if sw is not None:
        v = [int.from_bytes(bytes(sw["data"])[i:i + 32], "big") for i in (0, 32, 64, 96)]
        tokens = ((v[3] - v[1]) if wb0 else (v[2] - v[0])) / 10 ** dec
    dv = bnb - prev[0] if prev else bnb
    kind = ("старт" if prev is None else ("слив" if dv < 0 else "ликвидность") if sw is None
            else "слив" if dv < 0 and -dv > 0.5 * prev[0] else "покупка" if dv > 0 else "продажа")
    prev = (bnb, tkn)
    frm = t["from"].lower()
    rows.append(dict(time=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(TS[l["blockNumber"]])), block=l["blockNumber"], age_s=round((l["blockNumber"] - start) * 0.45),
                     idx=t["transactionIndex"], type=kind, wallet=frm, bnb=round(dv, 6), usd=round(dv * USD, 2), tokens=tokens if tokens == "" else round(tokens, 4),
                     price_bnb=f"{bnb / tkn:.12f}" if tkn else "", pool_bnb_after=round(bnb, 4), gas_gwei=round(t["gasPrice"] / 1e9, 4), gas_used=rc["gasUsed"] if rc else "",
                     gas_limit=t["gas"], fee_bnb=f"{(rc['gasUsed'] * t['gasPrice'] / 1e18) if rc else 0:.8f}", method=METH.get(bytes(t["input"])[:4].hex(), bytes(t["input"])[:4].hex()),
                     to=(t["to"] or "").lower(), nonce=t["nonce"], tx=Web3.to_hex(l["transactionHash"])))
creator = rows[0]["wallet"]


def nonce(a, b):
    return call(lambda w: w.eth.get_transaction_count(a, b))


def tx_blocks(a, lo, hi, nlo=None, nhi=None):
    nlo = nonce(a, lo) if nlo is None else nlo
    nhi = nonce(a, hi) if nhi is None else nhi
    if nlo is None or nhi is None or nlo == nhi:
        return []
    if hi - lo == 1:
        return [hi]
    m = (lo + hi) // 2
    nm = nonce(a, m)
    return [] if nm is None else tx_blocks(a, lo, m, nlo, nm) + tx_blocks(a, m, hi, nm, nhi)


cr = Web3.to_checksum_address(creator)
pays = []  # (block, to, bnb)
for b in tx_blocks(cr, start - 600, min(head, rows[-1]["block"] + 200)):
    blk = call(lambda w: w.eth.get_block(b, full_transactions=True))
    for x in blk.transactions if blk else []:
        if x["from"].lower() == creator and x["value"] > 0:
            pays.append((b, (x["to"] or "").lower(), x["value"] / 1e18))


def funder(a, before):
    hi_, lo_ = before, before - 4000
    bh, bl = call(lambda w: w.eth.get_balance(Web3.to_checksum_address(a), hi_)), call(lambda w: w.eth.get_balance(Web3.to_checksum_address(a), lo_))
    if bh is None or bl is None or bh <= bl:
        return ("нет пополнения за ~30 мин" if bh is not None else "история недоступна"), "", ""
    while hi_ - lo_ > 1:
        m = (lo_ + hi_) // 2
        bm = call(lambda w: w.eth.get_balance(Web3.to_checksum_address(a), m))
        if bm is None:
            return "история недоступна", "", ""
        lo_, hi_ = (m, hi_) if bm < bh - (bh - bl) * 0.5 else (lo_, m)
    blk = call(lambda w: w.eth.get_block(hi_, full_transactions=True))
    for x in blk.transactions if blk else []:
        if (x["to"] or "").lower() == a:
            return x["from"].lower(), round(x["value"] / 1e18, 6), before + 1 - hi_
    return "внутренний перевод", "", before + 1 - hi_


buys_of = collections.Counter(r["wallet"] for r in rows if r["type"] == "покупка")
seen = collections.Counter()
first = {}
for r in rows:
    first.setdefault(r["wallet"], r["block"])
need = [a for a in first if a != creator and a not in OURS]
with ThreadPoolExecutor(6) as ex:
    FUND = dict(zip(need, ex.map(lambda a: funder(a, first[a] - 1), need)))
roles = {}
for r in rows:
    a = r["wallet"]
    if r["type"] == "покупка":
        seen[a] += 1
    pay = next(((b, v) for b, to, v in pays if to == a and 0 <= r["block"] - b <= 12), None) if r["type"] == "покупка" else None
    r["role"] = "создатель" if a == creator else "мы" if a in OURS else "служебный (0 BNB)" if r["type"] == "покупка" and abs(r["bnb"]) < 1e-6 else "кошелёк"
    r["buy_no"] = seen[a] if r["type"] == "покупка" else ""
    r["buys_total"] = buys_of[a]
    r["paid_by_creator_bnb"] = round(pay[1], 6) if pay else ""
    r["paid_blocks_before"] = r["block"] - pay[0] if pay else ""
    f = FUND.get(a)
    r["funded_by"], r["funded_bnb"], r["funded_blocks_before_first"] = (f if f else ("", "", ""))
    if f and f[0] == creator:
        r["funded_by"] = "СОЗДАТЕЛЬ " + creator
cols = ["time", "block", "age_s", "idx", "type", "wallet", "role", "bnb", "usd", "tokens", "price_bnb", "pool_bnb_after", "buy_no", "buys_total",
        "paid_by_creator_bnb", "paid_blocks_before", "funded_by", "funded_bnb", "funded_blocks_before_first", "gas_gwei", "gas_used", "gas_limit", "fee_bnb",
        "method", "to", "nonce", "tx"]
out = os.path.join(HERE, f"pair_{''.join(c for c in sym if c.isalnum()) or 'coin'}.csv")
with open(out, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=cols, delimiter=";")
    w.writeheader()
    w.writerows({c: r.get(c, "") for c in cols} for r in rows)
kinds = collections.Counter(r["type"] for r in rows)
print(f"{sym} {TOKEN}, пара {pair}, сторож {guard}, создатель {creator}")
print(f"операций {len(rows)}: {dict(kinds)}; платежей создателя {len(pays)}; оплаченных покупок {sum(1 for r in rows if r['paid_by_creator_bnb'] != '')}")
print(f"файл {out}")
