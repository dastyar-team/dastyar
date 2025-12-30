#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/dastyar"
DEFAULT_REPO_URL="https://github.com/dastyar-team/dastyar.git"
REPO_URL="${REPO_URL:-$DEFAULT_REPO_URL}"
BRANCH="${BRANCH:-main}"
GITHUB_TOKEN="${GITHUB_TOKEN:-}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run as root (use sudo)."
  exit 1
fi

apt-get update
apt-get install -y --no-install-recommends ca-certificates curl git openssl

if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi

systemctl enable --now docker

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker install failed."
  exit 1
fi

read_prompt() {
  local __var="$1"
  local __prompt="$2"
  local __silent="${3:-0}"
  local __input=""
  if [[ -t 0 && -e /dev/tty ]]; then
    if [[ "${__silent}" == "1" ]]; then
      read -rsp "${__prompt}" __input </dev/tty || true
      echo </dev/tty
    else
      read -rp "${__prompt}" __input </dev/tty || true
    fi
  fi
  printf -v "${__var}" "%s" "${__input}"
}

generate_secret() {
  openssl rand -hex 32
}

generate_password() {
  openssl rand -hex 12
}

if [[ -z "${GITHUB_TOKEN}" ]]; then
  read_prompt token_input "GitHub token (for private repo, leave empty if using SSH): " 1
  if [[ -n "${token_input}" ]]; then
    GITHUB_TOKEN="${token_input}"
  fi
fi

if [[ -z "${REPO_URL}" ]]; then
  echo "Repo URL is required."
  exit 1
fi

if [[ -d "${APP_DIR}" && ! -d "${APP_DIR}/.git" ]]; then
  echo "Directory ${APP_DIR} exists but is not a git repo."
  exit 1
fi

