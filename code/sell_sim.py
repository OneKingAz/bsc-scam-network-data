"""09.11: can an outsider sell a coin of a family? Free check, no money: eth_call with a state override.
A fresh address gets coins written straight into the token's balance mapping (its slot found from a real holder) and an allowance
for the router; then the router's sell is called from it. Read-only. Usage: python sell_sim.py <code_hash10> [minutes back=40]"""
import sys

from eth_abi import encode
from web3 import Web3

sys.stdout.reconfigure(encoding="utf-8")
HASH = sys.argv[1]
MIN = float(sys.argv[2]) if len(sys.argv) > 2 else 40
w3 = Web3(Web3.HTTPProvider("https://bsc-rpc.publicnode.com", request_kwargs={"timeout": 25}))
F = Web3.to_checksum_address("0xca143ce32fe78f1f7019d7d551a6402fc5350c73")
ROUTER = Web3.to_checksum_address("0x10ED43C718714eb63d5aA57B78B54704E256024E")
WB = Web3.to_checksum_address("0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c")
PC = "0x" + Web3.keccak(text="PairCreated(address,address,address,uint256)").hex().replace("0x", "")
TR = "0x" + Web3.keccak(text="Transfer(address,address,uint256)").hex().replace("0x", "")
ERC = [{"name": "balanceOf", "inputs": [{"type": "address"}], "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
       {"name": "allowance", "inputs": [{"type": "address"}, {"type": "address"}], "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
       {"name": "symbol", "inputs": [], "outputs": [{"type": "string"}], "stateMutability": "view", "type": "function"}]
RABI = [{"name": "swapExactTokensForETHSupportingFeeOnTransferTokens", "type": "function", "stateMutability": "nonpayable", "outputs": [],
         "inputs": [{"type": "uint256"}, {"type": "uint256"}, {"type": "address[]"}, {"type": "address"}, {"type": "uint256"}]},
        {"name": "getAmountsOut", "type": "function", "stateMutability": "view", "outputs": [{"type": "uint256[]"}], "inputs": [{"type": "uint256"}, {"type": "address[]"}]}]
router = w3.eth.contract(ROUTER, abi=RABI)
head = w3.eth.block_number
coins = []
for a in range(head - int(MIN * 60 / 0.45), head + 1, 2000):
    for l in w3.eth.get_logs({"address": F, "fromBlock": a, "toBlock": min(head, a + 1999), "topics": [PC]}):
        t0, t1 = Web3.to_checksum_address("0x" + l["topics"][1].hex()[-40:]), Web3.to_checksum_address("0x" + l["topics"][2].hex()[-40:])
        if WB in (t0, t1):
            tok = t1 if t0 == WB else t0
            if Web3.keccak(bytes(w3.eth.get_code(tok))).hex().replace("0x", "")[:10] == HASH:
                coins.append((tok, Web3.to_checksum_address("0x" + bytes(l["data"])[12:32].hex()), l["blockNumber"]))
print(f"монет с кодом {HASH} за {MIN:g} мин: {len(coins)}")


def slot_of(tok, holder, bal):
    for i in range(0, 40):
        for key in (encode(["address", "uint256"], [holder, i]),):
            s = Web3.keccak(key)
            if int.from_bytes(w3.eth.get_storage_at(tok, s), "big") == bal:
                return i
    return None


FAKE = Web3.to_checksum_address("0x00000000000000000000000000000000c0ffee01")
for tok, pair, pb in coins[-3:]:
    t = w3.eth.contract(tok, abi=ERC)
    sym = t.functions.symbol().call()
    holders = {}
    for l in w3.eth.get_logs({"address": tok, "fromBlock": pb, "toBlock": head, "topics": [TR]}):
        to = Web3.to_checksum_address("0x" + l["topics"][2].hex()[-40:])
        if to not in (pair, ROUTER):
            holders[to] = 1
    bal, holder = 0, None
    for h in holders:
        b = t.functions.balanceOf(h).call()
        if b > bal:
            bal, holder = b, h
    if not holder:
        print(f"{sym}: держателей нет")
        continue
    i = slot_of(tok, holder, bal)
    if i is None:
        print(f"{sym}: не нашёл ячейку балансов (держатель {holder[:10]}, баланс {bal}) — запутанный контракт, проверка не получается")
        continue
    amt = bal // 100
    bslot = Web3.keccak(encode(["address", "uint256"], [FAKE, i]))
    over = {tok: {"stateDiff": {"0x" + bslot.hex().replace("0x", ""): "0x" + amt.to_bytes(32, "big").hex()}}}
    # the allowance mapping is usually the next slot: allowance[owner][spender] = keccak(spender, keccak(owner, j))
    for j in [i + 1, i - 1, i + 2] + list(range(0, 12)):
        inner = Web3.keccak(encode(["address", "uint256"], [FAKE, j]))
        aslot = Web3.keccak(encode(["address", "bytes32"], [ROUTER, inner]))
        o = {tok: {"stateDiff": dict(over[tok]["stateDiff"], **{"0x" + aslot.hex().replace("0x", ""): "0x" + (2**255).to_bytes(32, "big").hex()})}}
        try:
            if w3.eth.call({"to": tok, "data": t.encode_abi("allowance", args=[FAKE, ROUTER])}, "latest", o) == (2**255).to_bytes(32, "big"):
                break
        except Exception:
            continue
    else:
        print(f"{sym}: ячейку разрешений не нашёл")
        continue
    quote = router.functions.getAmountsOut(amt, [tok, WB]).call()[1] / 1e18
    data = router.encode_abi("swapExactTokensForETHSupportingFeeOnTransferTokens", args=[amt, 0, [tok, WB], FAKE, 2**40])
    try:
        w3.eth.call({"from": FAKE, "to": ROUTER, "data": data, "gas": 2_000_000}, "latest", o)
        res = "ПРОДАЖА ПРОШЛА бы"
    except Exception as e:
        res = "продажа ОТКЛОНЕНА: " + str(e)[:120]
    print(f"{sym} {tok[:10]}: ячейка балансов {i}, разрешений {j}; продаём 1% держателя (≈{quote:.4f} BNB по котировке): {res}")
    if res.startswith("ПРОДАЖА"):  # the tax: the highest minimum the sale still accepts, against the quote without tax
        lo_, hi_ = 0.0, 1.0
        for _ in range(12):
            mid = (lo_ + hi_) / 2
            d = router.encode_abi("swapExactTokensForETHSupportingFeeOnTransferTokens", args=[amt, int(quote * 1e18 * mid), [tok, WB], FAKE, 2**40])
            try:
                w3.eth.call({"from": FAKE, "to": ROUTER, "data": d, "gas": 2_000_000}, "latest", o)
                lo_ = mid
            except Exception:
                hi_ = mid
        print(f"   получили бы {lo_ * 100:.1f}% от котировки → налог на продажу ≈ {100 - lo_ * 100:.1f}%")
    hb = encode(["address", "uint256"], [holder, i])
    try:  # the same sale from the real holder, for comparison (its own coins, the allowance overridden)
        inner = Web3.keccak(encode(["address", "uint256"], [holder, j]))
        aslot = Web3.keccak(encode(["address", "bytes32"], [ROUTER, inner]))
        o2 = {tok: {"stateDiff": {"0x" + aslot.hex().replace("0x", ""): "0x" + (2**255).to_bytes(32, "big").hex()}}}
        data2 = router.encode_abi("swapExactTokensForETHSupportingFeeOnTransferTokens", args=[amt, 0, [tok, WB], holder, 2**40])
        w3.eth.call({"from": holder, "to": ROUTER, "data": data2, "gas": 2_000_000}, "latest", o2)
        print(f"   для сравнения: та же продажа от их бота {holder[:10]} проходит")
    except Exception as e:
        print(f"   для сравнения: от их бота {holder[:10]} тоже отклонена: {str(e)[:100]}")
