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
- Download bot for deep links with channel gating, countdown, and auto-delete.

## Project Structure
- `doi/mainbot.py` - Telegram bot entry point.
- `doi/download_bot.py` - Secondary bot for file delivery via links.
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
- MySQL (if you switch from SQLite)

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
Interactive prompts require a TTY; if you see no prompts, download the script and run it with `sudo bash`.

### Public Repo (One Line)
```bash
curl -fsSL https://raw.githubusercontent.com/dastyar-team/dastyar/main/install.sh | sudo bash
```

### SSH (Optional)
```bash
git clone git@github.com:dastyar-team/dastyar.git
sudo env REPO_URL="git@github.com:dastyar-team/dastyar.git" bash dastyar/scripts/install_ubuntu_22.sh
```

### Non-Interactive (Single Command)
Set variables inline to skip prompts:
```bash
curl -fsSL https://raw.githubusercontent.com/dastyar-team/dastyar/main/scripts/install_ubuntu_22.sh | \
  sudo env \
  ALLOW_NONINTERACTIVE=1 \
  TELEGRAM_BOT_TOKEN="<MAIN_BOT_TOKEN>" \
  SCINET_GROUP_CHAT_ID="<SCINET_GROUP_CHAT_ID>" \
  DOWNLOAD_BOT_TOKEN="<DOWNLOAD_BOT_TOKEN>" \
  DOWNLOAD_BOT_USERNAME="<DOWNLOAD_BOT_USERNAME>" \
  ADMIN_USER_ID="<ADMIN_USER_ID>" \
  RESEND_API_KEY="<RESEND_API_KEY>" \
  FROM_EMAIL="<FROM_EMAIL>" \
  SECRET_KEY="<SECRET_KEY>" \
  GROQ_API_KEY="<GROQ_API_KEY>" \
  GROQ_MODEL="<GROQ_MODEL>" \
  TWOCAPTCHA_API_KEY="<TWOCAPTCHA_API_KEY>" \
  IRANPAPER_EMAIL_1="<IRANPAPER_EMAIL_1>" \
  IRANPAPER_PASSWORD_1="<IRANPAPER_PASSWORD_1>" \
  DB_NAME="Dastyar" DB_USER="dastyar" DB_PASSWORD="<DB_PASSWORD>" DB_ROOT_PASSWORD="<DB_ROOT_PASSWORD>" \
  bash
```

### Update (One Line)
```bash
curl -fsSL https://raw.githubusercontent.com/dastyar-team/dastyar/main/scripts/update_ubuntu_22.sh | sudo bash
```

## Local Run (Without Docker)
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python doi/mainbot.py
```

## Configuration (.env)
Copy `.env.example` to `.env` and fill in the values.

Required:
- `TELEGRAM_BOT_TOKEN` - Telegram bot token.

Recommended:
- `DB_TYPE` - Database backend (`mysql` or `sqlite`).
- `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_ROOT_PASSWORD` - MySQL credentials.
- `DB_HOST`, `DB_PORT` - MySQL connection host/port.
- `DOWNLOAD_BOT_TOKEN` - Token for the download bot (secondary bot, required if you use download links).
- `DOWNLOAD_BOT_USERNAME` - Username of the download bot (without `@`).
- `DOWNLOAD_LINK_TTL_HOURS` - Link expiration time (default: 48).
- `DOWNLOAD_LINK_REQUIRE_SAME_USER` - Limit link to the original user (default: 1).
- `DOWNLOAD_LINK_DELETE_ON_SEND` - Delete the file after delivery (default: 1).
- `DOWNLOAD_LINK_DIR` - Directory for prepared ZIP files (default: `data/downloads`).
- `DOWNLOAD_REQUIRED_CHANNELS` - Comma-separated channel usernames/IDs to join.
- `DOWNLOAD_REQUIRED_CHANNEL_LINKS` - Optional join links for private channels.
- `DOWNLOAD_CHANNELS_ENFORCED` - Enforce channel membership check (default: 1).
- `DOWNLOAD_DELETE_DELAY_S` - Delete delay after sending (default: 60 seconds).
- `DOWNLOAD_COUNTDOWN_ENABLED` - Show countdown message (default: 1).
- `ADMIN_USER_ID` - Telegram user id for admin access.
- `GROQ_API_KEY` / `GROQ_MODEL` - AI classification.
- `SCINET_GROUP_CHAT_ID` - Group chat id for Sci-Net integration (if used).
- `RESEND_API_KEY` / `FROM_EMAIL` / `SECRET_KEY` - Email OTP verification settings.

Optional:
- `TWOCAPTCHA_API_KEY` - Captcha solving for Selenium.
- `API_ENABLED`, `API_HOST`, `API_PORT` - Local API for Chrome extension.
- `IRANPAPER_EMAIL_1..3`, `IRANPAPER_PASSWORD_1..3` - ScienceDirect automation.
- `LEGAL_PRE2022`, `LEGAL_2022PLUS` - Provider config (JSON array).
- `CHROME_HEADLESS`, `CHROMEDRIVER_PATH`, `CHROME_USE_UC` - Selenium options.

Notes:
- The install script prompts for DB credentials on the VPS and writes them into `.env`.
- For local runs, fill in `DB_*` values manually in `.env`.
- Database tables are created automatically on first start.
- Do not commit `.env` to a public repo; use `.env.example` as a template.

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
- `mysql_data` - Docker volume for MySQL data.
- `data/downloads` - Prepared ZIPs for download bot links.

## phpMyAdmin
The docker-compose file exposes phpMyAdmin on `http://127.0.0.1:8081` by default.
Login with:
- Host: `db`
- User: `root`
- Password: `DB_ROOT_PASSWORD`
On a remote VPS, use an SSH tunnel:
```bash
ssh -L 8081:127.0.0.1:8081 user@server
```

## Download Bot (Deep Links)
When `DOWNLOAD_BOT_USERNAME` is set, the main bot sends a link like:
```
https://t.me/<download_bot>?start=<token>
```
The user opens it, clicks Start, and the file is delivered by the download bot.
If `ADMIN_USER_ID` is set, the admin can bypass the same-user restriction.

Channel gating:
- Set `DOWNLOAD_REQUIRED_CHANNELS` to channel usernames or IDs (comma-separated).
- If channels are private, set `DOWNLOAD_REQUIRED_CHANNEL_LINKS` with join links.

Auto delete + countdown:
- `DOWNLOAD_DELETE_DELAY_S=60` removes the file/message after 60 seconds.
- `DOWNLOAD_COUNTDOWN_ENABLED=1` shows the second-by-second countdown.

Admin menu:
- Send `/admin` in the download bot to manage admins, users, and bot settings.
- Settings are saved in the DB (`DOWNLOAD_BOT_CONFIG`) and can be toggled live.

The download bot runs alongside the main bot by default; ensure `DOWNLOAD_BOT_TOKEN` and
`DOWNLOAD_BOT_USERNAME` are set in `.env`.

## Troubleshooting
- If Selenium fails, ensure Chrome/Chromium and chromedriver versions match.
- If Groq is disabled, set `GROQ_API_KEY`.
- If API is unreachable, check `API_HOST`, `API_PORT`, and Docker port mappings.

## Contributing
1) Fork the repo and create a feature branch.
2) Make changes and add tests where needed.
3) Open a PR with a clear description and screenshots/logs if relevant.

## Contributors
- dastyar-team
