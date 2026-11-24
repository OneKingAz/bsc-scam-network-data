"""How fast does a transaction reach a block through each node? Wallet A sends itself 0 BNB through one node at a time. Real gas: 21000 x 0.06 gwei
per transaction, about 0.00004 BNB for all of them. Only while the panel is idle (it uses A too). Started by the user:
    python analysis\\send_speed.py"""
import contextlib, io, json, os, statistics as st, sys, time, urllib.request
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.argv = sys.argv[:1]
with contextlib.redirect_stdout(io.StringIO()):
    import bot as B
from web3 import Web3
import requests

N, GWEI, PAUSE = 6, 0.06, 2.0
state = json.load(urllib.request.urlopen("http://127.0.0.1:8773/api/state", timeout=10))["state"]
if state["chain"] or state["running"] or state["manual"] or state["phase"] in ("Покупаю", "Продаю"):
    sys.exit("Панель занята. Сначала дождитесь конца теста или нажмите «Стоп», потом запустите снова.")
print(f"переводов: {N} на узел, расход примерно {N * 21000 * GWEI / 1e9 * 5:.6f} BNB. Начинаю через 5 с.")
time.sleep(5)
ends = {f"рабочий{i + 1}": u for i, u in enumerate(dict.fromkeys(B.CFG["send_rpc_urls"]))}  # the addresses are not printed
ends.update({"publicnode": "https://bsc-rpc.publicnode.com", "48club": "https://rpc-bsc.48.club", "defibit": "https://bsc-dataseed1.defibit.io"})
w3 = B.w3
acct = w3.eth.account.from_key(B.load_private_key())
heads = lambda: w3.eth.block_number
nonce = w3.eth.get_transaction_count(acct.address, "pending")
print("узел         попало в блок через, блоков (каждая отправка) | медиана | ответ узла на отправку, мс")
for name, url in ends.items():
    lags, posts = [], []
    s = requests.Session()
    for _ in range(N):
        tx = dict(to=acct.address, value=0, gas=21000, gasPrice=int(GWEI * 1e9), nonce=nonce, chainId=56)
        raw = acct.sign_transaction(tx).raw_transaction
        head, t = heads(), time.time()
        try:
            r = s.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "eth_sendRawTransaction", "params": ["0x" + raw.hex().removeprefix("0x")]}, timeout=5).json()
        except Exception as e:
            print(f"{name}: не отправилось: {str(e)[:60]}")
            break
        posts.append((time.time() - t) * 1000)
        if "result" not in r:
            print(f"{name}: узел отказал: {str(r.get('error'))[:80]}")
            break
        nonce += 1
        h = r["result"]
        for _w in range(60):
            try:
                rc = w3.eth.get_transaction_receipt(h)
                if rc:
                    lags.append(rc["blockNumber"] - head)
                    break
            except Exception:
                pass
            time.sleep(0.25)
        else:
            lags.append(99)
        time.sleep(PAUSE)
    if lags:
        print(f"{name:<12} {lags} | {st.median(lags)} | {st.median(posts):.0f}")
print("Готово. Пришлите этот текст.")
