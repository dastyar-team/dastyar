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

if [[ ! -d "${APP_DIR}/.git" ]]; then
  echo "Directory ${APP_DIR} is not a git repo. Run the install script first."
  exit 1
fi

CLONE_URL="${REPO_URL}"
if [[ -n "${GITHUB_TOKEN}" && "${REPO_URL}" =~ ^https://github.com/ && "${REPO_URL}" != *"@"* ]]; then
  CLONE_URL="https://${GITHUB_TOKEN}@github.com/${REPO_URL#https://github.com/}"
fi

if [[ "${CLONE_URL}" != "${REPO_URL}" ]]; then
  git -C "${APP_DIR}" remote set-url origin "${CLONE_URL}"
fi
git -C "${APP_DIR}" fetch --all
git -C "${APP_DIR}" checkout "${BRANCH}"
git -C "${APP_DIR}" pull --ff-only
if [[ "${CLONE_URL}" != "${REPO_URL}" ]]; then
  git -C "${APP_DIR}" remote set-url origin "${REPO_URL}"
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
${COMPOSE} up -d --build

echo "Done."
echo "Check logs: ${COMPOSE} logs -f --tail=200 bot"
