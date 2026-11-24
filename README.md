# BSC scam-network meme coins — raw data for pattern discovery

Data collected 26.10–24.11.2026 by a small trading bot on BNB Smart Chain (BSC) that tried to profit from
organised scam meme-coin networks, plus a Bitcoin side study. **The task for you (an AI model good at
computation): find statistically robust, exploitable regularities that we missed.** Everything we already
proved or ruled out is listed below so you don't repeat it. Times are local UTC+5 unless a column says `ts`
(unix seconds) or `T` (unix ms). CSV separator is `;` (BSC) or `,` (BTC), encoding utf-8 with BOM.
Long notes (`notes/`, `reports/`) are in Russian — machine translation is fine.

Companion repo (Solana "family 1" pump.fun-farm study): https://github.com/OneKingAz/solana-family1-investigation

## 1. How the scam networks work (established facts)

* **Networks ("сети")** are identified by token bytecode + a "guard" address in storage slot 6 + a cashier ("касса").
  | net | code (len / hash) | guard (slot 6) | cashier | pool start | life to drain |
  |---|---|---|---|---|---|
  | 1 | 2995 B `aebf2d66f861` | `0xbd418ce6…` | `0xde8eb937…` | 1 / 10 / 25 BNB | ~8 / 16–32 / 51–60 min |
  | 2 | 1365 B `71f433abef78` | `0xdbbf0824…` | `0xF51DD734…` | 1 / 10 / 25 | ~35 s (most) or 12–42 min |
  | 3 | 1365 B (`18449a1624`) | `0x623e7c23…` | `0x1ac2c5dd…` | 15 BNB always | 28.7–33 min |
  | 4 | 1365 B (`18449a1624`) | `0xA70B3f34…` | `0x238d7170…` | 15 BNB always | 28.7–33 min |
  | 5 | family 2941 `436f529b4d` | none (slot 6 = WBNB) | payer wallets (`analysis/net5_payers`) | ~22 BNB | ~6 min |
  | 6 | family 3553 `1f6f5a665a` | none | — | 58 BNB | 27–33 min (rug if outsider buys) |
* Each network runs **one coin at a time**; the next coin launches 0.4–1.3 min after the previous pool is drained.
  Names are recycled (BNQ launched 30×); a coin is identified only by address.
* **Creator pays bots**: the coin's creator sends BNB to one of ~50–70 of its own bot wallets, and that bot buys the
  coin for exactly that amount 3–8 blocks later (via router `swapExactETHForTokens`). Bots lose money; the creator
  takes everything with **one sell of the whole pool** (not removeLiquidity) and sends ~the same BNB back to the cashier.
  So the cashier recycles the same ~50 BNB.
* **Guard**: calls `approve(victim, MAX)` from the guard wallet on the token; the token's `_spendAllowance` hook then
  blocks any transfer/sell by that holder (hidden blacklist). It marks outside holders only (never its own bots).
  Mark delay after receiving tokens: 7–20 blocks in Oct; **now 4–5 blocks** (nets 1 and 4, 23.11). Since 27.10
  evening it marks the *largest* outside holder by a snapshot ~12 blocks old, ~1 mark per ~11 blocks.
* **Bot price tolerance**: bots of nets 3/4 set `amountOutMin` = 99.99 % of the quote computed ~2 blocks after the
  payment; since 19.11 net 1/2 bots use 99.999 % of the quote at the end of the payment block. Any buy that lands
  between the payment and the bot's quote makes the bot's tx revert.
* **Rug triggers**: families 2046 and 1526 drain the pool 0.5–7 s after *any* outside buy. Nets 3/4 creators stop
  paying bots while an unmarked outsider holds the coin, and drain 29–101 s after an outside LP Mint.
* All networks stopped simultaneously 16.11 07:11–07:18 UTC and restarted ~18.11 with the same code.

## 2. What we tried (results with real money unless "paper")

| idea | result |
|---|---|
| Early sniping 30–90 s after launch (live, Oct) | traps fire on our buy: 12/13 rugs in the same second as our buy |
| Series by cashier amount (paper +30…+130 %) | live loses; series 50.0 = guard blacklist |
| "Hop" chain: pass tokens wallet→wallet faster than the guard | works mechanically (535 hops, never caught) but gas ≈ 0.000145 BNB/min eats it; −0.0054 BNB on 5 trades |
| Decoy/dust wallets to occupy the guard queue | fails: guard marks the largest holder first |
| **Signal trading**: see creator→bot payment, buy before bot, sell after bot | **profitable 14–18.11**: ~+0.75 BNB, 1200+ trades, nets 1–4. Land at payment+1 → +2.97 % avg; +2 → 50 % win; +3 → loss |
| same after 19.11 | net 1/2 bots revert after our buy (stricter quote); nets 3/4 guard now marks at +4 → stuck (IGV −101 %) |
| Bundles to builders `[creator payment, our buy]` | 0/53 landed — payment already in a built block when we see it |
| "Pre-tick": creator pays on a 5.00 s grid; buy 1 block before predicted payment | lands correctly, but payment didn't come and guard marked us at +5 |
| Become LP instead of holder | creator drains 29–101 s after our Mint; −0.055 BNB |
| four.meme, pump.fun (3 rule families, copy leaders, near-graduation) | all negative after fees |
| BTC: 76 price rules, following bot prints, futures top-trader L/S ratio | none beat fees out-of-sample |

Current live test (24.11): nets 3/4 signal trading at $0.01 stake, selling 1 block after landing (before guard mark).
Bots now buy 4–5 blocks after us, so we exit before them.

