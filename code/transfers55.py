"""All incoming BNB transfers (>= 0.05) to the creators of 55.02 / 40.02 launches in history, 3000 blocks before to
100 after the launch: does a second payment tell early pulls apart? -> transfers55.jsonl"""
import json, sys, time
sys.path.insert(0, ".")
import hist as H
rows = [json.loads(l) for l in open("hist_launches.jsonl", encoding="utf-8")]
out = open("transfers55.jsonl", "w", encoding="utf-8")
for x in rows:
    if x["series"] not in ("55.02", "40.02") or not x["creator"]:
        continue
    r = H.rpc("nr_getAssetTransfers", [{"category": ["external"], "toAddress": x["creator"], "fromBlock": hex(x["block"] - 3000),
                                        "toBlock": hex(x["block"] + 100), "maxCount": "0x64"}], pause=1.8)
    tr = [(int(t["blockNum"], 16) - x["block"], round(int(t["value"], 16) / 1e18, 4), t["from"].lower())
          for t in (r or {}).get("transfers", []) if int(t["value"], 16) >= 5 * 10**16]
    out.write(json.dumps(dict(series=x["series"], time=x["time"], rug_s=x["rug_s"], bnb0=x["bnb0"], funder=x["funder"], tr=tr)) + "\n")
    out.flush()
print("готово")
