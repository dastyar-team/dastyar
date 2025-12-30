#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/dastyar"
RAW_BASE="https://raw.githubusercontent.com/dastyar-team/dastyar/main"
INSTALL_REMOTE="${RAW_BASE}/scripts/install_ubuntu_22.sh"
UPDATE_REMOTE="${RAW_BASE}/scripts/update_ubuntu_22.sh"

if [[ ! -e /dev/tty ]]; then
  echo "No TTY detected. Run in an interactive terminal."
  exit 1
fi

if [[ ${EUID} -ne 0 ]]; then
  echo "Please run as root (use sudo)."
  exit 1
fi

prompt() {
  local text="$1"
  local out=""
  read -rp "$text" out </dev/tty || true
  printf "%s" "$out"
}

confirm() {
  local text="$1"
  local default="${2:-N}"
  local answer
  answer="$(prompt "$text")"
  if [[ -z "$answer" ]]; then
    answer="$default"
  fi
  case "$answer" in
    [Yy]*) return 0 ;;
    *) return 1 ;;
  esac
}

get_compose() {
  if docker compose version >/dev/null 2>&1; then
    echo "docker compose"
  elif command -v docker-compose >/dev/null 2>&1; then
    echo "docker-compose"
  else
    echo ""
  fi
}

show_status() {
  echo "Status:"
  if [[ -d "${APP_DIR}/.git" ]]; then
    echo "- Installed: yes"
    if command -v git >/dev/null 2>&1; then
      local rev
      rev="$(git -C "${APP_DIR}" rev-parse --short HEAD 2>/dev/null || true)"
      if [[ -n "$rev" ]]; then
        echo "- Revision: $rev"
      fi
    fi
  else
    echo "- Installed: no"
  fi
  local compose
  compose="$(get_compose)"
  if [[ -n "$compose" && -f "${APP_DIR}/docker-compose.yml" ]]; then
    echo "- Containers:"
    ${compose} -f "${APP_DIR}/docker-compose.yml" ps || true
  fi
}

run_install() {
  if [[ -f "${APP_DIR}/scripts/install_ubuntu_22.sh" ]]; then
    bash "${APP_DIR}/scripts/install_ubuntu_22.sh"
    return
  fi
  local tmp
  tmp="/tmp/dastyar_install.sh"
  curl -fsSL "${INSTALL_REMOTE}" -o "$tmp"
  bash "$tmp"
}

run_update() {
  if [[ -f "${APP_DIR}/scripts/update_ubuntu_22.sh" ]]; then
    bash "${APP_DIR}/scripts/update_ubuntu_22.sh"
    return
  fi
  local tmp
  tmp="/tmp/dastyar_update.sh"
  curl -fsSL "${UPDATE_REMOTE}" -o "$tmp"
  bash "$tmp"
}

run_logs() {
  local compose
  compose="$(get_compose)"
  if [[ -z "$compose" ]]; then
    echo "Docker Compose is not available."
    return
  fi
  if [[ ! -f "${APP_DIR}/docker-compose.yml" ]]; then
    echo "No docker-compose.yml found in ${APP_DIR}."
    return
  fi
  ${compose} -f "${APP_DIR}/docker-compose.yml" logs -f --tail=200 bot
}

run_remove() {
  if [[ ! -d "${APP_DIR}" ]]; then
    echo "Nothing to remove."
    return
  fi
  if ! confirm "Remove ${APP_DIR}? (y/N): " "N"; then
    echo "Canceled."
    return
  fi
  local compose
  compose="$(get_compose)"
  if [[ -n "$compose" && -f "${APP_DIR}/docker-compose.yml" ]]; then
    if confirm "Remove Docker volumes (database)? This deletes data. (y/N): " "N"; then
      ${compose} -f "${APP_DIR}/docker-compose.yml" down -v || true
    else
      ${compose} -f "${APP_DIR}/docker-compose.yml" down || true
    fi
  fi
  rm -rf "${APP_DIR}"
  echo "Removed ${APP_DIR}."
}

show_menu() {
  while true; do
    clear
    echo "========================================"
    echo " DASTYAR Installer"
    echo "========================================"
    show_status
    echo ""
    echo "1) Install or Reinstall"
    echo "2) Update"
    echo "3) Remove"
    echo "4) View Logs"
    echo "5) Exit"
    echo ""
    local choice
    choice="$(prompt "Select an option [1-5]: ")"
    case "$choice" in
      1) run_install ;;
      2) run_update ;;
      3) run_remove ;;
      4) run_logs ;;
      5) exit 0 ;;
      *) echo "Invalid option." ;;
    esac
    echo ""
    prompt "Press Enter to continue..."
  done
}

show_menu
