"""17.11 (user, analyst's next step): one graph of funding, trading and asset moves of the GEKKO group, from sol_export_raw.jsonl.gz
(no network). For every wallet of the group (+creator, sponsor):
  - SOL in/out by transfers (who -> whom), swaps by coin (SOL spent/received, tokens), fees, other;
  - which other coins the group traded in the window (wallets, volume, net SOL), which coins the creator/sponsor created;
Out: sol_graph_edges.csv (SOL transfers aggregated source->destination), sol_graph_wallets.csv (flow per wallet),
     sol_graph_coins.csv (coins traded by the group), sol_graph.txt (summary).
Usage: python sol_graph.py <mint> <creator> <sponsor>"""
import collections
import csv
import gzip
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
MINT, CREATOR, SPONSOR = sys.argv[1:4]
WSOL = "So11111111111111111111111111111111111111112"
trades = list(csv.DictReader(open(os.path.join(HERE, "sol_export_trades.csv"), encoding="utf-8-sig"), delimiter=";"))
group = {r["wallet"]: r["group"] for r in trades}
G = set(group) | {CREATOR, SPONSOR}
role = lambda a: "создатель" if a == CREATOR else "спонсор" if a == SPONSOR else ("группа" if a in G else "")

edges = collections.defaultdict(lambda: [0.0, 0])
W = collections.defaultdict(lambda: collections.defaultdict(float))
coins = collections.defaultdict(lambda: dict(wallets=set(), buys=0, sells=0, spent=0.0, got=0.0, first=None, last=None))
created = collections.defaultdict(list)
seen = set()
n = 0
for line in gzip.open(os.path.join(HERE, "sol_export_raw.jsonl.gz"), "rt", encoding="utf-8"):
    d = json.loads(line)
    s, t = d["signature"], d["tx"]
    if s in seen or not t or not t.get("meta") or t["meta"].get("err"):
        continue
    seen.add(s)
    n += 1
    m = t["meta"]
    ks = [k["pubkey"] if isinstance(k, dict) else k for k in t["transaction"]["message"]["accountKeys"]]
    payer, bt = ks[0], t.get("blockTime")
    ins = list(t["transaction"]["message"]["instructions"])
    for g in m.get("innerInstructions") or []:
        ins += g.get("instructions") or []
    # a tx that moves any token is a swap/token op: its system transfers are wrapping SOL, fees, rent — not wallet-to-wallet moves
    token_tx = any(True for side in ("preTokenBalances", "postTokenBalances") for _ in (m.get(side) or []))
    moved = collections.defaultdict(float)  # SOL moved by plain transfers per wallet (pure transfer txs only)
    for i in ins:
        p = i.get("parsed") if isinstance(i, dict) else None
        if not token_tx and isinstance(p, dict) and i.get("program") == "system" and p.get("type") == "transfer":
            a, b, v = p["info"].get("source"), p["info"].get("destination"), p["info"].get("lamports", 0) / 1e9
            if a in G or b in G:
                edges[(a, b)][0] += v
                edges[(a, b)][1] += 1
            moved[a] -= v
            moved[b] += v
    logs = " ".join(m.get("logMessages") or [])
    if "Instruction: Create" in logs and payer in (CREATOR, SPONSOR):
        mints = {b["mint"] for b in (m.get("postTokenBalances") or []) if b["mint"] != WSOL}
        created[payer] += [(bt, x) for x in mints]
    if payer not in G:
        continue
    # the payer's balance change, split into transfers / swaps / fee
    fee = m.get("fee", 0) / 1e9
    native = (m["postBalances"][0] - m["preBalances"][0]) / 1e9
    tok = collections.defaultdict(float)
    for side, sign in (("preTokenBalances", -1), ("postTokenBalances", 1)):
        for b in m.get(side) or []:
            if b.get("owner") == payer:
                tok[b["mint"]] += sign * float(b["uiTokenAmount"].get("uiAmount") or 0)
    wsol = tok.pop(WSOL, 0.0)
    tok = {k: v for k, v in tok.items() if abs(v) > 0}
    w = W[payer]
    w["txs"] += 1
    w["fee"] += fee
    w["transfer_in"] += max(moved.get(payer, 0.0), 0.0)
    w["transfer_out"] += max(-moved.get(payer, 0.0), 0.0)
    swap = native + wsol + fee - moved.get(payer, 0.0)  # SOL change except plain transfers and the network fee (= the trades file's solAmount_ex_fee)
    if len(tok) == 1:
        mint, dv = next(iter(tok.items()))
        c = coins[mint]
        c["wallets"].add(payer)
        c["first"] = min(c["first"] or bt, bt)
        c["last"] = max(c["last"] or bt, bt)
        if dv > 0 and swap < 0:
            c["buys"] += 1; c["spent"] += -swap
            w["swap_spent"] += -swap
            w["gekko_spent" if mint == MINT else "other_spent"] += -swap
        elif dv < 0 and swap > 0:
            c["sells"] += 1; c["got"] += swap
            w["swap_got"] += swap
            w["gekko_got" if mint == MINT else "other_got"] += swap
        else:
            w["other_sol"] += swap  # token in without SOL out (airdrop, transfer in, create) or the reverse
    else:
        w["other_sol"] += swap  # rent, multi-token, closing accounts, etc.

