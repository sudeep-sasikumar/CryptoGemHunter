# Gem Hunter

One tracker: tokens with a market cap between **$100K and $700K** on **Solana, Ethereum, Robinhood, Base and BSC**
that could run to a multi-million market cap. It measures each one against the factors in
[GEM_HUNTER_FACTORS.md](GEM_HUNTER_FACTORS.md), calls the few that pass, follows every call with a trade plan
(entry, trade updates, exit on Telegram), grades every token it ever looked at, and retrains itself on those grades.

**Signals only. It never trades, holds no keys, and is not financial advice.** Most micro caps go to zero; a call is
a statistical edge at best, never a promise.

---

## 1. What it does, in one loop

```
GMGN lists per chain (trending in range, graduated) ─► watched token ─► every factor ─► gates + scorecard
                                                                             │
                                             SNAPSHOT of every token ◄───────┤   (called or not, gated or not)
                                                                             ▼ best candidates only
                    deep look: token page, security, candles, deployer history, holders, traders, GoPlus
                                                                             │ gates (final) + scorecard / model
                                                                             ▼
                              CALL ─► Telegram entry ─► trade plan on live prices ─► updates, warnings, exit
every snapshot is followed to its outcome: 3x before -50%, within 7 days
daily: retrain on all outcomes ─► test on the newest unseen data ─► switch only if better
```

Every token in range gets snapshots and grades, **not just the ones that were called**. That is what lets it learn
from the runners it missed and the losers it avoided, and what shows whether each gate removes losers or winners.

## 2. A call and its trade plan

| Message | When |
|---|---|
| 💎 **Entry** | A token passes every gate after a deep look and scores in the best 3% of everything seen in the last 7 days (and at least 72/100). With a learned model in use: its chance of a hit is over the model's threshold. |
| 📈 **Trade update** (reply under the entry) | Price reaches 2x, 3x, 5x, 10x, 20x, 50x; market cap passes $1M, $2M, $5M, $10M. |
| ⚠️ **Warning** (reply) | Bundlers start selling; KOLs buy while smart money sells; smart money starts selling. |
| ✅ / 🛑 **Exit** (reply) | -40% stop · once past 2x, 50% off the peak (trailing stop) · safety trigger: liquidity down 50% from entry, dev went from holding to sold, rug ratio over 0.30, wash-trading or honeypot flag, 3+ smart wallets selling within an hour · 7 days. |

Results assume 3% slippage on the entry and on the exit. A stop must show on two price polls in a row, so one freak
print does not exit. At most 2 calls an hour, 6 a day, 12 open, one per token per week. Every level is in
`config.yaml` under `trade:` and `call:`.

## 3. The factors

All groups of the spec are computed in `gemhunter/factors.py`; names carry the group.

| Spec group | Prefix | Source | When |
|---|---|---|---|
| 1 Contract safety (pass/fail) | `s_`, `gp_` | GMGN lists + security; GoPlus owner powers on EVM | every token; full check at the deep look |
| 2 Liquidity and structure | `l_` | DexScreener, GMGN | every token |
| 3 Deployer and dev | `d_` | GMGN lists, token page, created-tokens history | lists every token; history at the deep look |
| 4 Holders and manipulation | `h_` | GMGN lists, holders list, own history over time | lists every token; clusters at the deep look |
| 5 Cost basis and retention | `c_` | GMGN holders + traders lists | deep look, when the lists answer |
| 6 Smart money and wallets | `w_`, `own_` | GMGN counts, smart-money + KOL feeds, signals, holders list, own proven wallets | feeds every token; holders at the deep look |
| 7 Volume and order flow | `f_` | DexScreener, GMGN, traders list | every token |
| 8 Price action and lifecycle | `p_` | GMGN candles, lists, signals | lists every token; candles at the deep look |
| 9 Social and attention | `a_` | GMGN lists and signals | every token |
| 10 Market context | `mk_`, `m_` | CoinGecko (BTC, native coin), time of day | every token |

Not captured: X account quality, mention velocity, community activity (need paid social data); narrative fit and meme
quality (judgement); group 11 fundamentals (team, product, audit - manual). They are absent, not guessed.

**Gates** (`gates:` in config.yaml, `gemhunter/scoring.py`): honeypot, tax over 10%, mint / freeze authority, unverified
or owner-controlled EVM contracts, owner powers, liquidity not burned or locked, rug ratio over 0.30, wash trading,
top 10 over 40%, bundlers / insiders / entrapment over 30%, dev team over 10%, creator over 5%, thin liquidity,
under 150 holders, almost no trading, serial deployer, a deployer whose earlier call ended on a safety exit.

