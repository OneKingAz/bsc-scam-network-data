"""Watch the scam network's "cash desks" (funders): 26.10 every launch traced so far got its BNB from one of a few
wallets 25-60 s before the pool appeared (0x0ca17284 -> GINNAN/蚂蚁 58 BNB, 0x1f857c8e -> 7STONKS 17.8 BNB,
0xc2c0111a -> PEPE🐶 55 BNB). Following them shows a launch before it happens: the funded wallet is the next creator,
its contract creation is the token, its addLiquidityETH is the pool. No trading here: scam_watch.csv only, the data
for a sniper. New funders are learned from journal_swaps.csv: whoever paid the creator of a new launch >= 5 BNB."""
import csv, datetime as dt, os, threading, time

def _no_quickedit():
    """07.11: a click in the console window (QuickEdit selection) freezes print() and with it the whole program until Enter is pressed
    (scam_watch stood ~10 h like this). QuickEdit is switched off for this console."""
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h, mode = k.GetStdHandle(-10), ctypes.c_uint()
        if k.GetConsoleMode(h, ctypes.byref(mode)):
            k.SetConsoleMode(h, (mode.value | 0x80) & ~0x40)
    except Exception:
        pass


_no_quickedit()
from concurrent.futures import ThreadPoolExecutor
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
import bot as B

OUT = os.path.join(B.HERE, "scam_watch.csv")
FUNDERS_FILE = os.path.join(B.HERE, "scam_funders.csv")
SWAPS = os.path.join(B.HERE, "journal_swaps.csv")
SEED_FUNDERS = {"0x0ca1728491311b8cf5abbc2fcd2f9d83ffa9d808": "GINNAN, 蚂蚁 (58 BNB)",
                "0x1f857c8e02f913bd00ac420408635414b38f6090": "7STONKS, серия …7777 (17.8 BNB)",
                "0xc2c0111a1d0b5514a2e0cdc5864ed588a3dbb1d3": "PEPE🐶 (55 BNB)"}
ADD_LIQUIDITY_ETH = "0xf305d719"
CREATOR_TTL_S = 1800    # 27.10: a creator launches within minutes; later txs of it were 540 rows of "other" noise
LEARN_BACK_BLOCKS = 24000  # ~3 h: creators older than that funded one-off desks that are gone
LEARN_PER_ROUND, TRACE_BLOCKS = 10, 200  # the desk pays ~93 blocks before the pool
APPROVE = "0x095ea7b3"
ROUTER = B.ROUTER.lower()
# the public node answers whole blocks quickly; the bot's own RPC is kept for trading
archive = Web3(Web3.HTTPProvider("https://bsc-dataseed.binance.org", request_kwargs={"timeout": 20}))
archive.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)


def write(event, **kw):
    new = not os.path.exists(OUT)
    cols = ["time", "event", "funder", "creator", "bnb", "token", "to", "gas_gwei", "tx", "note"]
    with open(OUT, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        if new:
            w.writerow(cols)
        w.writerow([dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), event] + [kw.get(c, "") for c in cols[2:]])


