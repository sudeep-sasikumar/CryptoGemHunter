# Micro-Cap Gem Hunter — Factors

**Scope:** one tracker. Tokens with a market cap between **$100K and $700K** that could run to multi-million market cap.

This file lists only the factors to evaluate. How to build it is left to Claude Code.

**Notes on reading the tables**
- "GMGN field" is the GMGN data point that feeds the factor. Names are from GMGN's public API documentation and have not been checked against a live response with this API key. "—" means GMGN does not provide it and another source is needed.
- Levels shown are starting points to tune, not validated values. Where a level is GMGN's own, it says so.

---

## Priority order

1. **Manipulation and distribution** (groups 3 and 4): bundlers, insiders, snipers, entrapment, concentration, deployer history. Hardest to fake, most discriminating.
2. **Wallet quality and smart money** (group 6).
3. **Order flow** (group 7), with fees paid as the anchor for real demand.
4. **Holder cost basis and retention** (group 5).
5. **Price structure and lifecycle** (group 8).
6. **Attention, narrative and market context** (groups 9 and 10): these amplify a move more than they predict one.
7. **Contract safety** (group 1): mandatory pass/fail, but on Solana launchpad tokens nearly everything passes (mint and freeze are typically revoked by default and liquidity is burned at migration), so it removes few tokens there. It matters much more on EVM chains and for tokens not from a launchpad.
8. **Fundamentals** (group 11): only for tokens that claim a product.

---

## 1. Contract and token safety (pass/fail)