**Scorecard** until a model has passed its tests, weighted in the spec's priority order: manipulation and distribution
30, smart money 20, order flow 18, cost basis 12, price structure 10, attention 7, liquidity 3. A group with no data
scores neutral.

## 4. How it improves itself

1. **Grades.** Every snapshot is followed for 7 days: a hit = 3x before -50% (`label:`).
2. **Daily retrain** (`learn.auto_retrain`, `AUTO_RETRAIN`) once there are 800 graded snapshots and 40 winners. History is
   split by time: fit on the oldest 70%, test on the newest 15% it never saw. The new model replaces what is in use
   only if it clearly beats the scorecard, beats luck, and is no worse than the current model.
3. **Readable output.** Dashboard tab "What it has learned": for every factor, the hit rate in each value range - what
   matters, without trusting a model. Tab "Scoreboard": whether higher scores really hit more, and whether each gate
   removes losers.
4. **Own proven wallets.** The earliest real buyers of every deep-looked token are recorded; when the token is graded
   they are credited. Wallets early in winners far more often than average become "proven" and count as a factor.
5. **Own deployer record.** How each deployer's tokens ended here; a safety exit blocks that deployer for good.

### Past data on this PC (same pattern as Launch Radar)

`tools\sync_research.py` signs in to the dashboard (`research.sync_url`) with `DASHBOARD_PASSWORD` from this PC's `.env`,
downloads one file per UTC day into `var\research\vps\` (every graded snapshot with all factors and what happened next,
every call with its timeline), re-downloads the newest 9 days (outcomes take up to 7), and writes
`var\research\report-<date>.md`: what the runners had in common, by chain and launchpad, and which factor ranges held
up on newer data than they were picked on.

| To... | Run (PowerShell) |
|---|---|
| sync + report now | `.venv\Scripts\python.exe tools\sync_research.py` |
| report only | `.venv\Scripts\python.exe tools\analyze.py --days 14` |
| schedule it daily at 09:45 | `powershell -ExecutionPolicy Bypass -File tools\install_daily_sync.ps1` |
| see what model is in use | `.venv\Scripts\python.exe tools\retrain.py --status` |
| train a candidate, change nothing | `.venv\Scripts\python.exe tools\retrain.py` |
| ... and switch if it passes | `.venv\Scripts\python.exe tools\retrain.py --promote` |

## 5. Running on the VPS

1. Every push to `main` runs the tests, builds the image and publishes
   **`ghcr.io/sudeep-sasikumar/cryptogemhunter:latest`** (GitHub Actions).
2. GitHub -> your profile -> Packages -> `cryptogemhunter` -> Package settings -> make it **public** (once), so the VPS
   can pull it without a login.
3. Hostinger -> VPS -> Docker Manager -> Compose -> Compose from URL, project name `crypto-gem-hunter`:
   **`https://raw.githubusercontent.com/sudeep-sasikumar/CryptoGemHunter/main/docker-compose.yaml`**
