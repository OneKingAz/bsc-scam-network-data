"""How much can ONE late-entry dust test tell? On untraded 1 BNB coins: other buys in the 120 s after the first bot buy, and in
the next 120 s; how often a coin shows few buys by chance. Offline. Run: python analysis\\late_power.py"""
import collections, csv, json, os, statistics as st, sys
sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
SPB = 0.45
cache = json.load(open(os.path.join(HERE, "gas_options_cache.json"), encoding="utf-8"))
done = {r["token"].lower() for r in csv.DictReader(open(os.path.join(HERE, "hop_trades.csv"), encoding="utf-8-sig"), delimiter=";")}
src = [c for t, c in cache.items() if c.get("kind") == 1 and c.get("buys") and t.lower() not in done]
during, after, first = [], [], []
for c in src:
    b0 = c["buys"][0]
    first.append((b0 - c["t0"]) * SPB)
    during.append(sum(b0 < b <= b0 + 120 / SPB for b in c["buys"]))
    after.append(sum(b0 + 120 / SPB < b <= b0 + 240 / SPB for b in c["buys"]))
print(f"монет {len(src)}; первая покупка бота: медиана {st.median(first):.0f} с, от {min(first):.0f} до {max(first):.0f} с")
print("покупок за 120 с после первой:", sorted(collections.Counter(during).items()), "медиана", st.median(during))
print("покупок за следующие 120 с:", sorted(collections.Counter(after).items()), "медиана", st.median(after))
print("доля монет с 0–1 покупками за первые 120 с без нас:", round(sum(x <= 1 for x in during) / len(during) * 100), "%")
print("доля с 0–1 И потом ≥2:", round(sum(a <= 1 and b >= 2 for a, b in zip(during, after)) / len(during) * 100), "%")
