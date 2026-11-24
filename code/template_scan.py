"""Which recent journal tokens share the HELL/GOOGLc contract template? Features from the runtime bytecode only:
standard selectors, identity-precompile (0x04) staticcalls used as obfuscation, SSTORE/CALL counts, slot 6."""
import csv, json, os, sys
from collections import Counter

sys.argv = ["x", "--paper"]
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import bot as B

STD = {"06fdde03", "95d89b41", "313ce567", "18160ddd", "70a08231", "a9059cbb", "dd62ed3e", "23b872dd", "095ea7b3"}


def features(code):
    sels, ops, i, last4, ident = set(), Counter(), 0, None, 0
    while i < len(code):
        op = code[i]
        if 0x60 <= op <= 0x7F:
            n = op - 0x5F
            if n == 4:
                last4 = (i, code[i + 1:i + 5].hex())
            if n == 1 and code[i + 1] == 0x04:
                ident += 1
            i += 1 + n
            continue
        if op == 0x14 and last4 and last4[0] >= i - 6:
            sels.add(last4[1])
        ops[op] += 1
        i += 1
    return {"sels": len(sels), "std_only": sels <= STD and len(sels) == 9, "push04": ident,
            "sstore": ops[0x55], "call": ops[0xF1] + ops[0xF4] + ops[0xF2], "len": len(code)}


rows = [x for x in csv.DictReader(open(os.path.join(B.HERE, "journal_signals.csv"), encoding="utf-8-sig"), delimiter=";")
        if x["time"] >= "2026-10-26 20:00"]
out, groups = [], Counter()
for x in rows[-160:]:
    tok = B.Web3.to_checksum_address(x["token"])
    try:
        f = features(bytes(B.w3.eth.get_code(tok)))
        f["slot6"] = B.w3.eth.get_storage_at(tok, 6).hex()[-40:]
    except Exception:
        continue
    tmpl = f["std_only"] and f["push04"] >= 8 and f["call"] == 0
    f.update(symbol=x["symbol"], series=json.loads(x["features"]).get("series", ""), template=tmpl, max_pct=x["max_pct"])
    out.append(f)
    groups[(tmpl, f["slot6"] == B.GUARD[2:])] += 1
print("проверено", len(out), "| (шаблон, сторож в слоте 6):", dict(groups))
g = Counter((f["series"], f["len"], f["push04"], f["slot6"] == B.GUARD[2:]) for f in out)
for (ser, ln, p4, gd), n in sorted(g.items(), key=lambda kv: (kv[0][0], -kv[1])):
    print(f"  серия {ser:10} длина кода {ln:5} push04 {p4:3} сторож {'да ' if gd else 'нет'} — {n} токенов")
for f in []:
    if f["template"]:
        print(f"  {f['symbol'][:12]:12} серия {f['series']:9} сторож={'да' if f['slot6'] == B.GUARD[2:] else '0x' + f['slot6'][:8]} "
              f"push04={f['push04']} len={f['len']} max={f['max_pct']}")
