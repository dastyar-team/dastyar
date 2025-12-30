# -*- coding: utf-8 -*-
"""
downloadmain.py
زیرساخت: تنظیمات، لاگ، دیتابیس، متادیتا (Crossref/OpenAlex)، دسته‌بندی (Groq → OpenAlex fallback),
تشخیص Open Access و دانلود PDF، و اجرای batch پردازش DOIها برای ربات تلگرام.

هیچ UI/کیبورد تلگرامی در این فایل نیست؛ فقط پردازش و ارسال پیام/فایل.
"""



from __future__ import annotations
from dotenv import load_dotenv; load_dotenv()

import os
import re
import json
import time
import random
import asyncio
import logging
import sqlite3
import secrets
import string
import platform
import threading
import html as htmlmod
import base64
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus, unquote, urljoin, urlparse
from logging.handlers import RotatingFileHandler
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Dict, Any, List, Tuple, Optional, TYPE_CHECKING

try:
    import pymysql  # type: ignore
    from pymysql.cursors import DictCursor  # type: ignore
    _HAS_PYMYSQL = True
except Exception:
    pymysql = None  # type: ignore
    DictCursor = None  # type: ignore
    _HAS_PYMYSQL = False
from selenium import webdriver   # لازم برای Type Hint و استفاده در جاهای مختلف

import requests
from contextlib import suppress

try:
    import resend  # type: ignore
    _HAS_RESEND = True
except Exception:
    resend = None  # type: ignore
    _HAS_RESEND = False

try:
    import undetected_chromedriver as uc
    _HAS_UC = True
except Exception:
    uc = None  # type: ignore
    _HAS_UC = False
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager
from v2ray_helper import ensure_v2ray_running
from downloaders.sciencedirect import download_via_sciencedirect as scidir_download, warmup_accounts
from utils.zip_report import build_zip_with_summary

if TYPE_CHECKING:
    from twocaptcha import TwoCaptcha as _TwoCaptchaType
else:
    _TwoCaptchaType = Any  # type: ignore[misc]

try:
    from twocaptcha import TwoCaptcha  # type: ignore[import]
    _HAS_TWOCAPTCHA = True
except Exception:
    TwoCaptcha = None  # type: ignore
    _HAS_TWOCAPTCHA = False

logger = logging.getLogger(__name__)
# --- ParseMode اختیاری (برای HTML). اگر در محیط موجود نبود، بدون parse_mode ارسال می‌کنیم.
try:
    from telegram.constants import ParseMode as _PM  # type: ignore
    PARSE_HTML = _PM.HTML
except Exception:
    PARSE_HTML = None  # type: ignore

# ---- Groq SDK (async) ----
try:
    from groq import AsyncGroq, Groq  # Groq برای health-check سنک
    _HAS_GROQ = True
except Exception:
    AsyncGroq = None  # type: ignore
    Groq = None       # type: ignore
    _HAS_GROQ = False

import aiohttp





def make_driver(headless=True):
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    if _HAS_UC:
        return uc.Chrome(options=options)
    return webdriver.Chrome(options=options)

