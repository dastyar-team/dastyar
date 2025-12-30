#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_INSTALL="${SCRIPT_DIR}/scripts/install_ubuntu_22.sh"
REMOTE_INSTALL="https://raw.githubusercontent.com/dastyar-team/dastyar/main/scripts/install_ubuntu_22.sh"

if [[ -f "${LOCAL_INSTALL}" ]]; then
  exec bash "${LOCAL_INSTALL}"
fi

tmp_path="/tmp/dastyar_install.sh"
curl -fsSL "${REMOTE_INSTALL}" -o "${tmp_path}"
exec bash "${tmp_path}"
