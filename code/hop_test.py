"""27.10 (user): can tokens of a guard launch (0xbd418ce6 blacklists every new holder ~5-13 s after it gets tokens)
outrun the guard by hopping between fresh wallets faster than it marks them, and be sold from the last wallet?

Real money, small: wallet A buys `stake` BNB of the first guard launch, passes all tokens W1 -> W2 -> ... -> Wn every
`every` seconds, Wn sells at once. Hop wallets come from hop_wallets.env (HOP_KEY_1..100); each gets gas from A first.
Then for 3 minutes it records when the guard marked each wallet (allowance(guard, wallet) > 0).

Run:  python analysis\\hop_test.py [hops=5] [every_s=3] [stake=0.00001] [max_hours=12] [token already trading]
Out:  analysis\\hop_test.csv (one row per step), analysis\\hop_test.log. Keys are never printed."""
import csv, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bot as B
from web3 import Web3

HOPS = int(sys.argv[1]) if len(sys.argv) > 1 else 5
EVERY = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0
STAKE = float(sys.argv[3]) if len(sys.argv) > 3 else 0.00001
MAX_H = float(sys.argv[4]) if len(sys.argv) > 4 else 12
GIVEN = sys.argv[5] if len(sys.argv) > 5 else ""  # a guard token already trading: no waiting, buy at once
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "hop_test.csv")
w3 = B.w3
GUARD = Web3.to_checksum_address(B.GUARD)
TOK_ABI = B.TRANSFER_ABI + [
    {"name": "balanceOf", "type": "function", "stateMutability": "view", "inputs": [{"type": "address"}],
     "outputs": [{"type": "uint256"}]},
    {"name": "allowance", "type": "function", "stateMutability": "view", "inputs": [{"type": "address"}] * 2,
     "outputs": [{"type": "uint256"}]}]


def say(msg):
    line = time.strftime("%H:%M:%S ") + msg
    print(line, flush=True)
    with open(os.path.join(HERE, "hop_test.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def row(step, wallet, tx="", block="", note=""):
    new = not os.path.exists(OUT)
    with open(OUT, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        if new:
            w.writerow(["time", "token", "step", "wallet", "tx", "block", "note"])
        w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), TOKEN, step, wallet, tx, block, note])


def hop_keys(n):
    keys = {}
    for line in open(os.path.join(os.path.dirname(HERE), "hop_wallets.env"), encoding="utf-8"):
        if line.startswith("HOP_KEY_"):
            k, v = line.strip().split("=", 1)
            keys[int(k[8:])] = v
    return [keys[i] for i in range(1, n + 1)]


def wait(snd, h, sec=20):
    t_end = time.time() + sec
    while time.time() < t_end:
        time.sleep(0.15)
        rc = snd.receipt(h)
        if rc:  # the bot's parsed receipt has no block number
            try:
                rc["blockNumber"] = w3.eth.get_transaction_receipt(h)["blockNumber"]
            except Exception:
                rc["blockNumber"] = None
            return rc
    return None


def next_guard_launch():
    """Polls PancakeSwap PairCreated; returns (pair, token, t_seen) of the first WBNB pool >= 8 BNB whose token keeps
    the guard in storage slot 6."""
    last, t_end = w3.eth.block_number, time.time() + MAX_H * 3600
    while time.time() < t_end:
        time.sleep(1)
        try:
            head = w3.eth.block_number
            if head <= last:
                continue
            logs = w3.eth.get_logs({"address": B.FACTORY.address, "topics": [B.PAIR_CREATED_TOPIC],
                                    "fromBlock": last + 1, "toBlock": head})
            last = head
        except Exception:
            continue
        for l in logs:
            r = B.parse_pair_created(l)
            if not r:
                continue
            pair, token = r
            try:
                if w3.eth.get_storage_at(token, 6).hex()[-40:].lower() != B.GUARD[2:]:
                    continue
            except Exception:
                continue
            say(f"запуск со сторожем: токен {token}, пул {pair}")
            return pair, token, time.time()
    return None


A = B.Sender(B.load_private_key())
H = [B.Sender(k) for k in hop_keys(HOPS)]
TOKEN = ""
say(f"тест: {HOPS} кошельков, перекидывание каждые {EVERY} с, ставка {STAKE} BNB; A {A.address}")
for i, h in enumerate(H, 1):
    say(f"  W{i} {h.address}")

# gas for every hop wallet up front: a transfer each, the last one also approve + sell
gp = A.gas_price
for i, h in enumerate(H, 1):
    need = int(((B.CFG["gas_limit_sell"] + 100_000) if i == HOPS else 150_000) * gp * 1.3)
    have = w3.eth.get_balance(h.address)
    if have < need:
        rc = wait(A, A.send(h.address, b"", value=need - have, gas=21_000))
        say(f"  газ на W{i}: {(need - have) / 1e18:.7f} BNB — {'ок' if rc and rc['status'] == 1 else 'НЕ ДОШЁЛ'}")
        if not rc or rc["status"] != 1:
            sys.exit(1)
    h.resync()

