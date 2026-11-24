"""24.11: record BTC perpetual liquidations (forced orders) and the price every second from the public Bybit stream (no key).
(The Binance futures stream fstream.binance.com hangs from this provider; the spot one works.) A liquidation is a forced market
order: the closest BTC analogue of «a bot that must buy». Later: does price continue or bounce after big bursts?
-> liq_events.csv (ms, side of the forced order, BTC, price), btc_px.csv (ms, last price). Lock port 47673."""
import json, time, csv, socket, sys
from websockets.sync.client import connect
lock = socket.socket()
try: lock.bind(("127.0.0.1", 47673)); lock.listen(1)
except OSError: sys.exit("уже работает")
fl, fp = open("liq_events.csv", "a", newline=""), open("btc_px.csv", "a", newline="")
wl, wp = csv.writer(fl), csv.writer(fp); last = 0
while True:
    try:
        with connect("wss://stream.bybit.com/v5/public/linear", open_timeout=15, ping_interval=20) as ws:
            ws.send(json.dumps({"op": "subscribe", "args": ["allLiquidation.BTCUSDT", "publicTrade.BTCUSDT"]}))
            while True:
                m = json.loads(ws.recv(timeout=60))
                tp = m.get("topic", "")
                if tp.startswith("allLiquidation"):
                    for d in m["data"]: wl.writerow([d["T"], d["S"], d["v"], d["p"]])
                    fl.flush()
                elif tp.startswith("publicTrade"):
                    d = m["data"][-1]
                    if d["T"] - last >= 1000: wp.writerow([d["T"], d["p"]]); fp.flush(); last = d["T"]
    except Exception:
        time.sleep(3)
