"""06.11 (user: why did the main network stop?): read-only. Pauses in the launch history, balances of its desk and guard,
where the money went after the last coin, and what the two networks share (bots, funding)."""
import collections
import csv
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import nr
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

w3 = Web3(Web3.HTTPProvider(nr.URL, request_kwargs={"timeout": 30}))
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
SPB = 0.45
SWAP = "0x" + Web3.keccak(text="Swap(address,uint256,uint256,uint256,uint256,address)").hex().replace("0x", "")
head = w3.eth.block_number
now = w3.eth.get_block(head).timestamp
DESK1, DESK2 = "0xde8eb937cb5475eee5ac96dce6ba2d18e439c473", "0xf51dd7340c90bf1683bb32d6397ed3acefb6b6a7"
GUARD1, GUARD2 = "0xbd418ce6a83995971de9f1cf9f61f345e91aeadd", "0xdbbf08242c22a634960ceaad44d9f2564fb26452"
fmt = lambda t: time.strftime("%d.%m %H:%M", time.localtime(t))


def transfers(**k):
    r = nr.call("nr_getAssetTransfers", [dict(category=["external"], fromBlock=hex(head - 3_000_000), toBlock=hex(head), maxCount="0x64", excludeZeroValue=True, order="desc", **k)])
    if r.get("error"):
        print("  ошибка:", str(r["error"])[:120])
    return (r.get("result") or {}).get("transfers", [])


print("--- паузы основной сети (guard_launches.csv) ---")
rows = sorted({float(r["ts"]): r for r in csv.DictReader(open(os.path.join(HERE, "guard_launches.csv"), encoding="utf-8-sig"), delimiter=";")}.items())
ts = [t for t, _ in rows]
print("запусков", len(ts), "с", fmt(ts[0]), "по", fmt(ts[-1]), "| молчит сейчас", round((now - ts[-1]) / 3600, 1), "ч")
gaps = [(b - a, a, b) for a, b in zip(ts, ts[1:])]
med = sorted(g[0] for g in gaps)[len(gaps) // 2] / 60
print(f"обычный интервал (медиана) {med:.0f} мин; паузы дольше 2 ч:")
for g, a, b in sorted(g for g in gaps if g[0] > 7200)[::-1][:20]:
    print(f"  {g / 3600:5.1f} ч: {fmt(a)} → {fmt(b)}  (следующая {rows[ts.index(b)][1]['symbol']})")
by_day = collections.Counter(time.strftime("%d.%m", time.localtime(t)) for t in ts)
print("запусков по дням:", dict(sorted(by_day.items(), key=lambda kv: kv[0][3:] + kv[0][:2])[-14:]))

print("\n--- деньги кассы и сторожа ---")
for name, a in (("касса 1", DESK1), ("касса 2", DESK2), ("сторож 1", GUARD1), ("сторож 2", GUARD2)):
    A = Web3.to_checksum_address(a)
    print(f"{name} {a[:10]}: баланс {w3.eth.get_balance(A) / 1e18:.4f} BNB, nonce {w3.eth.get_transaction_count(A)}")
for name, a in (("касса 1", DESK1), ("касса 2", DESK2)):
    print(name, "последние исходящие:")
    for t in transfers(fromAddress=a)[:6]:
        print(f"   блок {int(t['blockNum'], 16)} (−{(head - int(t['blockNum'], 16)) * SPB / 3600:.1f} ч) → {t['to'][:12]} {int(t['value'], 16) / 1e18:.3f} BNB")
    print(name, "последние входящие:")
    for t in transfers(toAddress=a)[:6]:
        print(f"   блок {int(t['blockNum'], 16)} (−{(head - int(t['blockNum'], 16)) * SPB / 3600:.1f} ч) ← {t['from'][:12]} {int(t['value'], 16) / 1e18:.3f} BNB")
for name, a in (("сторож 1", GUARD1), ("сторож 2", GUARD2)):
    print(name, "входящие:", [(t["from"][:12], round(int(t["value"], 16) / 1e18, 3), f"−{(head - int(t['blockNum'], 16)) * SPB / 3600:.0f} ч") for t in transfers(toAddress=a)[:5]])

print("\n--- последняя монета INO: куда ушли деньги создателя ---")
cr = "0x70fcfaad4623fc7348801a3001e98b60167e59d8"
print("баланс создателя", w3.eth.get_balance(Web3.to_checksum_address(cr)) / 1e18)
for t in transfers(fromAddress=cr)[:8]:
    print(f"   −{(head - int(t['blockNum'], 16)) * SPB / 3600:.1f} ч → {t['to'][:12]} {int(t['value'], 16) / 1e18:.3f} BNB")

print("\n--- общие боты ---")


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
for t, r in rows[-4:]:
    lo = head - int((now - t) / SPB) - 100
    b = buyers(r["pair"], r["token"], lo, min(head, lo + 7000))
    main.update(b.keys())
    print(f"осн {r['symbol']}: покупателей {len(b)}", flush=True)
for r in csv.DictReader(open(os.path.join(HERE, "guard2_today.csv"), encoding="utf-8-sig"), delimiter=";"):
    if r.get("life_min") and float(r["life_min"]) > 3:
        b = buyers(r["pair"], r["token"], int(r["b_launch"]), int(r["b_drain"]))
        second.update(b.keys())
print(f"покупателей: основная {len(main)}, вторая {len(second)}, общих {len(set(main) & set(second))}")
for a in sorted(set(main) & set(second), key=lambda x: -(main[x] + second[x]))[:10]:
    print(f"  {a} монет: осн {main[a]}, 2-я {second[a]}")
print("готово")
