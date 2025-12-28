# Dastyar

Telegram bot for DOI processing and Open Access delivery. It fetches metadata (Crossref/OpenAlex),
classifies papers (Groq optional), attempts Open Access PDF retrieval, and sends results via Telegram.
It also exposes a local API for the Chrome extension to detect DOI on web pages and submit them to the bot.

## Features
- Telegram bot UI with account, quota, and admin controls.
- DOI normalization, metadata enrichment, and category classification.
- Open Access PDF discovery (OpenAlex, Unpaywall, Crossref, landing pages).
- Optional provider-based fallback and Selenium automation.
- Local API for Chrome extension integration.
- ZIP output with summary PDF report.

## Project Structure
- `doi/mainbot.py` - Telegram bot entry point.
- `downloadmain.py` - Core logic: config, DB, DOI processing, downloads.
- `api_server.py` - Local API for the Chrome extension.
- `downloaders/` - Selenium automation helpers (IranPaper/ScienceDirect).
- `chrome_extension/` - Chrome extension UI and scripts.
- `v2ray_helper.py` and `v2ray/` - Optional proxy tooling.
- `utils/zip_report.py` - ZIP + summary report builder.

## Requirements
Recommended: Docker (Ubuntu 22.04 tested).

If running locally without Docker:
- Python 3.11+
- Google Chrome or Chromium
- Chromedriver (or set `CHROMEDRIVER_PATH`)
- Optional: V2Ray binary if you use proxy configs

## Quick Start (Docker)
1) Clone the repo and create `.env` (see the Configuration section).
2) Build and start:
```bash
docker compose up -d --build
```

### HTTPS Reverse Proxy (Optional)
If you set `PUBLIC_DOMAIN`, you can enable the Caddy proxy:
```bash
docker compose --profile proxy up -d --build
```
Make sure ports 80/443 are open.

## One-Command Install (Ubuntu 22.04 + Docker)
This script installs Docker, pulls the repo, asks for key config values, and starts the bot.

Note: this repository is private. Use one of the methods below.

### Method A: GitHub token (HTTPS)
```bash
curl -fsSL -H "Authorization: token <TOKEN>" \
  https://raw.githubusercontent.com/hakam788/dastyar/main/scripts/install_ubuntu_22.sh | \
  sudo env GITHUB_TOKEN="<TOKEN>" bash
```

### Method B: SSH
```bash
git clone git@github.com:hakam788/dastyar.git
sudo env REPO_URL="git@github.com:hakam788/dastyar.git" bash dastyar/scripts/install_ubuntu_22.sh
```

## Local Run (Without Docker)
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python doi/mainbot.py
```

## Configuration (.env)
Required:
- `TELEGRAM_BOT_TOKEN` - Telegram bot token.

Recommended:
- `ADMIN_USER_ID` - Telegram user id for admin access.
- `GROQ_API_KEY` / `GROQ_MODEL` - AI classification.
- `SCINET_GROUP_CHAT_ID` - Group chat id for Sci-Net integration (if used).

Optional:
- `TWOCAPTCHA_API_KEY` - Captcha solving for Selenium.
- `API_ENABLED`, `API_HOST`, `API_PORT` - Local API for Chrome extension.
- `IRANPAPER_EMAIL_1..3`, `IRANPAPER_PASSWORD_1..3` - ScienceDirect automation.
- `LEGAL_PRE2022`, `LEGAL_2022PLUS` - Provider config (JSON array).
- `CHROME_HEADLESS`, `CHROMEDRIVER_PATH`, `CHROME_USE_UC` - Selenium options.

## Chrome Extension
1) Open Chrome > Extensions > Enable "Developer mode".
2) Load unpacked extension from `chrome_extension/`.
3) Set API base (default: `http://127.0.0.1:8787`).
4) Login with email/code from the bot, then send DOIs from pages.

## API Endpoints
All endpoints accept JSON POST:
- `/api/v1/login`
- `/api/v1/me`
- `/api/v1/doi_info`
- `/api/v1/submit_doi`

## Data and Logs
- `data/doi_bot.db` - SQLite database.
- `data/user_email_codes.json` - Stored email codes.
- `run_logs/` - Bot logs.

## Troubleshooting
- If Selenium fails, ensure Chrome/Chromium and chromedriver versions match.
- If Groq is disabled, set `GROQ_API_KEY`.
- If API is unreachable, check `API_HOST`, `API_PORT`, and Docker port mappings.

## Contributing
1) Fork the repo and create a feature branch.
2) Make changes and add tests where needed.
3) Open a PR with a clear description and screenshots/logs if relevant.

## Contributors
- hakam788
