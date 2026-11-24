"""Why do some attempts of the signal test see no price rise? For every attempt: the bot's router transaction with the paid sum
right after the payment, its token, its status (a revert means our buy may have broken it). Read-only.
Run: python analysis\\idle_check.py [last N attempts]"""
import contextlib, csv, io, os, sys, time
from concurrent.futures import ThreadPoolExecutor
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
N = int(sys.argv[1]) if len(sys.argv) > 1 else 0
sys.argv = sys.argv[:1]
with contextlib.redirect_stdout(io.StringIO()):
    import bot as B
    import journal as J
from web3 import Web3

a = Web3(Web3.HTTPProvider("https://bsc-mainnet.nodereal.io/v1/" + J.NODEREAL_KEY, request_kwargs={"timeout": 30}))
a.middleware_onion.inject(B.ExtraDataToPOAMiddleware, layer=0)
ROUTER = B.ROUTER.lower()


def call(fn, k=5):
    for i in range(k):
        try:
            return fn()
        except Exception:
            if i == k - 1:
                raise
            time.sleep(1 + i)


def look(r):
    if not r["land_block"] or not r["sell_block"]:
        return None
    land, sell, head = int(r["land_block"]), int(r["sell_block"]), int(r["send_head"])
    paid, token = float(r["bnb_paid"]), r["token"].lower()
    found = None
    for n in range(head - 2, land + 40):  # 40 blocks = 18 s
        for tx in call(lambda: a.eth.get_block(n, full_transactions=True))["transactions"]:
            if (tx["to"] or "").lower() != ROUTER or abs(tx["value"] / 1e18 - paid) > 0.0002:
                continue
            data = bytes(tx["input"]).hex()
            same = token[2:] in data
            st = call(lambda: a.eth.get_transaction_receipt(tx["hash"]))["status"]
            found = dict(block=n, same=same, status=st, sel=data[:8], idx=tx["transactionIndex"])
            break
        if found:
            break
    return r, found, land, sell


rows = list(csv.DictReader(open(os.path.join(HERE, "signal_test.csv"), encoding="utf-8-sig"), delimiter=";"))
rows = rows[-N:] if N else rows
with ThreadPoolExecutor(5) as ex:
    res = [x for x in ex.map(look, rows) if x]
print("попытка: монета пул % итог | покупка бота: блок от нашей покупки, та монета?, статус")
g = {}
for r, f, land, sell in res:
    stake = round(float(r["got_bnb"] or 0) - float(r["gas_bnb"]) - float(r["pnl_bnb"]), 4)
    pct = float(r["pnl_pct"])
    if not f:
        what = "нет такой покупки"
    elif not f["same"]:
        what = "другая монета"
    elif not f["status"]:
        what = "ОТКЛОНЕНА"
    elif f["block"] > sell:
        what = "после продажи"
    elif f["block"] >= land:
        what = "после нас, до продажи"
    else:
        what = "раньше нас"
    print(f"{r['time'][11:19]} {r['symbol']:<9} k{r['kind']:<3} {stake} {r['source']:<6} доля {r['share_pct']:>5}% {pct:>6}% | {what}" + (f" (блок {f['block'] - land:+d}, продажа {sell - land:+d}, {f['sel']})" if f else ""))
    g.setdefault(what, []).append((stake, pct))
print()
for what, v in sorted(g.items(), key=lambda x: -len(x[1])):
    print(f"{what}: {len(v)}, в среднем {sum(x[1] for x in v) / len(v):+.1f}%, по ставкам: " + ", ".join(f"{s}: {sum(x[0] == s for x in v)}" for s in sorted({x[0] for x in v})))
