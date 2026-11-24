"""05.11: a pasted note says 0x0B705D... pays the bot wallets every ~25 s. Check on chain: does each payment lead to a buy, how
many blocks later, for what amount? NodeReal nr_getAssetTransfers, public data. Run: python analysis\\funder_lead.py [blocks]"""
import collections, contextlib, csv, io, os, statistics as st, sys, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
BLOCKS = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
sys.argv = sys.argv[:1]
with contextlib.redirect_stdout(io.StringIO()):
    import bot as B
    import journal as J
import requests

URL = "https://bsc-mainnet.nodereal.io/v1/" + J.NODEREAL_KEY
DIST = "0x0b705d4b7ed2f27273a8da57e4f20387c5475739"
SPB, ROUTER = 0.45, B.ROUTER.lower()


def transfers(**q):
    out, key = [], None
    for _ in range(6):
        p = dict(category=["external"], maxCount="0x3e8", excludeZeroValue=True, **q)
        if key:
            p["pageKey"] = key
        for i in range(4):
            try:
                r = requests.post(URL, json={"jsonrpc": "2.0", "id": 1, "method": "nr_getAssetTransfers", "params": [p]}, timeout=30).json()
                if "error" in r:
                    raise RuntimeError(str(r["error"])[:120])
                break
            except Exception as e:
                if i == 3:
                    raise
                time.sleep(1 + i)
        res = r.get("result") or {}
        out += res.get("transfers") or []
        key = res.get("pageKey")
        if not key:
            break
    return out


head = B.w3.eth.block_number
pays = transfers(fromAddress=DIST, fromBlock=hex(head - BLOCKS), toBlock=hex(head))
pays = sorted(pays, key=lambda t: int(t["blockNum"], 16))
print(f"платежей от {DIST[:10]} за {BLOCKS * SPB / 60:.0f} мин: {len(pays)}; всего {sum(int(t['value'], 16) for t in pays) / 1e18:.2f} BNB; получателей {len({t['to'] for t in pays})}")
gaps = [(int(b["blockNum"], 16) - int(a["blockNum"], 16)) * SPB for a, b in zip(pays, pays[1:])]
if gaps:
    print(f"интервал, с: медиана {st.median(gaps):.1f}, 10% {st.quantiles(gaps, n=10)[0]:.1f}, 90% {st.quantiles(gaps, n=10)[8]:.1f}")
recv = collections.Counter()
tail = collections.Counter(round(int(t["value"], 16) / 1e18, 7) % 0.001 for t in pays)
leads, ratios, none_, other = [], [], 0, collections.Counter()
rows = []
for t in pays[-80:]:
    b, to, val = int(t["blockNum"], 16), t["to"].lower(), int(t["value"], 16) / 1e18
    nxt = transfers(fromAddress=to, fromBlock=hex(b), toBlock=hex(min(head, b + 400)))
    nxt = sorted((x for x in nxt if int(x["blockNum"], 16) >= b), key=lambda x: int(x["blockNum"], 16))
    if not nxt:
        none_ += 1
        rows.append((to[:10], round(val, 5), None, None, "нет исходящих"))
        continue
    x = nxt[0]
    lead, v2, dest = int(x["blockNum"], 16) - b, int(x["value"], 16) / 1e18, (x.get("to") or "").lower()
    isbuy = dest == ROUTER
    other[("роутер" if isbuy else dest[:10])] += 1
    if isbuy:
        leads.append(lead)
        ratios.append(v2 / val if val else 0)
    rows.append((to[:10], round(val, 5), lead, round(v2, 5), "покупка" if isbuy else dest[:10]))
print("\nпоследние платежи: получатель, сумма, через сколько блоков первая трата, её сумма, куда")
for r in rows[-16:]:
    print("  ", r)
print(f"\nиз {len(rows)} платежей: первая трата — покупка через роутер {len(leads)}, другое {sum(v for k, v in other.items() if k != 'роутер')}, нет трат за 3 мин {none_}")
print("куда уходит первая трата:", other.most_common(6))
if leads:
    s = sorted(leads)
    print(f"от платежа до покупки, блоков: мин {s[0]}, 25% {s[len(s) // 4]}, медиана {s[len(s) // 2]}, 75% {s[len(s) * 3 // 4]}, макс {s[-1]} (с: медиана {s[len(s) // 2] * SPB:.1f})")
    print(f"сумма покупки / сумма платежа: медиана {st.median(ratios):.3f}, мин {min(ratios):.3f}, макс {max(ratios):.3f}")
