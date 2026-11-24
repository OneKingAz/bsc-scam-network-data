"""13.11 (outside analyst's questions): every signal trade of the networks 3/4 (signal_test3.csv, signal_test4.csv), checked on chain.
For each: the creator's payment to the bot (block, index), our buy (block, index), the bot's buy (block, index, status), our place relative
to the bot, and why the row was or was not in the +1/+2/+3 table. Read-only, open nodes. Out: sig34_audit.csv + text."""
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
URLS = ["https://bsc-rpc.publicnode.com", "https://bsc-dataseed.bnbchain.org", "https://rpc.48.club"]
WS = []
for u in URLS:
    w = Web3(Web3.HTTPProvider(u, request_kwargs={"timeout": 30}))
    w.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    WS.append(w)
ROUTER = "0x10ed43c718714eb63d5aa57b78b54704e256024e"
OURS = {l.split(";")[1].strip().lower() for l in open(os.path.join(os.path.dirname(HERE), "hop_wallets.txt"), encoding="utf-8") if ";" in l}
OURS.add("0xb5dc73324b34482f9d3a4610c5c3fb9fb9d5d053")
BLK = {}


def blk(b):
    if b in BLK:
        return BLK[b]
    for k in range(8):
        try:
            BLK[b] = WS[k % len(WS)].eth.get_block(b, full_transactions=True)
            return BLK[b]
        except Exception:
            time.sleep(0.5 + 0.4 * k)
    return None


def rc(h):
    for k in range(8):
        try:
            return WS[k % len(WS)].eth.get_transaction_receipt(h)
        except Exception:
            time.sleep(0.5 + 0.4 * k)
    return None


rows = []
for net, fn in ((3, "signal_test3.csv"), (4, "signal_test4.csv")):
    for r in csv.DictReader(open(os.path.join(HERE, fn), encoding="utf-8-sig"), delimiter=";"):
        r["net"] = net
        rows.append(r)


def audit(r):
    out = dict(net=r["net"], time=r["time"], symbol=r["symbol"], source=r["source"], file_pay_block=r["pay_block"], file_land=r["land_block"],
               pnl_pct=r["pnl_pct"], note=r["note"])
    try:
        paid = float(r["bnb_paid"] or 0)
        land = int(r["land_block"]) if r["land_block"] else None
        head = int(r["send_head"]) if r["send_head"] else land
        tok = r["token"].lower()[2:]
        if not head:
            out["why"] = "покупка не отправлена / не попала"
            return out
        # the payment: a plain BNB transfer of bnb_paid (to 5 decimals) a few blocks around the send
        pay = None
        for b in range(head - 4, (land or head) + 2):
            B = blk(b)
            for x in B.transactions if B else []:
                if x["value"] and abs(x["value"] / 1e18 - paid) < 6e-6 and not bytes(x["input"]):
                    pay = (b, x["transactionIndex"], x["to"].lower(), x["from"].lower())
                    break
            if pay:
                break
        if not pay:
            out["why"] = "платёж боту не найден в блоках"
            return out
        out.update(pay_block=pay[0], pay_idx=pay[1], bot=pay[2], creator=pay[3])
        ours = bot_tx = None
        for b in range(pay[0], pay[0] + 12):
            B = blk(b)
            for x in B.transactions if B else []:
                to, frm, inp = (x["to"] or "").lower(), x["from"].lower(), bytes(x["input"]).hex()
                if to == ROUTER and tok in inp:
                    if frm in OURS and ours is None and x["value"] > 0:
                        ours = (b, x["transactionIndex"], Web3.to_hex(x["hash"]))
                    if frm == pay[2] and bot_tx is None:
                        bot_tx = (b, x["transactionIndex"], Web3.to_hex(x["hash"]))
        if ours:
            R = rc(ours[2])
            out.update(our_block=ours[0], our_idx=ours[1], our_status=R["status"] if R else "", our_vs_pay=ours[0] - pay[0])
        if bot_tx:
            R = rc(bot_tx[2])
            out.update(bot_block=bot_tx[0], bot_idx=bot_tx[1], bot_status=R["status"] if R else "", bot_vs_pay=bot_tx[0] - pay[0], bot_tx=bot_tx[2])
        if ours and bot_tx:
            out["order"] = "мы раньше бота" if (ours[0], ours[1]) < (bot_tx[0], bot_tx[1]) else "мы после бота"
            out["same_block"] = int(ours[0] == bot_tx[0])
        out["why"] = ("нет нашей покупки" if not ours else "бот не покупал" if not bot_tx else "")
    except Exception as e:
        out["why"] = "ошибка: " + str(e)[:60]
    return out


with ThreadPoolExecutor(6) as ex:
    res = list(ex.map(audit, rows))
cols = ["net", "time", "symbol", "source", "file_pay_block", "file_land", "pay_block", "pay_idx", "creator", "bot", "our_block", "our_idx", "our_status", "our_vs_pay",
        "bot_block", "bot_idx", "bot_status", "bot_vs_pay", "order", "same_block", "pnl_pct", "note", "why", "bot_tx"]
with open(os.path.join(HERE, "sig34_audit.csv"), "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=cols, delimiter=";")
    w.writeheader()
    w.writerows({c: r.get(c, "") for c in cols} for r in res)
L = [f"сделок в файлах: {len(res)}"]
L.append("без полной картины: " + str(collections.Counter(r.get("why") for r in res if r.get("why"))))
full = [r for r in res if not r.get("why")]
L.append(f"с платежом, нашей покупкой и покупкой бота: {len(full)}")
for k in sorted({r["our_vs_pay"] for r in full}):
    g = [r for r in full if r["our_vs_pay"] == k]
    ok = [r for r in g if r["bot_status"] == 1]
    pn = [float(r["pnl_pct"]) for r in g if r["pnl_pct"]]
    L.append(f"\nмы попали +{k} от платежа: {len(g)} сделок, покупка бота прошла {len(ok)}, отклонена {len(g) - len(ok)}; средний итог {sum(pn) / max(1, len(pn)):+.2f}%")
    for order in ("мы раньше бота", "мы после бота"):
        gg = [r for r in g if r["order"] == order]
        if gg:
            L.append(f"   {order}: {len(gg)} (в одном блоке с ботом {sum(r['same_block'] for r in gg)}), бот прошёл {sum(1 for r in gg if r['bot_status'] == 1)}, "
                     f"итог {sum(float(r['pnl_pct']) for r in gg if r['pnl_pct']) / len(gg):+.2f}%")
    bv = collections.Counter(r["bot_vs_pay"] for r in g)
    L.append(f"   бот попал от платежа: {dict(sorted(bv.items()))}")
open(os.path.join(HERE, "sig34_audit.txt"), "w", encoding="utf-8").write("\n".join(L))
print("\n".join(L))
