"""06.11 (user: "maybe it is the same thing"): read-only comparison of the main network's coins and the second network's.
Bytecode (selectors, opcodes, embedded addresses, storage slots), shared buying bots, who funds the guards and the desks."""
import collections
import csv
import difflib
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import nr
from web3 import Web3

w3 = Web3(Web3.HTTPProvider(nr.URL, request_kwargs={"timeout": 30}))
SPB = 0.45
SWAP = "0x" + Web3.keccak(text="Swap(address,uint256,uint256,uint256,uint256,address)").hex().replace("0x", "")
COINS = {
    "осн INO": "0x1805c04887DB6E03AC71589Bc84246F149969B5e",
    "осн TESLASI": "0x416f51a7C772DbfB1034311d6ae94529C09741CE",
    "осн ANT": "0x92B352dbF21f3b2cF6630f0C15FA175120Ee0468",
    "2-я DJIA": "0xA364388eea09a2800eafF3cd1Fa7F30115f74285",
    "2-я L1X": "0xc9aF30bC5005A7880A8d551927510847ab648cc2",
    "2-я SIRIUS": "0xF7cFd935dc58B002eABc5d4fDD48AF00006E87c6",
    "2-я HPEB": "0x60311dF296053AC20f9D36c12c6e1C67c69D96ea",
    "a70b 牛来": "0xd6743e36fe655685dd3BAea93A031d165e8fed2b",
    "623e DJIA": "0xE4f6203182ab90ae945dEf51E0111d5009Ca0192",
}
NAMES = {"06fdde03": "name", "95d89b41": "symbol", "313ce567": "decimals", "18160ddd": "totalSupply", "70a08231": "balanceOf", "dd62ed3e": "allowance",
         "095ea7b3": "approve", "a9059cbb": "transfer", "23b872dd": "transferFrom", "8da5cb5b": "owner", "715018a6": "renounceOwnership", "f2fde38b": "transferOwnership"}


def parse(code):
    ops, pushes, i = [], [], 0
    while i < len(code):
        op = code[i]
        if 0x60 <= op <= 0x7f:
            n = op - 0x5f
            pushes.append(code[i + 1:i + 1 + n])
            ops.append(op)
            i += n + 1
        else:
            ops.append(op)
            i += 1
    return ops, pushes


codes = {}
for name, a in COINS.items():
    code = bytes(w3.eth.get_code(Web3.to_checksum_address(a)))
    ops, pushes = parse(code)
    sel = sorted({p.hex() for p in pushes if len(p) == 4})
    addrs = sorted({p.hex() for p in pushes if len(p) == 20})
    long_ = [p.hex()[:16] for p in pushes if len(p) >= 21]
    h = Web3.keccak(code).hex()[:12]
    codes[name] = dict(code=code, ops=ops, sel=sel)
    print(f"\n{name} {a}\n  размер {len(code)}, хеш {h}, опкодов {len(ops)}")
    print("  функции:", ", ".join(NAMES.get(s, s) for s in sel))
    print("  адреса в коде:", addrs or "нет", "| длинные константы:", len(long_))
    print("  DELEGATECALL", ops.count(0xf4), "CALL", ops.count(0xf1), "STATICCALL", ops.count(0xfa), "SHA3", ops.count(0x20), "SSTORE", ops.count(0x55), "SLOAD", ops.count(0x54), "CREATE", ops.count(0xf0))
    print("  конец кода:", code[-50:].hex())
    tok = Web3.to_checksum_address(a)
    print("  слоты:", ", ".join(f"{k}={'…' + v[-12:] if int(v, 16) else '0'}" for k, v in ((k, bytes(w3.eth.get_storage_at(tok, k)).hex()) for k in range(0, 11))))
print("\n--- похожесть кода (последовательность опкодов, 1 = одинаково) ---")
names = list(codes)
for i, x in enumerate(names):
    print(f"{x:<14}", " ".join(f"{difflib.SequenceMatcher(None, codes[x]['ops'], codes[y]['ops'], autojunk=False).ratio():.2f}" for y in names))
print("колонки:", names)

print("\n--- общие боты ---")
head = w3.eth.block_number
now = w3.eth.get_block(head).timestamp


def buyers(pair, token, lo, hi):
    wb0 = int("0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c", 16) < int(token, 16)
    out = collections.Counter()
    for b in range(lo, hi + 1, 2000):
        for lg in w3.eth.get_logs({"address": Web3.to_checksum_address(pair), "fromBlock": b, "toBlock": min(hi, b + 1999), "topics": [SWAP]}):
            d = bytes(lg["data"])
            if int.from_bytes(d[0:32] if wb0 else d[32:64], "big"):
                out[w3.eth.get_transaction(lg["transactionHash"])["from"].lower()] += 1
    return out


main, second = collections.Counter(), collections.Counter()
for r in list(csv.DictReader(open(os.path.join(HERE, "guard_launches.csv"), encoding="utf-8-sig"), delimiter=";"))[-5:]:
    lo = head - int((now - float(r["ts"])) / SPB) - 100
    b = buyers(r["pair"], r["token"], lo, min(head, lo + 7000))
    main.update(b.keys())
    print(f"осн {r['symbol']}: покупателей {len(b)}", flush=True)
for r in csv.DictReader(open(os.path.join(HERE, "guard2_today.csv"), encoding="utf-8-sig"), delimiter=";"):
    if r.get("life_min") and float(r["life_min"]) > 3:
        b = buyers(r["pair"], r["token"], int(r["b_launch"]), int(r["b_drain"]))
        second.update(b.keys())
        print(f"2-я {r['token'][:10]}: покупателей {len(b)}", flush=True)
both = set(main) & set(second)
print(f"покупателей: основная {len(main)}, вторая {len(second)}, общих {len(both)}")
for a in sorted(both, key=lambda x: -(main[x] + second[x]))[:12]:
    print(f"  {a} монет: осн {main[a]}, 2-я {second[a]}")

print("\n--- кто пополняет ---")
FUND = {"сторож 1": "0xbd418ce6a83995971de9f1cf9f61f345e91aeadd", "сторож 2": "0xdbbf08242c22a634960ceaad44d9f2564fb26452",
        "касса 1": "0xde8eb937cb5475eee5ac96dce6ba2d18e439c473", "касса 2": "0xf51dd7340c90bf1683bb32d6397ed3acefb6b6a7"}
seen = {}
for name, a in FUND.items():
    r = nr.call("nr_getAssetTransfers", [{"category": ["external"], "toAddress": a, "fromBlock": hex(head - 2_000_000), "toBlock": hex(head), "maxCount": "0x64", "excludeZeroValue": True, "order": "desc"}])
    tr = (r.get("result") or {}).get("transfers", [])
    c = collections.Counter()
    for t in tr:
        c[t["from"].lower()] += int(t["value"], 16) / 1e18
    seen[name] = set(c)
    print(name, a[:10], "входящих", len(tr), "ошибка" if r.get("error") else "", [(k[:10], round(v, 2)) for k, v in c.most_common(5)], str(r.get("error") or "")[:80], flush=True)
keys = list(seen)
for i, x in enumerate(keys):
    for y in keys[i + 1:]:
        o = seen[x] & seen[y]
        if o:
            print(f"общие источники {x} и {y}:", [k[:10] for k in o])
print("\nслужебные адреса, которых метят оба сторожа: 0x8737a10d…, 0x426603ba…")
print("готово")
