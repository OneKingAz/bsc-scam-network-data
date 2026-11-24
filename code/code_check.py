"""Are the coins the hop trader takes all the same contract? Bytecode size and hash of the guard launches and of every coin we
traded; a token found by "slot 6 = the guard" could in theory be something else. Read-only. Run: python analysis\\code_check.py"""
import collections, contextlib, csv, io, os, sys
from concurrent.futures import ThreadPoolExecutor
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.argv = sys.argv[:1]
with contextlib.redirect_stdout(io.StringIO()):
    import bot as B
from web3 import Web3

w3 = B.w3
rows = list(csv.DictReader(open(os.path.join(HERE, "guard_launches.csv"), encoding="utf-8-sig"), delimiter=";"))
traded = list(csv.DictReader(open(os.path.join(HERE, "hop_trades.csv"), encoding="utf-8-sig"), delimiter=";"))
late = {r["token"].lower(): r["symbol"] for r in rows[-120:]}
tr = {t["token"].lower(): t["symbol"] for t in traded}


def look(token):
    for _ in range(4):
        try:
            t = Web3.to_checksum_address(token)
            code = bytes(w3.eth.get_code(t))
            slot = w3.eth.get_storage_at(t, 6).hex()[-40:].lower()
            return token, len(code), Web3.keccak(code).hex()[:12], slot == B.GUARD[2:].lower()
        except Exception:
            pass
    return token, None, None, None


with ThreadPoolExecutor(8) as ex:
    res = {r[0]: r for r in ex.map(look, set(late) | set(tr))}
for name, src in (("последние 120 запусков", late), ("наши сделки", tr)):
    v = [res[t] for t in src]
    print(f"\n{name}: {len(v)}")
    print("  размер кода:", collections.Counter(x[1] for x in v).most_common())
    print("  разных версий кода:", len({x[2] for x in v}), collections.Counter(x[2] for x in v).most_common(4))
    print("  сторож в слоте 6:", collections.Counter(x[3] for x in v).most_common())
odd = [(src_sym, res[t][1], res[t][3]) for t, src_sym in {**late, **tr}.items() if res[t][1] != 2995 or not res[t][3]]
print("\nне такие:", odd[:25])
g = open(os.path.join(HERE, "hop_trader.py"), encoding="utf-8").read()
print("проверка размера кода в перекидывателе:", "get_code" in g)
