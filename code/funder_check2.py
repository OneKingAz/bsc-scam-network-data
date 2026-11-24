"""05.11: who pays each bot right before its buy? For the buys in mempool_lead.csv and catch-test coins: the last payment to the
buyer before the buy, from whom, how many blocks ahead, is the amount the buy + gas. Is the payer one wallet per coin, and is it the
coin's creator? Run: python analysis\\funder_check2.py"""
import collections, contextlib, csv, io, os, statistics as st, sys, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.argv = sys.argv[:1]
with contextlib.redirect_stdout(io.StringIO()):
    import bot as B
    import journal as J
import requests

URL = "https://bsc-mainnet.nodereal.io/v1/" + J.NODEREAL_KEY
w3 = B.w3
creators = {}
for r in csv.DictReader(open(os.path.join(os.path.dirname(HERE), "journal_swaps.csv"), encoding="utf-8-sig"), delimiter=";"):
    if r.get("creator"):
        creators[r["symbol"]] = r["creator"].lower()


def incoming(to, b0, b1):
    for i in range(4):
        try:
            r = requests.post(URL, json={"jsonrpc": "2.0", "id": 1, "method": "nr_getAssetTransfers", "params": [dict(category=["external"], toAddress=to, fromBlock=hex(b0), toBlock=hex(b1), maxCount="0x14", excludeZeroValue=True)]}, timeout=30).json()
            return (r.get("result") or {}).get("transfers") or []
        except Exception:
            time.sleep(1 + i)
    return []


rows = list(csv.DictReader(open(os.path.join(HERE, "mempool_lead.csv"), encoding="utf-8-sig"), delimiter=";"))
rows = rows[-45:]
by = collections.defaultdict(collections.Counter)
leads, ratios, nopay = [], [], 0
for r in rows:
    try:
        tx = w3.eth.get_transaction(r["tx"])
    except Exception:
        continue
    who, b, val = tx["from"].lower(), tx["blockNumber"], tx["value"] / 1e18
    pays = [t for t in incoming(who, b - 60, b) if int(t["blockNum"], 16) <= b]
    if not pays:
        nopay += 1
        by[r["symbol"]]["(нет платежа за 60 бл.)"] += 1
        continue
    t = max(pays, key=lambda t: int(t["blockNum"], 16))
    pb, pv = int(t["blockNum"], 16), int(t["value"], 16) / 1e18
    leads.append(b - pb)
    if pv:
        ratios.append(val / pv)
    by[r["symbol"]][t["from"].lower()] += 1
print(f"покупок {len(rows)}: платёж перед покупкой найден {len(leads)}, нет {nopay}")
if leads:
    s = sorted(leads)
    print(f"от платежа до покупки, блоков: мин {s[0]}, 25% {s[len(s) // 4]}, медиана {s[len(s) // 2]}, 75% {s[len(s) * 3 // 4]}, макс {s[-1]}; доля ≥3 блоков {sum(x >= 3 for x in s) * 100 // len(s)}%, ≥4 блоков {sum(x >= 4 for x in s) * 100 // len(s)}%")
    print(f"покупка / платёж: медиана {st.median(ratios):.4f}, мин {min(ratios):.4f}, макс {max(ratios):.4f}")
for sym, c in by.items():
    print(f"{sym}: создатель {creators.get(sym, '?')[:12]}; кто платит:", [(k[:12], n) for k, n in c.most_common(5)])