| Factor | What to look for | GMGN field |
|---|---|---|
| Honeypot | Fail if tokens cannot be sold | `is_honeypot` (BSC/Base only; empty on Solana means not applicable, not safe) |
| Buy / sell tax | Fail if either is high (start: over 10%) or changeable by the owner | `buy_tax`, `sell_tax` |
| Mint authority (Solana) | Fail if not revoked | `renounced_mint` |
| Freeze authority (Solana) | Fail if not revoked | `renounced_freeze_account` |
| Contract source verified (EVM) | Fail if unverified | `open_source` |
| Ownership renounced (EVM) | Fail if owner retains control and liquidity is not locked | `owner_renounced` |
| Owner powers | Blacklist, pause, max-tx/max-wallet changes, upgradeable proxy, hidden owner-only transfer functions | — (GoPlus / contract read) |
| Liquidity burned or locked | Fail if not burned and lock is low or short | `burn_status`, `locked_ratio`, `lock_percent` |
| Liquidity removal events | Any LP pulled since launch | — (pool history) |
| Rug risk score | Fail above 0.30 (GMGN's own high-risk level) | `rug_ratio` |
| Wash trading flag | Fail if flagged | `is_wash_trading` |
| Market cap in range | $100K–$700K | `usd_market_cap`, `market_cap` |

---

## 2. Liquidity and market structure

| Factor | What to look for | GMGN field |
|---|---|---|
| Liquidity in dollars | Enough to enter and exit; too thin is a slippage trap | `liquidity` |
| Liquidity ÷ market cap | Too low = fragile; unusually high at this size can be manufactured | `liquidity`, `market_cap` |
| Slippage on a realistic trade | Price impact of a $500 / $1K / $5K buy and sell | `base_reserve`, `quote_reserve`, `fee_ratio` |
| Fully diluted vs circulating value | Unlock overhang | `total_supply`, `circulating_supply`, `max_supply` |
| Number of pools and which DEX | Fragmented or single thin pool | `pool`, `exchange`, `biggest_pool_address` |
| Pool age | Time since the pool opened | `pool.creation_timestamp`, `open_timestamp` |
| Launchpad and migration status | Which launchpad; migrated or still on the curve; time taken to migrate | `launchpad_platform`, `launchpad_status`, `launchpad_progress`, `complete_timestamp`, `complete_cost_time`, `migration_market_cap` |
| Initial liquidity | Size at launch vs now | `initial_liquidity` |

---

## 3. Deployer and dev

| Factor | What to look for | GMGN field |
|---|---|---|
| Dev holding status | Still holding (sell-pressure risk) vs sold/closed | `creator_token_status`, `creator_close` |
| Dev and team share of supply | Lower is better (start: team under 10%, creator under 5%) | `dev_team_hold_rate`, `creator_balance_rate`, `creator_hold_rate`, `creator_token_balance` |
| Dev has been selling | Timing and size of dev sells | `creator_token_status`, top-holders list filtered by tag `dev` |
| Dev burned tokens | Share burned | `dev_token_burn_ratio` |
| Number of tokens previously created | Serial deployers are the strongest rug marker | `creator_open_count`, created-tokens `inner_count` + `open_count` |
| Graduation ratio of previous tokens | Share that reached the open market | created-tokens `open_ratio` |
| Best previous token | Highest all-time-high market cap achieved | `creator_ath_info.ath_mc`, `ath_token_info` |
| Outcome of each previous token | Dead, rugged, or sustained | created-tokens `tokens[]` (`market_cap`, `token_ath_mc`, `holders`, `bundler_rate`) |
| Time since last creation | Launching tokens back to back | `last_create_timestamp` |
| Dev wallet funding source | Fresh exchange withdrawal, mixer, or a wallet tied to earlier rugs | `fund_from`, `fund_from_ts` |
| Creator fee behaviour | Dev earning fees rather than dumping; who receives fees | `fee_distribution` (Pump / Bankr tokens) |
| Dev's own record in your past calls | Same deployer seen before, and how it ended | — (own history) |

---

## 4. Holder distribution and manipulation

| Factor | What to look for | GMGN field |
|---|---|---|
| Top-10 holder share | Excluding pool, burn and launchpad addresses (start: fail above 40%) | `top_10_holder_rate` |
| Bundled buys | Share of supply/volume bought in bundles at launch (GMGN "safe" level: under 0.30) | `bundler_trader_amount_rate`, `bundler_rate`, `top_bundler_trader_percentage`, `wallet_tags_stat.bundler_wallets` |
| Insider ("rat trader") share | Wallets trading on inside knowledge (GMGN "safe" level: under 0.30) | `rat_trader_amount_rate`, `suspected_insider_hold_rate`, `top_rat_trader_percentage`, `wallet_tags_stat.rat_trader_wallets` |
| Entrapment ratio | Trading pattern built to trap buyers (start: under 0.30) | `entrapment_ratio`, `top_entrapment_trader_percentage` |
| Snipers | How many, how much they hold, whether they have exited | `sniper_count`, `top70_sniper_hold_rate`, `wallet_tags_stat.sniper_wallets` |
| Bundler selling | Bundlers starting to unload | signal type 10 (bundler sell) |
| Wallet clusters | Top holders funded from the same source = one entity behind many wallets | holders `native_transfer.address`, `is_suspicious` |
| Fresh-wallet share | High share of brand-new wallets points to farmed holders | `fresh_wallet_rate`, `wallet_tags_stat.fresh_wallets`, holders `is_new` |
| Hidden supply | Supply parked in private vaults | `private_vault_hold_rate` |
| Holder count | Absolute number | `holder_count` |
| Holder growth | New holders per hour, accelerating or slowing | `holder_count` over time |
| Distribution trend | Supply spreading out (healthy) or concentrating | `top_10_holder_rate` over time |
| Whales | Count and whether they hold or add through dips | `wallet_tags_stat.whale_wallets`, holders `netflow_usd` |

---

## 5. Holder cost basis and retention

| Factor | What to look for | GMGN field |
|---|---|---|
| Supply sitting at large profit | Holders up 5x–10x+ are future sell pressure | holders `avg_cost`, `unrealized_pnl`, `unrealized_profit`, `amount_percentage` |
| Holder base near current price | Supply that changed hands near today's price after a pullback is a healthier base | holders `avg_cost` vs current `price` |
| Early-buyer retention | Share of early buyers still holding | holders `start_holding_at`, `end_holding_at`, `sell_amount_percentage` |
| Paper hands vs holders | Token-level behaviour tags on wallets | holders `maker_token_tags` |
| Tokens received by transfer, not bought | Airdropped or seeded positions | holders `transfer_in`, `history_transfer_in_amount`, `transfer_in_count` |
| Profit already taken | How much the top traders have realised | traders `realized_profit`, `history_sold_income` |

---

## 6. Smart money and wallet quality

| Factor | What to look for | GMGN field |
|---|---|---|
| Smart-money wallets in the token | Count of GMGN-tagged smart wallets | `smart_degen_count`, `wallet_tags_stat.smart_wallets`, holders/traders tag `smart_degen` |
| Bought vs transferred in | Count a smart wallet only if it actually bought; devs send tokens to known wallets to fake this | holders `buy_tx_count_cur`, `buy_volume_cur`, `transfer_in` |
| Smart-money convergence | Three or more smart wallets buying the same token within about 30 minutes | smart-money trade feed (`maker`, `side`, `base_address`, `timestamp`), signal type 12 |
| Smart-money position size and direction | Opening/adding vs reducing/closing | `is_open_or_close`, `amount_usd`, holders `netflow_usd` |
| Smart-money entry vs current price | How late you are relative to them | holders `avg_cost` |
| Smart money exiting | Early smart buyers now selling or fully closed | smart-money feed `side` = sell, holders `end_holding_at` |
| Track record of the first buyers | Win rate and profit of the earliest wallets | wallet stats `winrate`, `pnl`, `realized_profit`; wallet profits `total_realized_profit` |
| Wallet age and funding source | Old, funded wallets vs fresh ones | wallet `created_at`, `fund_from`, `fund_amount` |
| KOL wallets and KOL buys | Capture, but treat as neutral until your own data shows whether they precede runs or mark exit liquidity | `renowned_count`, `wallet_tags_stat.renowned_wallets`, KOL trade feed, signal type 20 |
| KOL in, smart money out | Divergence suggests distribution | KOL feed + smart-money feed |
| Blue-chip holder share | Holders who also hold established tokens | `bluechip_owner_percentage`, tag `bluechip_owner` |
| Your own proven-wallet list | Wallets that were early in your past winners; more durable than public tags, which are crowded and gamed | — (own history) |

---

## 7. Volume and order flow

| Factor | What to look for | GMGN field |
|---|---|---|
| Volume ÷ liquidity | Turnover relative to pool depth | `volume_1h`, `volume_24h`, `liquidity` |
| Volume ÷ market cap | Activity relative to size | `volume_*`, `market_cap` |
| Buy/sell ratio by trade count | | `buys_*`, `sells_*` (1m, 5m, 1h, 6h, 24h) |
| Buy/sell ratio by dollar value | Often disagrees with the count ratio; the gap is informative | `buy_volume_*`, `sell_volume_*` |
| Net inflow and its trend | Net buying per 5m / 1h / 24h, accelerating or fading | `net_buy_24h`, buy minus sell volume per window, traders `netflow_usd` |
| Swap count and acceleration | 1m vs 1h vs 24h run-rate | `swaps_1m`, `swaps_1h`, `swaps_24h` |
| Unique buyers vs unique sellers | Breadth of demand | top-traders list |
| Trade-size distribution | Many small retail buys is healthy; a few huge buys is not | top-traders list (`buy_volume_cur`, `buy_tx_count_cur`) |
| Fees paid relative to market cap | Costly-to-fake proxy for real demand; wash volume is cheap, fees are not | `total_fee` (trenches filter), `gas_fee` |
| Bot share of activity | Sandwich/arbitrage bots vs real buyers | `bot_degen_rate`, `bot_degen_count`, tag `dex_bot` |
| Wash-trading pattern | Same wallets cycling, round-number repeats | `is_wash_trading` |
| Large and repeated buys | Unusually large or clustered buys | signal types 14, 15, 16 |
| Trending rank | Position and heat on GMGN's ranking | `rank`, `hot_level` |

---

## 8. Price action and lifecycle

| Factor | What to look for | GMGN field |
|---|---|---|
| Token age | Time since creation and since open-market trading began | `creation_timestamp`, `open_timestamp` |
| Survived the first dump | Most tokens die in the first hours | candles, `ath_price` |
| Depth of pullback from the high | How far it retraced and whether it held | candles, `ath_price`, `history_highest_market_cap` |
| Base quality | Higher lows, tightening range before expansion | candles |
| Multiple from launch price | Already far extended means much is priced in | candles, `price` |
| Distance to all-time high | Room overhead, or breaking into new highs | `ath_price`, signal type 7 |
| Market-cap level breaks | Clearing key levels tends to bring a new wave of buyers | signal type 8 |
| Price spike events | Sudden moves with or without volume behind them | signal types 1 and 6, `price_change_percent*` |
| Renewed activity in an older token | Volume and holders growing again after a quiet period | `swaps_*`, `volume_*`, `holder_count` over time |
| Community takeover | Dev left, community took over; can be strong when real | `cto_flag`, signal type 11 |

---

## 9. Social and attention

| Factor | What to look for | GMGN field |
|---|---|---|
| Has real socials | X, Telegram, website present and verified | `has_at_least_one_social`, `link.*`, `link.verify_status` |
| X follower count | Size of the account | `x_user_follower` |
| X account quality | Account age, engagement-to-follower ratio, bot replies | — (X data) |
| Recycled X account | Handle renamed from earlier tokens | `twitter_name_change_history`, `twitter-rename-count` filter |
| Deleted token posts | Account deleted posts about earlier tokens | `twitter_del_post_token_count` |
| Tokens promoted by the same account | One account pushing many tokens | `twitter_create_token_count` |
| Duplicate socials or image | Links or artwork copied from another token | filters `not_social_dup`, `not_image_dup` |
| Search heat | How many people are looking the token up, and the trend | hot-searches `visiting_count` |
| Telegram call count | Calls across Telegram groups | `tg_call_count` filter |
| Mention velocity | Rate of change of mentions on X and Telegram | — (social data) |
| Community activity | Members actually posting vs member count | — (Telegram/Discord) |
| Platform calls | Called on the platform | signal types 13 and 19 |
| Live stream | Token has an active live stream | signal type 9 |
| DexScreener paid profile, boost, ads, trending bar | Shows the dev is spending; weak signal because it can be bought | `dexscr_ad`, `dexscr_update_link`, `dexscr_boost_fee`, `dexscr_trending_bar`, signal types 2–5 |
| Original vs copycat | When several tokens share a ticker, the original usually takes the flow | `og` |
| Meme quality | Simple, repeatable, image-driven, fits current culture | `link.description` (judgement) |

---

## 10. Narrative, meta and market context

| Factor | What to look for | GMGN field |
|---|---|---|
| Fit with the current narrative | AI agents, a new chain's ecosystem, political or culture memes, whatever is live | — (name/description + judgement) |
| Chain rotation | Where new money is flowing; early tokens on a newly live chain are their own category | — |
| Launchpad health | Launches per hour, graduation rate, total launchpad volume | trenches feed (aggregate) |
| Overall market | BTC trend, chain native coin trend, risk-on vs risk-off | — |
| Time of day / day of week | Activity cycles | — |
| Listing and programme catalysts | Chance of a tier-2 exchange listing or an exchange "alpha" programme | — |
| Fee-claim events | Creator or platform fee claims | signal types 17, 18, 21 |

---

## 11. Fundamentals (only for tokens claiming a product)

| Factor | What to look for | GMGN field |
|---|---|---|
| Team | Doxxed or with a verifiable track record | — |
| Product | Something actually live and used | `link.website` (manual check) |
| On-chain usage | Fees, revenue, TVL, active users | — |
| Code activity | Real repository with real commits, not a costume | — |
| Tokenomics | Allocations, vesting, unlock calendar, treasury wallets | `locked_ratio`, supply fields |
| Audit | From a firm that can be verified | — |
| Partnerships | Confirmed by the partner, not only announced by the project | — |
