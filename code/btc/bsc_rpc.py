import requests, time
NODES = ["https://rpc-bsc.48.club", "https://bsc-rpc.publicnode.com", "https://bsc-dataseed.bnbchain.org"]
S = requests.Session()
def rpc(m, p, tries=6):
    for k in range(tries):
        u = NODES[k % len(NODES)]
        try:
            r = S.post(u, json={"jsonrpc": "2.0", "id": 1, "method": m, "params": p}, timeout=30).json()
            if "result" in r and r["result"] is not None: return r["result"]
        except Exception: pass
        time.sleep(0.3)
    raise RuntimeError(m)
USDT = "0x55d398326f99059ff775485246999027b3197955"; BTCB = "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c"
TR = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
pad = lambda a: "0x" + "0" * 24 + a[2:].lower()
