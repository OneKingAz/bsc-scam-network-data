"""05.11: tiny payments to the distributor 0x0B705D... from addresses that look like its real recipients: a signal or address
poisoning? Incoming transfers in the named blocks; each sender against the real recipients (start and end of the address).
Public data. Run: python analysis\\dust_check.py"""
import contextlib, io, os, sys, time
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.argv = sys.argv[:1]
with contextlib.redirect_stdout(io.StringIO()):
    import bot as B
    import journal as J
import requests
from web3 import Web3

URL = "https://bsc-mainnet.nodereal.io/v1/" + J.NODEREAL_KEY
D = "0x0b705d4b7ed2f27273a8da57e4f20387c5475739"


def q(**p):
    for i in range(4):
        try:
            r = requests.post(URL, json={"jsonrpc": "2.0", "id": 1, "method": "nr_getAssetTransfers", "params": [dict(category=["external"], maxCount="0x3e8", excludeZeroValue=True, **p)]}, timeout=30).json()
            return (r.get("result") or {}).get("transfers") or []
        except Exception:
            time.sleep(1 + i)
    return []


real = {t["to"].lower(): int(t["value"], 16) / 1e18 for t in q(fromAddress=D, fromBlock=hex(131690000), toBlock=hex(131705000)) if t.get("to")}
print("настоящих получателей:", len(real))
for blk in (131696998, 131697422):
    inc = q(toAddress=D, fromBlock=hex(blk - 2), toBlock=hex(blk + 2))
    print(f"\nблок ~{blk}: входящих {len(inc)}")
    for t in inc:
        frm, v = t["from"].lower(), int(t["value"], 16) / 1e18
        twin = next((r for r in real if r[:6] == frm[:6] and r[-4:] == frm[-4:] and r != frm), None)
        near = twin or next((r for r in real if r[:6] == frm[:6] and r != frm), None)
        print(f"  {int(t['blockNum'], 16)} {frm} {v:.8f} BNB | похож на настоящего: {near} {'(начало и конец совпадают)' if twin else ''}")
all_in = q(toAddress=D, fromBlock=hex(131680000), toBlock=hex(131705000))
small = [t for t in all_in if int(t["value"], 16) < 10**13]
print(f"\nвсего входящих {len(all_in)}, мелких меньше 0.00001 BNB {len(small)}")
same = sum(1 for t in small if any(r[:6] == t["from"].lower()[:6] and r[-4:] == t["from"].lower()[-4:] for r in real))
print(f"из мелких совпадают с настоящими получателями по началу и концу адреса: {same}")
one = small[0]["from"] if small else None
if one:
    a = Web3.to_checksum_address(one)
    print("пример отправителя:", a, "транзакций", B.w3.eth.get_transaction_count(a), "байт кода", len(B.w3.eth.get_code(a)))
    print("откуда у него деньги:", [(t["from"][:12], int(t["value"], 16) / 1e18) for t in q(toAddress=one, fromBlock=hex(131600000), toBlock=hex(131705000))][:4])