## 3. Open questions — please work on these

1. **Predict the creator's payment** (which bot, when, how big) before it is visible. Payments of nets 1/2 sit on a
   5.00 s grid with per-coin phase (see `bsc/our_trades/signal_skip*.csv`, `signal_test*.csv`, columns `pay_block`,
   `bnb_paid`, `share_pct`). Is the next big payment (≥3 % of pool) predictable to the block?
2. **Predict the drain** (the creator's full-pool sell) from bot-buy counts, silences, pool growth
   (`bsc/network/bot_watch.csv`, `guard_launches.csv`, `hist_launches.jsonl`). Known: last bot buy ~3.58 min before drain.
3. **Guard timing**: exact rule of when the guard marks (`guard_marks.csv`, `bot_watch.csv` rows with role guard).
   Is there a holder size or block window it never marks within?
4. **Which signal trades win**: features in `signal_test*.csv` vs `pnl_pct` (source mempool/block, share, age,
   land_block − pay_block, net, hour). What separates +3 % from −0.6 %?
5. Any regularity in `journal_signals.csv` (every launch with features + 300 s price path) and
   `journal_swaps.csv` (every swap in tracked pools with wallet roles).
6. BTC: do liquidation bursts (`btc/liq_events.csv`, recording since 24.11) predict the next minutes of `btc/btc_px.csv`?

Be strict: report out-of-sample results (split by day), fees (pool 0.25 % per side + gas ~0.00002–0.00005 BNB
per tx), and how many rules you tried.

## 4. Files

### `bsc/our_trades/` — our own trades
* `hop_trades.csv` — every real trade: `stake_bnb, got_bnb, gas_bnb, pnl_bnb, pnl_pct, reason, note`.
* `signal_test.csv` (net 1), `signal_test2..5.csv` — signal trades. Key columns: `source` (мемпул = seen in mempool,
  блок = only in block), `pay_block` creator payment block, `bnb_paid`, `pool_bnb`, `share_pct` payment/pool,
  `send_head` chain head when we sent, `land_block` our buy, `bot_buy_block`, `sell_block`, `got_bnb`, `pnl_pct`,
  `note` (e.g. «опоздали — отменена сроком» = cancelled by deadline, «мимо» = bot never bought).
* `signal_skip*.csv` — payments seen but not traded (with reason) → the full payment stream.
* `pretick_test.csv`, `bundle_test.csv`, `chain_test.csv`, `send_test.csv`, `lp_test.csv`, `lp_auto.csv`, `sell_fail.csv`,
  `hop_timing.csv`, `tick_check.csv` — the experiments from section 2.
### `bsc/network/` — observations of the networks
* `guard_launches.csv` (net 1 coins), `guard2_launches.csv` (nets 2–8; `symbol` holds the net label).
* `guard_marks.csv` — guard transactions (`wallet` marked, `method` 095ea7b3 = approve).
* `bot_watch.csv` (+`_v1`) — every tx in pools of tracked families: `age_s`, `kind`, `bnb`, `pool_after`, `sender`,
  `role` (creator/bot/guard/outsider/our), `method`, `gwei`, `tokens`, `nonce`.
* `hist_launches.jsonl` — 6000+ launches ≥8 BNB, 19–26.10: creator, funder (cashier), series, `rug_s`, price `path`.
* `coin_families.csv`, `family_scan.csv`, `family_deep_swaps.csv`, `pattern_scan.csv`, `net_finder_events.csv` —
  all code families on BSC (24 h scans), paid-buy detection.
* `bot_quote_check.csv`, `bot_revert.csv`, `sig34_audit.csv` — bot `amountOutMin` vs reserves, why bots revert.
* `cash_watch.csv`, `bot_swaps_18h.csv`, `who_won34.csv`, `net7_txs.csv`, `after_our_buy.csv`, `funded_buys.csv`, `fast_econ.csv`, `gas_options_cache.json`.
### `bsc/journal/` — passive journal (26.10 →)
* `journal_signals.csv` — each launch that passed basic filters: `features` (JSON: pool, supply %, GoPlus, funder,
  series, age_at_signal_s, tax, …), returns `r10s…r300s`, `path` = JSON `[[t, pct], …]` for 300 s, net of gas.
* `journal_swaps.csv.gz` — swaps in journal pools with wallet and side.
* `rug_mempool.csv` — liquidity pulls: `held_s`, `bnb_before`, seen in mempool, lead time.
* `scam_funders.csv`, `scam_watch.csv` — creators and their funders.
### `btc/`
* `btc_1m_180.csv` — BTCUSDT 1-min candles, 181 days (Binance). `btc_trades.csv.gz` — 1.44 M trades, 24 h.
* `metrics/` — Binance futures metrics (OI, top-trader long/short ratios) 179 days; `fut_metrics.csv`, `fut_funding.csv`.
* `liq_events.csv` (T ms, side, qty, price) + `btc_px.csv` — Bybit liquidations and price, live since 24.11 10:50.
### `code/`, `reports/`, `notes/`
* `code/` — every analysis script (Python) and the bot core (`bot.py`, `journal.py`, `scam_watch.py`). Endpoints:
  public BSC RPC (`https://rpc-bsc.48.club` works for logs ≤400 blocks); NodeReal key is read from env, not included.
* `reports/` — text outputs of the analyses. `notes/findings_log_ru.md` — chronological log of every finding (largest
  source of context), `full_review_2026-11-23_ru.md` — summary review.

No private keys or API tokens are included. Our own wallet addresses appear in the data (they are public on-chain).
