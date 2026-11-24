"""08.11 (user's checklist): the paid bots of networks 3/4 whose buys failed after ours. Read-only, open nodes.
For each our trade since SINCE: the bot's tx (value = the payment, to the router, within 4 blocks after our buy): status, to, method,
amountOutMin, deadline vs block time, revert reason (eth_call replay at the block before, if the node keeps that state),
pool reserves before our buy and before the bot's tx (Sync logs), what the bot would get at both moments, gas it lost.
Usage: python bot_revert.py ["2026-11-08 19:49"]"""
import csv
import sys

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

sys.stdout.reconfigure(encoding="utf-8")
SINCE = sys.argv[1] if len(sys.argv) > 1 else "2026-11-08 19:49"
w3 = Web3(Web3.HTTPProvider("https://bsc-rpc.publicnode.com", request_kwargs={"timeout": 20}))
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
W48 = Web3(Web3.HTTPProvider("https://rpc.48.club", request_kwargs={"timeout": 20}))
W48.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
ROUTER = "0x10ED43C718714eb63d5aA57B78B54704E256024E"
WB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
F = Web3.to_checksum_address("0xca143ce32fe78f1f7019d7d551a6402fc5350c73")
SYNC = "0x" + Web3.keccak(text="Sync(uint112,uint112)").hex().replace("0x", "")
RABI = [{"name": n, "type": "function", "stateMutability": "payable", "outputs": [],
         "inputs": [{"name": "amountOutMin", "type": "uint256"}, {"name": "path", "type": "address[]"}, {"name": "to", "type": "address"}, {"name": "deadline", "type": "uint256"}]}
        for n in ("swapExactETHForTokens", "swapExactETHForTokensSupportingFeeOnTransferTokens")]
RABI.append({"name": "swapETHForExactTokens", "type": "function", "stateMutability": "payable", "outputs": [],
             "inputs": [{"name": "amountOut", "type": "uint256"}, {"name": "path", "type": "address[]"}, {"name": "to", "type": "address"}, {"name": "deadline", "type": "uint256"}]})
router = w3.eth.contract(ROUTER, abi=RABI)
fc = w3.eth.contract(F, abi=[{"name": "getPair", "inputs": [{"type": "address"}, {"type": "address"}], "outputs": [{"type": "address"}], "stateMutability": "view", "type": "function"}])
T0 = [{"name": "token0", "inputs": [], "outputs": [{"type": "address"}], "stateMutability": "view", "type": "function"}]


def out_amount(bnb_in, r_bnb, r_tok):
    a = bnb_in * 9975
    return a * r_tok // (r_bnb * 10000 + a)


def reserves_before(pair, wb0, block, idx):
    """Reserves of the pair just before tx #idx of `block`: the last Sync before it."""
    logs = w3.eth.get_logs({"address": pair, "fromBlock": block - 400, "toBlock": block, "topics": [SYNC]})
    logs = [l for l in logs if (l["blockNumber"], l["transactionIndex"]) < (block, idx)]
    d = bytes(logs[-1]["data"])
    r0, r1 = int.from_bytes(d[:32], "big"), int.from_bytes(d[32:64], "big")
    return (r0, r1) if wb0 else (r1, r0)  # (bnb, token)


OUT = []
rows = []
for n in (3, 4):
    for r in csv.DictReader(open(rf"D:\bot\analysis\signal_test{n}.csv", encoding="utf-8-sig"), delimiter=";"):
        if r["time"] >= SINCE and r["land_block"]:
            rows.append((n, r))