class Watch:
    def __init__(self):
        self.funders = dict(SEED_FUNDERS)
        if os.path.exists(FUNDERS_FILE):
            for r in csv.DictReader(open(FUNDERS_FILE, encoding="utf-8-sig"), delimiter=";"):
                self.funders.setdefault(r["funder"], r["note"])
        self.creators = {}  # creator -> (funder, time funded)
        self.traced = set()  # creators of journal launches already traced
        self.lock = threading.Lock()

    def __call__(self, tx):  # mempool thread: cheap checks only
        frm = (tx.get("from") or "").lower()
        to = (tx.get("to") or "").lower()
        value = int(tx.get("value") or "0x0", 16) / 1e18
        data = (tx.get("input") or "").lower()
        gwei = round(int(tx.get("gasPrice") or "0x0", 16) / 1e9, 3)
        h = tx.get("hash", "")
        if frm in self.funders and to and value >= 1:
            self.creators[to] = (frm, time.time())
            write("funded", funder=frm, creator=to, bnb=round(value, 4), gas_gwei=gwei, tx=h, note=self.funders[frm])
            B.log(f"СЛЕЖКА: касса {frm[:10]} дала {value:.2f} BNB кошельку {to[:10]} — скоро запуск")
            return
        if frm not in self.creators:
            return
        if time.time() - self.creators[frm][1] > CREATOR_TTL_S:
            self.creators.pop(frm, None)
            return
        funder = self.creators[frm][0]
        wait = round(time.time() - self.creators[frm][1], 1)
        if not to:
            write("deploy", funder=funder, creator=frm, gas_gwei=gwei, tx=h, note=f"{wait} с после денег")
            B.log(f"СЛЕЖКА: {frm[:10]} выпускает токен ({wait} с после денег)")
        elif data[:10] == ADD_LIQUIDITY_ETH and to == ROUTER:
            token = "0x" + data[10 + 24:10 + 64]
            write("add_liquidity", funder=funder, creator=frm, bnb=round(value, 4), token=token, gas_gwei=gwei, tx=h,
                  note=f"{wait} с после денег")
            B.log(f"СЛЕЖКА: {frm[:10]} кладёт {value:.2f} BNB в пул токена {token} — запуск сейчас")
        elif data[:10] == APPROVE:
            spender = "0x" + data[10 + 24:10 + 64]
            write("approve", funder=funder, creator=frm, token=to, to=spender, gas_gwei=gwei, tx=h,
                  note="роутер" if spender == ROUTER else "НЕ роутер: возможно, контракт изъятия")
        else:
            write("other", funder=funder, creator=frm, bnb=round(value, 6), to=to, gas_gwei=gwei, tx=h, note=data[:10])

    def learn(self):
        """New launches in the journal: who funded their creator? A wallet that paid >= 5 BNB within ~2 min before the
        first swap becomes a watched funder."""
        while True:
            try:
                first = {}
                if os.path.exists(SWAPS):
                    for r in csv.DictReader(open(SWAPS, encoding="utf-8-sig"), delimiter=";"):
                        c = (r.get("creator") or "").lower()
                        if c and r.get("block") and c not in self.traced:
                            first[c] = min(first.get(c, 10**12), int(r["block"]))
                # 27.10: only creators of the last ~3 h, oldest first, a few per round. Before: the 20 newest every
                # round (older ones never), each reading 300 full blocks - the heaviest network user of all the bots
                head = archive.eth.block_number
                todo = sorted((b0, c) for c, b0 in first.items() if b0 >= head - LEARN_BACK_BLOCKS)
                for c, b0 in first.items():
                    if b0 < head - LEARN_BACK_BLOCKS:
                        self.traced.add(c)
                for b0, creator in todo[:LEARN_PER_ROUND]:
                    self.traced.add(creator)
                    self.trace(creator, b0)
            except Exception as e:
                B.log(f"СЛЕЖКА: ошибка обучения ({str(e)[:80]})")
            time.sleep(120)

    def trace(self, creator, b0):
        def blk(n):
            for _ in range(3):
                try:
                    return archive.eth.get_block(n, full_transactions=True)
                except Exception:
                    time.sleep(1)
            return None
        with ThreadPoolExecutor(6) as ex:
            for b in ex.map(blk, range(b0 - TRACE_BLOCKS, b0)):
                if not b:
                    continue
                for tx in b.transactions:
                    if (tx["to"] or "").lower() == creator and tx["value"] >= 5 * 10**18:
                        f = tx["from"].lower()
                        if f not in self.funders:
                            note = f"нашёл по создателю {creator[:10]}: {tx['value'] / 1e18:.2f} BNB"
                            self.funders[f] = note
                            new = not os.path.exists(FUNDERS_FILE)
                            with open(FUNDERS_FILE, "a", newline="", encoding="utf-8-sig") as fh:
                                w = csv.writer(fh, delimiter=";")
                                if new:
                                    w.writerow(["funder", "first_seen", "note"])
                                w.writerow([f, dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), note])
                            B.log(f"СЛЕЖКА: новая касса {f[:10]} ({note}) — касс {len(self.funders)}")
                        return


def main():
    B.single_instance("Слежка за скамерами", 47661)
    watch = Watch()
    B.log(f"СЛЕЖКА запущена: касс {len(watch.funders)}, события пишу в {os.path.basename(OUT)}")
    mp = B.Mempool()
    mp.watchers.append(watch)
    mp.start()
    threading.Thread(target=watch.learn, daemon=True).start()
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
