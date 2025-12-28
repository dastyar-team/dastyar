from __future__ import annotations

import hashlib
import json
import os
import platform
import socket
import subprocess
import time
import urllib.request
import zipfile
from contextlib import suppress
from io import BytesIO
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlparse, parse_qs

import logging

LOGGER = logging.getLogger("doi_bot.v2ray")

BASE_DIR = Path("v2ray")
BIN_DIR = BASE_DIR / "bin"
CONF_DIR = BASE_DIR / "configs"
BIN_NAME = "v2ray"
INBOUND_PORTS = {"iran": 21870, "global": 21880}

_PROCS: Dict[str, subprocess.Popen] = {}
_CONF_HASH: Dict[str, str] = {}


def _archive_candidates() -> list[str]:
    system = (platform.system() or "").lower()
    arch = (platform.machine() or "").lower()
    is_arm = "arm" in arch or "aarch64" in arch
    base = "https://github.com/v2fly/v2ray-core/releases/latest/download/"

    if "linux" in system:
        if is_arm:
            names = ["v2ray-linux-arm64-v8a.zip", "v2ray-linux-arm64.zip"]
        else:
            names = ["v2ray-linux-64.zip", "v2ray-linux-amd64.zip"]
    elif "darwin" in system or "mac" in system:
        if is_arm:
            names = ["v2ray-macos-arm64-v8a.zip", "v2ray-macos-arm64.zip"]
        else:
            names = ["v2ray-macos-64.zip"]
    elif "windows" in system:
        if is_arm:
            names = ["v2ray-windows-arm64-v8a.zip", "v2ray-windows-arm64.zip"]
        else:
            names = ["v2ray-windows-64.zip"]
    else:
        names = ["v2ray-linux-64.zip"]

    return [base + name for name in names]


def _extract_binary_from_zip(data: bytes) -> None:
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(BytesIO(data)) as zf:
        for member in zf.namelist():
            name = Path(member).name
            if name in {"v2ray", "geoip.dat", "geosite.dat"}:
                target = BIN_DIR / name
                with zf.open(member) as src, open(target, "wb") as dst:
                    dst.write(src.read())
                if name == "v2ray":
                    os.chmod(target, 0o755)


def ensure_v2ray_installed() -> Path:
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    binary = BIN_DIR / BIN_NAME
    if binary.exists():
        return binary
    last_err: Optional[Exception] = None
    for url in _archive_candidates():
        try:
            LOGGER.info("downloading_v2ray | url=%s", url)
            with urllib.request.urlopen(url) as resp:
                data = resp.read()
            _extract_binary_from_zip(data)
            if binary.exists():
                return binary
        except Exception as exc:
            last_err = exc
            LOGGER.warning("v2ray_download_failed | url=%s err=%s", url, exc)
    raise RuntimeError("Failed to install v2ray binary") from last_err


def _write_config(name: str, config_text: str) -> Path:
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    path = CONF_DIR / f"{name}.json"
    path.write_text(config_text, encoding="utf-8")
    return path


def _extract_proxy_url(cfg_text: str) -> Optional[str]:
    try:
        data = json.loads(cfg_text)
    except Exception as exc:
        LOGGER.warning("v2ray_cfg_parse_failed | err=%s", exc)
        return None
    inbounds = data.get("inbounds")
    if not isinstance(inbounds, list):
        return None
    for item in inbounds:
        if not isinstance(item, dict):
            continue
        protocol = (item.get("protocol") or "").lower()
        port = item.get("port")
        if not port:
            continue
        try:
            port = int(port)
        except Exception:
            continue
        if protocol == "socks":
            return f"socks5://127.0.0.1:{port}"
        if protocol == "http":
            return f"http://127.0.0.1:{port}"
    return None


def _proxy_port(proxy_url: str) -> Optional[int]:
    try:
        parsed = urlparse(proxy_url)
        return parsed.port
    except Exception:
        return None