if GIVEN:
    tk = Web3.to_checksum_address(GIVEN)
    if w3.eth.get_storage_at(tk, 6).hex()[-40:].lower() != B.GUARD[2:]:
        sys.exit(say("в этом токене нет сторожа"))
    found = (B.FACTORY.functions.getPair(tk, B.WBNB).call(), tk, time.time() - 30)
    say(f"монета уже торгуется: токен {tk}, пул {found[0]}")
else:
    found = next_guard_launch()
if not found:
    sys.exit(say("за отведённое время запусков со сторожем не было"))
pair, token, t_seen = found
TOKEN = token
tok = w3.eth.contract(token, abi=TOK_ABI)
# a wallet the guard already marked on THIS token can receive but never pass the tokens on: leave it out
skip = [i for i, h in enumerate(H, 1) if tok.functions.allowance(GUARD, h.address).call() > 0]
if skip:
    say("уже помечены на этой монете, пропускаю: " + ", ".join(f"W{i}" for i in skip))
    H = [h for i, h in enumerate(H, 1) if i not in skip]
if not H or tok.functions.allowance(GUARD, A.address).call() > 0:
    sys.exit(say("нет чистых кошельков (или A помечен) на этой монете"))
HOPS = len(H)
last = H[-1]
rc = wait(last, last.approve(token))  # the router may take the last wallet's tokens; approve has no blacklist check
row("approve", "W%d" % HOPS, "", rc and rc["blockNumber"], "ok" if rc and rc["status"] == 1 else "FAIL")
time.sleep(max(0.0, 30 - (time.time() - t_seen)))  # the same 30 s entry as the 50.0 history

rc = wait(A, A.buy(token, Web3.to_wei(STAKE, "ether"), 0))
if not rc or rc["status"] != 1:
    row("buy", "A", "", "", "FAIL")
    sys.exit(say("покупка не прошла"))
t0, b0 = time.time(), rc["blockNumber"] or w3.eth.block_number
row("buy", "A", "", b0, f"stake {STAKE}")
say(f"куплено в блоке {b0}")

chain = [A] + H
for i in range(len(chain) - 1):
    frm, to = chain[i], chain[i + 1]
    amt = tok.functions.balanceOf(frm.address).call()
    try:  # dry run first: a marked wallet costs no gas
        tok.functions.transfer(to.address, amt).call({"from": frm.address})
    except Exception:
        row(f"hop {i + 1}", f"W{i}" if i else "A", "", "", "ПОМЕЧЕН до перевода")
        sys.exit(say(f"шаг {i + 1}: кошелёк уже помечен — сторож успел, токены застряли"))
    h = frm.send(token, tok.encode_abi("transfer", args=[to.address, amt]), gas=150_000)
    rc = wait(frm, h)
    ok = rc and rc["status"] == 1
    row(f"hop {i + 1}", f"W{i + 1}", h, rc and rc["blockNumber"], f"{'ok' if ok else 'FAIL'} +{time.time() - t0:.1f} с")
    say(f"шаг {i + 1}: {'A' if not i else 'W%d' % i} -> W{i + 1} {'ок' if ok else 'НЕ ПРОШЁЛ'}, блок {rc and rc['blockNumber']}")
    if not ok:
        sys.exit(1)
    if i < len(chain) - 2:
        time.sleep(max(0.0, EVERY - 1.0))

amt = tok.functions.balanceOf(last.address).call()
data = B.router.encode_abi("swapExactTokensForETHSupportingFeeOnTransferTokens",
                           args=[amt, 0, [token, B.WBNB], last.address, int(time.time()) + 120])
bal0 = w3.eth.get_balance(last.address)
try:
    w3.eth.call({"from": last.address, "to": B.ROUTER, "data": data})
    rc = wait(last, last.sell(token, amt, 0))
    ok = rc and rc["status"] == 1
    got = (w3.eth.get_balance(last.address) - bal0 + (rc["gasUsed"] * rc["effectiveGasPrice"] if rc else 0)) / 1e18
    row("sell", f"W{HOPS}", "", rc and rc["blockNumber"], f"{'ok' if ok else 'FAIL'} {got:.8f} BNB +{time.time() - t0:.1f} с")
    say(f"ПРОДАЖА с W{HOPS}: {'прошла' if ok else 'отклонена'}, получено {got:.8f} BNB из {STAKE} "
        f"({(got / STAKE - 1) * 100:+.0f}% без газа), {time.time() - t0:.1f} с от покупки")
except Exception as e:
    row("sell", f"W{HOPS}", "", "", "проба не прошла " + str(e)[:60])
    say(f"ПРОДАЖА с W{HOPS}: проба не прошла ({str(e)[:60]})")

# when did the guard mark each wallet?
marked, t_end = {}, time.time() + 180
while time.time() < t_end and len(marked) < len(chain):
    for i, s in enumerate(chain):
        if i not in marked and tok.functions.allowance(GUARD, s.address).call() > 0:
            marked[i] = w3.eth.block_number
            name = "A" if not i else f"W{i}"
            row("mark", name, "", marked[i], f"+{marked[i] - b0} блоков от покупки")
            say(f"сторож пометил {name} в блоке {marked[i]} (+{marked[i] - b0} от покупки)")
    time.sleep(1)
say(f"итог: помечено {len(marked)} из {len(chain)} кошельков за 3 мин; подробности в hop_test.csv")
