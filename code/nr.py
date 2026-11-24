import json, sys, urllib.request
KEY = ""
for l in open(r"D:\bot\.env", encoding="utf-8-sig"):
    if l.strip().startswith("NODEREAL_KEY="): KEY = l.split("=", 1)[1].strip().strip('"\'')
URL = "https://bsc-mainnet.nodereal.io/v1/" + KEY
def call(method, params):
    try:
        req = urllib.request.Request(URL, json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
                                     {"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=30).read())
    except Exception as e:
        return {"error": str(e).replace(KEY, "***") if KEY else str(e)}
if __name__ == "__main__":
    print("ключ найден" if KEY else "ключ НЕ найден", len(KEY))
    cr = "0x475a53bd8d8f8745df5b32fff7feeea122c96cc7"; b0 = 129849961
    r = call("nr_getAssetTransfers", [{"category": ["external"], "toAddress": cr, "fromBlock": hex(b0 - 2000), "toBlock": hex(b0), "maxCount": "0x14", "excludeZeroValue": True}])
    print(json.dumps(r)[:800].replace(KEY, "***") if KEY else json.dumps(r)[:800])