with open(os.path.join(HERE, "sol_graph_edges.csv"), "w", newline="", encoding="utf-8-sig") as f:
    wr = csv.writer(f, delimiter=";")
    wr.writerow(["source", "source_role", "destination", "destination_role", "sol", "transfers"])
    for (a, b), (v, k) in sorted(edges.items(), key=lambda kv: -kv[1][0]):
        wr.writerow([a, role(a), b, role(b), round(v, 9), k])
cols = ["txs", "transfer_in", "transfer_out", "swap_spent", "swap_got", "gekko_spent", "gekko_got", "other_spent", "other_got", "fee", "other_sol"]
with open(os.path.join(HERE, "sol_graph_wallets.csv"), "w", newline="", encoding="utf-8-sig") as f:
    wr = csv.writer(f, delimiter=";")
    wr.writerow(["wallet", "role", "group"] + cols)
    for a in sorted(G, key=lambda a: -W[a]["txs"]):
        wr.writerow([a, role(a), group.get(a, "")] + [round(W[a][c], 9) for c in cols])
with open(os.path.join(HERE, "sol_graph_coins.csv"), "w", newline="", encoding="utf-8-sig") as f:
    wr = csv.writer(f, delimiter=";")
    wr.writerow(["mint", "group_wallets", "buys", "sells", "sol_spent", "sol_got", "net_sol", "first", "last", "created_by"])
    cr = {x: who for who, lst in created.items() for _, x in lst}
    for mint, c in sorted(coins.items(), key=lambda kv: -(kv[1]["spent"] + kv[1]["got"])):
        wr.writerow([mint, len(c["wallets"]), c["buys"], c["sells"], round(c["spent"], 6), round(c["got"], 6), round(c["got"] - c["spent"], 6),
                     c["first"], c["last"], role(cr[mint]) if mint in cr else ""])

# summary
tot = lambda k, ws: sum(W[a][k] for a in ws)
main = [a for a in group if a not in (CREATOR, SPONSOR)]
inner = sum(v for (a, b), (v, k) in edges.items() if a in G and b in G)
out_ = collections.defaultdict(float)
in_ = collections.defaultdict(float)
for (a, b), (v, k) in edges.items():
    if a in G and b not in G:
        out_[b] += v
    if b in G and a not in G:
        in_[a] += v
L = [f"транзакций разобрано {n}; кошельков группы {len(G)}",
     f"переводы SOL внутри группы: {inner:.2f} SOL; из группы наружу {sum(out_.values()):.2f} SOL ({len(out_)} адресов); снаружи в группу {sum(in_.values()):.2f} SOL ({len(in_)} адресов)",
     "главные получатели снаружи: " + ", ".join(f"{a} {v:.2f}" for a, v in sorted(out_.items(), key=lambda kv: -kv[1])[:6]),
     "главные источники снаружи: " + ", ".join(f"{a} {v:.2f}" for a, v in sorted(in_.items(), key=lambda kv: -kv[1])[:6]),
     f"монет, которыми торговала группа в окне: {len(coins)} (GEKKO среди них)",
     f"кошельки группы (без создателя/спонсора), в окне: GEKKO потратили {tot('gekko_spent', main):.2f} / получили {tot('gekko_got', main):.2f} SOL, "
     f"другие монеты потратили {tot('other_spent', main):.2f} / получили {tot('other_got', main):.2f}, комиссии сети {tot('fee', main):.3f}, прочее {tot('other_sol', main):+.2f}",
     f"создатель: GEKKO {W[CREATOR]['gekko_spent']:.2f}/{W[CREATOR]['gekko_got']:.2f}, другие {W[CREATOR]['other_spent']:.2f}/{W[CREATOR]['other_got']:.2f}, "
     f"переводы получил {W[CREATOR]['transfer_in']:.2f} / отправил {W[CREATOR]['transfer_out']:.2f}, прочее {W[CREATOR]['other_sol']:+.2f}, транзакций {W[CREATOR]['txs']:.0f}",
     f"спонсор: GEKKO {W[SPONSOR]['gekko_spent']:.2f}/{W[SPONSOR]['gekko_got']:.2f}, другие {W[SPONSOR]['other_spent']:.2f}/{W[SPONSOR]['other_got']:.2f}, "
     f"переводы получил {W[SPONSOR]['transfer_in']:.2f} / отправил {W[SPONSOR]['transfer_out']:.2f}, прочее {W[SPONSOR]['other_sol']:+.2f}, транзакций {W[SPONSOR]['txs']:.0f}",
     f"создатель создал монет (в просмотренной истории): {len(created.get(CREATOR, []))}; спонсор: {len(created.get(SPONSOR, []))}",
     "\nтоп-15 монет по обороту группы: mint | кошельков группы | покупок/продаж | потрачено/получено SOL | итог | создана"]
cr = {x: who for who, lst in created.items() for _, x in lst}
for mint, c in sorted(coins.items(), key=lambda kv: -(kv[1]["spent"] + kv[1]["got"]))[:15]:
    L.append(f"{mint} | {len(c['wallets'])} | {c['buys']}/{c['sells']} | {c['spent']:.2f}/{c['got']:.2f} | {c['got'] - c['spent']:+.2f} | {role(cr.get(mint, '')) or '-'}")
print("\n".join(L))
open(os.path.join(HERE, "sol_graph.txt"), "w", encoding="utf-8").write("\n".join(L))
