"""20.11: four.meme BSC — 24 h of manager events (fourmeme_logs.jsonl from fourmeme_dl.py). Per coin: launch, trades, BNB on the curve
(funds), graduation; the curve price is a formula of funds, so «what happens after the curve reached X BNB» is the question.
Usage: python fourmeme_an.py   Out: fourmeme_an.txt"""
import json, collections, os, sys, statistics as st
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
BUY, SELL, CREATE = "0x0a5575b3", "0x7db52723", "0x396d5e90"
GRAD = {"0xc18aa711", "0x8f9ab4bd", "0x70300ab9"}
coins = collections.defaultdict(lambda: dict(created=None, creator=None, ev=[], grad=None))
other = collections.Counter()
for line in open(os.path.join(HERE, "fourmeme_logs.jsonl"), encoding="utf-8"):
    l = json.loads(line)
    t, b, d = l["topics"][0][:10], int(l["blockNumber"], 16), l["data"][2:]
    w = [d[i:i + 64] for i in range(0, len(d), 64)]
    if t == CREATE:
        tok = "0x" + w[1][-40:]
        coins[tok]["created"], coins[tok]["creator"] = b, "0x" + w[0][-40:]
    elif t in (BUY, SELL):
        tok, acct = "0x" + w[0][-40:], "0x" + w[1][-40:]
        price, amount, cost, fee, offers, funds = [int(x, 16) for x in w[2:8]]
        coins[tok]["ev"].append((b, t == BUY, acct, price, cost / 1e18, funds / 1e18, offers / 1e18))
    elif t in GRAD:
        other[t] += 1
        for x in w:
            if x[:24] == "0" * 24 and int(x, 16) > 2 ** 100:
                c = "0x" + x[-40:]
                if c in coins:
                    coins[c]["grad"] = b
L = []
born = [c for c, v in coins.items() if v["created"]]
L.append(f"монет создано за сутки: {len(born)}; с торговлей: {sum(1 for c in born if coins[c]['ev'])}; событий выхода с кривой: {dict(other)}")
mx = sorted(max((e[5] for e in coins[c]["ev"]), default=0) for c in born)
q = lambda a, p: a[min(len(a) - 1, int(p * len(a)))]
L.append(f"максимум BNB на кривой у монеты: медиана {q(mx, .5):.3f}, 90% {q(mx, .9):.2f}, 99% {q(mx, .99):.2f}, макс {mx[-1]:.2f}")
for th in (0.5, 1, 2, 4, 8, 12, 16, 18, 20):
    L.append(f"  дошли до {th:>4} BNB: {sum(1 for x in mx if x >= th)}")
# what happens after a coin first reaches X BNB: the later maximum and the value 5/15/60 min later (curve price ~ funds)
L.append("\nПОСЛЕ ТОГО КАК МОНЕТА ВПЕРВЫЕ ДОШЛА ДО X BNB (цена на кривой — формула от BNB в кривой):")
for th in (0.5, 1, 2, 4, 8, 12, 16):
    res = []
    for c in born:
        ev = coins[c]["ev"]
        i = next((k for k, e in enumerate(ev) if e[5] >= th), None)
        if i is None:
            continue
        b0, p0 = ev[i][0], ev[i][3]
        later = ev[i:]
        pmax = max(e[3] for e in later)
        def at(m):
            x = [e for e in later if e[0] <= b0 + m * 133]
            return x[-1][3] / p0 if x else 1
        res.append((pmax / p0, at(5), at(15), at(60), coins[c]["grad"] is not None))
    if not res:
        continue
    r5 = sorted(r[1] for r in res); r15 = sorted(r[2] for r in res); r60 = sorted(r[3] for r in res)
    L.append(f"  от {th:>4} BNB: монет {len(res):4}, вышли с кривой {sum(r[4] for r in res):3}; цена через 5 мин медиана x{q(r5, .5):.2f}, "
             f"через 15 мин x{q(r15, .5):.2f}, через 60 мин x{q(r60, .5):.2f}; доля выросших к 15 мин {sum(1 for x in r15 if x > 1.03) * 100 // len(r15)}%; "
             f"дальнейший максимум медиана x{q(sorted(r[0] for r in res), .5):.2f}")
# who trades: top accounts by buys, creator self-buys
acc = collections.Counter(); first = collections.Counter()
for c in born:
    for k, e in enumerate(coins[c]["ev"]):
        if e[1]:
            acc[e[2]] += 1
            if k < 3:
                first[e[2]] += 1
L.append(f"\nпокупателей всего {len(acc)}; топ по покупкам: " + ", ".join(f"{a[:10]}:{n}" for a, n in acc.most_common(8)))
L.append("кто покупает первым (первые 3 сделки монеты): " + ", ".join(f"{a[:10]}:{n}" for a, n in first.most_common(8)))
out = "\n".join(L)
print(out)
open(os.path.join(HERE, "fourmeme_an.txt"), "w", encoding="utf-8").write(out)