CLONE_URL="${REPO_URL}"
if [[ -n "${GITHUB_TOKEN}" && "${REPO_URL}" =~ ^https://github.com/ && "${REPO_URL}" != *"@"* ]]; then
  CLONE_URL="https://${GITHUB_TOKEN}@github.com/${REPO_URL#https://github.com/}"
fi

if [[ -d "${APP_DIR}/.git" ]]; then
  if [[ "${CLONE_URL}" != "${REPO_URL}" ]]; then
    git -C "${APP_DIR}" remote set-url origin "${CLONE_URL}"
  fi
  git -C "${APP_DIR}" fetch --all || echo "Warning: git fetch failed, using existing files."
  git -C "${APP_DIR}" pull --ff-only || echo "Warning: git pull failed, using existing files."
  if [[ "${CLONE_URL}" != "${REPO_URL}" ]]; then
    git -C "${APP_DIR}" remote set-url origin "${REPO_URL}"
  fi
else
  git clone --branch "${BRANCH}" "${CLONE_URL}" "${APP_DIR}"
  if [[ "${CLONE_URL}" != "${REPO_URL}" ]]; then
    git -C "${APP_DIR}" remote set-url origin "${REPO_URL}"
  fi
fi

ENV_FILE="${APP_DIR}/.env"
if [[ ! -f "${ENV_FILE}" ]]; then
  touch "${ENV_FILE}"
fi

get_env_value() {
  local key="$1"
  awk -F= -v k="$key" '($1==k){sub(/^[^=]+=/,""); print; exit}' "${ENV_FILE}" || true
}

set_env_value() {
  local key="$1"
  local val="$2"
  if grep -q "^${key}=" "${ENV_FILE}"; then
    sed -i "s|^${key}=.*|${key}=${val}|" "${ENV_FILE}"
  else
    echo "${key}=${val}" >> "${ENV_FILE}"
  fi
}

current_token="$(get_env_value TELEGRAM_BOT_TOKEN)"
if [[ -n "${TELEGRAM_BOT_TOKEN:-}" ]]; then
  set_env_value TELEGRAM_BOT_TOKEN "${TELEGRAM_BOT_TOKEN}"
else
  if [[ -n "${current_token}" ]]; then
    echo "Telegram bot token: current value is set."
  else
    echo "Telegram bot token: current value is empty."
  fi
  read_prompt token_input "Telegram bot token (leave empty to keep current): " 1
  if [[ -n "${token_input}" ]]; then
    set_env_value TELEGRAM_BOT_TOKEN "${token_input}"
  fi
fi

current_admin_id="$(get_env_value ADMIN_USER_ID)"
if [[ -n "${ADMIN_USER_ID:-}" ]]; then
  set_env_value ADMIN_USER_ID "${ADMIN_USER_ID}"
else
  if [[ -n "${current_admin_id}" ]]; then
    echo "Admin user id: current value is set."
  else
    echo "Admin user id: current value is empty."
  fi
  read_prompt admin_input "Admin Telegram user id (leave empty to keep current): "
  if [[ -n "${admin_input}" && "${admin_input}" =~ ^[0-9]+$ ]]; then
    set_env_value ADMIN_USER_ID "${admin_input}"
  elif [[ -n "${admin_input}" ]]; then
    echo "Invalid admin user id, skipping."
  fi
fi

current_scinet_group="$(get_env_value SCINET_GROUP_CHAT_ID)"
if [[ -n "${SCINET_GROUP_CHAT_ID:-}" ]]; then
  set_env_value SCINET_GROUP_CHAT_ID "${SCINET_GROUP_CHAT_ID}"
else
  if [[ -n "${current_scinet_group}" ]]; then
    echo "SCINET group chat id: current value is set."
  else
    echo "SCINET group chat id: current value is empty."
  fi
  read_prompt scinet_input "SCINET group chat id (optional, leave empty to keep current): "
  if [[ -n "${scinet_input}" && "${scinet_input}" =~ ^-?[0-9]+$ ]]; then
    set_env_value SCINET_GROUP_CHAT_ID "${scinet_input}"
  elif [[ -n "${scinet_input}" ]]; then
    echo "Invalid SCINET group chat id, skipping."
  fi
fi

current_download_token="$(get_env_value DOWNLOAD_BOT_TOKEN)"
if [[ -n "${DOWNLOAD_BOT_TOKEN:-}" ]]; then
  set_env_value DOWNLOAD_BOT_TOKEN "${DOWNLOAD_BOT_TOKEN}"
else
  if [[ -n "${current_download_token}" ]]; then
    echo "Download bot token: current value is set."
  else
    echo "Download bot token: current value is empty."
  fi
  read_prompt download_token_input "Download bot token (leave empty to keep current): " 1
  if [[ -n "${download_token_input}" ]]; then
    set_env_value DOWNLOAD_BOT_TOKEN "${download_token_input}"
  fi
fi

current_download_user="$(get_env_value DOWNLOAD_BOT_USERNAME)"
if [[ -n "${DOWNLOAD_BOT_USERNAME:-}" ]]; then
  download_user_final="${DOWNLOAD_BOT_USERNAME#@}"
  set_env_value DOWNLOAD_BOT_USERNAME "${download_user_final}"
else
  if [[ -n "${current_download_user}" ]]; then
    echo "Download bot username: current value is set."
  else
    echo "Download bot username: current value is empty."
  fi
  read_prompt download_user_input "Download bot username (without @, leave empty to keep current): "
  if [[ -n "${download_user_input}" ]]; then
    download_user_input="${download_user_input#@}"
    set_env_value DOWNLOAD_BOT_USERNAME "${download_user_input}"
  fi
fi

current_resend_key="$(get_env_value RESEND_API_KEY)"
if [[ -n "${RESEND_API_KEY:-}" ]]; then
  set_env_value RESEND_API_KEY "${RESEND_API_KEY}"
else
  if [[ -n "${current_resend_key}" ]]; then
    echo "Resend API key: current value is set."
  else
    echo "Resend API key: current value is empty."
  fi
  read_prompt resend_input "Resend API key (leave empty to keep current): " 1
  if [[ -n "${resend_input}" ]]; then
    set_env_value RESEND_API_KEY "${resend_input}"
  fi
fi

current_from_email="$(get_env_value FROM_EMAIL)"
if [[ -n "${FROM_EMAIL:-}" ]]; then
  set_env_value FROM_EMAIL "${FROM_EMAIL}"
else
  if [[ -n "${current_from_email}" ]]; then
    echo "From email: current value is set."
  else
    echo "From email: current value is empty."
  fi
  read_prompt from_email_input "From email (leave empty to keep current): "
  if [[ -n "${from_email_input}" ]]; then
    set_env_value FROM_EMAIL "${from_email_input}"
  fi
fi

current_secret="$(get_env_value SECRET_KEY)"
if [[ -n "${SECRET_KEY:-}" ]]; then
  set_env_value SECRET_KEY "${SECRET_KEY}"
else
  if [[ -n "${current_secret}" ]]; then
    echo "Secret key: current value is set."
  else
    echo "Secret key: current value is empty."
  fi
  read_prompt secret_input "Secret key for OTP (leave empty to auto-generate): " 1
  if [[ -n "${secret_input}" ]]; then
    set_env_value SECRET_KEY "${secret_input}"
  elif [[ -z "${current_secret}" ]]; then
    set_env_value SECRET_KEY "$(generate_secret)"
  fi
fi

current_groq_key="$(get_env_value GROQ_API_KEY)"
if [[ -n "${GROQ_API_KEY:-}" ]]; then
  set_env_value GROQ_API_KEY "${GROQ_API_KEY}"
else
  if [[ -n "${current_groq_key}" ]]; then
    echo "Groq API key: current value is set."
  else
    echo "Groq API key: current value is empty."
  fi
  read_prompt groq_input "Groq API key (optional, leave empty to keep current): " 1
  if [[ -n "${groq_input}" ]]; then
    set_env_value GROQ_API_KEY "${groq_input}"
  fi
fi

if [[ -n "${GROQ_MODEL:-}" ]]; then
  set_env_value GROQ_MODEL "${GROQ_MODEL}"
fi

current_twocaptcha_key="$(get_env_value TWOCAPTCHA_API_KEY)"
if [[ -n "${TWOCAPTCHA_API_KEY:-}" ]]; then
  set_env_value TWOCAPTCHA_API_KEY "${TWOCAPTCHA_API_KEY}"
else
  if [[ -n "${current_twocaptcha_key}" ]]; then
    echo "2Captcha API key: current value is set."
  else
    echo "2Captcha API key: current value is empty."
  fi
  read_prompt twocaptcha_input "2Captcha API key (optional, leave empty to keep current): " 1
  if [[ -n "${twocaptcha_input}" ]]; then
    set_env_value TWOCAPTCHA_API_KEY "${twocaptcha_input}"
  fi
fi

current_iran_email="$(get_env_value IRANPAPER_EMAIL_1)"
if [[ -n "${IRANPAPER_EMAIL_1:-}" ]]; then
  set_env_value IRANPAPER_EMAIL_1 "${IRANPAPER_EMAIL_1}"
else
  if [[ -n "${current_iran_email}" ]]; then
    echo "IranPaper email (1): current value is set."
  else
    echo "IranPaper email (1): current value is empty."
  fi
  read_prompt iran_email_input "IranPaper email (1) (optional, leave empty to keep current): "
  if [[ -n "${iran_email_input}" ]]; then
    set_env_value IRANPAPER_EMAIL_1 "${iran_email_input}"
  fi
fi

current_iran_pass="$(get_env_value IRANPAPER_PASSWORD_1)"
if [[ -n "${IRANPAPER_PASSWORD_1:-}" ]]; then
  set_env_value IRANPAPER_PASSWORD_1 "${IRANPAPER_PASSWORD_1}"
else
  if [[ -n "${current_iran_pass}" ]]; then
    echo "IranPaper password (1): current value is set."
  else
    echo "IranPaper password (1): current value is empty."
  fi
  read_prompt iran_pass_input "IranPaper password (1) (optional, leave empty to keep current): " 1
  if [[ -n "${iran_pass_input}" ]]; then
    set_env_value IRANPAPER_PASSWORD_1 "${iran_pass_input}"
  fi
fi

current_db_type="$(get_env_value DB_TYPE)"
if [[ -z "${current_db_type}" || "${current_db_type}" != "mysql" ]]; then
  set_env_value DB_TYPE "mysql"
fi
set_env_value DB_HOST "db"
set_env_value DB_PORT "3306"

current_db_name="$(get_env_value DB_NAME)"
if [[ -n "${DB_NAME:-}" ]]; then
  db_name_final="${DB_NAME}"
else
  read_prompt db_name_input "DB name [${current_db_name:-Dastyar}]: "
  db_name_final="${db_name_input:-${current_db_name:-Dastyar}}"
fi
set_env_value DB_NAME "${db_name_final}"

current_db_user="$(get_env_value DB_USER)"
if [[ -n "${DB_USER:-}" ]]; then
  db_user_final="${DB_USER}"
else
  read_prompt db_user_input "DB user [${current_db_user:-dastyar}]: "
  db_user_final="${db_user_input:-${current_db_user:-dastyar}}"
fi
set_env_value DB_USER "${db_user_final}"

current_db_pass="$(get_env_value DB_PASSWORD)"
if [[ -n "${DB_PASSWORD:-}" ]]; then
  db_pass_final="${DB_PASSWORD}"
else
  if [[ -n "${current_db_pass}" ]]; then
    echo "DB password: current value is set."
  else
    echo "DB password: current value is empty."
  fi
  read_prompt db_pass_input "DB password (leave empty to keep current): " 1
  if [[ -n "${db_pass_input}" ]]; then
    db_pass_final="${db_pass_input}"
  elif [[ -n "${current_db_pass}" ]]; then
    db_pass_final="${current_db_pass}"
  else
    db_pass_final="$(generate_password)"
  fi
fi
set_env_value DB_PASSWORD "${db_pass_final}"

current_db_root_pass="$(get_env_value DB_ROOT_PASSWORD)"
if [[ -n "${DB_ROOT_PASSWORD:-}" ]]; then
  db_root_final="${DB_ROOT_PASSWORD}"
else
  if [[ -n "${current_db_root_pass}" ]]; then
    echo "DB root password: current value is set."
  else
    echo "DB root password: current value is empty."
  fi
  read_prompt db_root_input "DB root password (leave empty to keep current): " 1
  if [[ -n "${db_root_input}" ]]; then
    db_root_final="${db_root_input}"
  elif [[ -n "${current_db_root_pass}" ]]; then
    db_root_final="${current_db_root_pass}"
  else
    db_root_final="${db_pass_final}"
  fi
fi
set_env_value DB_ROOT_PASSWORD "${db_root_final}"

current_domain="$(get_env_value PUBLIC_DOMAIN)"
if [[ -n "${PUBLIC_DOMAIN:-}" ]]; then
  domain_input="${PUBLIC_DOMAIN#http://}"
  domain_input="${domain_input#https://}"
  domain_input="${domain_input%%/*}"
  set_env_value PUBLIC_DOMAIN "${domain_input}"
else
  if [[ -n "${current_domain}" ]]; then
    echo "Public domain: current value is set."
  else
    echo "Public domain: current value is empty."
  fi
  read_prompt domain_input "Public domain for HTTPS proxy (optional): "
  if [[ -n "${domain_input}" ]]; then
    domain_input="${domain_input#http://}"
    domain_input="${domain_input#https://}"
    domain_input="${domain_input%%/*}"
    set_env_value PUBLIC_DOMAIN "${domain_input}"
  fi
fi

if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="docker-compose"
else
  echo "Docker Compose is not available."
  exit 1
fi

cd "${APP_DIR}"

final_domain="$(get_env_value PUBLIC_DOMAIN)"
profiles=()
if [[ -n "${final_domain}" ]]; then
  profiles+=(--profile proxy)
fi
${COMPOSE} "${profiles[@]}" up -d --build

echo "Done."
echo "Check logs: ${COMPOSE} logs -f --tail=200 bot"