for n, r in rows:
    tok = Web3.to_checksum_address(r["token"])
    pair = fc.functions.getPair(tok, Web3.to_checksum_address(WB)).call()
    wb0 = w3.eth.contract(pair, abi=T0).functions.token0().call().lower() == WB
    land, paid = int(r["land_block"]), float(r["bnb_paid"])
    print(f"\nсеть {n} {r['time'][11:16]} {r['symbol']}: платёж {paid} BNB, наша покупка в блоке {land}, итог {r['pnl_pct']}%")
    found = None
    for bn in range(land, land + 5):
        blk = w3.eth.get_block(bn, full_transactions=True)
        for t in blk.transactions:
            if t["to"] and t["to"].lower() == ROUTER.lower() and t["value"] and abs(t["value"] / 1e18 - paid) <= 0.002 * paid + 0.0001:
                found = (t, blk)
                break
        if found:
            break
    if not found:
        print("   транзакции бота на сумму платежа нет (бот не прислал покупку)")
        continue
    t, blk = found
    rc = w3.eth.get_transaction_receipt(t["hash"])
    fn, args = router.decode_function_input(t["input"])
    mn = args.get("amountOutMin", args.get("amountOut"))
    our = w3.eth.get_transaction_receipt(Web3.to_hex(bytes.fromhex(r["tx"].replace("0x", "")))) if r.get("tx") else None
    our_idx = None
    # our buy: the first swap tx of our wallet in the land block is not stored; take reserves before the land block's first Sync instead
    rb0, rt0 = reserves_before(pair, wb0, land, 0)  # before anything in our block (≈ when the bot was paid)
    rb1, rt1 = reserves_before(pair, wb0, blk["number"], t["transactionIndex"])  # right before the bot's tx
    exp0, exp1 = out_amount(t["value"], rb0, rt0), out_amount(t["value"], rb1, rt1)
    reason = ""
    try:
        W48.eth.call({"from": t["from"], "to": t["to"], "data": t["input"], "value": t["value"], "gas": t["gas"]}, blk["number"] - 1)
        reason = "повтор на блоке до: прошла бы"
    except Exception as e:
        reason = str(e)[:90]
    print(f"   бот {t['from'][:10]}: tx {Web3.to_hex(t['hash'])[:14]}… блок {blk['number']} (+{blk['number'] - land}), статус {rc['status']}")
    print(f"   кому {t['to'][:10]} (роутер PancakeSwap), операция {fn.fn_name}, путь {len(args['path'])} шага")
    print(f"   deadline {args['deadline']} vs время блока {blk['timestamp']}: {'истёк' if args['deadline'] < blk['timestamp'] else 'не истёк'} (запас {args['deadline'] - blk['timestamp']} с)")
    print(f"   цена в пуле: BNB {rb0 / 1e18:.4f} → {rb1 / 1e18:.4f} (+{(rb1 / rb0 - 1) * 100:.3f}%)")
    print(f"   бот получил бы: до нас {exp0 / 1e18:,.2f}, перед его tx {exp1 / 1e18:,.2f}; минимум бота (amountOutMin) {mn / 1e18:,.2f}")
    print(f"   минимум = {mn / exp0 * 100:.3f}% от цены до нас; после нас он получил бы {exp1 / exp0 * 100:.3f}%  → допуск бота {100 - mn / exp0 * 100:.3f}%")
    print(f"   причина: {reason}")
    print(f"   газ бота: {rc['gasUsed']} × {t['gasPrice'] / 1e9:.3f} gwei = {rc['gasUsed'] * t['gasPrice'] / 1e18:.7f} BNB")
    OUT.append(dict(net=n, time=r["time"], symbol=r["symbol"], pnl_pct=r["pnl_pct"], our_block=land, bot_tx=Web3.to_hex(t["hash"]), bot_block=blk["number"],
                    bot_index=t["transactionIndex"], status=rc["status"], gas_used=rc["gasUsed"], amount_in_wei=t["value"], amount_out_min=mn,
                    reserve_bnb_before=rb1, reserve_token_before=rt1, get_amounts_out=exp1, reserve_bnb_before_us=rb0, get_amounts_out_before_us=exp0,
                    min_vs_out_pct=round(mn / exp1 * 100, 4), deadline=args["deadline"], block_time=blk["timestamp"]))


import atexit


def _save():
    if OUT:
        with open(r"D:\bot\analysis\bot_revert.csv", "w", newline="", encoding="utf-8-sig") as f:
            wr = csv.DictWriter(f, fieldnames=list(OUT[0]), delimiter=";")
            wr.writeheader()
            wr.writerows(OUT)
        print(f"\nзаписано {len(OUT)} строк в bot_revert.csv")


_save()
