#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/dastyar"
DEFAULT_REPO_URL="https://github.com/hakam788/dastyar.git"
REPO_URL="${REPO_URL:-$DEFAULT_REPO_URL}"
BRANCH="${BRANCH:-main}"
GITHUB_TOKEN="${GITHUB_TOKEN:-}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run as root (use sudo)."
  exit 1
fi

apt-get update
apt-get install -y --no-install-recommends ca-certificates curl git

if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi

systemctl enable --now docker

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker install failed."
  exit 1
fi

if [[ -z "${GITHUB_TOKEN}" && -t 0 ]]; then
  read -rsp "GitHub token (for private repo, leave empty if using SSH): " token_input
  echo
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
if [[ -n "${current_token}" ]]; then
  echo "Telegram bot token: current value is set."
else
  echo "Telegram bot token: current value is empty."
fi
read -rsp "Telegram bot token (leave empty to keep current): " token_input
echo
if [[ -n "${token_input}" ]]; then
  set_env_value TELEGRAM_BOT_TOKEN "${token_input}"
fi

current_admin_id="$(get_env_value ADMIN_USER_ID)"
if [[ -n "${current_admin_id}" ]]; then
  echo "Admin user id: current value is set."
else
  echo "Admin user id: current value is empty."
fi
read -rp "Admin Telegram user id (leave empty to keep current): " admin_input
if [[ -n "${admin_input}" && "${admin_input}" =~ ^[0-9]+$ ]]; then
  set_env_value ADMIN_USER_ID "${admin_input}"
elif [[ -n "${admin_input}" ]]; then
  echo "Invalid admin user id, skipping."
fi

current_domain="$(get_env_value PUBLIC_DOMAIN)"
if [[ -n "${current_domain}" ]]; then
  echo "Public domain: current value is set."
else
  echo "Public domain: current value is empty."
fi
read -rp "Public domain for HTTPS proxy (optional): " domain_input
if [[ -n "${domain_input}" ]]; then
  domain_input="${domain_input#http://}"
  domain_input="${domain_input#https://}"
  domain_input="${domain_input%%/*}"
  set_env_value PUBLIC_DOMAIN "${domain_input}"
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
if [[ -n "${final_domain}" ]]; then
  ${COMPOSE} --profile proxy up -d --build
else
  ${COMPOSE} up -d --build
fi

echo "Done."
echo "Check logs: ${COMPOSE} logs -f --tail=200 bot"