def _restart_process(name: str, binary: Path, config_path: Path) -> subprocess.Popen:
    proc = _PROCS.get(name)
    if proc and proc.poll() is None:
        proc.terminate()
        proc.wait(timeout=5)
    stdout = open(CONF_DIR / f"{name}.log", "ab")
    proc = subprocess.Popen(
        [str(binary), "run", "-config", str(config_path)],
        stdout=stdout,
        stderr=subprocess.STDOUT,
    )
    _PROCS[name] = proc
    return proc


def _wait_for_port(port: Optional[int], timeout: float = 6.0) -> None:
    if not port:
        return
    deadline = time.time() + timeout
    while time.time() < deadline:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.5)
        try:
            sock.connect(("127.0.0.1", port))
            sock.close()
            return
        except OSError:
            time.sleep(0.2)
        finally:
            with suppress(Exception):
                sock.close()


def _build_inbound(region: str) -> Dict[str, any]:
    port = INBOUND_PORTS.get(region, 21900)
    return {
        "listen": "127.0.0.1",
        "port": port,
        "protocol": "socks",
        "settings": {"udp": True, "auth": "noauth"},
        "sniffing": {"enabled": True, "destOverride": ["http", "tls"]},
    }


def _share_to_config(text: str, region: str) -> Optional[str]:
    url = text.strip()
    parsed = urlparse(url)
    if parsed.scheme.lower() != "vless":
        LOGGER.warning("v2ray_share_unsupported_scheme | scheme=%s", parsed.scheme)
        return None
    if not parsed.hostname or not parsed.username:
        LOGGER.warning("v2ray_share_missing_fields")
        return None
    qs = parse_qs(parsed.query)
    network = (qs.get("type", ["tcp"])[0] or "tcp").lower()
    security = (qs.get("security", ["none"])[0] or "none").lower()
    header_type = (qs.get("headerType", ["none"])[0] or "none").lower()
    host_override = qs.get("host", [None])[0]
    path = qs.get("path", ["/"])[0] or "/"

    stream_settings: Dict[str, any] = {"network": network}
    if security != "none":
        stream_settings["security"] = security
        if host_override:
            stream_settings.setdefault("tlsSettings", {})["serverName"] = host_override
        elif parsed.hostname:
            stream_settings.setdefault("tlsSettings", {})["serverName"] = parsed.hostname

    if network == "tcp" and header_type == "http":
        stream_settings["tcpSettings"] = {
            "header": {
                "type": "http",
                "request": {
                    "path": [path],
                    "headers": {"Host": [host_override or parsed.hostname]},
                },
            }
        }

    config = {
        "log": {"loglevel": "warning"},
        "inbounds": [_build_inbound(region)],
        "outbounds": [
            {
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": parsed.hostname,
                            "port": parsed.port or 443,
                            "users": [
                                {
                                    "id": parsed.username,
                                    "encryption": qs.get("encryption", ["none"])[0] or "none",
                                }
                            ],
                        }
                    ]
                },
                "streamSettings": stream_settings,
            }
        ],
    }
    return json.dumps(config, ensure_ascii=False)


def ensure_v2ray_running(region: str, config_text: str) -> Optional[str]:
    cfg = (config_text or "").strip()
    if not cfg:
        LOGGER.info("v2ray_config_empty | region=%s", region)
        return None
    if not cfg.lstrip().startswith("{"):
        converted = _share_to_config(cfg, region)
        if not converted:
            LOGGER.warning("v2ray_share_convert_failed | region=%s", region)
            return None
        LOGGER.info("v2ray_share_converted | region=%s", region)
        cfg = converted
    proxy = _extract_proxy_url(cfg)
    if not proxy:
        LOGGER.warning("v2ray_no_inbound_proxy | region=%s", region)
        return None
    port = _proxy_port(proxy)
    binary = ensure_v2ray_installed()
    config_path = _write_config(region, cfg)
    cfg_hash = hashlib.sha256(cfg.encode("utf-8")).hexdigest()
    proc = _PROCS.get(region)
    if _CONF_HASH.get(region) != cfg_hash or not proc or proc.poll() is not None:
        LOGGER.info("v2ray_starting | region=%s proxy=%s", region, proxy)
        _restart_process(region, binary, config_path)
        _CONF_HASH[region] = cfg_hash
        _wait_for_port(port)
    return proxy