4. Environment variables, then Deploy:

   | Variable | Needed | What |
   |---|---|---|
   | `GMGN_API_KEY` | yes | the GMGN key |
   | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | for signals | the bot Launch Radar posts with works; several chats comma-separated |
   | `DASHBOARD_PASSWORD` | yes | a long password of your choice (also in this PC's `.env` for the sync) |
   | `GMGN_WEIGHT_PER_SEC`, `GMGN_BUDGET` | optional | the key's plan (5 / 20 / 50) and this program's share of it (default 0.2) |
   | `CALL_MIN_SCORE`, `CALLS_PER_DAY`, `MCAP_MIN_USD`, `MCAP_MAX_USD`, `AUTO_RETRAIN` | optional | see `docker-compose.yaml` |
   | `TELEGRAM_THREAD_ID`, `TELEGRAM_SILENT_UPDATES`, `TELEGRAM_DAILY_SUMMARY_UTC` | optional | |

5. Dashboard: **`https://srv1479428.hstgr.cloud/gems/`** - same hostname and certificate as Ignition & Coil and Launch
   Radar (`/radar/`); Traefik routes `/gems` here.

Data (database, models, logs) lives in the `gem_data` volume and survives redeploys. A restart carries on: open calls
and unfinished snapshots are reloaded from the database. Disk is capped at 5 GB (`storage.max_gb`).

## 6. Run it on this PC

```
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
copy .env.example .env                        set GMGN_API_KEY
.venv\Scripts\python tools\probe_gmgn.py      checks every GMGN route and field with your key
.venv\Scripts\python main.py                  dashboard: http://localhost:8020 (no login on localhost)
.venv\Scripts\python main.py --minutes 5      smoke test
.venv\Scripts\pytest -q
```

Without Telegram settings, messages are printed to the console.

## 7. Checked live on 2026-11-11

- **GMGN**, with the key, on Solana: every route this program uses answered, with every expected field (rank 38/38,
  holders 19/19, feeds 8/8, candles 6/6). The market-cap filter is applied by GMGN itself. On Ethereum: rank, feeds,
  token page, security and candles answered. An 8-minute run found 397 tokens in range: Solana 150, Robinhood 98,
  Base 98, Ethereum 40, BSC 11 (a rate-limit pause cut the BSC sweep short; a run before it found 74).
- Differences from GMGN's documentation, handled in code: candles carry USD in `volume` and token units in `amount`
  (the docs say the reverse) and come at most 100 per request; the graduated list calls market cap `market_cap`;
  fees (`gas_fee`, `total_fee`) are in the chain's native coin and are converted to USD; GMGN's smart-wallet count runs
  into the hundreds, so the scorecard scales it and counts separately the smart holders that actually bought.
- **DexScreener** prices every chain including Robinhood (chain id `robinhood`). **GoPlus** and **CoinGecko** answered.
- Of 358 tokens in range, 68 passed every gate. What removed the rest, most often: entrapment over 30% (median 22% on
  Solana, about 50% on BSC and Robinhood), liquidity not burned or locked, Solana rug ratio over 0.30, top 10 over 40%.
  The entrapment level comes from the spec; the Scoreboard tab will show within days whether it removes losers or winners.

## 8. Known limits

- **The GMGN key is shared with Launch Radar and has no spare room.** GMGN's own header showed 0-2 units left in the
  bucket (of 5) after almost every request, and 6 of 40 requests in the test run met a ban. A ban stops *both* programs,
  and each request either one sends during it adds 5 seconds. Gem Hunter therefore waits for the bucket to refill
  before every request, stays away at least 2 minutes after a ban, spaces its sweeps widely and does at most one deep
  look per cycle. It works, slowly. Holder and trader lists cost the whole free bucket (weight 5) and are often refused;
  a deep look then goes on without them and those factors score neutral (`deep_holders` / `deep_traders` record which).
  **A separate GMGN key for Gem Hunter, or a Plus plan, removes this** - then set `GMGN_BUDGET=0.7` (own key) or
  `GMGN_WEIGHT_PER_SEC=20` and, in `config.yaml`, `sources.gmgn.shared_use: 0`.
- **Day one = scorecard only.** Its weights follow the spec's priority order and were scaled on one hour of live data,
  not on outcomes. A first model needs 800 graded snapshots and 40 winners: a week or more.
- **Tokens are followed by price polls** (20 s for open calls, 60 s for the rest), not trade by trade: a very brief
  spike or dip can be missed.
- **Base's graduated list is mostly Zora content coins** (a dozen holders each); the holder and trading gates remove them.
- **GMGN's labels are GMGN's.** Insider, bundler, entrapment and smart-money tags are its own wallet classification.
- **Liquidity is thin at these market caps.** A result on paper is not a fill; size matters.

## 9. Layout

```
main.py                    entry point
config.yaml                every threshold
gemhunter/engine.py        the loop: discovery, snapshots, deep looks, calls, trade plan, grading, retraining
gemhunter/factors.py       every factor
gemhunter/scoring.py       gates + scorecard
gemhunter/trade.py         grading a snapshot; a call's trade plan (updates, stops, safety exits)
gemhunter/model.py         learner: time-split test, promotion, threshold, lift tables
gemhunter/wallets.py       own proven wallets and deployer records
gemhunter/notify.py        Telegram: entry, updates, warnings, exit, daily summary
gemhunter/sources/         GMGN, DexScreener, GoPlus, CoinGecko
gemhunter/web/             dashboard
gemhunter/export.py        daily research files served at /api/research
tools/sync_research.py     PC: daily copy of the research files + traits report
tools/analyze.py           PC: what the runners had in common
tools/retrain.py           PC: ask the VPS for a retrain
tools/probe_gmgn.py        PC: check GMGN routes and fields with your key
tests/                     21 tests
```
