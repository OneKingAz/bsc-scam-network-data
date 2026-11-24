"""28.10 (user: "find what we missed"): did the guard 0xbd418ce6 change how it marks around 27.10 evening?
For every victim mark in guard_marks.csv since FROM (method approve, not the network testers 0x8737/0x4266): the
coin's Transfer logs (NodeReal) give the target's last receipt before the mark and the balances 3 blocks before it.
Per mark: delay = mark block - receipt block, whether the target still held the coin, its balance rank among the
coin's not-yet-marked victims, and whether it was the newest receiver. A "pass" starts after a gap of >20 blocks.
Out: guard_shift.csv + a summary by 4-hour slots. Run: python guard_shift.py ["2026-10-27 12:00"]"""
import collections, contextlib, csv, io, os, statistics as st, sys, time
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FROM = sys.argv[1] if len(sys.argv) > 1 else "2026-10-27 12:00"
sys.argv = sys.argv[:1]
with contextlib.redirect_stdout(io.StringIO()):
    import bot as B
    import journal as J
from web3 import Web3

HERE = os.path.dirname(os.path.abspath(__file__))
a = Web3(Web3.HTTPProvider("https://bsc-mainnet.nodereal.io/v1/" + J.NODEREAL_KEY, request_kwargs={"timeout": 30}))
TRANSFER = "0x" + Web3.keccak(text="Transfer(address,address,uint256)").hex().removeprefix("0x")
ours = {"0xfafd8a4e4a494da281ebfdd6f09f9a4345f90cdd": "A"}
for line in open(os.path.join(os.path.dirname(HERE), "hop_wallets.txt"), encoding="utf-8"):
    p = line.strip().split(";")
    if len(p) > 1 and p[1].startswith("0x"):
        ours[p[1].lower()] = "W" + p[0]


def call(fn, k=5):
    for i in range(k):
        try:
            return fn()
        except Exception:
            if i == k - 1:
                raise
            time.sleep(1 + i)


t0 = time.mktime(time.strptime(FROM, "%Y-%m-%d %H:%M"))
marks = [r for r in csv.DictReader(open(os.path.join(HERE, "guard_marks.csv"), encoding="utf-8-sig"), delimiter=";")
         if int(r["ts"]) >= t0 and r["method"] == "095ea7b3" and r["wallet"][:6] not in ("0x8737", "0x4266")]
by_tok = collections.defaultdict(list)
for r in marks:
    by_tok[r["token"]].append(r)
rows = []
for tok, ms in by_tok.items():
    ms.sort(key=lambda r: int(r["block"]))
    lo, hi = int(ms[0]["block"]) - 4000, int(ms[-1]["block"])
    logs = []
    for b in range(lo, hi + 1, 2000):
        try:
            logs += call(lambda: a.eth.get_logs({"address": Web3.to_checksum_address(tok), "fromBlock": b,
                                                 "toBlock": min(hi, b + 1999), "topics": [TRANSFER]}))
        except Exception as e:
            print("логи", tok[:10], b, e)
    tr = sorted((l["blockNumber"], l["logIndex"], "0x" + l["topics"][1].hex()[-40:], "0x" + l["topics"][2].hex()[-40:],
                 int.from_bytes(bytes(l["data"])[:32], "big")) for l in logs)
    victims = {r["wallet"].lower() for r in ms}
    done, prev = set(), None
    for r in ms:
        blk, tgt = int(r["block"]), r["wallet"].lower()
        bal, last_rx = collections.Counter(), {}
        for b, li, f, t, v in tr:
            if b > blk - 3:
                break
            bal[f] -= v
            bal[t] += v
            last_rx[t] = b
        pend = [x for x in victims if x not in done and x in last_rx]
        new_pass = prev is None or blk - prev > 20
        prev = blk
        done.add(tgt)
        if tgt not in last_rx or tgt not in pend:
            continue
        by_bal = sorted(pend, key=lambda x: -bal[x])
        newest = max(pend, key=lambda x: last_rx[x])
        rows.append(dict(time=time.strftime("%m-%d %H:%M:%S", time.localtime(int(r["ts"]))), token=tok, block=blk,
                         target=ours.get(tgt, tgt[:10]), ours=tgt in ours, delay=blk - last_rx[tgt],
                         holds=int(bal[tgt] > 0), bal_rank=by_bal.index(tgt) + 1, cand=len(pend),
                         newest=int(tgt == newest), new_pass=int(new_pass)))
    print(f"{tok[:10]}: меток {len(ms)}, переводов {len(tr)}", flush=True)
rows.sort(key=lambda r: r["time"])
with open(os.path.join(HERE, "guard_shift.csv"), "w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter=";")
    w.writeheader()
    w.writerows(rows)
slots = collections.defaultdict(list)
for r in rows:
    d, h = r["time"][:5], int(r["time"][6:8])
    slots[f"{d} {h // 4 * 4:02d}-{h // 4 * 4 + 4:02d}"].append(r)
print("\nслот          меток  держал  задержка державших (мин/медиана)  1-я метка прохода: крупнейший / новейший / n")
for k in sorted(slots):
    s = slots[k]
    hd = [r["delay"] for r in s if r["holds"]]
    fp = [r for r in s if r["new_pass"] and r["holds"]]
    print(f"{k:13} {len(s):5} {len(hd):6}   {min(hd) if hd else '-':>4} / {st.median(hd) if hd else '-':>5}"
          f"                   {sum(r['bal_rank'] == 1 for r in fp):3} / {sum(r['newest'] for r in fp):3} / {len(fp)}")