# =========================
# تنظیمات
# =========================
@dataclass(frozen=True)
class Config:
    TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    DOWNLOAD_BOT_TOKEN: str = os.environ.get("DOWNLOAD_BOT_TOKEN", "")

    LOG_DIR: Path = Path("run_logs")
    LOG_FILE: Path = Path("run_logs/bot.log")
    LOG_MAX_BYTES: int = 5_000_000
    LOG_BACKUP_COUNT: int = 5

    CATEGORY_LOG_FILE: Path = Path("run_logs/category.log")
    DEBUG: bool = True
    DEBUG_LOG_FILE: Path = Path("run_logs/debug.log")
    LOG_CONCEPT_ROWS: bool = True

    ZARINPAL_URL: str = "https://zarinp.al/mam"
    ADMIN_USERNAME: str = "H_koosha"
    ADMIN_USER_ID: int = int(os.environ.get("ADMIN_USER_ID", "0") or 0)

    EXTRA_EMAIL_DELIVERY_FEE: int = 10_000

    DATA_DIR: Path = Path("data")
    DB_FILE: Path = Path("data/doi_bot.db")
    DB_TYPE: str = os.environ.get("DB_TYPE", "sqlite").strip().lower()
    DB_HOST: str = os.environ.get("DB_HOST", "127.0.0.1")
    DB_PORT: int = int(os.environ.get("DB_PORT", "3306"))
    DB_NAME: str = os.environ.get("DB_NAME", "Dastyar")
    DB_USER: str = os.environ.get("DB_USER", "dastyar")
    DB_PASSWORD: str = os.environ.get("DB_PASSWORD", "")
    DB_CONNECT_RETRIES: int = int(os.environ.get("DB_CONNECT_RETRIES", "15"))
    DB_CONNECT_WAIT_S: float = float(os.environ.get("DB_CONNECT_WAIT_S", "2"))
    TWOCAPTCHA_API_KEY: str = os.environ.get("TWOCAPTCHA_API_KEY", "")

    USER_TOKEN_LEN: int = 12  # طول توکن افزونه

    # HTTP/API
    HTTP_TIMEOUT: int = int(os.environ.get("HTTP_TIMEOUT", "15"))
    MAX_CONCURRENCY: int = int(os.environ.get("MAX_CONCURRENCY", "4"))
    POLITE_CONTACT: str = os.environ.get("POLITE_CONTACT", "you@example.com")  # ایمیل تماس
    CROSSREF_BASE: str = "https://api.crossref.org/works"
    OPENALEX_BASE: str = "https://api.openalex.org/works"

    # AI-first Category
    AI_BACKEND: str = os.environ.get("AI_BACKEND", "groq")  # groq | none
    AI_MIN_CONF: float = float(os.environ.get("AI_MIN_CONF", "0.40"))

    # Groq (SDK)
    GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
    GROQ_MODEL: str = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

    # آستانهٔ اعتماد برای انتخاب دسته از OpenAlex (فالبک)
    CATEGORY_MIN_SHARE: float = 0.30

    # دانلود PDF
    OA_DOWNLOAD_MAX_MB: int = int(os.environ.get("OA_DOWNLOAD_MAX_MB", "40"))
    DOWNLOAD_TMP_DIR: Path = Path("data/tmp")
    DOWNLOAD_LINK_DIR: Path = Path(os.environ.get("DOWNLOAD_LINK_DIR", "data/downloads"))
    DOWNLOAD_BOT_USERNAME: str = (os.environ.get("DOWNLOAD_BOT_USERNAME", "") or "").strip().lstrip("@")
    DOWNLOAD_LINK_TTL_HOURS: int = int(os.environ.get("DOWNLOAD_LINK_TTL_HOURS", "48"))
    DOWNLOAD_LINK_REQUIRE_SAME_USER: bool = os.environ.get("DOWNLOAD_LINK_REQUIRE_SAME_USER", "1").lower() not in {"0", "false", "no"}
    DOWNLOAD_LINK_DELETE_ON_SEND: bool = os.environ.get("DOWNLOAD_LINK_DELETE_ON_SEND", "1").lower() not in {"0", "false", "no"}
    DOWNLOAD_REQUIRED_CHANNELS: str = os.environ.get("DOWNLOAD_REQUIRED_CHANNELS", "")
    DOWNLOAD_REQUIRED_CHANNEL_LINKS: str = os.environ.get("DOWNLOAD_REQUIRED_CHANNEL_LINKS", "")
    DOWNLOAD_DELETE_DELAY_S: int = int(os.environ.get("DOWNLOAD_DELETE_DELAY_S", "60"))
    DOWNLOAD_COUNTDOWN_ENABLED: bool = os.environ.get("DOWNLOAD_COUNTDOWN_ENABLED", "1").lower() not in {"0", "false", "no"}
    DOWNLOAD_CHANNELS_ENFORCED: bool = os.environ.get("DOWNLOAD_CHANNELS_ENFORCED", "1").lower() not in {"0", "false", "no"}

    # Providerهای قانونی (JSON string)
    LEGAL_PRE2022: str = os.environ.get("LEGAL_PRE2022", """
    [
        {
            "name": "scihub",
            "type": "search",
            "query": "https://sci-hub.se/{doi}",
            "pdf_regex": "src=[\\\"'](https?://[^\\\"']+?\\\\.pdf)[\\\"']|href=[\\\"'](https?://[^\\\"']+?\\\\.pdf)[\\\"']|onclick=[\\\"'][^\\\"']*location\\\\.href=[\\\"']([^\\\"']+?\\\\.pdf)[^\\\"']*[\\\"']"
        },
        {
            "name": "scihub_moscow",
            "type": "search",
            "query": "https://moscow.sci-hub.se/{doi}",
            "pdf_regex": "src=[\\\"'](https?://[^\\\"']+?\\\\.pdf)[\\\"']|href=[\\\"'](https?://[^\\\"']+?\\\\.pdf)[\\\"']|onclick=[\\\"'][^\\\"']*location\\\\.href=[\\\"']([^\\\"']+?\\\\.pdf)[^\\\"']*[\\\"']"
        }
    ]
    """)
    LEGAL_2022PLUS: str = os.environ.get("LEGAL_2022PLUS", """
    [
        {
            "name": "scihub",
            "type": "search",
            "query": "https://sci-hub.se/{doi}",
            "pdf_regex": "src=[\\\"'](https?://[^\\\"']+?\\\\.pdf)[\\\"']|href=[\\\"'](https?://[^\\\"']+?\\\\.pdf)[\\\"']|onclick=[\\\"'][^\\\"']*location\\\\.href=[\\\"']([^\\\"']+?\\\\.pdf)[^\\\"']*[\\\"']"
        },
        {
            "name": "scihub_moscow",
            "type": "search",
            "query": "https://moscow.sci-hub.se/{doi}",
            "pdf_regex": "src=[\\\"'](https?://[^\\\"']+?\\\\.pdf)[\\\"']|href=[\\\"'](https?://[^\\\"']+?\\\\.pdf)[\\\"']|onclick=[\\\"'][^\\\"']*location\\\\.href=[\\\"']([^\\\"']+?\\\\.pdf)[^\\\"']*[\\\"']"
        }
    ]
    """)

    # پروکسی اختیاری برای Providerهای قانونی
    LEGAL_HTTP_PROXY: Optional[str] = os.environ.get("LEGAL_HTTP_PROXY", None)

    # IranPaper proxy credentials (optional)
    IRANPAPER_EMAIL: str = os.environ.get("IRANPAPER_EMAIL", "")
    IRANPAPER_PASSWORD: str = os.environ.get("IRANPAPER_PASSWORD", "")

    SCINET_USERNAME: str = os.environ.get("SCINET_USERNAME", "")
    SCINET_PASSWORD: str = os.environ.get("SCINET_PASSWORD", "")
    SECRET_KEY: str = os.environ.get("SECRET_KEY", "")
    RESEND_API_KEY: str = os.environ.get("RESEND_API_KEY", "")
    FROM_EMAIL: str = os.environ.get("FROM_EMAIL", "")

    # Chrome headless toggle (به‌صورت پیش‌فرض UI را نمایش می‌دهیم)
    CHROME_HEADLESS: bool = os.environ.get("CHROME_HEADLESS", "0").lower() not in {"0","false","no"}
    CHROME_PROFILE_DIR: str = os.environ.get("CHROME_PROFILE_DIR", "")  # ← اضافه
    USE_UNDETECTED: bool = os.environ.get("CHROME_USE_UC", "1").lower() in {"1","true","yes"}  # ← اضافه
    CHROMEDRIVER_PATH: str = os.environ.get("CHROMEDRIVER_PATH", "")
    SCINET_GROUP_CHAT_ID: int = int(os.environ.get("SCINET_GROUP_CHAT_ID", "-4841805049"))
    PAYMENT_GROUP_CHAT_ID: int = int(os.environ.get("PAYMENT_GROUP_CHAT_ID") or os.environ.get("SCINET_GROUP_CHAT_ID", "0") or 0)
    STORE_CARD_NUMBER: str = os.environ.get("STORE_CARD_NUMBER", "")
    STORE_PLAGIARISM_PRICE: int = int(os.environ.get("STORE_PLAGIARISM_PRICE", "0") or 0)
    STORE_PLAGIARISM_AI_PRICE: int = int(os.environ.get("STORE_PLAGIARISM_AI_PRICE", "0") or 0)

    # Local API (for Chrome extension / integrations)
    API_ENABLED: bool = os.environ.get("API_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
    API_HOST: str = os.environ.get("API_HOST", "127.0.0.1")
    API_PORT: int = int(os.environ.get("API_PORT", "8787"))
    API_RATE_WINDOW_S: int = int(os.environ.get("API_RATE_WINDOW_S", "60"))
    API_RATE_MAX_HITS: int = int(os.environ.get("API_RATE_MAX_HITS", "60"))
CFG = Config()
_TWOCAPTCHA_KEY = (CFG.TWOCAPTCHA_API_KEY or "").strip()

_twocaptcha_tls = threading.local()
_SCIHUB_DRIVER: Optional[webdriver.Chrome] = None
ACTIVATION_KEY = "SCIDIR_ACTIVATION_FLAG"

# =========================
# Email OTP (Resend)
# =========================
OTP_TTL_SECONDS: Final[int] = 10 * 60
OTP_RESEND_COOLDOWN_S: Final[int] = 60
OTP_MAX_ATTEMPTS: Final[int] = 5


def generate_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _utcnow() -> datetime:
    return datetime.utcnow()


def _dt_to_str(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _parse_db_datetime(val: Any) -> Optional[datetime]:
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    text = str(val)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _otp_secret() -> Optional[bytes]:
    secret = (CFG.SECRET_KEY or "").strip()
    return secret.encode("utf-8") if secret else None


def _hash_otp_code(code: str, secret: bytes) -> str:
    salt = secrets.token_bytes(16)
    digest = hmac.new(secret, salt + code.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{base64.b64encode(salt).decode('ascii')}${digest}"


def _verify_otp_code(code: str, stored: str, secret: bytes) -> bool:
    if not stored or "$" not in stored:
        return False
    salt_b64, expected = stored.split("$", 1)
    try:
        salt = base64.b64decode(salt_b64, validate=True)
    except Exception:
        return False
    digest = hmac.new(secret, salt + code.encode("utf-8"), hashlib.sha256).hexdigest()
    return secrets.compare_digest(digest, expected)


def _send_otp_email(to_email: str, code: str) -> None:
    if not _HAS_RESEND:
        raise RuntimeError("Resend package is not installed")
    if not CFG.RESEND_API_KEY or not CFG.FROM_EMAIL:
        raise RuntimeError("Missing RESEND_API_KEY or FROM_EMAIL")
    resend.api_key = CFG.RESEND_API_KEY
    html = (
        "<p>از اینکه از ربات Dastyar استفاده می‌کنید سپاسگزاریم.</p>"
        "<p>کد تایید شما:</p>"
        f"<p style=\"font-size: 20px;\"><strong>{code}</strong></p>"
        "<p>این کد تا ۱۰ دقیقه معتبر است.</p>"
    )
    resend.Emails.send({
        "from": CFG.FROM_EMAIL,
        "to": to_email,
        "subject": "کد تایید شما",
        "html": html,
    })


def _db_get_latest_otp(email: str) -> Dict[str, Any]:
    cur = _db_execute(
        "SELECT * FROM email_otps WHERE lower(email)=? ORDER BY id DESC LIMIT 1",
        (email.lower(),),
    )
    row = cur.fetchone()
    cur.close()
    return dict(row) if row else {}


def _db_insert_otp(
    *,
    email: str,
    code_hash: str,
    expires_at: datetime,
    last_sent_at: datetime,
    user_id: Optional[int],
) -> int:
    with _conn:
        cur = _db_execute(
            """
            INSERT INTO email_otps (email, code_hash, expires_at, attempts, last_sent_at, created_at, verified_at, user_id)
            VALUES (?, ?, ?, 0, ?, CURRENT_TIMESTAMP, NULL, ?)
            """,
            (
                email.lower(),
                code_hash,
                _dt_to_str(expires_at),
                _dt_to_str(last_sent_at),
                int(user_id) if user_id else None,
            ),
        )
        otp_id = getattr(cur, "lastrowid", None)
        cur.close()
    return int(otp_id or 0)


def _db_update_otp_send(
    otp_id: int,
    *,
    code_hash: str,
    expires_at: datetime,
    last_sent_at: datetime,
    user_id: Optional[int],
) -> None:
    with _conn:
        cur = _db_execute(
            """
            UPDATE email_otps
               SET code_hash=?,
                   expires_at=?,
                   attempts=0,
                   last_sent_at=?,
                   verified_at=NULL,
                   user_id=?
             WHERE id=?
            """,
            (
                code_hash,
                _dt_to_str(expires_at),
                _dt_to_str(last_sent_at),
                int(user_id) if user_id else None,
                int(otp_id),
            ),
        )
        cur.close()


def _db_increment_otp_attempts(otp_id: int) -> int:
    with _conn:
        cur = _db_execute(
            "UPDATE email_otps SET attempts = COALESCE(attempts, 0) + 1 WHERE id=?",
            (int(otp_id),),
        )
        cur.close()
    cur = _db_execute("SELECT attempts FROM email_otps WHERE id=?", (int(otp_id),))
    rec = cur.fetchone()
    cur.close()
    return int(rec["attempts"] or 0) if rec else 0


def _db_mark_otp_verified(otp_id: int, verified_at: datetime) -> None:
    with _conn:
        cur = _db_execute(
            "UPDATE email_otps SET verified_at=? WHERE id=?",
            (_dt_to_str(verified_at), int(otp_id)),
        )
        cur.close()


def _db_delete_otp(otp_id: int) -> None:
    with _conn:
        cur = _db_execute("DELETE FROM email_otps WHERE id=?", (int(otp_id),))
        cur.close()


def _db_restore_otp(
    otp_id: int,
    *,
    code_hash: str,
    expires_at: Optional[datetime],
    attempts: int,
    last_sent_at: Optional[datetime],
    verified_at: Optional[datetime],
    user_id: Optional[int],
) -> None:
    with _conn:
        cur = _db_execute(
            """
            UPDATE email_otps
               SET code_hash=?,
                   expires_at=?,
                   attempts=?,
                   last_sent_at=?,
                   verified_at=?,
                   user_id=?
             WHERE id=?
            """,
            (
                code_hash,
                _dt_to_str(expires_at) if expires_at else None,
                int(attempts),
                _dt_to_str(last_sent_at) if last_sent_at else None,
                _dt_to_str(verified_at) if verified_at else None,
                int(user_id) if user_id else None,
                int(otp_id),
            ),
        )
        cur.close()


def request_email_verification(email: str, user_id: Optional[int] = None) -> Dict[str, Any]:
    em = (email or "").strip().lower()
    if not _valid_email(em):
        return {"ok": False, "error": "invalid_email"}
    secret = _otp_secret()
    if not secret:
        logger.error("OTP secret missing")
        return {"ok": False, "error": "missing_secret"}

    now = _utcnow()
    latest = _db_get_latest_otp(em)
    if latest:
        last_sent = _parse_db_datetime(latest.get("last_sent_at"))
        if last_sent:
            delta = (now - last_sent).total_seconds()
            if delta < OTP_RESEND_COOLDOWN_S:
                retry_after = max(1, int(OTP_RESEND_COOLDOWN_S - delta))
                return {"ok": False, "error": "rate_limited", "retry_after": retry_after}

    code = generate_otp()
    code_hash = _hash_otp_code(code, secret)
    expires_at = now + timedelta(seconds=OTP_TTL_SECONDS)

    updated_existing = False
    prev_state: Dict[str, Any] = {}
    try:
        if latest:
            expires_prev = _parse_db_datetime(latest.get("expires_at"))
            if not latest.get("verified_at") and expires_prev and expires_prev > now:
                prev_state = {
                    "code_hash": str(latest.get("code_hash") or ""),
                    "expires_at": expires_prev,
                    "attempts": int(latest.get("attempts") or 0),
                    "last_sent_at": _parse_db_datetime(latest.get("last_sent_at")),
                    "verified_at": _parse_db_datetime(latest.get("verified_at")),
                    "user_id": int(latest.get("user_id") or 0) or None,
                }
                _db_update_otp_send(
                    int(latest.get("id") or 0),
                    code_hash=code_hash,
                    expires_at=expires_at,
                    last_sent_at=now,
                    user_id=user_id,
                )
                otp_id = int(latest.get("id") or 0)
                updated_existing = True
            else:
                otp_id = _db_insert_otp(
                    email=em,
                    code_hash=code_hash,
                    expires_at=expires_at,
                    last_sent_at=now,
                    user_id=user_id,
                )
        else:
            otp_id = _db_insert_otp(
                email=em,
                code_hash=code_hash,
                expires_at=expires_at,
                last_sent_at=now,
                user_id=user_id,
            )
    except Exception:
        logger.exception("otp_db_write_failed")
        return {"ok": False, "error": "db_error"}

    try:
        _send_otp_email(em, code)
    except Exception:
        logger.exception("otp_send_failed")
        if otp_id:
            if updated_existing:
                _db_restore_otp(int(otp_id), **prev_state)
            else:
                _db_delete_otp(otp_id)
        return {"ok": False, "error": "send_failed"}

    return {
        "ok": True,
        "expires_in": OTP_TTL_SECONDS,
        "expires_at": _dt_to_str(expires_at),
    }


def verify_email_code(email: str, code: str) -> Dict[str, Any]:
    em = (email or "").strip().lower()
    cd = (code or "").strip()
    if not _valid_email(em) or not cd:
        return {"ok": False, "error": "invalid_input"}
    secret = _otp_secret()
    if not secret:
        logger.error("OTP secret missing")
        return {"ok": False, "error": "missing_secret"}

    rec = _db_get_latest_otp(em)
    if not rec:
        return {"ok": False, "error": "not_found"}

    if rec.get("verified_at"):
        return {"ok": False, "error": "already_verified"}

    expires_at = _parse_db_datetime(rec.get("expires_at"))
    if not expires_at or _utcnow() > expires_at:
        return {"ok": False, "error": "expired"}

    attempts = int(rec.get("attempts") or 0)
    if attempts >= OTP_MAX_ATTEMPTS:
        return {"ok": False, "error": "too_many_attempts"}

    if not _verify_otp_code(cd, str(rec.get("code_hash") or ""), secret):
        new_attempts = _db_increment_otp_attempts(int(rec.get("id") or 0))
        remaining = max(0, OTP_MAX_ATTEMPTS - new_attempts)
        return {
            "ok": False,
            "error": "invalid_code",
            "attempts_left": remaining,
        }

    verified_at = _utcnow()
    _db_mark_otp_verified(int(rec.get("id") or 0), verified_at)

    user_id = int(rec.get("user_id") or 0)
    if user_id:
        db_set_email(user_id, em)
        db_set_email_verified(user_id, True)
    else:
        user = db_get_user_by_email(em)
        if user:
            db_set_email(int(user.get("user_id")), em)
            db_set_email_verified(int(user.get("user_id")), True)
            user_id = int(user.get("user_id"))

    return {"ok": True, "user_id": user_id or None}


def set_activation(flag: bool) -> None:
    db_set_setting(ACTIVATION_KEY, "1" if flag else "0")


def is_activation_on() -> bool:
    val = db_get_setting(ACTIVATION_KEY) or "0"
    return val.strip() == "1"


def _driver_alive(driver: Optional[webdriver.Chrome]) -> bool:
    if not driver:
        return False
    try:
        driver.execute_script("return document.readyState")
        return True
    except Exception:
        return False


def _get_twocaptcha_session() -> requests.Session:
    sess = getattr(_twocaptcha_tls, "session", None)
    if sess is None:
        sess = requests.Session()
        _twocaptcha_tls.session = sess
    return sess


solver: Optional[_TwoCaptchaType] = None
_HAS_TWOCAPTCHA = bool(_TWOCAPTCHA_KEY and TwoCaptcha)
if _HAS_TWOCAPTCHA and TwoCaptcha:
    try:
        solver = TwoCaptcha(_TWOCAPTCHA_KEY)
    except Exception as exc:
        logger.warning("twocaptcha_init_failed | err=%s", exc)
        solver = None
        _HAS_TWOCAPTCHA = False


def test_twocaptcha(sitekey: str, url: str) -> None:
    """تست ساده برای حل کپچای reCAPTCHA با 2Captcha."""
    if not _HAS_TWOCAPTCHA:
        print("TwoCaptcha is not configured.")
        return
    try:
        token = _solve_recaptcha_with_retry(sitekey, url, max_runs=1)
        print("2Captcha token:", token)
    except Exception as exc:
        print(f"2Captcha error: {exc}")


def _extract_recaptcha_sitekey(driver: webdriver.Chrome) -> Optional[str]:
    selectors = [
        "div.g-recaptcha[data-sitekey]",
        "div[data-sitekey]",
        "iframe[src*='recaptcha']"
    ]
    for sel in selectors:
        try:
            element = driver.find_element(By.CSS_SELECTOR, sel)
        except Exception:
            continue
        if element:
            with suppress(Exception):
                key = element.get_attribute("data-sitekey")
                if key:
                    return key
            with suppress(Exception):
                src = element.get_attribute("src") or ""
                if "sitekey=" in src:
                    return src.split("sitekey=")[1].split("&")[0]
    return None


def _twocaptcha_submit(sitekey: str, page_url: str) -> str:
    payload = {
        "key": _TWOCAPTCHA_KEY,
        "method": "userrecaptcha",
        "googlekey": sitekey,
        "pageurl": page_url,
        "json": 1,
        "soft_id": 0,
    }
    resp = _get_twocaptcha_session().post(
        "https://2captcha.com/in.php",
        data=payload,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if int(data.get("status", 0)) == 1 and data.get("request"):
        return str(data["request"])
    raise RuntimeError(f"submit_failed:{data}")


def _twocaptcha_poll_result(captcha_id: str, page_url: str) -> Optional[str]:
    params = {
        "key": _TWOCAPTCHA_KEY,
        "action": "get",
        "id": captcha_id,
        "json": 1,
    }
    for attempt in range(90):  # 90 * 5s = 450s
        time.sleep(5)
        resp = _get_twocaptcha_session().get(
            "https://2captcha.com/res.php",
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if int(data.get("status", 0)) == 1:
            token = data.get("request")
            if token:
                return str(token)
            logger.warning("twocaptcha_empty_token | id=%s url=%s", captcha_id, page_url)
            return None
        req = str(data.get("request", "")).upper()
        if req == "CAPCHA_NOT_READY":
            continue
        logger.warning(
            "twocaptcha_poll_error | id=%s url=%s response=%s",
            captcha_id,
            page_url,
            data,
        )
        return None
    logger.warning("twocaptcha_poll_timeout | id=%s url=%s", captcha_id, page_url)
    return None


def _twocaptcha_report_bad(captcha_id: str) -> None:
    params = {
        "key": _TWOCAPTCHA_KEY,
        "action": "reportbad",
        "id": captcha_id,
    }
    try:
        _get_twocaptcha_session().get(
            "https://2captcha.com/res.php",
            params=params,
            timeout=30,
        )
    except Exception as exc:
        logger.debug("twocaptcha_reportbad_failed | id=%s err=%s", captcha_id, exc)


def _solve_recaptcha_with_retry(sitekey: str, page_url: str, *, max_runs: int = 3) -> str:
    last_error: Optional[Exception] = None
    for run in range(1, max_runs + 1):
        captcha_id = None
        try:
            captcha_id = _twocaptcha_submit(sitekey, page_url)
            logger.debug("twocaptcha_submitted | run=%d id=%s", run, captcha_id)
            token = _twocaptcha_poll_result(captcha_id, page_url)
            if token:
                return token
            logger.warning("twocaptcha_invalid_response | run=%d id=%s", run, captcha_id)
            if captcha_id:
                _twocaptcha_report_bad(captcha_id)
        except Exception as exc:
            last_error = exc
            logger.warning("twocaptcha_attempt_failed | run=%d err=%s", run, exc)
            if captcha_id:
                _twocaptcha_report_bad(captcha_id)
        time.sleep(2)
    raise RuntimeError(f"twocaptcha_failed_after_{max_runs}_runs") from last_error


def _maybe_solve_recaptcha(driver: webdriver.Chrome, page_url: str) -> bool:
    if not _HAS_TWOCAPTCHA:
        return False
    try:
        sitekey = _extract_recaptcha_sitekey(driver)
        if not sitekey:
            return False
        logger.info("recaptcha_detected | sitekey=%s url=%s", sitekey, page_url)
        token = _solve_recaptcha_with_retry(sitekey, page_url)
        logger.info("recaptcha_solved | url=%s", page_url)
        driver.execute_script(
            """
            var token = arguments[0];
            var fields = document.querySelectorAll('[name="g-recaptcha-response"]');
            if (!fields || fields.length === 0) {
                var textarea = document.createElement('textarea');
                textarea.name = 'g-recaptcha-response';
                textarea.id = 'g-recaptcha-response';
                textarea.style.display = 'none';
                document.body.appendChild(textarea);
                fields = [textarea];
            }
            fields.forEach(function(field){
                field.value = token;
                field.innerHTML = token;
            });
        """,
            token,
        )
        return True
    except Exception as exc:
        logger.warning("recaptcha_solve_failed | url=%s err=%s", page_url, exc)
        return False

# =========================
# لاگ
# =========================
logger = logging.getLogger("doi_bot")
logger.setLevel(logging.DEBUG if CFG.DEBUG else logging.INFO)

CFG.LOG_DIR.mkdir(parents=True, exist_ok=True)

_fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s", "%Y-%m-%d %H:%M:%S")

# کنسول: فقط INFO+
_console = logging.StreamHandler()
_console.setLevel(logging.INFO)
_console.setFormatter(_fmt)
logger.addHandler(_console)

# فایل اصلی: INFO+
_file = RotatingFileHandler(CFG.LOG_FILE, maxBytes=CFG.LOG_MAX_BYTES, backupCount=CFG.LOG_BACKUP_COUNT, encoding="utf-8")
_file.setLevel(logging.INFO)
_file.setFormatter(_fmt)
logger.addHandler(_file)

# فایل دیباگ: DEBUG
if CFG.DEBUG:
    _dbg = RotatingFileHandler(CFG.DEBUG_LOG_FILE, maxBytes=CFG.LOG_MAX_BYTES, backupCount=CFG.LOG_BACKUP_COUNT, encoding="utf-8")
    _dbg.setLevel(logging.DEBUG)
    _dbg.setFormatter(_fmt)
    logger.addHandler(_dbg)

# Logger مخصوصِ تشخیص دسته‌بندی
catlog = logging.getLogger("doi_bot.category")
catlog.setLevel(logging.DEBUG if CFG.DEBUG else logging.INFO)
_cat_file = RotatingFileHandler(CFG.CATEGORY_LOG_FILE, maxBytes=CFG.LOG_MAX_BYTES, backupCount=CFG.LOG_BACKUP_COUNT, encoding="utf-8")
_cat_file.setLevel(logging.DEBUG if CFG.DEBUG else logging.INFO)
_cat_file.setFormatter(_fmt)
catlog.addHandler(_cat_file)

# =========================
# دیتابیس
# =========================
CFG.DATA_DIR.mkdir(parents=True, exist_ok=True)
CFG.DOWNLOAD_TMP_DIR.mkdir(parents=True, exist_ok=True)
CFG.DOWNLOAD_LINK_DIR.mkdir(parents=True, exist_ok=True)

DB_IS_MYSQL = (CFG.DB_TYPE or "sqlite").lower() == "mysql"

def _connect_mysql():
    if not _HAS_PYMYSQL:
        raise RuntimeError("pymysql is required for mysql")
    last_err = None
    for attempt in range(1, CFG.DB_CONNECT_RETRIES + 1):
        try:
            return pymysql.connect(
                host=CFG.DB_HOST,
                port=int(CFG.DB_PORT),
                user=CFG.DB_USER,
                password=CFG.DB_PASSWORD,
                database=CFG.DB_NAME,
                charset="utf8mb4",
                autocommit=True,
                cursorclass=DictCursor,
            )
        except Exception as exc:
            last_err = exc
            logger.warning("mysql_connect_failed | attempt=%d err=%s", attempt, exc)
            time.sleep(CFG.DB_CONNECT_WAIT_S)
    raise RuntimeError("Failed to connect to MySQL") from last_err

def _connect_sqlite():
    conn = sqlite3.connect(CFG.DB_FILE, check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn

_conn = _connect_mysql() if DB_IS_MYSQL else _connect_sqlite()

def _normalize_sql(sql: str) -> str:
    return sql.replace("?", "%s") if DB_IS_MYSQL else sql

def _db_execute(sql: str, params: Optional[Any] = None, *, many: bool = False):
    sql = _normalize_sql(sql)
    if DB_IS_MYSQL:
        global _conn

        def _run() -> Any:
            cur = _conn.cursor()
            if many:
                cur.executemany(sql, params or [])
            else:
                cur.execute(sql, params or ())
            return cur

        try:
            if _conn is None or not getattr(_conn, "open", False):
                _conn = _connect_mysql()
            else:
                _conn.ping(reconnect=True)
            return _run()
        except (pymysql.err.InterfaceError, pymysql.err.OperationalError):
            _conn = _connect_mysql()
            return _run()

    cur = _conn.cursor()
    if many:
        cur.executemany(sql, params or [])
    else:
        cur.execute(sql, params or ())
    return cur

def db_init() -> None:
    if DB_IS_MYSQL:
        statements = [
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username VARCHAR(255),
                seen_welcome TINYINT DEFAULT 0,
                email VARCHAR(255),
                email_verified TINYINT DEFAULT 0,
                delivery_method VARCHAR(10) NULL,
                delivery_chosen TINYINT DEFAULT 0,
                plan_type VARCHAR(32),
                plan_label VARCHAR(64),
                plan_price INT,
                plan_status VARCHAR(32),
                plan_note TEXT,
                user_token VARCHAR(64),
                token_created_at DATETIME,
                quota_free INT DEFAULT 0,
                quota_paid INT DEFAULT 0,
                used_free INT DEFAULT 0,
                used_paid INT DEFAULT 0,
                wallet_balance INT DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY user_token_unique (user_token)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS dois (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                doi VARCHAR(512) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uniq_user_doi (user_id, doi),
                INDEX idx_dois_user_id (user_id),
                CONSTRAINT fk_dois_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS doi_meta (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                doi VARCHAR(512) NOT NULL,
                title TEXT,
                year INT,
                category VARCHAR(128),
                source VARCHAR(64),
                status VARCHAR(32),
                error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uniq_user_doi (user_id, doi),
                INDEX idx_doi_meta_user_doi (user_id, doi),
                CONSTRAINT fk_doi_meta_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS settings (
                `key` VARCHAR(128) PRIMARY KEY,
                value TEXT
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS download_links (
                token VARCHAR(64) PRIMARY KEY,
                user_id BIGINT NOT NULL,
                file_path TEXT NOT NULL,
                filename VARCHAR(255),
                created_at BIGINT NOT NULL,
                expires_at BIGINT NOT NULL,
                used_at BIGINT NULL,
                used_by BIGINT NULL,
                INDEX idx_download_links_expires (expires_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS payment_requests (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                username VARCHAR(255),
                chat_id BIGINT NOT NULL,
                product_key VARCHAR(64) NOT NULL,
                amount INT NOT NULL,
                total_amount INT,
                wallet_used INT,
                card_number VARCHAR(64),
                status VARCHAR(32) NOT NULL,
                payment_code VARCHAR(32) NOT NULL,
                receipt_file_id TEXT,
                receipt_unique_id TEXT,
                receipt_message_id BIGINT,
                review_chat_id BIGINT,
                review_message_id BIGINT,
                admin_id BIGINT,
                admin_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_payment_user (user_id),
                INDEX idx_payment_status (status)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            """
            CREATE TABLE IF NOT EXISTS email_otps (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                email VARCHAR(255) NOT NULL,
                code_hash TEXT NOT NULL,
                expires_at DATETIME NOT NULL,
                attempts INT DEFAULT 0,
                last_sent_at DATETIME,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                verified_at DATETIME NULL,
                user_id BIGINT NULL,
                INDEX idx_email_otps_email (email)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
        ]
        for stmt in statements:
            cur = _db_execute(stmt)
            cur.close()
    else:
        with _conn:
            _conn.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=NORMAL;
            PRAGMA foreign_keys=ON;

            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                seen_welcome INTEGER DEFAULT 0,
                email TEXT,
                email_verified INTEGER DEFAULT 0,
                delivery_method TEXT CHECK (delivery_method IN ('bot','email')) NULL,
                delivery_chosen INTEGER DEFAULT 0,
                plan_type TEXT,
                plan_label TEXT,
                plan_price INTEGER,
                plan_status TEXT,
                plan_note TEXT,
                user_token TEXT UNIQUE,
                token_created_at TEXT,
                quota_free INTEGER DEFAULT 0,
                quota_paid INTEGER DEFAULT 0,
                used_free  INTEGER DEFAULT 0,
                used_paid  INTEGER DEFAULT 0,
                wallet_balance INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS dois (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                doi TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, doi),
                FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS doi_meta (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                doi TEXT NOT NULL,
                title TEXT,
                year INTEGER,
                category TEXT,
                source TEXT,
                status TEXT,     -- ok / not_found / error
                error TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, doi),
                FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_dois_user_id ON dois(user_id);
            CREATE INDEX IF NOT EXISTS idx_doi_meta_user_doi ON doi_meta(user_id, doi);
            """)
            _conn.executescript("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS download_links (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                file_path TEXT NOT NULL,
                filename TEXT,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                used_at INTEGER,
                used_by INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_download_links_expires ON download_links(expires_at);
            CREATE TABLE IF NOT EXISTS payment_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                chat_id INTEGER NOT NULL,
                product_key TEXT NOT NULL,
                amount INTEGER NOT NULL,
                total_amount INTEGER,
                wallet_used INTEGER,
                card_number TEXT,
                status TEXT NOT NULL,
                payment_code TEXT NOT NULL,
                receipt_file_id TEXT,
                receipt_unique_id TEXT,
                receipt_message_id INTEGER,
                review_chat_id INTEGER,
                review_message_id INTEGER,
                admin_id INTEGER,
                admin_reason TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_payment_user ON payment_requests(user_id);
            CREATE INDEX IF NOT EXISTS idx_payment_status ON payment_requests(status);
            CREATE TABLE IF NOT EXISTS email_otps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                code_hash TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                attempts INTEGER DEFAULT 0,
                last_sent_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                verified_at TEXT,
                user_id INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_email_otps_email ON email_otps(email);
            """)
    _ensure_column("users", "user_token", "TEXT" if not DB_IS_MYSQL else "VARCHAR(64)")
    _ensure_column("users", "token_created_at", "TEXT" if not DB_IS_MYSQL else "DATETIME")
    _ensure_column("users", "quota_free", "INTEGER" if not DB_IS_MYSQL else "INT")
    _ensure_column("users", "quota_paid", "INTEGER" if not DB_IS_MYSQL else "INT")
    _ensure_column("users", "used_free", "INTEGER" if not DB_IS_MYSQL else "INT")
    _ensure_column("users", "used_paid", "INTEGER" if not DB_IS_MYSQL else "INT")
    _ensure_column("users", "email_verified", "INTEGER" if not DB_IS_MYSQL else "TINYINT")
    _ensure_column("users", "wallet_balance", "INTEGER" if not DB_IS_MYSQL else "INT")
    _ensure_column("payment_requests", "total_amount", "INTEGER" if not DB_IS_MYSQL else "INT")
    _ensure_column("payment_requests", "wallet_used", "INTEGER" if not DB_IS_MYSQL else "INT")

def _ensure_column(table: str, column: str, coltype: str) -> None:
    if DB_IS_MYSQL:
        cur = _db_execute(
            "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA=? AND TABLE_NAME=? AND COLUMN_NAME=?",
            (CFG.DB_NAME, table, column),
        )
        row = cur.fetchone()
        cur.close()
        if row:
            return
        try:
            cur = _db_execute(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {coltype}")
            cur.close()
        except Exception as e:
            logger.warning("ALTER TABLE failed (maybe exists): %s", e)
        return

    cur = _db_execute(f"PRAGMA table_info({table})")
    cols = [r["name"] for r in cur.fetchall()]
    cur.close()
    if column not in cols:
        try:
            with _conn:
                cur = _db_execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
                cur.close()
        except Exception as e:
            logger.warning("ALTER TABLE failed (maybe exists): %s", e)

# ---- User CRUD ----
def db_upsert_user(user_id: int, username: Optional[str]) -> None:
    if DB_IS_MYSQL:
        sql = """
            INSERT INTO users (user_id, username)
            VALUES (?, ?)
            ON DUPLICATE KEY UPDATE username=VALUES(username), updated_at=CURRENT_TIMESTAMP
        """
    else:
        sql = """
            INSERT INTO users (user_id, username)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, updated_at=CURRENT_TIMESTAMP
        """
    with _conn:
        cur = _db_execute(sql, (user_id, username or None))
        cur.close()

def db_get_user(user_id: int) -> Dict[str, Any]:
    cur = _db_execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    cur.close()
    return dict(row) if row else {}

def db_get_user_by_email(email: str) -> Dict[str, Any]:
    em = (email or "").strip().lower()
    if not em:
        return {}
    cur = _db_execute("SELECT * FROM users WHERE lower(email)=?", (em,))
    row = cur.fetchone()
    cur.close()
    return dict(row) if row else {}

def db_set_seen_welcome(user_id: int) -> None:
    with _conn:
        cur = _db_execute("UPDATE users SET seen_welcome=1, updated_at=CURRENT_TIMESTAMP WHERE user_id=?", (user_id,))
        cur.close()

def db_set_email(user_id: int, email: str) -> None:
    with _conn:
        cur = _db_execute(
            "UPDATE users SET email=?, email_verified=0, updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
            (email, user_id),
        )
        cur.close()


def db_set_email_verified(user_id: int, verified: bool = True) -> None:
    val = 1 if verified else 0
    with _conn:
        cur = _db_execute(
            "UPDATE users SET email_verified=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
            (val, int(user_id)),
        )
        cur.close()

def db_set_delivery(user_id: int, method: str) -> None:
    with _conn:
        cur = _db_execute(
            "UPDATE users SET delivery_method=?, delivery_chosen=1, updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
            (method, user_id),
        )
        cur.close()

def db_set_plan(user_id: int, ptype: str, label: str, price: int, status: str, note: str) -> None:
    with _conn:
        cur = _db_execute("""
            UPDATE users
               SET plan_type=?, plan_label=?, plan_price=?, plan_status=?, plan_note=?, updated_at=CURRENT_TIMESTAMP
             WHERE user_id=?
        """, (ptype, label, price, status, note, user_id))
        cur.close()

def db_count_dois(user_id: int) -> int:
    cur = _db_execute("SELECT COUNT(*) AS c FROM dois WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    cur.close()
    return int(row["c"]) if row else 0

def db_add_dois(user_id: int, dois: List[str]) -> int:
    if not dois:
        return 0
    normalized: List[str] = []
    seen: set[str] = set()
    for raw in dois:
        doi = normalize_doi(raw)
        if not doi or doi in seen:
            continue
        seen.add(doi)
        normalized.append(doi)
    if not normalized:
        return 0
    if DB_IS_MYSQL:
        with _conn:
            cur = _db_execute(
                "INSERT IGNORE INTO dois (user_id, doi) VALUES (?, ?)",
                [(user_id, d) for d in normalized],
                many=True,
            )
            count = int(cur.rowcount or 0)
            cur.close()
        return count

    before = _conn.total_changes
    with _conn:
        cur = _db_execute(
            "INSERT OR IGNORE INTO dois (user_id, doi) VALUES (?, ?)",
            [(user_id, d) for d in normalized],
            many=True,
        )
        cur.close()
    return _conn.total_changes - before

# ---- doi_meta CRUD ----
def db_upsert_meta(user_id: int, doi: str, *, title: Optional[str], year: Optional[int],
                   category: Optional[str], source: str, status: str, error: Optional[str]) -> None:
    if DB_IS_MYSQL:
        sql = """
            INSERT INTO doi_meta (user_id, doi, title, year, category, source, status, error, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON DUPLICATE KEY UPDATE
                title=VALUES(title),
                year=VALUES(year),
                category=VALUES(category),
                source=VALUES(source),
                status=VALUES(status),
                error=VALUES(error),
                updated_at=CURRENT_TIMESTAMP
        """
    else:
        sql = """
            INSERT INTO doi_meta (user_id, doi, title, year, category, source, status, error, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, doi) DO UPDATE SET
                title=excluded.title,
                year=excluded.year,
                category=excluded.category,
                source=excluded.source,
                status=excluded.status,
                error=excluded.error,
                updated_at=CURRENT_TIMESTAMP
        """
    with _conn:
        cur = _db_execute(sql, (user_id, doi, title, year, category, source, status, (error or None)))
        cur.close()

# ---- Token helpers ----
ALNUM = string.ascii_letters + string.digits  # قوی‌تر از فقط حروف بزرگ
def _generate_token(n: int = CFG.USER_TOKEN_LEN) -> str:
    return "".join(secrets.choice(ALNUM) for _ in range(n))

def db_get_token(user_id: int) -> Optional[str]:
    cur = _db_execute("SELECT user_token FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    cur.close()
    return row["user_token"] if row and row["user_token"] else None

def db_set_new_token(user_id: int) -> str:
    for _ in range(50):
        tok = _generate_token()
        cur = _db_execute("SELECT 1 FROM users WHERE user_token=?", (tok,))
        exists = cur.fetchone()
        cur.close()
        if not exists:
            with _conn:
                cur = _db_execute(
                    "UPDATE users SET user_token=?, token_created_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
                    (tok, user_id),
                )
                cur.close()
            return tok
    raise RuntimeError("failed to generate unique token")

def db_get_or_create_token(user_id: int) -> str:
    tok = db_get_token(user_id)
    if tok:
        return tok
    return db_set_new_token(user_id)

# ---- Settings CRUD ----
def db_get_setting(key: str) -> Optional[str]:
    sql = "SELECT value FROM settings WHERE key=?" if not DB_IS_MYSQL else "SELECT value FROM settings WHERE `key`=?"
    cur = _db_execute(sql, (key,))
    row = cur.fetchone()
    cur.close()
    return row["value"] if row else None

def db_set_setting(key: str, value: str) -> None:
    if DB_IS_MYSQL:
        sql = (
            "INSERT INTO settings (`key`, value) VALUES (?, ?) "
            "ON DUPLICATE KEY UPDATE value = VALUES(value)"
        )
    else:
        sql = (
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
        )
    with _conn:
        cur = _db_execute(sql, (key, value))
        cur.close()

# ---- Payment Requests ----
PAYMENT_STATUS_AWAITING: Final[str] = "awaiting_receipt"
PAYMENT_STATUS_PENDING: Final[str] = "pending_review"
PAYMENT_STATUS_APPROVED: Final[str] = "approved"
PAYMENT_STATUS_REJECTED: Final[str] = "rejected"

def _generate_payment_code() -> str:
    for _ in range(20):
        code = "".join(secrets.choice(string.digits) for _ in range(6))
        cur = _db_execute("SELECT 1 FROM payment_requests WHERE payment_code=?", (code,))
        exists = cur.fetchone()
        cur.close()
        if not exists:
            return code
    return f"{int(time.time()) % 1_000_000:06d}"

def db_create_payment_request(
    user_id: int,
    username: Optional[str],
    chat_id: int,
    product_key: str,
    amount: int,
    card_number: str,
    *,
    total_amount: Optional[int] = None,
    wallet_used: int = 0,
) -> Dict[str, Any]:
    code = _generate_payment_code()
    final_total = int(total_amount) if total_amount is not None else int(amount)
    final_wallet = int(wallet_used or 0)
    with _conn:
        cur = _db_execute(
            """
            INSERT INTO payment_requests
                (user_id, username, chat_id, product_key, amount, total_amount, wallet_used, card_number, status, payment_code)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(user_id),
                (username or ""),
                int(chat_id),
                product_key,
                int(amount),
                final_total,
                final_wallet,
                card_number,
                PAYMENT_STATUS_AWAITING,
                code,
            ),
        )
        payment_id = getattr(cur, "lastrowid", None)
        cur.close()
    return {"id": int(payment_id or 0), "payment_code": code, "status": PAYMENT_STATUS_AWAITING}

def db_get_payment_request(payment_id: int) -> Dict[str, Any]:
    cur = _db_execute("SELECT * FROM payment_requests WHERE id=?", (int(payment_id),))
    row = cur.fetchone()
    cur.close()
    return dict(row) if row else {}

def db_get_open_payment_request(user_id: int) -> Dict[str, Any]:
    sql = (
        "SELECT * FROM payment_requests WHERE user_id=? AND status IN (?, ?) "
        "ORDER BY id DESC LIMIT 1"
    )
    cur = _db_execute(
        sql,
        (int(user_id), PAYMENT_STATUS_AWAITING, PAYMENT_STATUS_PENDING),
    )
    row = cur.fetchone()
    cur.close()
    return dict(row) if row else {}

def db_update_payment_receipt(
    payment_id: int,
    *,
    file_id: str,
    file_unique_id: str,
    message_id: int,
) -> None:
    with _conn:
        cur = _db_execute(
            """
            UPDATE payment_requests
               SET receipt_file_id=?,
                   receipt_unique_id=?,
                   receipt_message_id=?,
                   status=?,
                   updated_at=CURRENT_TIMESTAMP
             WHERE id=?
            """,
            (
                file_id,
                file_unique_id,
                int(message_id),
                PAYMENT_STATUS_PENDING,
                int(payment_id),
            ),
        )
        cur.close()

def db_set_payment_review_message(payment_id: int, chat_id: int, message_id: int) -> None:
    with _conn:
        cur = _db_execute(
            """
            UPDATE payment_requests
               SET review_chat_id=?,
                   review_message_id=?,
                   updated_at=CURRENT_TIMESTAMP
             WHERE id=?
            """,
            (int(chat_id), int(message_id), int(payment_id)),
        )
        cur.close()

def db_set_payment_status(
    payment_id: int,
    status: str,
    *,
    admin_id: Optional[int] = None,
    admin_reason: Optional[str] = None,
) -> None:
    with _conn:
        cur = _db_execute(
            """
            UPDATE payment_requests
               SET status=?,
                   admin_id=?,
                   admin_reason=?,
                   updated_at=CURRENT_TIMESTAMP
             WHERE id=?
            """,
            (status, int(admin_id) if admin_id else None, admin_reason, int(payment_id)),
        )
        cur.close()

# ---- Wallet ----
def db_add_wallet_balance(user_id: int, delta: int) -> None:
    with _conn:
        cur = _db_execute(
            """
            UPDATE users
               SET wallet_balance = COALESCE(wallet_balance, 0) + ?,
                   updated_at = CURRENT_TIMESTAMP
             WHERE user_id = ?
            """,
            (int(delta), int(user_id)),
        )
        cur.close()

# ---- Quotas ----
def db_add_quota(user_id: int, *, free_add: int = 0, paid_add: int = 0) -> None:
    with _conn:
        cur = _db_execute(
            """
            UPDATE users
               SET quota_free = COALESCE(quota_free, 0) + ?,
                   quota_paid = COALESCE(quota_paid, 0) + ?,
                   updated_at = CURRENT_TIMESTAMP
             WHERE user_id = ?
            """,
            (int(free_add), int(paid_add), int(user_id)),
        )
        cur.close()


def db_add_quota_by_email(email: str, *, free_add: int = 0, paid_add: int = 0) -> bool:
    user = db_get_user_by_email(email)
    if not user:
        return False
    db_add_quota(int(user["user_id"]), free_add=free_add, paid_add=paid_add)
    return True


def db_inc_used(user_id: int, *, free_inc: int = 0, paid_inc: int = 0) -> None:
    with _conn:
        cur = _db_execute(
            """
            UPDATE users
               SET used_free = COALESCE(used_free, 0) + ?,
                   used_paid = COALESCE(used_paid, 0) + ?,
                   updated_at = CURRENT_TIMESTAMP
             WHERE user_id = ?
            """,
            (int(free_inc), int(paid_inc), int(user_id)),
        )
        cur.close()


def db_get_quota_status(user_id: int) -> Dict[str, int]:
    user = db_get_user(int(user_id))
    qf = int(user.get("quota_free") or 0)
    qp = int(user.get("quota_paid") or 0)
    uf = int(user.get("used_free") or 0)
    up = int(user.get("used_paid") or 0)
    return {
        "quota_free": qf,
        "quota_paid": qp,
        "used_free": uf,
        "used_paid": up,
        "remaining_free": max(0, qf - uf),
        "remaining_paid": max(0, qp - up),
    }

def _download_bot_deeplink(token: str) -> Optional[str]:
    name = (CFG.DOWNLOAD_BOT_USERNAME or "").strip().lstrip("@")
    if not name or not token:
        return None
    return f"https://t.me/{name}?start={token}"

CB_DL_DONE: Final[str] = "dl:done"

def _download_link_done_kb() -> Optional[Any]:
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    except Exception:
        return None
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ انجام شد", callback_data=CB_DL_DONE)]]
    )

def db_create_download_link(
    user_id: int,
    file_path: str,
    filename: Optional[str],
    *,
    ttl_hours: Optional[int] = None,
) -> Optional[str]:
    if not file_path:
        return None
    ttl = int(ttl_hours if ttl_hours is not None else CFG.DOWNLOAD_LINK_TTL_HOURS)
    created_at = int(time.time())
    expires_at = created_at + max(3600, ttl * 3600)
    for _ in range(10):
        token = secrets.token_urlsafe(24)
        try:
            with _conn:
                cur = _db_execute(
                    "INSERT INTO download_links (token, user_id, file_path, filename, created_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (token, int(user_id), str(file_path), (filename or ""), created_at, expires_at),
                )
                cur.close()
            return token
        except Exception as exc:
            logger.warning("download_link_insert_failed | err=%s", exc)
            continue
    return None

def db_get_download_link(token: str) -> Dict[str, Any]:
    if not token:
        return {}
    cur = _db_execute("SELECT * FROM download_links WHERE token=?", (token,))
    row = cur.fetchone()
    cur.close()
    return dict(row) if row else {}

def db_mark_download_link_used(token: str, *, used_by: Optional[int] = None) -> None:
    if not token:
        return
    with _conn:
        cur = _db_execute(
            "UPDATE download_links SET used_at=?, used_by=? WHERE token=?",
            (int(time.time()), int(used_by) if used_by else None, token),
        )
        cur.close()

def db_delete_download_link(token: str) -> None:
    if not token:
        return
    with _conn:
        cur = _db_execute("DELETE FROM download_links WHERE token=?", (token,))
        cur.close()

def db_cleanup_download_links(*, include_used: bool = True) -> int:
    now = int(time.time())
    where = "expires_at < ?"
    params: List[Any] = [now]
    if include_used:
        where = "(expires_at < ? OR used_at IS NOT NULL)"
    cur = _db_execute(f"SELECT token, file_path FROM download_links WHERE {where}", tuple(params))
    rows = cur.fetchall()
    cur.close()
    tokens: List[str] = []
    for row in rows:
        if isinstance(row, dict):
            token = row.get("token")
            fpath = row.get("file_path")
        else:
            token = row["token"] if row and "token" in row.keys() else None  # type: ignore[union-attr]
            fpath = row["file_path"] if row and "file_path" in row.keys() else None  # type: ignore[union-attr]
        if token:
            tokens.append(str(token))
        if fpath:
            with suppress(Exception):
                Path(str(fpath)).unlink()
    if not tokens:
        return 0
    placeholders = ",".join(["?"] * len(tokens))
    with _conn:
        cur = _db_execute(f"DELETE FROM download_links WHERE token IN ({placeholders})", tuple(tokens))
        count = int(cur.rowcount or 0)
        cur.close()
    return count

def _v2ray_key(region: str) -> str:
    return f"V2RAY_CONFIGS_{region.upper()}"

def _norm_region(region: str) -> str:
    region = (region or "").lower()
    return "iran" if region not in {"iran", "global"} else region

def vpn_load_configs(region: str) -> List[Dict[str, Any]]:
    region = _norm_region(region)
    raw = db_get_setting(_v2ray_key(region)) or "[]"
    try:
        arr = json.loads(raw)
        if isinstance(arr, list):
            out: List[Dict[str, Any]] = []
            for item in arr:
                if isinstance(item, dict) and item.get("data"):
                    out.append(item)
            return out
    except Exception:
        pass
    return []

def vpn_save_configs(region: str, configs: List[Dict[str, Any]]) -> None:
    region = _norm_region(region)
    db_set_setting(_v2ray_key(region), json.dumps(configs, ensure_ascii=False))

def vpn_add_config(region: str, label: str, data: str) -> Dict[str, Any]:
    region = _norm_region(region)
    configs = vpn_load_configs(region)
    entry = {
        "id": secrets.token_hex(4),
        "label": label or f"Config {len(configs)+1}",
        "data": data.strip(),
        "active": not configs,
        "status": None,
        "ping_ms": None,
    }
    configs.append(entry)
    vpn_save_configs(region, configs)
    return entry

def vpn_remove_config(region: str, cfg_id: str) -> bool:
    region = _norm_region(region)
    configs = vpn_load_configs(region)
    removed = False
    for i, cfg in enumerate(list(configs)):
        if cfg.get("id") == cfg_id:
            removed = True
            configs.pop(i)
            break
    if not removed:
        return False
    if configs and not any(c.get("active") for c in configs):
        configs[0]["active"] = True
    vpn_save_configs(region, configs)
    return True

def vpn_set_active(region: str, cfg_id: str) -> bool:
    region = _norm_region(region)
    configs = vpn_load_configs(region)
    found = False
    for cfg in configs:
        if cfg.get("id") == cfg_id:
            cfg["active"] = True
            found = True
        else:
            cfg["active"] = False
    if not found:
        return False
    vpn_save_configs(region, configs)
    return True

def vpn_get_active_config(region: str) -> Optional[Dict[str, Any]]:
    region = _norm_region(region)
    configs = vpn_load_configs(region)
    for cfg in configs:
        if cfg.get("active"):
            return cfg
    return configs[0] if configs else None

def vpn_update_status(region: str, cfg_id: str, status: Optional[str], ping_ms: Optional[int]) -> None:
    region = _norm_region(region)
    configs = vpn_load_configs(region)
    changed = False
    for cfg in configs:
        if cfg.get("id") == cfg_id:
            cfg["status"] = status
            cfg["ping_ms"] = ping_ms
            changed = True
            break
    if changed:
        vpn_save_configs(region, configs)

def vpn_ping_config(region: str, cfg_id: str) -> Tuple[bool, Optional[int]]:
    region = _norm_region(region)
    configs = vpn_load_configs(region)
    entry = next((c for c in configs if c.get("id") == cfg_id), None)
    if not entry:
        return False, None
    proxy = ensure_v2ray_running(region, entry["data"])
    if not proxy:
        vpn_update_status(region, cfg_id, "fail", None)
        return False, None
    start = time.perf_counter()
    try:
        resp = requests.get(
            "https://1.1.1.1/cdn-cgi/trace",
            proxies={"http": proxy, "https": proxy},
            timeout=6,
        )
        resp.raise_for_status()
        ping_ms = int((time.perf_counter() - start) * 1000)
        vpn_update_status(region, cfg_id, "ok", ping_ms)
        return True, ping_ms
    except Exception as exc:
        logger.warning("v2ray_ping_failed | region=%s id=%s err=%s", region, cfg_id, exc)
        vpn_update_status(region, cfg_id, "fail", None)
        return False, None

def vpn_ping_all(region: str) -> Tuple[int, int]:
    configs = vpn_load_configs(region)
    ok = 0
    for cfg in configs:
        success, _ = vpn_ping_config(region, cfg.get("id"))
        if success:
            ok += 1
    return ok, len(configs)


# =========================
# IranPaper accounts (multi-slot)
# =========================
IRANPAPER_SLOTS: List[int] = [1, 2, 3]
IRANPAPER_STATE_KEY = "IRANPAPER_STATE_V2"
IRANPAPER_VPN_MAP_KEY = "IRANPAPER_VPN_MAP"


def _env_accounts() -> List[Dict[str, Any]]:
    accounts: List[Dict[str, Any]] = []
    for slot in IRANPAPER_SLOTS:
        email = os.environ.get(f"IRANPAPER_EMAIL_{slot}", "")
        pwd = os.environ.get(f"IRANPAPER_PASSWORD_{slot}", "")
        accounts.append({"slot": slot, "email": email, "password": pwd})
    # سازگاری عقب: اگر حساب اصلی قبلی تنظیم شده بود و اسلات ۱ خالی است
    if accounts and not accounts[0]["email"] and CFG.IRANPAPER_EMAIL and CFG.IRANPAPER_PASSWORD:
        accounts[0]["email"] = CFG.IRANPAPER_EMAIL
        accounts[0]["password"] = CFG.IRANPAPER_PASSWORD
    return accounts


def _load_account_state() -> Dict[str, Any]:
    raw = db_get_setting(IRANPAPER_STATE_KEY) or "{}"
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    # پیش‌فرض: همه غیرفعال
    return {"active": {}, "primary": None}


def _save_account_state(state: Dict[str, Any]) -> None:
    try:
        payload = json.dumps(state, ensure_ascii=False)
    except Exception:
        payload = "{}"
    db_set_setting(IRANPAPER_STATE_KEY, payload)


def iranpaper_set_active(slot: int, flag: bool) -> None:
    state = _load_account_state()
    active_map = state.get("active") if isinstance(state.get("active"), dict) else {}
    active_map[str(slot)] = bool(flag)
    state["active"] = active_map
    _save_account_state(state)


def iranpaper_set_primary(slot: int) -> None:
    state = _load_account_state()
    state["primary"] = int(slot)
    _save_account_state(state)


def _load_vpn_map() -> Dict[str, str]:
    raw = db_get_setting(IRANPAPER_VPN_MAP_KEY) or "{}"
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except Exception:
        pass
    return {}


def iranpaper_set_vpn(slot: int, cfg_id: str) -> None:
    data = _load_vpn_map()
    data[str(slot)] = cfg_id
    db_set_setting(IRANPAPER_VPN_MAP_KEY, json.dumps(data, ensure_ascii=False))

def iranpaper_vpn_map() -> Dict[str, str]:
    return _load_vpn_map()


def _vpn_data_for_slot(slot: int) -> Optional[str]:
    vpn_map = _load_vpn_map()
    cfg_id = vpn_map.get(str(slot))
    if not cfg_id:
        return None
    configs = vpn_load_configs("iran")
    for cfg in configs:
        if cfg.get("id") == cfg_id:
            return cfg.get("data")
    return None

def _vpn_id_for_slot(slot: int) -> Optional[str]:
    vpn_map = _load_vpn_map()
    return vpn_map.get(str(slot))


def iranpaper_accounts_ordered() -> List[Dict[str, Any]]:
    env_accounts = _env_accounts()
    state = _load_account_state()
    active_map = state.get("active") if isinstance(state.get("active"), dict) else {}
    primary_slot = int(state.get("primary") or 0) if state.get("primary") else 0

    accounts: List[Dict[str, Any]] = []
    for acc in env_accounts:
        slot = acc["slot"]
        cfg_id = _vpn_id_for_slot(slot)
        has_cred = bool(acc.get("email") and acc.get("password"))
        acc["active"] = bool(active_map.get(str(slot), False) and has_cred)
        acc["primary"] = bool(primary_slot == slot)
        acc["vpn_id"] = cfg_id
        acc["vpn_data"] = _vpn_data_for_slot(slot) or ""
        acc["base_url"] = "https://iranpaper.ir/directaccess"
        acc["has_cred"] = has_cred
        accounts.append(acc)

    # مرتب‌سازی: اول primary، بعد بقیه بر اساس شماره اسلات
    accounts.sort(key=lambda a: (0 if a.get("primary") else 1, a["slot"]))
    return accounts


async def warmup_scidir_accounts() -> None:
    await warmup_accounts(
        iranpaper_accounts_ordered(),
        cfg=CFG,
        build_chrome_driver=_build_chrome_driver,
        ensure_v2ray_running=ensure_v2ray_running,
        solve_recaptcha=_maybe_solve_recaptcha,
    )



def _get_download_links() -> List[Dict[str, Any]]:
    raw = db_get_setting("DOWNLOAD_LINKS") or "[]"
    try:
        arr = json.loads(raw)
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    if isinstance(arr, list):
        for item in arr:
            if isinstance(item, dict) and item.get("url"):
                out.append(item)
    return out

# =========================
# Utility: DOI normalize + HTTP
# =========================
def normalize_doi(raw: str) -> str:
    s = (raw or "").strip()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.IGNORECASE)
    s = s.strip().rstrip(" .;,،؛)")
    return s

async def _http_get_json(session: aiohttp.ClientSession, url: str, params: Dict[str, Any]) -> Tuple[int, Optional[Dict[str, Any]]]:
    # Backoff با jitter برای 429/5xx
    for attempt, backoff in enumerate([0.5, 1.5, 3, 6], start=1):
        try:
            async with session.get(url, params=params, timeout=CFG.HTTP_TIMEOUT) as resp:
                status = resp.status
                text = await resp.text()
                if status == 200:
                    try:
                        return status, await resp.json()
                    except Exception:
                        try:
                            return status, json.loads(text)
                        except Exception:
                            return status, {"_raw": text}
                if status in (400, 404):
                    return status, {"_raw": text}
                if status in (429, 500, 502, 503, 504):
                    await asyncio.sleep(backoff + secrets.randbelow(200)/1000)
                    continue
                return status, {"_raw": text}
        except Exception:
            await asyncio.sleep(backoff)
    return 0, None

# =========================
# واکشی متادیتا از Crossref/OpenAlex
# =========================
async def fetch_crossref(session: aiohttp.ClientSession, doi: str) -> Tuple[Optional[str], Optional[int], Optional[str], Optional[str], str]:
    """عنوان، سال، ژورنال و ابسترکت را از Crossref می‌گیریم."""
    url = f"{CFG.CROSSREF_BASE}/{quote_plus(doi)}"
    params = {}
    if _valid_email(CFG.POLITE_CONTACT):
        params["mailto"] = CFG.POLITE_CONTACT
    status, data = await _http_get_json(session, url, params=params)
    if status != 200 or not data:
        return None, None, None, None, "crossref"
    msg = data.get("message", {})
    title = (msg.get("title") or [None])[0]
    journal = None
    try:
        journal = (msg.get("container-title") or [None])[0]
        if not journal:
            journal = (msg.get("short-container-title") or [None])[0]
    except Exception:
        journal = None

    abstract = None
    try:
        abs_raw = msg.get("abstract")
        if isinstance(abs_raw, str):
            abstract = re.sub(r"<[^>]+>", "", abs_raw).strip()
    except Exception:
        abstract = None

    year = None
    def _first_year_from_parts(obj: Dict[str, Any]) -> Optional[int]:
        try:
            parts = obj.get("date-parts", [[]])[0]
            y = int(parts[0]);  # type: ignore
            if 1800 <= y <= 9999:
                return y
        except Exception:
            pass
        return None
    for key in ("published-print", "published-online", "issued"):
        if key in msg:
            year = _first_year_from_parts(msg.get(key) or {})
            if year: break
    return title, year, journal, abstract, "crossref"

async def fetch_openalex(session: aiohttp.ClientSession, doi: str) -> Tuple[Optional[str], Optional[int], Optional[str], Optional[str], List[Dict[str, Any]], str, Dict[str, Any]]:
    url = f"{CFG.OPENALEX_BASE}/doi:{doi}"
    params = {}
    if _valid_email(CFG.POLITE_CONTACT):
        params["mailto"] = CFG.POLITE_CONTACT
    status, data = await _http_get_json(session, url, params=params)
    if status != 200 or not data:
        # فالبک سبک (search by filter)
        fb_url = f"{CFG.OPENALEX_BASE}?filter=doi:{quote_plus(doi)}"
        fb_params = {}
        if _valid_email(CFG.POLITE_CONTACT):
            fb_params["mailto"] = CFG.POLITE_CONTACT
        fb_status, fb_data = await _http_get_json(session, fb_url, params=fb_params)
        if fb_status == 200 and isinstance(fb_data, dict) and isinstance(fb_data.get("results"), list) and fb_data["results"]:
            w = fb_data["results"][0]
            title = w.get("title")
            year = w.get("publication_year")
            journal = None
            try:
                journal = w.get("host_venue", {}).get("display_name")
            except Exception:
                journal = None
            abstract = w.get("abstract_inverted_index")
            if isinstance(abstract, dict):
                # تبدیل abstract_inverted_index به متن
                tokens: List[Tuple[int, str]] = []
                for key, positions in abstract.items():
                    if not isinstance(positions, list):
                        continue
                    for pos in positions:
                        tokens.append((int(pos), key))
                tokens.sort()
                abstract = " ".join(word for _, word in tokens)
            elif isinstance(abstract, str):
                abstract = abstract
            else:
                abstract = None
            concepts: List[Dict[str, Any]] = []
            for c in (w.get("concepts") or []):
                if not isinstance(c, dict):
                    continue
                concepts.append({
                    "display_name": c.get("display_name", ""),
                    "score": float(c.get("score", 0.0) or 0.0),
                    "level": c.get("level"),
                    "ancestors": [
                        {"display_name": a.get("display_name",""), "level": a.get("level")}
                        for a in (c.get("ancestors") or []) if isinstance(a, dict)
                    ],
                })
            logger.info("OA fallback ok | doi=%s concepts=%d year=%r title_present=%s",
                        doi, len(concepts), year, bool(title))
            return title, (int(year) if year else None), journal, abstract if isinstance(abstract, str) else None, concepts, "openalex_fallback_filter", fb_data
        return None, None, None, None, [], "openalex", {}

    title = data.get("title")
    year = data.get("publication_year")
    journal = None
    try:
        journal = data.get("host_venue", {}).get("display_name")
    except Exception:
        journal = None
    abstract = data.get("abstract") or data.get("abstract_inverted_index")
    if isinstance(abstract, dict):
        tokens: List[Tuple[int, str]] = []
        for key, positions in abstract.items():
            if not isinstance(positions, list):
                continue
            for pos in positions:
                tokens.append((int(pos), key))
        tokens.sort()
        abstract = " ".join(word for _, word in tokens)
    elif not isinstance(abstract, str):
        abstract = None
    concepts: List[Dict[str, Any]] = []
    for c in (data.get("concepts") or []):
        if not isinstance(c, dict):
            continue
        concepts.append({
            "display_name": c.get("display_name", ""),
            "score": float(c.get("score", 0.0) or 0.0),
            "level": c.get("level"),
            "ancestors": [
                {"display_name": a.get("display_name",""), "level": a.get("level")}
                for a in (c.get("ancestors") or []) if isinstance(a, dict)
            ],
        })
    logger.info("OA fetch ok | doi=%s concepts=%d year=%r title_present=%s",
                doi, len(concepts), year, bool(title))
    return title, (int(year) if year else None), journal, abstract if isinstance(abstract, str) else None, concepts, "openalex", data

# =========================
# نگاشت OpenAlex concepts → ۳ دسته
# =========================
ROOT_TO_CATEGORY: Dict[str, str] = {
    # پزشکی
    "medicine": "علوم پزشکی","public health": "علوم پزشکی","health care": "علوم پزشکی",
    "nursing": "علوم پزشکی","dentistry": "علوم پزشکی","pharmacology": "علوم پزشکی",
    "neuroscience": "علوم پزشکی","immunology": "علوم پزشکی","epidemiology": "علوم پزشکی",
    "oncology": "علوم پزشکی","psychiatry": "علوم پزشکی","physiology": "علوم پزشکی",
    "pathology": "علوم پزشکی","anatomy": "علوم پزشکی","biochemistry": "علوم پزشکی",
    "molecular biology": "علوم پزشکی","cell biology": "علوم پزشکی","genetics": "علوم پزشکی",
    "urology": "علوم پزشکی","nephrology": "علوم پزشکی","gynecology": "علوم پزشکی",
    "obstetrics": "علوم پزشکی","cardiology": "علوم پزشکی","dermatology": "علوم پزشکی",
    "endocrinology": "علوم پزشکی","gastroenterology": "علوم پزشکی","pulmonology": "علوم پزشکی",
    "otolaryngology": "علوم پزشکی","ophthalmology": "علوم پزشکی","rheumatology": "علوم پزشکی",
    "pediatrics": "علوم پزشکی","sleep medicine": "علوم پزشکی",

    # مهندسی
    "engineering": "مهندسی","materials science": "مهندسی","mechanical engineering": "مهندسی",
    "electrical engineering": "مهندسی","civil engineering": "مهندسی","chemical engineering": "مهندسی",
    "aerospace engineering": "مهندسی","computer science": "مهندسی","information systems": "مهندسی",
    "software engineering": "مهندسی","robotics": "مهندسی","nanotechnology": "مهندسی",

    # انسانی/اجتماعی
    "humanities": "انسانی","history": "انسانی","philosophy": "انسانی","linguistics": "انسانی",
    "law": "انسانی","education": "انسانی","religion": "انسانی","literature": "انسانی",
    "arts": "انسانی","anthropology": "انسانی","archaeology": "انسانی","cultural studies": "انسانی",
    "psychology": "انسانی","sociology": "انسانی","economics": "انسانی","political science": "انسانی",
    "international relations": "انسانی","geography": "انسانی",
}

def _category_from_openalex_concepts(concepts: List[Dict[str, Any]]) -> Tuple[Optional[str], Dict[str, float]]:
    if not concepts:
        return None, {"علوم پزشکی": 0.0, "مهندسی": 0.0, "انسانی": 0.0}
    buckets: Dict[str, float] = {"علوم پزشکی": 0.0, "مهندسی": 0.0, "انسانی": 0.0}
    total = 0.0
    for c in concepts:
        score = float(c.get("score") or 0.0)
        if score <= 0:
            continue
        names = [str(a.get("display_name", "")).lower() for a in (c.get("ancestors") or [])]
        names.append(str(c.get("display_name", "")).lower())
        for name in names:
            cat = ROOT_TO_CATEGORY.get(name)
            if cat:
                buckets[cat] += score
                total += score
                break
    if total <= 0:
        return None, buckets
    best_cat, best_val = max(buckets.items(), key=lambda kv: kv[1])
    share = best_val / total
    if share >= CFG.CATEGORY_MIN_SHARE:
        return best_cat, buckets
    return None, buckets

# =========================
# لاگ تشخیصی دسته‌بندی
# =========================
def _category_diagnostics_payload(
    doi: str,
    title: Optional[str],
    year: Optional[int],
    concepts: List[Dict[str, Any]],
    buckets_input: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    rows = []
    buckets = {"علوم پزشکی": 0.0, "مهندسی": 0.0, "انسانی": 0.0}
    total_mapped = 0.0
    matched_count = 0

    for c in concepts:
        disp = (c.get("display_name") or "").strip()
        score = float(c.get("score") or 0.0)
        level = c.get("level")
        anc_list = [str(a.get("display_name","")).strip() for a in (c.get("ancestors") or []) if isinstance(a, dict)]
        names_chain = [*anc_list, disp]

        matched_cat = None
        matched_by = None
        reason = None

        if score <= 0:
            reason = "nonpositive_score"
        else:
            for nm in names_chain:
                key = nm.lower().strip()
                if not key:
                    continue
                cat = ROOT_TO_CATEGORY.get(key)
                if cat:
                    matched_cat = cat
                    matched_by = nm
                    buckets[cat] += score
                    total_mapped += score
                    matched_count += 1
                    break
            if not matched_cat:
                reason = "no_mapping_for_names"

        row = {"name": disp, "score": score, "level": level, "ancestors": anc_list,
               "matched": bool(matched_cat), "matched_cat": matched_cat, "matched_by": matched_by,
               "skip_reason": reason}
        if CFG.LOG_CONCEPT_ROWS:
            rows.append(row)

    if buckets_input:
        for k, v in buckets_input.items():
            buckets[k] = float(v)

    if total_mapped > 0:
        best_cat, best_val = max(buckets.items(), key=lambda kv: kv[1])
        share = best_val / total_mapped if total_mapped > 0 else 0.0
    else:
        best_cat, share = None, 0.0

    threshold = CFG.CATEGORY_MIN_SHARE
    reason_unknown = None
    decided_category = None
    if total_mapped <= 0:
        reason_unknown = "no_concepts_mapped_to_any_bucket"
    else:
        if share >= threshold:
            decided_category = best_cat
        else:
            reason_unknown = f"share_below_threshold ({share:.3f} < {threshold:.2f})"

    payload = {
        "doi": doi, "title": title, "year": year, "threshold": threshold,
        "buckets": buckets, "total_mapped_score": total_mapped,
        "best_cat": best_cat, "best_share": round(share,4),
        "matched_concepts": matched_count, "total_concepts": len(concepts),
        "decided_category": decided_category or "نامشخص", "unknown_reason": reason_unknown,
    }
    if CFG.LOG_CONCEPT_ROWS:
        payload["concept_rows"] = rows
    return payload

def log_category_debug(
    doi: str,
    title: Optional[str],
    year: Optional[int],
    concepts: List[Dict[str, Any]],
    chosen_category: Optional[str],
    buckets: Dict[str, float],
) -> None:
    payload = _category_diagnostics_payload(doi, title, year, concepts, buckets_input=buckets or {})
    logger.info(
        "cat| doi=%s decided=%s best=%s share=%.3f totals=%s mapped=%d/%d",
        doi, payload["decided_category"], payload["best_cat"], payload["best_share"],
        {k: round(v, 3) for k, v in payload["buckets"].items()},
        payload["matched_concepts"], payload["total_concepts"],
    )
    try:
        catlog.debug(json.dumps(payload, ensure_ascii=False))
    except Exception as e:
        logger.warning("category_log_json_failed: %s", e)

# =========================
# AI-first: دسته‌بندی با هوش مصنوعی (Groq SDK)
# =========================
CANDIDATE_LABELS: List[str] = ["علوم پزشکی", "مهندسی", "انسانی"]

def _build_ai_prompt_from_title(title: Optional[str]) -> Tuple[str, str]:
    t = (title or "").strip()
    system = (
        "You are a precise classifier. Choose exactly ONE label from: "
        "['علوم پزشکی','مهندسی','انسانی']. Return STRICT JSON only as: "
        '{"label":"<one>","confidence":0..1,"reason":"short"}'
    )
    user = (
        "دسته‌بندی مقاله فقط براساس «عنوان» را مشخص کن.\n"
        "لیبل‌ها: علوم پزشکی | مهندسی | انسانی\n"
        f"عنوان: {t}"
    )
    return system, user

def _normalize_ai_label(lab: str) -> Optional[str]:
    if not lab: return None
    s = lab.strip().lower()
    mapping = {
        "علوم پزشکی":"علوم پزشکی","پزشکی":"علوم پزشکی","medical":"علوم پزشکی","medicine":"علوم پزشکی",
        "health":"علوم پزشکی","biomedical":"علوم پزشکی",
        "مهندسی":"مهندسی","engineering":"مهندسی","computer science":"مهندسی","technology":"مهندسی",
        "انسانی":"انسانی","humanities":"انسانی","social science":"انسانی","social sciences":"انسانی",
        "psychology":"انسانی","law":"انسانی","economics":"انسانی"
    }
    for k, v in mapping.items():
        if s == k or k in s:
            return v
    if lab in CANDIDATE_LABELS:
        return lab
    return None

async def _ai_classify_via_groq_title(title: Optional[str]) -> Tuple[Optional[str], float, str]:
    if not _HAS_GROQ or not CFG.GROQ_API_KEY:
        return None, 0.0, "groq_unavailable"

    client = AsyncGroq(api_key=CFG.GROQ_API_KEY)
    model_main = CFG.GROQ_MODEL or "llama-3.3-70b-versatile"
    model_fb = "llama-3.1-8b-instant"

    sys_msg, user_msg = _build_ai_prompt_from_title(title)

    # 1) تلاش اصلی با Chat Completions
    content = None
    source = "groq_sdk_chat"
    try:
        resp = await client.chat.completions.create(
            model=model_main,
            messages=[{"role":"system","content":sys_msg},{"role":"user","content":user_msg}],
            temperature=0,
            response_format={"type":"json_object"},
        )
        content = resp.choices[0].message.content if getattr(resp, "choices", None) else None
    except Exception as e:
        logger.warning("groq_sdk_chat_failed: %s", e)
        content = None
        source = "groq_sdk_chat_err"

    # 2) Responses API (fallback)
    if not content:
        try:
            resp = await client.responses.create(
                model=model_main,
                instructions=sys_msg,
                input=user_msg,
                temperature=0,
                response_format={"type":"json_object"},
            )
            content = getattr(resp, "output_text", None)
            if not content:
                try:
                    content = resp.output[0].content[0].text  # type: ignore
                except Exception:
                    content = None
            source = "groq_sdk_responses"
        except Exception as e:
            logger.warning("groq_sdk_responses_failed: %s", e)
            content = None
            source = "groq_sdk_responses_err"

    # 3) مدل fallback
    if not content and model_main != model_fb:
        try:
            resp = await client.chat.completions.create(
                model=model_fb,
                messages=[{"role":"system","content":sys_msg},{"role":"user","content":user_msg}],
                temperature=0,
                response_format={"type":"json_object"},
            )
            content = resp.choices[0].message.content if getattr(resp, "choices", None) else None
            source = "groq_sdk_chat_fb"
        except Exception as e:
            logger.warning("groq_sdk_chat_fb_failed: %s", e)

    if not content:
        return None, 0.0, source

    # --- Parse JSON ---
    try:
        obj = json.loads(content)
    except Exception:
        i, j = content.find("{"), content.rfind("}")
        if i >= 0 and j >= 0 and j > i:
            try:
                obj = json.loads(content[i:j+1])
            except Exception:
                logger.warning("groq_json_extract_fail | content=%s", content[:300])
                return None, 0.0, source
        else:
            logger.warning("groq_no_json | content=%s", content[:300])
            return None, 0.0, source

    lab = _normalize_ai_label(str(obj.get("label","")))
    conf = float(obj.get("confidence") or 0.0)
    if not lab:
        return None, conf, source
    return lab, conf, source

async def ai_classify_category_from_title(title: Optional[str]) -> Tuple[Optional[str], float, str]:
    if (CFG.AI_BACKEND or "none").lower() != "groq":
        return None, 0.0, "disabled"
    return await _ai_classify_via_groq_title(title)


def groq_health_check_sync() -> None:
    if not _HAS_GROQ or not CFG.GROQ_API_KEY:
        logger.info("groq_health_skip | has_groq=%s", _HAS_GROQ)
        return
    try:
        client = Groq(api_key=CFG.GROQ_API_KEY)
        models = client.models.list()
        ids = [m.id for m in getattr(models, "data", [])]
        logger.info("groq_models | count=%d sample=%s", len(ids), ids[:8])
    except Exception as e:
        logger.warning("groq_models_list_failed: %s", e)

# =========================
# Provider & OA utilities
# =========================
# ---------- Sci-Hub links stored by admin ----------
# ---------- Sci-Hub links stored by admin ----------
_DEFAULT_SCIHUB_REGEX = (
    r'src=["\'](https?://[^"\']+?\.pdf)["\']|'
    r'href=["\'](https?://[^"\']+?\.pdf)["\']|'
    r'onclick=["\'][^"\']*location\.href=["\']([^"\']+?\.pdf)[^"\']*["\']'
)

def _dynamic_scihub_providers() -> List[Dict[str, Any]]:
    """لینک‌هایی که ادمین در ربات ذخیره کرده است → provider."""
    raw = db_get_setting("SCI_HUB_LINKS") or ""
    urls = [u.strip() for u in raw.splitlines() if u.strip()]
    out: List[Dict[str, Any]] = []
    for i, base in enumerate(urls, start=1):
        if not base.lower().startswith("http"):
            continue
        tpl = base.rstrip("/") + "/{doi}"
        out.append({
            "name": f"scihub_custom{i}",       # مهم: با scihub_ شروع شود
            "type": "search",
            "query": tpl,
            "pdf_regex": _DEFAULT_SCIHUB_REGEX,
            "headers": {},
        })
    return out


def _parse_providers(json_str: str, *, source: str) -> List[Dict[str, Any]]:
    """Parse providers JSON از .env (فقط موارد «قانونی»)."""
    try:
        arr = json.loads(json_str)
    except Exception as e:
        logger.warning(
            "providers_parse_failed | source=%s | err=%s | snippet=%r",
            source, e, (json_str or "")[:200]
        )
        return []

    if not isinstance(arr, list):
        logger.warning(
            "providers_parse_failed | source=%s | err=not_a_list | snippet=%r",
            source, (json_str or "")[:200]
        )
        return []

    out: List[Dict[str, Any]] = []
    for p in arr:
        if not isinstance(p, dict):
            logger.warning("providers_parse_skip_non_dict | source=%s | item=%r", source, p)
            continue

        name = str(p.get("name") or "").strip() or "provider"
        ptype = str(p.get("type") or "").strip()  # direct_template | pdf_template | search
        if ptype not in ("direct_template", "pdf_template", "search"):
            logger.warning("providers_parse_unsupported_type | source=%s | name=%s | type=%r", source, name, ptype)
            continue

        headers = p.get("headers") if isinstance(p.get("headers"), dict) else {}
        out.append({
            "name": name,
            "type": ptype,
            "template": p.get("template"),
            "query": p.get("query"),
            "pdf_regex": p.get("pdf_regex"),
            "headers": headers
        })
    return out


def _get_legal_providers(year: Optional[int]) -> List[Dict[str, Any]]:
    """برمی‌گرداند کدام Providerها در این اجرا مجازند."""
    # ➊ لیست‌های ثابتی که از ENV می‌آید
    pre  = _parse_providers(CFG.LEGAL_PRE2022,  source="LEGAL_PRE2022")
    post = _parse_providers(CFG.LEGAL_2022PLUS, source="LEGAL_2022PLUS")

    # ➋ لیست پویا که ادمین داده
    dyn = _dynamic_scihub_providers()
    if dyn:
        # تمام Sci-Hubهای ثابت را حذف کن
        pre  = [p for p in pre  if not p["name"].startswith("scihub")]
        post = [p for p in post if not p["name"].startswith("scihub")]
        # Sci-Hubهای سفارشی را جلوتر از بقیه بگذار
        pre  = dyn + pre
        post = dyn + post

    # ➌ خروجی نهایی بر اساس سال
    if year is None:
        return post + pre
    return pre if year <= 2021 else post


async def _find_oa_pdf_from_unpaywall(session: aiohttp.ClientSession, doi: str) -> Optional[str]:
    """استخراج PDF از Unpaywall API."""
    try:
        url = f"https://api.unpaywall.org/v2/{quote_plus(doi)}?email={quote_plus(CFG.POLITE_CONTACT)}"
        status, data = await _http_get_json(session, url, params={})
        if status != 200 or not data:
            logger.info("unpaywall_failed | doi=%s status=%s", doi, status)
            return None
        best_oa = data.get("best_oa_location") or {}
        pdf_url = best_oa.get("url_for_pdf") or best_oa.get("url")
        if pdf_url and str(pdf_url).lower().endswith(".pdf"):
            logger.info("unpaywall_pdf_found | doi=%s url=%s", doi, pdf_url)
            return str(pdf_url)
        return None
    except Exception as e:
        logger.warning("unpaywall_error | doi=%s err=%s", doi, e)
        return None

def _find_oa_pdf_from_openalex_raw(oa_raw: Dict[str, Any]) -> Optional[str]:
    if not isinstance(oa_raw, dict):
        return None
    loc = oa_raw.get("best_oa_location") or {}
    pdf = loc.get("url_for_pdf") or loc.get("pdf_url")
    if not pdf:
        pdf = loc.get("url")
        if pdf and not str(pdf).lower().endswith(".pdf"):
            pdf = None
    if pdf:
        return str(pdf)
    try:
        oa = oa_raw.get("open_access") or {}
        alt = oa.get("oa_url")
        if alt and str(alt).lower().endswith(".pdf"):
            return str(alt)
    except Exception:
        pass
    return None

def _openalex_landing_url(oa_raw: Dict[str, Any]) -> Optional[str]:
    if not isinstance(oa_raw, dict):
        return None
    loc = oa_raw.get("best_oa_location") or {}
    for key in ("landing_page_url", "url_for_landing_page", "url"):
        val = loc.get(key)
        if val:
            return str(val)
    try:
        oa = oa_raw.get("open_access") or {}
        alt = oa.get("oa_url")
        if alt:
            return str(alt)
    except Exception:
        pass
    return None

async def _extract_pdf_from_landing_page(session: aiohttp.ClientSession, landing_url: str) -> Optional[str]:
    try:
        async with session.get(landing_url, timeout=CFG.HTTP_TIMEOUT, allow_redirects=True) as resp:
            if resp.status != 200:
                logger.debug("landing_pdf_non200 | url=%s status=%s", landing_url, resp.status)
                return None
            html = await resp.text()
            landing_host = resp.url.host.lower() if resp and resp.url and resp.url.host else ""
    except Exception as exc:
        logger.debug("landing_pdf_fetch_failed | url=%s err=%s", landing_url, exc)
        return None
    else:
        landing_host = locals().get("landing_host", "")

    patterns = [
        r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)["\']',
        r'<a[^>]+href=["\']([^"\']+\.pdf)["\'][^>]*>(?:[^<]*PDF[^<]*)</a>',
    ]
    for pat in patterns:
        m = re.search(pat, html, flags=re.IGNORECASE)
        if m:
            pdf_url = htmlmod.unescape(m.group(1)).strip()
            if pdf_url:
                full = urljoin(landing_url, pdf_url)
                host = urlparse(full).netloc.lower()
                doi_fragment = landing_url.split("doi.org/")[-1].replace("/", "_") if "doi.org/" in landing_url else ""
                if landing_host and host and landing_host not in host and (doi_fragment and doi_fragment not in full):
                    logger.info("landing_pdf_rejected_host | landing_host=%s pdf_host=%s url=%s", landing_host, host, full)
                    continue
                logger.info("landing_pdf_extracted | base=%s pdf=%s", landing_url, full)
                return full
    return None

async def fetch_crossref_pdf_link(session: aiohttp.ClientSession, doi: str) -> Optional[str]:
    url = f"{CFG.CROSSREF_BASE}/{quote_plus(doi)}"
    params = {}
    if _valid_email(CFG.POLITE_CONTACT):
        params["mailto"] = CFG.POLITE_CONTACT
    status, data = await _http_get_json(session, url, params=params)
    if status != 200 or not isinstance(data, dict):
        return None
    msg = data.get("message") or {}
    for link in (msg.get("link") or []):
        try:
            if (link.get("content-type") or "").lower() == "application/pdf" and link.get("URL"):
                return str(link["URL"])
        except Exception:
            continue
    return None

_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
def _safe_filename(name: str, suffix: str = ".pdf") -> str:
    name = _SAFE_CHARS.sub("_", name.strip())[:120] or f"file_{int(time.time())}"
    if not name.lower().endswith(suffix.lower()):
        name += suffix
    # جلوگیری از خروج از پوشه tmp
    name = os.path.basename(name)
    return name

async def download_pdf_to_tmp(session: aiohttp.ClientSession, url: str, *, hint: str = "paper") -> Optional[Path]:
    max_bytes = CFG.OA_DOWNLOAD_MAX_MB * 1024 * 1024
    retries = 2
    for attempt in range(1, retries + 1):
        headers = session.headers.copy()
        if attempt == 2:
            headers.pop("User-Agent", None)
        try:
            async with session.get(url, headers=headers, timeout=CFG.HTTP_TIMEOUT, allow_redirects=True) as resp:
                if resp.status != 200:
                    logger.warning("pdf_dl_non200 | attempt=%d url=%s status=%s headers=%r", attempt, url, resp.status, dict(resp.headers))
                    if resp.status == 403 and attempt < retries:
                        await asyncio.sleep(1)
                        continue
                    return None

                ctype = (resp.headers.get("Content-Type") or "").lower()
                if ("pdf" not in ctype) and (not url.lower().endswith(".pdf")):
                    logger.info("pdf_dl_suspicious_ctype | url=%s ctype=%s", url, ctype)

                clen = resp.headers.get("Content-Length")
                if clen:
                    try:
                        if int(clen) > max_bytes:
                            logger.warning("pdf_too_large_by_header | url=%s size=%sB", url, clen)
                            return None
                    except Exception:
                        pass

                fname = _safe_filename(hint or "paper")
                fpath = CFG.DOWNLOAD_TMP_DIR / fname
                total = 0
                with open(fpath, "wb") as f:
                    async for chunk in resp.content.iter_chunked(64 * 1024):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > max_bytes:
                            logger.warning("pdf_too_large_stream | url=%s written=%sB", url, total)
                            try:
                                fpath.unlink(missing_ok=True)
                            except Exception:
                                pass
                            return None
                        f.write(chunk)
                if total == 0:
                    logger.warning("pdf_empty | url=%s", url)
                    try:
                        fpath.unlink(missing_ok=True)
                    except Exception:
                        pass
                    return None
                return fpath
        except Exception as e:
            logger.warning("pdf_dl_failed | attempt=%d url=%s err=%s", attempt, url, e)
            if attempt < retries:
                await asyncio.sleep(1)
                continue
    return None

async def _find_pdf_via_provider(session: aiohttp.ClientSession, provider: Dict[str, Any], doi: str) -> Optional[str]:
    ptype = provider.get("type")
    headers = provider.get("headers") or {}
    if ptype in ("direct_template", "pdf_template"):
        tpl = provider.get("template")
        if not tpl: 
            return None
        return str(tpl).format(doi=quote_plus(doi))
    elif ptype == "search":
        qtpl = provider.get("query")
        if not qtpl:
            return None
        url = str(qtpl).format(doi=quote_plus(doi))
        retries = 1  # تغییر از 3 به 1
        for attempt in range(1, retries + 1):
            try:
                proxy = CFG.LEGAL_HTTP_PROXY
                async with session.get(url, timeout=60 if attempt > 1 else 30, headers=headers, proxy=proxy) as resp:
                    if resp.status != 200:
                        logger.info("provider_search_non200 | name=%s attempt=%d status=%s headers=%r", provider.get("name"), attempt, resp.status, dict(resp.headers))
                        if resp.status in (403, 429, 504) and attempt < retries:
                            await asyncio.sleep(5 * attempt)
                            continue
                        return None
                    html = await resp.text()
                    logger.debug("provider_html_snippet | name=%s url=%s attempt=%d html=%r", provider.get("name"), url, attempt, html[:1000])
            except Exception as e:
                import traceback
                logger.warning("provider_search_failed | name=%s url=%s attempt=%d err=%s traceback=%s", provider.get("name"), url, attempt, str(e), traceback.format_exc())
                if attempt < retries:
                    await asyncio.sleep(5 * attempt)
                    continue
                return None
            rx = provider.get("pdf_regex") or r'src=["\'](https?://[^"\']+?\.pdf)["\']|href=["\'](https?://[^"\']+?\.pdf)["\']|onclick=["\'][^"\']*location\.href=["\']([^"\']+?\.pdf)[^"\']*["\']'
            try:
                m = re.search(rx, html, flags=re.IGNORECASE)
                if not m:
                    return None
                pdf_candidate = next((g for g in m.groups() if g), None)
                if not pdf_candidate:
                    return None
                pdf_url = pdf_candidate
                pdf_url = urljoin(url, pdf_url)
                logger.info("provider_pdf_found | name=%s doi=%s url=%s", provider.get("name"), doi, pdf_url)
                return pdf_url
            except Exception:
                return None
    return None

async def try_download_via_providers(session: aiohttp.ClientSession, doi: str, year: Optional[int]) -> Optional[Path]:
    providers = _get_legal_providers(year)
    if not providers:
        return None
    for p in providers:
        name = p.get("name")
        url = await _find_pdf_via_provider(session, p, doi)
        if not url:
            logger.info("provider_no_pdf_url | name=%s doi=%s", name, doi)
            continue
        logger.info("provider_pdf_candidate | name=%s url=%s", name, url)
        fpath = await download_pdf_to_tmp(session, url, hint=f"{doi.replace('/','_')}_{name}")
        if fpath:
            return fpath
    return None


def _resolve_wdm_arch() -> str:
    env_arch = os.environ.get("WDM_ARCH")
    if env_arch:
        return env_arch
    machine = (platform.machine() or "").lower()
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    if machine in {"x86_64", "amd64", "x64"}:
        return "x64"
    return machine or "x64"


def _build_chrome_driver(proxy_url: Optional[str] = None) -> webdriver.Chrome:
    opts = Options()
    if CFG.CHROME_HEADLESS:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-notifications")
    opts.add_argument("--start-maximized")

    # ↓↓↓ مهم: پروفایل واقعی + ضد شناسایی
    if CFG.CHROME_PROFILE_DIR:
        opts.add_argument(f"--user-data-dir={CFG.CHROME_PROFILE_DIR}")
        opts.add_argument("--profile-directory=Default")

    #opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    #opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument("--disable-blink-features=AutomationControlled")

    if proxy_url:
        opts.add_argument(f"--proxy-server={proxy_url}")

    use_uc = bool(CFG.USE_UNDETECTED and _HAS_UC)
    if CFG.USE_UNDETECTED and not _HAS_UC:
        logger.warning("undetected_chromedriver not available; falling back to standard chromedriver")

    os.environ["WDM_ARCH"] = _resolve_wdm_arch()

    driver_path = CFG.CHROMEDRIVER_PATH.strip()
    if driver_path:
        _LOGGER = logging.getLogger("doi_bot.selenium")
        _LOGGER.info("Using chromedriver from CHROMEDRIVER_PATH=%s", driver_path)
        if use_uc:
            return uc.Chrome(driver_executable_path=driver_path, options=opts)
        return webdriver.Chrome(service=Service(driver_path), options=opts)

    drv_path = ChromeDriverManager().install()

    if use_uc:
        return uc.Chrome(options=opts)
    return webdriver.Chrome(service=Service(drv_path), options=opts)

def _get_scihub_driver() -> webdriver.Chrome:
    global _SCIHUB_DRIVER
    if not _driver_alive(_SCIHUB_DRIVER):
        if _SCIHUB_DRIVER:
            with suppress(Exception):
                _SCIHUB_DRIVER.quit()
        _SCIHUB_DRIVER = _build_chrome_driver()
        with suppress(Exception):
            _SCIHUB_DRIVER.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
                "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            })
        _SCIHUB_DRIVER.get("https://www.sci-hub.ee/")
        _human_pause(1, 2)
        logger.info("scihub_driver_initialized")
    return _SCIHUB_DRIVER


class ScihubNoResultError(RuntimeError):
    """Raised when Sci-Hub explicitly reports missing content."""


def _scihub_no_result_banner(driver: webdriver.Chrome) -> bool:
    try:
        body = driver.find_element(By.TAG_NAME, "body")
        text = (body.text or "").lower()
    except Exception:
        return False
    keywords = [
        "scientific mutual aid community",
        "please try to search again",
        "you can request this article",
    ]
    return any(kw in text for kw in keywords)


def _selenium_extract_pdf_url(doi: str) -> str:
    """کروم را اجرا می‌کند و لینک PDF داخل iframe را برمی‌گرداند."""
    driver = _get_scihub_driver()
    logger.info("selenium_starting | doi=%s", doi)

    url = f"https://www.sci-hub.ee/{quote_plus(doi)}"
    driver.get(url)
    with suppress(Exception):
        _maybe_solve_recaptcha(driver, driver.current_url)
    _human_pause(2, 3)
    if _scihub_no_result_banner(driver):
        raise ScihubNoResultError("scihub_no_pdf_banner")
    try:
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "iframe#pdf, iframe[src*='.pdf']"))
        )
    except TimeoutException as exc:
        if _scihub_no_result_banner(driver):
            raise ScihubNoResultError("scihub_iframe_missing") from exc
        raise

    iframe = driver.find_element(By.CSS_SELECTOR, "iframe#pdf, iframe[src*='.pdf']")
    pdf_url = iframe.get_attribute("src") or ""
    if pdf_url.startswith("//"):
        pdf_url = "https:" + pdf_url
    pdf_url = unquote(pdf_url)
    logger.info("selenium_pdf_src | doi=%s url=%s", doi, pdf_url)
    if not pdf_url:
        raise RuntimeError("pdf_url_not_found")
    return pdf_url


async def download_via_sciencedirect(
    session: aiohttp.ClientSession,
    doi: str,
    title: Optional[str],
    abstract: Optional[str],
    journal: Optional[str],
    *,
    bot,
    chat_id: int,
    force: bool = False,
) -> Optional[Path]:
    accounts = iranpaper_accounts_ordered()
    return await scidir_download(
        session,
        doi,
        title,
        abstract,
        journal,
        cfg=CFG,
        bot=bot,
        chat_id=chat_id,
        accounts=accounts,
        build_chrome_driver=_build_chrome_driver,
        solve_recaptcha=_maybe_solve_recaptcha,
        db_get_setting=db_get_setting,
        db_set_setting=db_set_setting,
        download_pdf_to_tmp=download_pdf_to_tmp,
        ensure_v2ray_running=ensure_v2ray_running,
        force=force,
    )


async def download_pdf_with_selenium(doi: str) -> Optional[Path]:
    """Sci-Hub را باز می‌کند، لینک PDF را می‌یابد و فایل را داخل پوشهٔ موقت ذخیره می‌کند."""
    try:
        pdf_url = await asyncio.to_thread(_selenium_extract_pdf_url, doi)

        async with aiohttp.ClientSession() as session:
            hint = f"{doi.replace('/', '_')}_scihub"
            return await download_pdf_to_tmp(session, pdf_url, hint=hint)
    except ScihubNoResultError:
        raise
    except Exception as e:
        logger.error("selenium_failed | doi=%s err=%s", doi, e)
        return None
# =========================
# ScienceDirect automation
# =========================


def _human_pause(min_s: float, max_s: float) -> None:
    time.sleep(random.uniform(min_s, max_s))


# =========================
# پردازش DOIها
# =========================
def _valid_email(s: Optional[str]) -> bool:
    return bool(s and re.match(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$", s))


async def fetch_unpaywall_pdf_link(session: aiohttp.ClientSession, doi: str, email: str) -> Optional[str]:
    url = f"https://api.unpaywall.org/v2/{quote_plus(doi)}?email={quote_plus(email)}"
    try:
        async with session.get(url, timeout=30) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get('is_oa') and data.get('best_oa_location', {}).get('url_for_pdf'):
                    return data['best_oa_location']['url_for_pdf']
                return None
            logger.warning("unpaywall_fetch_failed | doi=%s status=%s", doi, resp.status)
            return None
    except Exception as e:
        logger.warning("unpaywall_fetch_error | doi=%s err=%s", doi, str(e))
        return None

async def process_single_doi(session: aiohttp.ClientSession, user_id: int, doi_raw: str) -> Dict[str, Any]:
    doi = normalize_doi(doi_raw)
    try:
        logger.info("process_doi | raw=%r normalized=%r", doi_raw, doi)
    except Exception:
        pass

    try:
        # متادیتا را موازی می‌گیریم
        cr_task = asyncio.create_task(fetch_crossref(session, doi))
        oa_task = asyncio.create_task(fetch_openalex(session, doi))
        cr_title, cr_year, cr_journal, cr_abs, _cr_src = await cr_task
        oa_title, oa_year, oa_journal, oa_abs, oa_concepts, _oa_src, _oa_raw = await oa_task

        # عنوان/سال: ترجیح Crossref، بعد OpenAlex
        title = cr_title or oa_title
        year = cr_year or oa_year
        journal = cr_journal or oa_journal
        abstract = cr_abs or oa_abs

        # ======== AI-first: دسته‌بندی با Groq از روی عنوان ========
        category = "نامشخص"
        source = "none"

        ai_label, ai_conf, ai_src = await ai_classify_category_from_title(title)
        if ai_label and (ai_conf >= CFG.AI_MIN_CONF):
            category = ai_label
            source = f"ai:{ai_src}"
            logger.info("ai_category | doi=%s label=%s conf=%.2f backend=%s", doi, category, ai_conf, ai_src)
        else:
            # فالبک به OpenAlex concepts (برای پایداری)
            fallback_cat, fallback_buckets = _category_from_openalex_concepts(oa_concepts)
            if fallback_cat:
                category = fallback_cat
                source = "openalex_concepts"
            # لاگ خلاصهٔ تصمیم
            try:
                total_score = sum((fallback_buckets or {}).values()) if isinstance(fallback_buckets, dict) else 0.0
                logger.info(
                    "cat_decision_fallback | doi=%s category=%s total_score=%.3f buckets=%s oa_concepts=%d ai_conf=%.2f ai_backend=%s",
                    doi, category, total_score, {k: round(v, 3) for k, v in (fallback_buckets or {}).items()},
                    len(oa_concepts), ai_conf or 0.0, ai_src
                )
            except Exception:
                pass
            # دیباگ عمیق
            try:
                log_category_debug(doi, title, year, oa_concepts, category, fallback_buckets or {})
            except Exception as _e:
                logger.debug("category_diagnostic_failed: %s", _e)

        # --- تشخیص OA PDF ---
        oa_pdf_url = None
        try:
            oa_pdf_url = _find_oa_pdf_from_openalex_raw(_oa_raw or {})
            if not oa_pdf_url:
                landing_url = _openalex_landing_url(_oa_raw or {})
                if landing_url:
                    extracted = await _extract_pdf_from_landing_page(session, landing_url)
                    if extracted:
                        oa_pdf_url = extracted
            if not oa_pdf_url:
                # استفاده از ایمیل کاربر اگه تنظیم شده، وگرنه POLITE_CONTACT
                user = db_get_user(user_id)
                email = user.get("email") or CFG.POLITE_CONTACT
                oa_pdf_url = await fetch_unpaywall_pdf_link(session, doi, email)
            if not oa_pdf_url:
                oa_pdf_url = await fetch_crossref_pdf_link(session, doi)
            # Fallback به Sci-Hub
            if not oa_pdf_url:
                scihub_providers = [p for p in _get_legal_providers(year) if p["name"].startswith("scihub")]
                for p in scihub_providers:
                    oa_pdf_url = await _find_pdf_via_provider(session, p, doi)
                    if oa_pdf_url:
                        logger.info("scihub_pdf_found | doi=%s url=%s", doi, oa_pdf_url)
                        break
            if oa_pdf_url:
                logger.info("oa_pdf_found | doi=%s url=%s", doi, oa_pdf_url)
        except Exception as e:
            logger.debug("oa_pdf_detect_failed | doi=%s err=%s", doi, e)

        status = "ok" if (title or year) else "not_found"
        db_upsert_meta(user_id, doi, title=title, year=year, category=category, source=source, status=status, error=None)
        return {
            "doi": doi,
            "title": title,
            "year": year,
            "journal": journal,
            "abstract": abstract,
            "category": category,
            "status": status,
            "oa_pdf_url": oa_pdf_url,
        }

    except Exception as e:
        db_upsert_meta(user_id, doi, title=None, year=None, category=None, source="none", status="error", error=str(e)[:300])
        return {
            "doi": doi,
            "title": None,
            "year": None,
            "journal": None,
            "abstract": None,
            "category": None,
            "status": "error",
            "oa_pdf_url": None,
        }


async def process_single_doi_oa_only(session: aiohttp.ClientSession, user_id: int, doi_raw: str) -> Dict[str, Any]:
    """
    مثل process_single_doi اما فقط مسیرهای Open-Access/قانونی را بررسی می‌کند:
    OpenAlex/landing page/Unpaywall/Crossref PDF link. (بدون Sci-Hub/ScienceDirect)
    """
    doi = normalize_doi(doi_raw)
    try:
        logger.info("process_doi_oa_only | raw=%r normalized=%r", doi_raw, doi)
    except Exception:
        pass

    try:
        cr_task = asyncio.create_task(fetch_crossref(session, doi))
        oa_task = asyncio.create_task(fetch_openalex(session, doi))
        cr_title, cr_year, cr_journal, cr_abs, _cr_src = await cr_task
        oa_title, oa_year, oa_journal, oa_abs, oa_concepts, _oa_src, _oa_raw = await oa_task

        title = cr_title or oa_title
        year = cr_year or oa_year
        journal = cr_journal or oa_journal
        abstract = cr_abs or oa_abs

        category = "نامشخص"
        source = "none"

        ai_label, ai_conf, ai_src = await ai_classify_category_from_title(title)
        if ai_label and (ai_conf >= CFG.AI_MIN_CONF):
            category = ai_label
            source = f"ai:{ai_src}"
        else:
            fallback_cat, _fallback_buckets = _category_from_openalex_concepts(oa_concepts)
            if fallback_cat:
                category = fallback_cat
                source = "openalex_concepts"

        oa_pdf_url = None
        try:
            oa_pdf_url = _find_oa_pdf_from_openalex_raw(_oa_raw or {})
            if not oa_pdf_url:
                landing_url = _openalex_landing_url(_oa_raw or {})
                if landing_url:
                    extracted = await _extract_pdf_from_landing_page(session, landing_url)
                    if extracted:
                        oa_pdf_url = extracted
            if not oa_pdf_url:
                user = db_get_user(user_id)
                email = user.get("email") or CFG.POLITE_CONTACT
                oa_pdf_url = await fetch_unpaywall_pdf_link(session, doi, email)
            if not oa_pdf_url:
                oa_pdf_url = await fetch_crossref_pdf_link(session, doi)
        except Exception as e:
            logger.debug("oa_only_pdf_detect_failed | doi=%s err=%s", doi, e)

        status = "ok" if (title or year) else "not_found"
        db_upsert_meta(user_id, doi, title=title, year=year, category=category, source=source, status=status, error=None)
        return {
            "doi": doi,
            "title": title,
            "year": year,
            "journal": journal,
            "abstract": abstract,
            "category": category,
            "status": status,
            "oa_pdf_url": oa_pdf_url,
        }

    except Exception as e:
        db_upsert_meta(user_id, doi, title=None, year=None, category=None, source="none", status="error", error=str(e)[:300])
        return {
            "doi": doi,
            "title": None,
            "year": None,
            "journal": None,
            "abstract": None,
            "category": None,
            "status": "error",
            "oa_pdf_url": None,
        }


# --- ارسال سند با retry نمایی
SEND_SEM = asyncio.Semaphore(1)

async def _send_document_with_retry(bot, chat_id: int, file_path: Path, caption: str, *, tries: int = 3, timeout: int = 180) -> bool:
    for i in range(1, tries + 1):
        try:
            await bot.send_chat_action(chat_id=chat_id, action="upload_document")
            with open(file_path, "rb") as f:
                await bot.send_document(
                    chat_id,
                    document=f,
                    filename=os.path.basename(file_path),
                    caption=caption,
                    read_timeout=timeout,
                    parse_mode=PARSE_HTML if PARSE_HTML else None
                )
            return True
        except Exception as e:
            wait = 2 ** i  # 2,4,8
            logger.warning("send_document_retry_%d/%d | err=%s | wait=%ss", i, tries, e, wait)
            await asyncio.sleep(wait)
    return False

def _is_valid_font(path: Path, *, min_bytes: int = 1024) -> bool:
    try:
        return path.exists() and path.stat().st_size >= min_bytes
    except OSError:
        return False

def _summary_font_path() -> Path:
    preferred = Path("assets/fonts/Vazirmatn-Regular.ttf")
    fallback = Path("assets/fonts/DejaVuSans.ttf")
    if _is_valid_font(preferred):
        return preferred
    if preferred.exists():
        try:
            size = preferred.stat().st_size
        except OSError:
            size = "unknown"
        logger.warning("report_font_invalid | path=%s size=%s", preferred, size)
    if _is_valid_font(fallback):
        return fallback
    if fallback.exists():
        return fallback
    if preferred.exists():
        return preferred
    return fallback

async def process_dois_batch(user_id: int, dois: List[str], chat_id: int, bot) -> None:
    """پردازش یک‌جای DOIها: متادیتا + تعیین دسته + کشف OA + دانلود/ارسال PDF."""
    sem = asyncio.Semaphore(CFG.MAX_CONCURRENCY)

    # User-Agent مودبانه
    ua = "doi-bot/1.0"
    if _valid_email(CFG.POLITE_CONTACT):
        ua += f" (+mailto:{CFG.POLITE_CONTACT})"
    headers = {"User-Agent": ua}

    async with aiohttp.ClientSession(headers=headers) as session:
        async def run_with_limit(d):
            async with sem:
                return await process_single_doi(session, user_id, d)

        # اجرای موازی پردازش متادیتا/کشف OA
        tasks = [asyncio.create_task(run_with_limit(d)) for d in dois]
        results: List[Dict[str, Any]] = []
        for t in asyncio.as_completed(tasks):
            r = await t
            results.append(r)

        # خلاصهٔ نتایج
        ok = [r for r in results if r["status"] == "ok"]
        not_found = [r for r in results if r["status"] == "not_found"]
        errors = [r for r in results if r["status"] == "error"]

        def _short_title(t: Optional[str]) -> str:
            if not t:
                return "—"
            t = re.sub(r"\s+", " ", t).strip()
            return (t[:70] + "…") if len(t) > 72 else t

        lines = []
        for r in ok[:10]:
            lines.append(f"• {r['year'] or '—'} | {r['category']} | {_short_title(r['title'])}")
        extra = ""
        if len(ok) > 10:
            extra = f"\n… و {len(ok) - 10} مورد دیگر"

        summary = (
            "📊 <b>نتیجهٔ پردازش DOIها</b>\n"
            f"کل: <b>{len(results)}</b> | موفق: <b>{len(ok)}</b> | نامشخص: <b>{len(not_found)}</b> | خطا: <b>{len(errors)}</b>\n\n"
            + ("\n".join(lines) if lines else "موردی برای نمایش نیست.")
            + extra
            + ("\n\nℹ️ نتیجهٔ کامل در سیستم ذخیره شد.")
        )
        try:
            await bot.send_message(chat_id, summary, parse_mode=PARSE_HTML if PARSE_HTML else None)
        except Exception as e:
            logger.warning("failed to send summary: %s", e)

        # دانلودها و بسته‌بندی ZIP + PDF فهرست
        activation = is_activation_on()
        entries: List[Dict[str, Any]] = []
        font_path = _summary_font_path()
        zip_path = CFG.DOWNLOAD_LINK_DIR / f"downloads_{int(time.time())}.zip"

        for r in results:
            doi = r["doi"]
            year = r.get("year")
            title = r.get("title") or doi
            fpath: Optional[Path] = None
            cost_label = "نامشخص"
            status_label = "دانلود نشده"

            if r["status"] != "ok":
                status_label = "دانلود نشده (متادیتا ناقص)"
            elif not activation:
                status_label = "دانلود نشده (غیرفعال)"
            else:
                oa_pdf_url = r.get("oa_pdf_url")
                if oa_pdf_url:
                    fpath = await download_pdf_to_tmp(session, oa_pdf_url, hint=doi.replace("/", "_"))
                    cost_label = "رایگان"
                    status_label = "دانلود موفق" if fpath else "دانلود ناموفق"

                if not fpath:
                    fpath = await try_download_via_providers(session, doi, year)
                    if fpath:
                        cost_label = "رایگان"
                        status_label = "دانلود موفق"

                if not fpath and (year or 0) >= 2022:
                    fpath = await download_via_sciencedirect(
                        session,
                        doi,
                        title,
                        r.get("abstract"),
                        r.get("journal"),
                        bot=bot,
                        chat_id=chat_id,
                        force=False,
                    )
                    if fpath:
                        cost_label = "هزینه‌دار"
                        status_label = "دانلود موفق"

                if not fpath:
                    try:
                        fpath = await download_pdf_with_selenium(doi)
                    except ScihubNoResultError:
                        fpath = None
                        logger.info("scihub_no_result_detected | doi=%s", doi)
                    if fpath:
                        cost_label = "رایگان"
                        status_label = "دانلود موفق"

            fname = fpath.name if fpath else "—"
            entries.append({
                "doi": doi,
                "title": title,
                "year": year or "—",
                "filename": fname,
                "file_path": str(fpath) if fpath else None,
                "cost": cost_label,
                "status": status_label,
            })

        try:
            zip_file = build_zip_with_summary(entries, zip_path, font_path)
        except Exception as e:
            logger.warning("zip_build_failed | err=%s", e)
            zip_file = None

        if zip_file and zip_file.exists():
            link = None
            token = None
            if CFG.DOWNLOAD_BOT_USERNAME:
                token = db_create_download_link(user_id, str(zip_file), zip_file.name)
                link = _download_bot_deeplink(token) if token else None
            if link:
                try:
                    await bot.send_message(
                        chat_id,
                        f"Download link (start the download bot):\n{link}",
                        parse_mode=PARSE_HTML if PARSE_HTML else None,
                        reply_markup=_download_link_done_kb(),
                    )
                except Exception as e:
                    logger.warning("download_link_send_failed | err=%s", e)
                    link = None
            if not link:
                if token:
                    db_delete_download_link(token)
                try:
                    async with SEND_SEM:
                        await bot.send_chat_action(chat_id=chat_id, action="upload_document")
                        with open(zip_file, "rb") as f:
                            await bot.send_document(
                                chat_id,
                                document=f,
                                filename=zip_file.name,
                                caption="بستهٔ دانلود شده + فهرست",
                                read_timeout=240,
                            )
                except Exception as e:
                    logger.warning("zip_send_failed | err=%s", e)
                finally:
                    with suppress(Exception):
                        zip_file.unlink()
        else:
            await bot.send_message(chat_id, "❗️ ساخت بستهٔ دانلود/فهرست انجام نشد.", parse_mode=PARSE_HTML if PARSE_HTML else None)
        for item in entries:
            fp = item.get("file_path")
            if fp:
                with suppress(Exception):
                    Path(fp).unlink()


async def process_dois_batch_oa_only(user_id: int, dois: List[str], chat_id: int, bot) -> None:
    """پردازش DOIها فقط برای مسیرهای Open-Access/قانونی + ارسال به تلگرام."""
    sem = asyncio.Semaphore(CFG.MAX_CONCURRENCY)

    ua = "doi-bot/1.0"
    if _valid_email(CFG.POLITE_CONTACT):
        ua += f" (+mailto:{CFG.POLITE_CONTACT})"
    headers = {"User-Agent": ua}

    async with aiohttp.ClientSession(headers=headers) as session:
        async def run_with_limit(d):
            async with sem:
                return await process_single_doi_oa_only(session, user_id, d)

        tasks = [asyncio.create_task(run_with_limit(d)) for d in dois]
        results: List[Dict[str, Any]] = []
        for t in asyncio.as_completed(tasks):
            results.append(await t)

        ok = [r for r in results if r["status"] == "ok"]
        errors = [r for r in results if r["status"] == "error"]

        summary = (
            "📊 <b>نتیجهٔ بررسی (Open Access)</b>\n"
            f"کل: <b>{len(results)}</b> | موفق: <b>{len(ok)}</b> | خطا: <b>{len(errors)}</b>\n\n"
            "ℹ️ فقط در صورت وجود لینک Open-Access تلاش به دانلود انجام می‌شود."
        )
        with suppress(Exception):
            await bot.send_message(chat_id, summary, parse_mode=PARSE_HTML if PARSE_HTML else None)

        activation = is_activation_on()
        entries: List[Dict[str, Any]] = []
        font_path = _summary_font_path()
        zip_path = CFG.DOWNLOAD_LINK_DIR / f"downloads_oa_{int(time.time())}.zip"

        for r in results:
            doi = r["doi"]
            title = r.get("title") or doi
            year = r.get("year")

            fpath: Optional[Path] = None
            status_label = "دانلود نشده"
            cost_label = "رایگان (Open Access)"

            if r["status"] != "ok":
                status_label = "دانلود نشده (متادیتا ناقص)"
            elif not activation:
                status_label = "دانلود نشده (غیرفعال)"
            else:
                oa_pdf_url = r.get("oa_pdf_url")
                if oa_pdf_url:
                    fpath = await download_pdf_to_tmp(session, oa_pdf_url, hint=doi.replace("/", "_"))
                    status_label = "دانلود موفق" if fpath else "دانلود ناموفق"
                    if fpath:
                        db_inc_used(user_id, free_inc=1)
                else:
                    status_label = "Open Access پیدا نشد"

            entries.append({
                "doi": doi,
                "title": title,
                "year": year or "—",
                "filename": fpath.name if fpath else "—",
                "file_path": str(fpath) if fpath else None,
                "cost": cost_label,
                "status": status_label,
            })

        zip_file = None
        try:
            zip_file = build_zip_with_summary(entries, zip_path, font_path)
        except Exception as e:
            logger.warning("zip_build_failed | err=%s", e)

        if zip_file and zip_file.exists():
            link = None
            token = None
            if CFG.DOWNLOAD_BOT_USERNAME:
                token = db_create_download_link(user_id, str(zip_file), zip_file.name)
                link = _download_bot_deeplink(token) if token else None
            if link:
                try:
                    await bot.send_message(
                        chat_id,
                        f"Download link (start the download bot):\n{link}",
                        parse_mode=PARSE_HTML if PARSE_HTML else None,
                        reply_markup=_download_link_done_kb(),
                    )
                except Exception as e:
                    logger.warning("download_link_send_failed | err=%s", e)
                    link = None
            if not link:
                if token:
                    db_delete_download_link(token)
                try:
                    async with SEND_SEM:
                        await bot.send_chat_action(chat_id=chat_id, action="upload_document")
                        with open(zip_file, "rb") as f:
                            await bot.send_document(
                                chat_id,
                                document=f,
                                filename=zip_file.name,
                                caption="بستهٔ Open-Access + فهرست",
                                read_timeout=240,
                            )
                except Exception as e:
                    logger.warning("zip_send_failed | err=%s", e)
                finally:
                    with suppress(Exception):
                        zip_file.unlink()
        else:
            with suppress(Exception):
                await bot.send_message(
                    chat_id,
                    "⚠️ ساخت بستهٔ دانلود/فهرست انجام نشد.",
                    parse_mode=PARSE_HTML if PARSE_HTML else None,
                )
        for item in entries:
            fp = item.get("file_path")
            if fp:
                with suppress(Exception):
                    Path(fp).unlink()
