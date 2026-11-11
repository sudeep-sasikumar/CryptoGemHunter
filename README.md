# Gem Hunter

Tracks tokens with a market cap of $100K-$700K on Solana, Ethereum, Robinhood, Base and BSC, measures each one
against the factors in GEM_HUNTER_FACTORS.md, calls the few that pass, follows each call with a trade plan, grades
every snapshot, and retrains itself on the outcomes. **Signals only: it never trades and holds no keys.**

## Run on this PC
```
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
copy .env.example .env        (set GMGN_API_KEY)
.venv\Scripts\python tools\probe_gmgn.py     checks every GMGN route and field with your key
.venv\Scripts\python main.py                 dashboard: http://localhost:8020
.venv\Scripts\pytest -q
```

## Run on the VPS (same pattern as Launch Radar)
1. Push to `main`: GitHub Actions tests, builds and publishes `ghcr.io/sudeep-sasikumar/cryptogemhunter:latest`.
2. GitHub -> Packages -> `cryptogemhunter` -> make it public (once).
3. Hostinger -> Docker Manager -> Compose from URL:
   `https://raw.githubusercontent.com/sudeep-sasikumar/CryptoGemHunter/main/docker-compose.yaml`
4. Set `GMGN_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `DASHBOARD_PASSWORD` (others: see docker-compose.yaml).
5. Dashboard: `https://srv1479428.hstgr.cloud/gems/`

## Past data on this PC
`tools\sync_research.py` signs in to the dashboard with `DASHBOARD_PASSWORD` from `.env`, downloads one file per day
into `var\research\vps\` and writes `var\research\report-<date>.md`. Daily: `tools\install_daily_sync.ps1`.
`tools\retrain.py [--promote|--status]` asks the VPS for a retrain.

## Layout
`gemhunter/engine.py` loop · `factors.py` every factor · `scoring.py` gates + scorecard · `trade.py` trade plan and
grading · `model.py` learner · `wallets.py` proven wallets, deployers · `notify.py` Telegram · `web/` dashboard ·
`sources/` GMGN, DexScreener, GoPlus, CoinGecko · `config.yaml` every threshold.
