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
   deep look: token page, security, deployer history, holders, traders (GMGN) · candles (GeckoTerminal) · GoPlus ·
              Telegram group, website + domain age, GitHub, CoinGecko listing, DefiLlama usage (all free)
                                                                             │ gates (final) + scorecard / model
                                                                             ▼
                              CALL ─► Telegram entry ─► trade plan on live prices ─► updates, warnings, exit
every snapshot gets two grades: FAST (1.5x before -30%, within a day) and FULL (3x before -50%, within 7 days)
every 3 h: the scorecard re-weights itself from the fast grades (from 5 winners on)
every 12 h: retrain a model on the full grades ─► test on the newest unseen data ─► switch only if better
```

**Signals start on day one.** Nothing waits for a model: the scorecard calls as soon as 200 snapshots exist to rank
against (minutes after the first start on a free key of its own; longer on a shared one, see "Known limits").

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
| 8 Price action and lifecycle | `p_` | GeckoTerminal hourly candles (GMGN's as fallback), lists, signals | lists every token; candles at the deep look |
| 9 Social and attention | `a_` | GMGN lists and signals; Telegram public page; website; own sightings over time | lists every token; pages at the deep look |
| 10 Narrative and market context | `m_`, `mk_`, `narrative` | the watched market itself; CoinGecko (BTC, native coin, categories) | every token |
| 11 Fundamentals | `fu_`, `a_web_*` | website, RDAP domain record, GitHub, CoinGecko, DefiLlama | deep look, scored only when the token claims a product |

### What used to need paid data, and how it is measured for free

| Spec factor | Paid source it would need | What is measured instead |
|---|---|---|
| Mention velocity | X API / social-listening feed | Growth per hour of GMGN's Telegram call count, call-out count, page visits and X followers between this program's own sightings (`a_tg_calls_per_h`, `a_callouts_per_h`, `a_visits_per_h`, `a_x_followers_per_h`) |
| Community activity | Telegram / Discord bot access | The group's public t.me page: members and how many are online now (`a_tg_members`, `a_tg_online_pct`); a broadcast-only channel is told apart from a group |
| X account quality | X API (about $200 a month) | From GMGN: followers, following and their ratio, renames, tokens the account promoted, deleted token posts, whether the "account" is just one post, X community (`a_x_*`, `d_x_*`). **Not measured: account age, engagement per post, bot replies** |
| Narrative fit | a judge | The theme read from name, ticker, description and CoinGecko categories (`narrative`: ai, dog, cat, political ...), and whether that theme is trading *now*: share of the watched volume flowing through other tokens that share a word with it, and how many recent winners did (`m_meta_heat`, `m_meta_winners_n`) |
| Meme quality | a judge | Shape only: ticker and name length, plain ticker, has an image, image and socials not copied (`a_symbol_len`, `a_name_words`, `a_has_logo`, `a_image_dup_n`). It cannot tell a good meme from a bad one |
| Original vs copycat | - | GMGN's `og` flag, plus this program's own count of tokens with the same ticker and whether this one is the oldest (`a_symbol_twins_n`, `a_symbol_oldest`) |
| Chain rotation, launchpad health | - | Own records: each chain's share of watched volume, and how often recent snapshots per chain / launchpad got a fast hit (`m_chain_vol_share`, `m_chain_fast_rate_3d`, `m_pad_fast_rate_7d`) |
| Product | manual check | Site live, on its own domain, how much text is on it, mentions of docs / app / audit, and the domain's age from the registry (`a_web_*`, `fu_web_*`). A site built entirely in JavaScript shows little text |
| Code activity | - | GitHub repository linked from the site or CoinGecko: last push, stars, age, fork or original (`fu_gh_*`) |
| On-chain usage, audit | - | DefiLlama: TVL and audits on record for the token (`fu_tvl_usd`, `fu_audits_n`); rare at this size |
| Listing catalysts | - | Listed on CoinGecko, its watchlist size (`fu_cg_listed`, `fu_cg_watchlist`) |

Still not captured, and not guessed: a doxxed team, partnerships confirmed by the partner, unlock calendars beyond the
lock ratio, and the three X items above. Every address fetched from a token's links is creator-written: only public
hosts are fetched, with size caps, and private or internal addresses are refused.

**Gates** (`gates:` in config.yaml, `gemhunter/scoring.py`): honeypot, tax over 10%, mint / freeze authority, unverified
or owner-controlled EVM contracts, owner powers, liquidity not burned or locked, rug ratio over 0.30, wash trading,
top 10 over 40%, bundlers / insiders / entrapment over 30%, dev team over 10%, creator over 5%, thin liquidity,
under 150 holders, almost no trading, serial deployer, a deployer whose earlier call ended on a safety exit.

**Scorecard** until a model has passed its tests, weighted in the spec's priority order: manipulation and distribution
30, smart money 20, order flow 18, cost basis 12, price structure 10, attention 7, liquidity 3, fundamentals 3. A group
with no data scores neutral.

## 4. How it improves itself

Three stages, each taking over only when it has earned it. Calls go out from the first.

| Stage | Decides calls | Starts | Learns from |
|---|---|---|---|
| 1 | Scorecard, weights from the spec | day one | - |
| 2 | **Tuned scorecard** | as soon as 5 winners are graded (hours) | fast grades, every 3 hours |
| 3 | Learned model | once it beats the tuned scorecard on unseen data (300 graded snapshots and 30 winners at least) | fast grades, every 12 hours |

1. **Grades.** Every snapshot gets a fast grade (1.5x before -30% within 24 hours, `label.fast`) and a full one (3x before
   -50% within 7 days, `label`). Fast grades arrive within hours and are what makes learning possible on day one.
2. **Scorecard tuning** (`learn.tune`, `gemhunter/tuning.py`). Every 3 hours each scorecard item is checked: does its mark
   rank winners above the rest? An item that does gains weight inside its group, one that points the wrong way loses
   it, groups are re-weighted the same way. The step is deliberately small while winners are few (with 5 winners a
   weight moves about a seventh of the way it would with hundreds), nothing is inverted or dropped, and the gates are
   never touched. The dashboard lists every item's separation and multiplier.
   **Only snapshots that passed every gate are learned from** (`learn.callable_only`, `learn.tune.callable_only`). Gated
   tokens (insiders 70%, dev holding 88% ...) often pump 3-10x within the hour and then go to zero; counted as winners in
   the first two days they taught the tuner that heavy manipulation is good.
3. **Model retrain** (`learn.auto_retrain`, `AUTO_RETRAIN`) every 12 hours once there are 300 graded snapshots and 30
   winners. It learns the fast grade (`learn.target: fast`): in the first two days 1 of 335 gate-passing snapshots reached
   3x before -50%, too few to learn from.
   History is split by time: fit on the oldest 70%, test on the newest 15% it never saw. The new model replaces what
   is in use only if it clearly beats the scorecard, beats luck, and is no worse than the current model.
4. **Readable output.** Dashboard tab "What it has learned": for every factor, the hit rate in each value range - what
   matters, without trusting a model. Tab "Scoreboard": whether higher scores really hit more, and whether each gate
   removes losers.
5. **Own proven wallets.** The earliest real buyers of every deep-looked token are recorded; when the token is graded
   they are credited. Wallets early in winners far more often than average become "proven" and count as a factor.
6. **Own deployer record.** How each deployer's tokens ended here; a safety exit blocks that deployer for good.

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
   | `DASHBOARD_PASSWORD` | yes | the value already in this PC's `.env` (generated there; the sync signs in with it) |
   | `GMGN_WEIGHT_PER_SEC`, `GMGN_BUDGET`, `GMGN_SHARED_USE` | optional | the key's plan (5 / 20 / 50), this program's share of it (default 0.2), another program's share (default 0.8) |
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

- **The GMGN key is shared with Launch Radar and is saturated.** Measured on 2026-11-11: the key was already banned
  before Gem Hunter had sent a single request (45 minutes after its last one), GMGN's header showed 0-2 units left of 5
  after almost every request, and in four 8-minute runs 9-15% of requests were refused. A ban stops *both* programs.
  What Gem Hunter does about it: waits for the bucket to refill before every request; stays away 1-4 minutes after a
  ban; never waits on GMGN for a single token (a deep look during a ban goes on with the list figures, free candles,
  GoPlus and the community sources, and records what was missing in `deep_page` / `deep_holders` / `deep_traders`);
  takes candles from GeckoTerminal instead. The result: it runs and calls, but sweeps are slow and incomplete (one run
  reached only Solana, Base, BSC and Robinhood) and holder / trader lists (weight 5, the whole free bucket) rarely answer.
  **A separate GMGN key for Gem Hunter, or a Plus plan, removes this** - then set `GMGN_BUDGET=0.7` (own key) or
  `GMGN_WEIGHT_PER_SEC=20`, and `GMGN_SHARED_USE=0`.
- **GMGN's graduated list does not always quote in USD.** On Meteora curve tokens its `price` and `liquidity` came in
  another unit (price 4e-12 on a $236K token), which made the next DexScreener price look like a 60-million-x hit; 94 such
  snapshots were removed on 2026-11-13. The price is now never taken from that list, every snapshot's price must agree
  with its market cap and with the last price seen, and a move of 25x or more in one poll counts only when the next poll
  confirms it.
- **The first hours run on the spec's weights**, scaled on one hour of live data, not on outcomes. Tuning starts with
  5 graded winners and moves in small steps; a learned model needs days.
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
gemhunter/tuning.py        the scorecard re-weighting itself from fast grades
gemhunter/model.py         learner: time-split test, promotion, threshold, lift tables
gemhunter/narrative.py     theme, theme heat, meme shape, original or copy
gemhunter/wallets.py       own proven wallets and deployer records
gemhunter/notify.py        Telegram: entry, updates, warnings, exit, daily summary
gemhunter/sources/         GMGN, DexScreener, GeckoTerminal, GoPlus, CoinGecko, social.py (Telegram, website, RDAP,
                           GitHub, CoinGecko listing, DefiLlama)
gemhunter/web/             dashboard
gemhunter/export.py        daily research files served at /api/research
tools/sync_research.py     PC: daily copy of the research files + traits report
tools/analyze.py           PC: what the runners had in common
tools/retrain.py           PC: ask the VPS for a retrain
tools/probe_gmgn.py        PC: check GMGN routes and fields with your key
tests/                     33 tests
```
