import csv, json, sys
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, sys.argv[2]); from nr import call
D = r"D:\bot"
c = {}
for x in csv.DictReader(open(D + r"\journal_swaps.csv", encoding="utf-8-sig"), delimiter=";"):
    if x["creator"] and x["block"]:
        k = x["creator"].lower(); b = int(x["block"])
        if k not in c or b < c[k][0]: c[k] = (b, x["token"].lower(), x["signal_time"])
def one(item):
    cr, (b0, tok, st) = item
    r = call("nr_getAssetTransfers", [{"category": ["external"], "toAddress": cr, "fromBlock": hex(b0 - 3000), "toBlock": hex(b0), "maxCount": "0x32", "excludeZeroValue": True}])
    tr = [t for t in (r.get("result") or {}).get("transfers", []) if int(t["value"], 16) >= 10**18]
    out = dict(creator=cr, token=tok, signal_time=st, b0=b0, err=str(r.get("error", ""))[:100], funder="", bnb=0, lead_blocks=-1, n_in=len(tr))
    if tr:
        t = max(tr, key=lambda t: int(t["blockNum"], 16))  # last big payment before launch
        out.update(funder=t["from"].lower(), bnb=int(t["value"], 16) / 1e18, lead_blocks=b0 - int(t["blockNum"], 16))
    return out
with ThreadPoolExecutor(5) as ex:
    res = list(ex.map(one, c.items()))
with open(sys.argv[1], "w", encoding="utf-8") as f:
    for r in res: f.write(json.dumps(r) + "\n")
print("всего", len(res), "с кассой", sum(1 for r in res if r["funder"]), "ошибок", sum(1 for r in res if r["err"]))
