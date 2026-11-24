"""Finds, on chain, the guard's approve() on our wallets and what our wallet then tried: every transaction from a marked wallet
after its mark with its status. Public data, addresses only. Run: python analysis\\mark_proof.py"""
import contextlib, csv, io, os, sys, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.argv = sys.argv[:1]
with contextlib.redirect_stdout(io.StringIO()):
    import bot as B
    import journal as J
from web3 import Web3

a = Web3(Web3.HTTPProvider("https://bsc-mainnet.nodereal.io/v1/" + J.NODEREAL_KEY, request_kwargs={"timeout": 30}))
a.middleware_onion.inject(B.ExtraDataToPOAMiddleware, layer=0)
OLD_A = "0xfafd8a4e4a494da281ebfdd6f09f9a4345f90cdd"
ours = {OLD_A: "старый A"}
for p, tag in ((r"C:\Users\IT\Desktop\bsc_meme_bot\hop_wallets.txt", "старый W"), (os.path.join(ROOT, "hop_wallets.txt"), "новый W")):
    if os.path.exists(p):
        for l in open(p, encoding="utf-8-sig"):
            x = l.strip().split(";")
            if len(x) >= 2 and x[1].startswith("0x"):
                ours[x[1].lower()] = f"{tag}{x[0]}"
marks = {}
for p in (os.path.join(ROOT, "old_data", "analysis", "guard_marks.csv"), os.path.join(HERE, "guard_marks.csv")):
    for r in csv.DictReader(open(p, encoding="utf-8-sig"), delimiter=";"):
        w = r["wallet"].lower()
        if w in ours and r["method"] == "095ea7b3":
            marks.setdefault((r["token"].lower(), w), r)
print("наших помеченных кошельков в журнале меток:", len(marks))
# only what can have tried to move after a mark: old A, and the wallets where a trade ended "застряли на Wn"
stuck = set()
for p in (os.path.join(ROOT, "old_data", "analysis", "hop_trades.csv"),):
    for r in csv.DictReader(open(p, encoding="utf-8-sig"), delimiter=";"):
        if "застряли на W" in r["reason"]:
            stuck.add((r["token"].lower(), "старый W" + r["reason"].split("застряли на W")[1].split()[0]))
marks = {k: m for k, m in marks.items() if ours[k[1]] == "старый A" or (k[0], ours[k[1]]) in stuck}
print("из них проверяю:", len(marks), [ours[k[1]] for k in marks][:20])


def call(fn, k=4):
    for i in range(k):
        try:
            return fn()
        except Exception:
            if i == k - 1:
                raise
            time.sleep(1)


NAMES = {"0x791ac947": "ПРОДАЖА swapExactTokensForETH", "0xa9059cbb": "transfer", "0x095ea7b3": "approve"}
head = a.eth.block_number
found = []
for (token, w), m in marks.items():
    b = int(m["block"])
    n0, n1 = call(lambda: a.eth.get_transaction_count(Web3.to_checksum_address(w), b)), call(lambda: a.eth.get_transaction_count(Web3.to_checksum_address(w), min(head, b + 2000)))
    if n1 <= n0:
        continue  # nothing was sent after the mark: an empty old holder
    print(f"кандидат {ours[w]} {w[:10]} токен {token[:10]} метка в блоке {b}: транзакций после {n1 - n0}", flush=True)
    for nonce in range(n0, n1):
        lo, hi = b, min(head, b + 2000)
        while lo < hi:  # the block where this nonce was used
            mid = (lo + hi) // 2
            if call(lambda: a.eth.get_transaction_count(Web3.to_checksum_address(w), mid)) > nonce:
                hi = mid
            else:
                lo = mid + 1
        block = call(lambda: a.eth.get_block(lo, full_transactions=True))
        for tx in block.transactions:
            if tx["from"].lower() == w and tx["nonce"] == nonce:
                rc = call(lambda: a.eth.get_transaction_receipt(tx["hash"]))
                sel = bytes(tx["input"])[:4].hex()
                tohex = (tx["to"] or "").lower()
                row = dict(wallet=w, name=ours[w], token=token, mark=m, block=lo, tx="0x" + tx["hash"].hex().replace("0x", ""), status=rc["status"], sel="0x" + sel, to=tohex, gas=rc["gasUsed"])
                found.append(row)
                print(f"   после метки +{lo - b} бл.: {NAMES.get('0x' + sel, '0x' + sel)} статус {rc['status']} → {tohex[:10]} {row['tx']}", flush=True)
                break
fails = [f for f in found if f["status"] == 0]
print(f"\nвсего транзакций после метки {len(found)}, неудачных {len(fails)}")
sells = [f for f in fails if f["sel"] == "0x791ac947"]
print("неудачных продаж:", len(sells))
for f in (sells or fails)[:6]:
    m = f["mark"]
    tx = call(lambda: a.eth.get_transaction_by_block(int(m["block"]), int(m["index"])))
    print(f"\n{f['name']} {f['wallet']}\n токен {f['token']}\n approve сторожа: 0x{tx['hash'].hex().replace('0x', '')} (блок {m['block']}, от {tx['from']})\n неудачная: {f['tx']} (блок {f['block']}, {NAMES.get(f['sel'], f['sel'])}, газ {f['gas']})")
