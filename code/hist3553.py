"""09.11 (user): the family 3553 (code 1f6f5a665a, pools of 58 BNB) over the last day: pool at start, growth by minute, when it is drained,
and a paper trade "buy at 20 s, sell at minute T" (a drain before T = the whole stake lost). Read-only, open nodes.
Out: hist3553.csv, hist3553.txt. Usage: python hist3553.py [hours=24] [hash=1f6f5a665a]"""
import csv
import itertools
import os
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from web3 import Web3

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
H = float(sys.argv[1]) if len(sys.argv) > 1 else 24
HASH = sys.argv[2] if len(sys.argv) > 2 else "1f6f5a665a"
URLS = ["https://bsc-rpc.publicnode.com", "https://rpc.48.club", "https://bsc-dataseed.bnbchain.org", "https://bsc-dataseed1.defibit.io"]
W3S = [Web3(Web3.HTTPProvider(u, request_kwargs={"timeout": 25})) for u in URLS]
RR, LOCK = itertools.count(), threading.Lock()
SPB = 0.45
F = Web3.to_checksum_address("0xca143ce32fe78f1f7019d7d551a6402fc5350c73")
WB = "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c"
PC = "0x" + Web3.keccak(text="PairCreated(address,address,address,uint256)").hex().replace("0x", "")
SYNC = "0x" + Web3.keccak(text="Sync(uint112,uint112)").hex().replace("0x", "")


def call(f, logs=False, tries=8):
    for k in range(tries):
        with LOCK:
            i = next(RR) % (1 if logs else len(W3S))  # logs: publicnode only (the dataseeds refuse wide get_logs)
        try:
            return f(W3S[i])
        except Exception:
            time.sleep(0.5 + k * 0.8)
    return None


head = call(lambda w: w.eth.block_number)
lo = head - int(H * 3600 / SPB)
pairs = []
for b in range(lo, head + 1, 2000):
    for l in call(lambda w: w.eth.get_logs({"address": F, "fromBlock": b, "toBlock": min(head, b + 1999), "topics": [PC]}), logs=True) or []:
        t0, t1 = "0x" + l["topics"][1].hex()[-40:], "0x" + l["topics"][2].hex()[-40:]
        if WB in (t0.lower(), t1.lower()):
            pairs.append(dict(pair=Web3.to_checksum_address("0x" + bytes(l["data"])[12:32].hex()), token=Web3.to_checksum_address(t1 if t0.lower() == WB else t0),
                              wb0=t0.lower() == WB, block=l["blockNumber"]))
print(f"пулов с BNB за {H:g} ч: {len(pairs)}", flush=True)


def is_family(p):
    c = call(lambda w: bytes(w.eth.get_code(p["token"])))
    return bool(c) and Web3.keccak(c).hex().replace("0x", "")[:10] == HASH


with ThreadPoolExecutor(8) as ex:
    fam = [p for p, ok in zip(pairs, ex.map(is_family, pairs)) if ok]
print(f"монет семьи {HASH}: {len(fam)}", flush=True)

MINS = [5, 10, 15, 18, 20, 22, 24, 25, 26, 27, 28]
out = []


def study(p):
    end = min(head, p["block"] + int(45 * 60 / SPB))
    logs = []
    for a in range(p["block"], end + 1, 2000):
        logs += call(lambda w: w.eth.get_logs({"address": p["pair"], "fromBlock": a, "toBlock": min(end, a + 1999), "topics": [SYNC]}), logs=True) or []
    if not logs:
        return None

    def res(l):
        d = bytes(l["data"])
        r0, r1 = int.from_bytes(d[:32], "big"), int.from_bytes(d[32:64], "big")
        return (r0, r1) if p["wb0"] else (r1, r0)

    def at(s):
        ls = [x for x in logs if x["blockNumber"] <= p["block"] + s / SPB]
        return res(ls[-1]) if ls else None

    start = res(logs[0])[0] / 1e18
    peak_l = max(logs, key=lambda l: res(l)[0])
    peak = res(peak_l)[0] / 1e18
    drain = next(((l["blockNumber"] - p["block"]) * SPB for l in logs if res(l)[0] / 1e18 < 0.2 * peak and l["blockNumber"] > peak_l["blockNumber"] - 1), None)
    if drain is None and end < p["block"] + int(40 * 60 / SPB):
        return None  # too young to know
    rbE, rtE = at(20)
    st = 0.01e18
    tk = st * 0.9975 * rtE / (rbE + st * 0.9975)
    row = dict(time=time.strftime("%d.%m %H:%M", time.localtime(time.time() - (head - p["block"]) * SPB)), pair=p["pair"], start=round(start, 2),
               peak=round(peak, 2), peak_min=round((peak_l["blockNumber"] - p["block"]) * SPB / 60, 1), drain_min=round(drain / 60, 1) if drain else "")
    for m in MINS:
        if drain is not None and drain <= m * 60:
            row[f"m{m}"] = -100.0
            continue
        rb, rt = at(m * 60)
        row[f"m{m}"] = round((tk * 0.9975 * rb / (rt + tk * 0.9975) / 1e18 / 0.01 - 1) * 100, 2)
    return row


with ThreadPoolExecutor(4) as ex:
    out = [r for r in ex.map(study, fam) if r]
out.sort(key=lambda r: r["time"])
with open(os.path.join(HERE, "hist3553.csv"), "w", newline="", encoding="utf-8-sig") as f:
    if out:
        w = csv.DictWriter(f, fieldnames=list(out[0]), delimiter=";")
        w.writeheader()
        w.writerows(out)
lines = [f"семья {HASH} за {H:g} ч: монет {len(fam)}, разобрано {len(out)}"]
starts = [r["start"] for r in out]
dr = [r["drain_min"] for r in out if r["drain_min"] != ""]
if out:
    lines.append(f"стартовый пул: мин {min(starts)}, медиана {statistics.median(starts)}, макс {max(starts)} BNB")
if dr:
    q = sorted(dr)
    lines.append(f"слив (мин): самый ранний {q[0]}, 10% {q[len(q) // 10]}, медиана {statistics.median(q)}, самый поздний {q[-1]}; не слиты {len(out) - len(dr)}")
    lines.append("ранние сливы (<25 мин): " + ", ".join(f"{r['time']} {r['drain_min']}" for r in out if r["drain_min"] != "" and r["drain_min"] < 25))
for m in MINS:
    v = [r[f"m{m}"] for r in out]
    if v:
        lost = sum(1 for x in v if x <= -99)
        lines.append(f"продать на {m:>2} мин: среднее {statistics.mean(v):+.1f}%, медиана {statistics.median(v):+.1f}%, слито до продажи {lost} из {len(v)}")
lines.append("")
for r in out:
    lines.append(f"{r['time']} {r['pair'][:10]} пул {r['start']} → пик {r['peak']} на {r['peak_min']} мин, слив {r['drain_min'] or '—'} мин; "
                 f"20 мин {r['m20']:+.0f}%, 25 мин {r['m25']:+.0f}%")
open(os.path.join(HERE, "hist3553.txt"), "w", encoding="utf-8").write("\n".join(lines))
print("\n".join(lines[:20]))
