# -*- coding: utf-8 -*-
from __future__ import annotations
# mainbot.py  ── لایهٔ UI ربات تلگرام
# ---------------------------------------
import asyncio
import contextlib
import html as htmlmod
import json
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Final, Dict, Any, List, Tuple, Optional

# اجازه بده اجرای مستقیم `python3 doi/mainbot.py` هم کار کند
# (در این حالت sys.path روی پوشهٔ `doi/` قرار می‌گیرد و ماژول‌های ریشه مثل `downloadmain.py` پیدا نمی‌شوند.)
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
)
from telegram.error import BadRequest
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, CallbackContext, filters
)

# زیرساخت، تنظیمات، دیتابیس، پردازش DOI
from downloadmain import (  # noqa: F401  # type: ignore
    CFG, logger, catlog,
    db_init, db_upsert_user, db_get_user, db_set_seen_welcome, db_set_email, db_set_delivery,
    db_set_plan, db_set_plan_period, db_set_doi_quota, db_inc_doi_quota_used,
    db_set_doi_daily_quota, db_inc_doi_daily_used,
    db_add_quota, db_count_dois, db_add_dois, db_get_or_create_token, db_set_new_token,
    db_get_setting, db_set_setting,
    db_create_payment_request, db_get_payment_request, db_get_open_payment_request,
    db_update_payment_receipt, db_set_payment_review_message, db_set_payment_status,
    PAYMENT_STATUS_AWAITING, PAYMENT_STATUS_PENDING, PAYMENT_STATUS_APPROVED, PAYMENT_STATUS_REJECTED,
    db_add_wallet_balance,
    normalize_doi,
    request_email_verification, verify_email_code,
    db_add_quota_by_email, db_get_user_by_email, db_get_quota_status,
    vpn_load_configs, vpn_add_config, vpn_remove_config, vpn_set_active, vpn_ping_all,
    _get_scihub_driver, _build_chrome_driver, _maybe_solve_recaptcha,
    process_dois_batch, groq_health_check_sync, ensure_v2ray_running, CB_DL_DONE,
    iranpaper_accounts_ordered, iranpaper_set_active, iranpaper_set_primary, iranpaper_set_vpn,
    set_activation, is_activation_on, iranpaper_vpn_map,
)
from downloaders.sciencedirect import warmup_accounts
from telegram.request import HTTPXRequest
from doi.ui_email_verification import (
    build_email_verification_conversation,
    show_profile_card,
    CB_PLAN_CONTINUE,
)
# Sci-Net automation
try:
    from scinet import (
        ensure_session as ensure_scinet_session,
        monitor_cycle as scinet_monitor_cycle,
        complete_active_request as scinet_complete_active_request,
        SCINET_DONE_CALLBACK,
    )
except Exception:  # pragma: no cover - Sci-Net optional dependency
    ensure_scinet_session = None
    scinet_monitor_cycle = None
    scinet_complete_active_request = None
    SCINET_DONE_CALLBACK = "scinet:done"

try:
    from api_server import start_api_server, stop_api_server
except Exception:  # pragma: no cover - optional local API
    start_api_server = None  # type: ignore
    stop_api_server = None   # type: ignore
# ↙️ اضافه کنید
try:
    from groq import AsyncGroq
    _HAS_GROQ_LOCAL = True
except Exception:
    AsyncGroq = None          # type: ignore
    _HAS_GROQ_LOCAL = False
# =========================
# ثابت‌های UI / CallbackData
# =========================
WELCOME_TEXT: Final[str] = (
    "👋 به ربات DOI خوش اومدید.\n"
    "اینجا می‌تونید با DOI مقاله‌ها رو دریافت کنید، اشتراک بخرید و چک پلاژیاریسم انجام بدید.\n\n"
    "راهنمای دکمه‌ها:\n"
    "• ارسال DOI: ثبت DOI برای دریافت فایل\n"
    "• چک پلاژیاریسم: ارسال فایل/متن برای بررسی\n"
    "• خرید اشتراک: انتخاب و خرید پلن\n"
    "• شارژ کیف پول: افزایش موجودی کیف پول\n"
    "• حساب کاربری: وضعیت اشتراک و اطلاعات شما"
)

# --- کاربر عادی
CB_MENU_SEND_DOI   = "menu:send_doi"
CB_MENU_ACCOUNT    = "menu:account"
CB_MENU_TOPUP      = "menu:topup"
CB_MENU_WALLET_TOPUP = "menu:wallet_topup"
CB_MENU_ROOT       = "menu:root"
CB_MENU_PLAGIARISM = "menu:plagiarism"

CB_PLAGIARISM_ONLY = "plag:only"
CB_PLAGIARISM_AI   = "plag:ai"
CB_PLAGIARISM_PAY_PREFIX = "plag:pay:"
CB_PLAGIARISM_WALLET_PREFIX = "plag:wallet:"
CB_PLAGIARISM_SUBMIT_PREFIX = "plag:submit:"

# --- پنل ادمین
CB_ADMIN_USER_MENU = "admin:user_menu"   # منوی کاربر عادی
CB_ADMIN_LINKS     = "admin:links"       # شاخهٔ «لینک‌ها»
CB_ADMIN_VPN       = "admin:vpn"
CB_ADMIN_ACCOUNTS  = "admin:accounts"
CB_ADMIN_ACTIVATION= "admin:activation"
CB_ADMIN_CHARGE    = "admin:charge"
CB_ADMIN_STORE     = "admin:store"
CB_STORE_SET_PRICE_PLAG = "store:set_price_plag"
CB_STORE_SET_PRICE_AI   = "store:set_price_ai"
CB_STORE_SET_CARD       = "store:set_card"
CB_STORE_SET_GROUP      = "store:set_group"

CB_PAYMENT_APPROVE_PREFIX = "pay:approve:"
CB_PAYMENT_REJECT_PREFIX  = "pay:reject:"
CB_PAYMENT_CANCEL_PREFIX  = "pay:cancel:"
CB_PAYMENT_DONE           = "pay:done"
CB_BACK_ADMIN_ROOT = "admin:back_root"   # بازگشت از زیرمنوها

# --- زیرمنوی لینک‌ها
CB_LINKS_SCIHUB    = "links:scihub"
CB_LINKS_DOWNLOAD  = "links:download" 
CB_LINKS_VPN       = "links:vpn"
CB_SCIHUB_EDIT     = "scihub:edit"
CB_VPN_IR          = "vpn:cfg:iran"
CB_VPN_GLOBAL      = "vpn:cfg:global"
CB_DL_EDIT         = "dl:edit"
CB_DL_ADD          = "dl:add"
CB_DL_DELETE       = "dl:delete"
CB_DL_BACKUP       = "dl:backup"
# --- پلن و سایر (همان فایل قبلی)
CB_PLAN_NORMAL     = "plan:normal"
CB_PLAN_PREMIUM    = "plan:premium"
CB_NORMAL_40       = "select:normal:40"
CB_NORMAL_100      = "select:normal:100"
CB_PREMIUM_1M      = "select:premium:1m"
CB_PREMIUM_3M      = "select:premium:3m"
CB_PLAN_PAY_PREFIX = "plan:pay:"
CB_PLAN_WALLET_PREFIX = "plan:wallet:"
CB_CONFIRM         = "confirm"
CB_BACK            = "back"
CB_BACK_ROOT       = "back_root"

# حساب کاربری
CB_ACCOUNT_EMAIL   = "account:email"
CB_ACCOUNT_DELIVERY= "account:delivery"
CB_ACCOUNT_TOKEN   = "account:token"
CB_TOKEN_REGEN     = "token:regen"
CB_DELIVERY_BOT    = "delivery:set:bot"
CB_DELIVERY_EMAIL  = "delivery:set:email"

# DOI
CB_DOI_FINISH      = "doi:finish"

# Conversation states
WAITING_FOR_DOI:   Final[int] = 1
WAITING_FOR_EMAIL: Final[int] = 2
WAITING_FOR_EMAIL_CODE: Final[int] = 3
WAITING_SCIHUB:    Final[int] = 10   # ویرایش لینک‌های Sci-Hub
WAITING_DL_ADD     = 20      # ← اضافه
WAITING_DL_DELETE  = 21      # ← اضافه
WAITING_DL_RATE   = 22  
WAITING_VPN_LABEL  = 30
WAITING_VPN_CONFIG = 31
WAITING_VPN_SELECT = 32
WAITING_VPN_DELETE = 33
WAITING_VPN_ASSIGN_CFG  = 34
WAITING_VPN_ASSIGN_SLOT = 35
WAITING_CHARGE_EMAIL: Final[int] = 50
WAITING_CHARGE_PAID:  Final[int] = 51
WAITING_CHARGE_FREE:  Final[int] = 52
WAITING_STORE_PRICE_PLAG: Final[int] = 60
WAITING_STORE_PRICE_AI:   Final[int] = 61
WAITING_STORE_CARD:       Final[int] = 62
WAITING_STORE_GROUP:      Final[int] = 63
WAITING_PAYMENT_RECEIPT:  Final[int] = 70
WAITING_PLAGIARISM_SUBMIT: Final[int] = 71
WAITING_WALLET_TOPUP_AMOUNT: Final[int] = 72
DOI_REGEX   = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Z0-9]+)\b", re.IGNORECASE)
EMAIL_REGEX = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

STORE_PRICE_PLAG_KEY = "STORE_PRICE_PLAGIARISM"
STORE_PRICE_PLAG_AI_KEY = "STORE_PRICE_PLAGIARISM_AI"
STORE_CARD_KEY = "STORE_CARD_NUMBER"
STORE_GROUP_KEY = "STORE_PAYMENT_GROUP_CHAT_ID"

PLAGIARISM_PRODUCT = "plagiarism"
PLAGIARISM_AI_PRODUCT = "plagiarism_ai"
WALLET_TOPUP_PRODUCT = "wallet_topup"
PLAN_PRODUCT_PREFIX = "plan:"

PLAGIARISM_PRODUCTS = {
    PLAGIARISM_PRODUCT: {"label": "چک پلاژياریسم", "price_key": STORE_PRICE_PLAG_KEY},
    PLAGIARISM_AI_PRODUCT: {"label": "چک پلاژياریسم و AI", "price_key": STORE_PRICE_PLAG_AI_KEY},
}

PLAN_PRODUCTS = {
    "normal_40": {
        "label": "🧰 پلن معمولی — ۴۰ مقاله (اعتبار ۱ سال)",
        "base_price": 240000,
        "quota_paid": 40,
        "note": "اعتبار ۱ ساله",
        "duration_days": 365,
        "doi_limit": 40,
    },
    "normal_100": {
        "label": "🧰 پلن معمولی — ۱۰۰ مقاله (اعتبار ۱ سال)",
        "base_price": 500000,
        "quota_paid": 100,
        "note": "اعتبار ۱ ساله",
        "duration_days": 365,
        "doi_limit": 100,
    },
    "premium_1m": {
        "label": "⭐️ پریمیوم — ۱ ماه (نامحدود با سقف ۱۵ در روز)",
        "base_price": 240000,
        "quota_paid": 450,
        "note": "دانلود نامحدود (۱۵ در روز)",
        "duration_days": 30,
        "doi_unlimited": True,
        "daily_limit": 15,
    },
    "premium_3m": {
        "label": "⭐️ پریمیوم — ۳ ماه (نامحدود با سقف ۱۵ در روز)",
        "base_price": 600000,
        "quota_paid": 1350,
        "note": "دانلود نامحدود (۱۵ در روز)",
        "duration_days": 90,
        "doi_unlimited": True,
        "daily_limit": 15,
    },
}

PENDING_PAYMENT_KEY = "pending_payment_id"
PENDING_PAYMENT_PRODUCT_KEY = "pending_payment_product"
PENDING_REJECT_KEY = "pending_reject_payment_id"
PENDING_REJECT_MSG_KEY = "pending_reject_message"
PENDING_SUBMISSION_KEY = "pending_submission_payment_id"
# -----------------


def _plan_product_key(plan_type: str) -> str:
    return f"{PLAN_PRODUCT_PREFIX}{plan_type}"


def _plan_type_from_product_key(product_key: str) -> Optional[str]:
    if product_key.startswith(PLAN_PRODUCT_PREFIX):
        return product_key[len(PLAN_PRODUCT_PREFIX):]
    return None


def _plan_info(plan_type: str) -> Optional[Dict[str, Any]]:
    return PLAN_PRODUCTS.get(plan_type)


def _plan_label(plan_type: str) -> str:
    info = _plan_info(plan_type)
    return info["label"] if info else "نامشخص"


def _plan_base_price(plan_type: str) -> int:
    info = _plan_info(plan_type)
    return int(info["base_price"]) if info else 0


def _plan_quota_paid(plan_type: str) -> int:
    info = _plan_info(plan_type)
    return int(info.get("quota_paid") or 0) if info else 0


def _plan_note(plan_type: str) -> str:
    info = _plan_info(plan_type)
    return str(info.get("note") or "") if info else ""


def _plan_duration_days(plan_type: str) -> int:
    info = _plan_info(plan_type)
    return int(info.get("duration_days") or 0) if info else 0


def _plan_doi_limit(plan_type: str) -> int:
    info = _plan_info(plan_type)
    return int(info.get("doi_limit") or 0) if info else 0


def _plan_is_unlimited(plan_type: str) -> bool:
    info = _plan_info(plan_type)
    return bool(info and info.get("doi_unlimited"))


PLAN_STATUS_ACTIVE: Final[str] = "فعال"
PLAN_STATUS_EXPIRED: Final[str] = "منقضی"


def _plan_daily_limit(plan_type: str) -> int:
    info = _plan_info(plan_type)
    return int(info.get("daily_limit") or 0) if info else 0


def _now_ts() -> int:
    return int(time.time())


def _today_key() -> int:
    return int(datetime.now().strftime("%Y%m%d"))


def _format_expiry_date(ts: int) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(int(ts)).strftime("%Y/%m/%d")


def _sync_plan_window(user: Dict[str, Any], plan_type: str) -> Dict[str, Any]:
    if user.get("plan_status") != PLAN_STATUS_ACTIVE:
        return user
    duration_days = _plan_duration_days(plan_type)
    if duration_days <= 0:
        return user
    started_at = int(user.get("plan_started_at") or 0)
    expires_at = int(user.get("plan_expires_at") or 0)
    if started_at <= 0 or expires_at <= 0 or expires_at <= started_at:
        now = _now_ts()
        started_at = now
        expires_at = now + (duration_days * 86400)
        user_id = int(user.get("user_id") or 0)
        if user_id:
            db_set_plan_period(user_id, started_at=started_at, expires_at=expires_at)
        user["plan_started_at"] = started_at
        user["plan_expires_at"] = expires_at
    return user


def _sync_doi_quota(user: Dict[str, Any], plan_type: str) -> Dict[str, Any]:
    user_id = int(user.get("user_id") or 0)
    if not user_id:
        return user
    if _plan_is_unlimited(plan_type):
        if int(user.get("doi_quota_limit") or 0) != 0 or int(user.get("doi_quota_used") or 0) != 0:
            db_set_doi_quota(user_id, limit=0, used=0)
            user["doi_quota_limit"] = 0
            user["doi_quota_used"] = 0
        return user
    limit = int(user.get("doi_quota_limit") or 0)
    if limit <= 0:
        limit = _plan_doi_limit(plan_type)
        used = int(user.get("doi_quota_used") or 0)
        used = min(used, limit) if limit > 0 else used
        if limit > 0:
            db_set_doi_quota(user_id, limit=limit, used=used)
            user["doi_quota_limit"] = limit
            user["doi_quota_used"] = used
    return user


def _sync_doi_daily_quota(user: Dict[str, Any], plan_type: str) -> Dict[str, Any]:
    user_id = int(user.get("user_id") or 0)
    if not user_id:
        return user
    plan_daily = _plan_daily_limit(plan_type)
    daily_limit = int(user.get("doi_daily_limit") or 0)
    daily_used = int(user.get("doi_daily_used") or 0)
    day_key = int(user.get("doi_daily_day") or 0)
    today = _today_key()

    if plan_daily <= 0:
        if daily_limit or daily_used or day_key:
            db_set_doi_daily_quota(user_id, limit=0, used=0, day_key=0)
            user["doi_daily_limit"] = 0
            user["doi_daily_used"] = 0
            user["doi_daily_day"] = 0
        return user

    if day_key != today:
        daily_used = 0
        day_key = today
    if daily_limit != plan_daily or user.get("doi_daily_day") != day_key:
        db_set_doi_daily_quota(user_id, limit=plan_daily, used=daily_used, day_key=day_key)
        user["doi_daily_limit"] = plan_daily
        user["doi_daily_used"] = daily_used
        user["doi_daily_day"] = day_key
    return user


def _doi_access_status(user: Dict[str, Any]) -> Dict[str, Any]:
    plan_type = str(user.get("plan_type") or "").strip()
    status = str(user.get("plan_status") or "").strip()
    if not plan_type:
        return {"ok": False, "reason": "no_plan"}
    if status != PLAN_STATUS_ACTIVE:
        return {"ok": False, "reason": "inactive", "status": status or "?"}
    user = _sync_plan_window(user, plan_type)
    expires_at = int(user.get("plan_expires_at") or 0)
    if expires_at and _now_ts() > expires_at:
        user_id = int(user.get("user_id") or 0)
        if user_id:
            label = (user.get("plan_label") or _plan_label(plan_type))
            price_val = user.get("plan_price")
            price = int(price_val) if isinstance(price_val, int) else _plan_base_price(plan_type)
            note = (user.get("plan_note") or _plan_note(plan_type))
            db_set_plan(user_id, plan_type, label, price, PLAN_STATUS_EXPIRED, note)
        return {"ok": False, "reason": "expired", "expires_at": expires_at}
    if _plan_is_unlimited(plan_type):
        user = _sync_doi_daily_quota(user, plan_type)
        daily_limit = int(user.get("doi_daily_limit") or 0)
        daily_used = int(user.get("doi_daily_used") or 0)
        daily_remaining = max(0, daily_limit - daily_used) if daily_limit > 0 else 0
        if daily_limit > 0 and daily_remaining <= 0:
            return {"ok": False, "reason": "daily_exhausted", "daily_limit": daily_limit, "expires_at": expires_at}
        return {
            "ok": True,
            "unlimited": True,
            "expires_at": expires_at,
            "plan_type": plan_type,
            "daily_limit": daily_limit,
            "daily_used": daily_used,
            "daily_remaining": daily_remaining,
        }
    user = _sync_doi_quota(user, plan_type)
    limit = int(user.get("doi_quota_limit") or 0)
    used = int(user.get("doi_quota_used") or 0)
    remaining = max(0, limit - used) if limit > 0 else 0
    if limit <= 0:
        return {"ok": False, "reason": "limit_unset", "plan_type": plan_type}
    if remaining <= 0:
        return {"ok": False, "reason": "quota_exhausted", "limit": limit, "used": used, "expires_at": expires_at}
    return {
        "ok": True,
        "limit": limit,
        "used": used,
        "remaining": remaining,
        "expires_at": expires_at,
        "plan_type": plan_type,
    }


def _doi_status_lines(access: Dict[str, Any], buffer_count: int = 0) -> List[str]:
    if not access or not access.get("ok"):
        return []
    expires_at = int(access.get("expires_at") or 0)
    lines: List[str] = []
    if access.get("unlimited"):
        line = "⭐️ پلن پریمیوم فعال"
        if expires_at:
            line += f" تا {_format_expiry_date(expires_at)}"
        lines.append(line)
        daily_limit = int(access.get("daily_limit") or 0)
        if daily_limit > 0:
            daily_remaining = int(access.get("daily_remaining") or 0)
            daily_after = max(0, daily_remaining - max(0, int(buffer_count or 0)))
            lines.append(f"📅 سهمیه امروز: {daily_after} از {daily_limit} باقی مانده")
        return lines
    limit = int(access.get("limit") or 0)
    remaining = int(access.get("remaining") or 0)
    remaining_after = max(0, remaining - max(0, int(buffer_count or 0)))
    if limit > 0:
        lines.append(f"🔢 سهمیه DOI: {remaining_after} از {limit} باقی مانده")
    if expires_at:
        lines.append(f"⏳ اعتبار تا: {_format_expiry_date(expires_at)}")
    return lines


def _doi_block_message(access: Dict[str, Any]) -> str:
    reason = access.get("reason")
    if reason == "no_plan":
        return "برای ارسال DOI باید اشتراک فعال داشته باشید. از بخش «خرید اشتراک» پلن خود را تهیه کنید."
    if reason == "inactive":
        status = access.get("status") or "—"
        return f"وضعیت اشتراک شما: {status}. فعلاً امکان ارسال DOI ندارید."
    if reason == "expired":
        return "اشتراک شما منقضی شده است. لطفاً از بخش «خرید اشتراک» تمدید کنید."
    if reason == "quota_exhausted":
        return "سهمیه DOI شما برای این اشتراک تمام شده است."
    if reason == "daily_exhausted":
        return "سهمیه امروز شما تمام شده است. لطفاً فردا دوباره تلاش کنید."
    return "امکان ارسال DOI در حال حاضر فعال نیست. لطفاً با پشتیبانی تماس بگیرید."



PROVIDER_LABELS = [
    "ScienceDirect", "SpringerLink", "Wiley", "ACS",
    "Taylor & Francis", "IEEE", "Other"
]

def _valid_email(s: Optional[str]) -> bool:
    return bool(s and EMAIL_REGEX.match(s))

def _valid_email_code(code: str) -> bool:
    c = _normalize_digits(code).strip()
    return len(c) == 6 and c.isdigit()

def _email_code_rules_text() -> str:
    return (
        "📧 <b>کد تایید ارسال شد</b>\n"
        "کد ۶ رقمی ارسال‌شده به ایمیل را وارد کنید.\n"
        "اعتبار کد: ۱۰ دقیقه\n\n"
        "برای انصراف: /cancel"
    )

# =========================
# دسترسی ادمین
# =========================

def is_admin(update: Update) -> bool:
    """
    ادمین را به دو روش تشخیص می‌دهد:
    1) اگر متغیر ثابت ADMIN_USER_ID در Config ست شده و با user_id برابر باشد.
    2) یا اگر username (بدون @) پس از lowercase دقیقاً با ADMIN_USERNAME یکی باشد.
    """
    u = update.effective_user
    if not u:
        return False

    # تشخیص امن بر اساس آی‌دی عددی
    admin_uid = getattr(CFG, "ADMIN_USER_ID", None)
    if admin_uid and int(u.id) == int(admin_uid):
        return True

    # تشخیص بر اساس username (غیروابسته به حروف بزرگ/کوچک)
    return bool(u.username and u.username.lower() == CFG.ADMIN_USERNAME.lower())


def ensure_user(user_id: int, username: Optional[str]) -> Dict[str, Any]:
    db_upsert_user(user_id, username)
    return db_get_user(user_id)

def _format_price_toman(price: Optional[int]) -> str:
    if not isinstance(price, int) or price <= 0:
        return "نامشخص"
    return f"{price:,}".replace(",", "٬")


_DIGIT_TRANS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")


def _normalize_digits(text: str) -> str:
    return (text or "").translate(_DIGIT_TRANS)


def _parse_amount(text: str) -> Optional[int]:
    s = _normalize_digits(text).replace(",", "").replace("٬", "").strip()
    if not s.isdigit():
        return None
    n = int(s)
    return n if n > 0 else None

def _get_store_price(key: str, default: int) -> int:
    raw = db_get_setting(key)
    if raw is None:
        return int(default or 0)
    try:
        return int(raw)
    except Exception:
        return int(default or 0)

def _get_store_card_number() -> str:
    raw = db_get_setting(STORE_CARD_KEY)
    card = (raw if raw is not None else getattr(CFG, "STORE_CARD_NUMBER", "")).strip()
    return card

def _get_store_group_id() -> int:
    raw = db_get_setting(STORE_GROUP_KEY)
    if raw is None:
        return int(getattr(CFG, "PAYMENT_GROUP_CHAT_ID", 0) or 0)
    try:
        return int(raw)
    except Exception:
        return 0

def _format_card_number(card: str) -> str:
    digits = re.sub(r"\D", "", card or "")
    return digits or card

# =========================
# Keyboards
# =========================
# -- کاربر عادی
def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 ارسال DOI", callback_data=CB_MENU_SEND_DOI)],
        [InlineKeyboardButton("🔎 چک پلاژیاریسم و AI", callback_data=CB_MENU_PLAGIARISM)],
        [InlineKeyboardButton("🧾 خرید اشتراک", callback_data=CB_MENU_TOPUP)],
        [
            InlineKeyboardButton("👤 حساب کاربری", callback_data=CB_MENU_ACCOUNT),
            InlineKeyboardButton("💳 شارژ کیف پول", callback_data=CB_MENU_WALLET_TOPUP),
        ],
    ])

def plagiarism_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("چک پلاژياریسم", callback_data=CB_PLAGIARISM_ONLY)],
        [InlineKeyboardButton("چک پلاژياریسم و AI", callback_data=CB_PLAGIARISM_AI)],
        [InlineKeyboardButton("↩️ بازگشت", callback_data=CB_MENU_ROOT)],
    ])

def plagiarism_product_kb(product_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🫧 پرداخت", callback_data=f"{CB_PLAGIARISM_PAY_PREFIX}{product_key}")],
        [InlineKeyboardButton("💰 پرداخت با کیف پول", callback_data=f"{CB_PLAGIARISM_WALLET_PREFIX}{product_key}")],
        [InlineKeyboardButton("↩️ بازگشت", callback_data=CB_MENU_PLAGIARISM)],
    ])

def admin_root_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("منوی اصلی", callback_data=CB_ADMIN_USER_MENU)],
        [InlineKeyboardButton("📡 کانفیگ V2Ray", callback_data=CB_ADMIN_VPN)],
        [InlineKeyboardButton("👥 اکانت‌ها (IranPaper)", callback_data=CB_ADMIN_ACCOUNTS)],
        [InlineKeyboardButton("🔓 فعال‌سازی دانلود", callback_data=CB_ADMIN_ACTIVATION)],
        [InlineKeyboardButton("🛒 فروشگاه", callback_data=CB_ADMIN_STORE)],
        [InlineKeyboardButton("💳 شارژ حساب", callback_data=CB_ADMIN_CHARGE)],
    ])

# -- شاخهٔ «لینک‌ها»
def store_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 تنظیم قیمت چک پلاژياریسم", callback_data=CB_STORE_SET_PRICE_PLAG)],
        [InlineKeyboardButton("💰 تنظیم قیمت چک پلاژياریسم و AI", callback_data=CB_STORE_SET_PRICE_AI)],
        [InlineKeyboardButton("💳 تنظیم شماره کارت", callback_data=CB_STORE_SET_CARD)],
        [InlineKeyboardButton("👥 تنظیم گروه بررسی پرداخت", callback_data=CB_STORE_SET_GROUP)],
        [InlineKeyboardButton("↩️ بازگشت به پنل ادمین", callback_data=CB_BACK_ADMIN_ROOT)],
    ])

def store_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("↩️ بازگشت به فروشگاه", callback_data=CB_ADMIN_STORE)],
    ])

def payment_review_kb(payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تایید پرداخت", callback_data=f"{CB_PAYMENT_APPROVE_PREFIX}{payment_id}")],
        [InlineKeyboardButton("❌ رد پرداخت", callback_data=f"{CB_PAYMENT_REJECT_PREFIX}{payment_id}")],
    ])

def payment_review_done_kb(label: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=CB_PAYMENT_DONE)],
    ])

def payment_cancel_kb(payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ لغو پرداخت", callback_data=f"{CB_PAYMENT_CANCEL_PREFIX}{payment_id}")],
        [InlineKeyboardButton("↩️ بازگشت به منوی اصلی", callback_data=CB_MENU_ROOT)],
    ])

def links_root_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 لینک سای‌هاب",   callback_data=CB_LINKS_SCIHUB)],
        [InlineKeyboardButton("⬇️ لینک‌های دانلود", callback_data=CB_LINKS_DOWNLOAD)],
        [InlineKeyboardButton("🛡 VPN‌ها",          callback_data=CB_LINKS_VPN)],
        [InlineKeyboardButton("↩️ بازگشت به پنل ادمین", callback_data=CB_BACK_ADMIN_ROOT)],
    ])

def links_scihub_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ تغییر", callback_data=CB_SCIHUB_EDIT)],
        [InlineKeyboardButton("↩️ بازگشت", callback_data=CB_ADMIN_LINKS)],
    ])

# -- سایر کیبوردها (بدون تغییر)
def back_to_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("↩️ بازگشت به منوی اصلی", callback_data=CB_MENU_ROOT)]])

def account_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 روش ارسال", callback_data=CB_ACCOUNT_DELIVERY)],
        [InlineKeyboardButton("🏠 منو", callback_data=CB_MENU_ROOT)],
    ])

def token_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔁 ساخت توکن جدید", callback_data=CB_TOKEN_REGEN)],
        [InlineKeyboardButton("↩️ بازگشت به حساب کاربری", callback_data=CB_MENU_ACCOUNT)],
        [InlineKeyboardButton("↩️ بازگشت به منوی اصلی", callback_data=CB_MENU_ROOT)],
    ])

def topup_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧰 پلن معمولی", callback_data=CB_PLAN_NORMAL)],
        [InlineKeyboardButton("⭐️ پلن اشتراکی پریمیوم", callback_data=CB_PLAN_PREMIUM)],
        [InlineKeyboardButton("↩️ بازگشت به منوی اصلی", callback_data=CB_MENU_ROOT)],
    ])

def normal_subplan_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("۴۰ مقاله — ۲۴۰٬۰۰۰ تومان", callback_data=CB_NORMAL_40)],
        [InlineKeyboardButton("۱۰۰ مقاله — ۵۰۰٬۰۰۰ تومان", callback_data=CB_NORMAL_100)],
        [InlineKeyboardButton("↩️ بازگشت", callback_data=CB_BACK)],
        [InlineKeyboardButton("↩️ بازگشت به منوی اصلی", callback_data=CB_MENU_ROOT)],
    ])

def premium_subplan_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("۱ ماه — ۲۴۰٬۰۰۰ تومان", callback_data=CB_PREMIUM_1M)],
        [InlineKeyboardButton("۳ ماه — ۶۰۰٬۰۰۰ تومان", callback_data=CB_PREMIUM_3M)],
        [InlineKeyboardButton("↩️ بازگشت", callback_data=CB_BACK)],
        [InlineKeyboardButton("↩️ بازگشت به منوی اصلی", callback_data=CB_MENU_ROOT)],
    ])

def confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تایید پلن", callback_data=CB_CONFIRM)],
        [InlineKeyboardButton("↩️ بازگشت", callback_data=CB_BACK)],
        [InlineKeyboardButton("↩️ بازگشت به منوی اصلی", callback_data=CB_MENU_ROOT)],
    ])

def plan_payment_kb(plan_type: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🫧 پرداخت", callback_data=f"{CB_PLAN_PAY_PREFIX}{plan_type}")],
        [InlineKeyboardButton("💰 پرداخت با کیف پول", callback_data=f"{CB_PLAN_WALLET_PREFIX}{plan_type}")],
        [InlineKeyboardButton("↩️ بازگشت به انتخاب پلن", callback_data=CB_BACK)],
        [InlineKeyboardButton("↩️ بازگشت به منوی اصلی", callback_data=CB_MENU_ROOT)],
    ])

def delivery_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📲 ارسال در ربات", callback_data=CB_DELIVERY_BOT)],
        [InlineKeyboardButton("📧 ارسال از طریق ایمیل", callback_data=CB_DELIVERY_EMAIL)],
        [InlineKeyboardButton("↩️ بازگشت به حساب کاربری", callback_data=CB_MENU_ACCOUNT)],
        [InlineKeyboardButton("↩️ بازگشت به منوی اصلی", callback_data=CB_MENU_ROOT)],
    ])

def doi_control_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ پایان ارسال DOIها", callback_data=CB_DOI_FINISH)],
        [InlineKeyboardButton("↩️ بازگشت به منوی اصلی", callback_data=CB_MENU_ROOT)],
    ])
def links_download_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ تغییر", callback_data=CB_DL_EDIT)],
        [InlineKeyboardButton("↩️ بازگشت", callback_data=CB_ADMIN_LINKS)],
    ])

def dl_edit_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ اضافه کردن لینک", callback_data=CB_DL_ADD)],
        [InlineKeyboardButton("❌ حذف لینک خاص",  callback_data=CB_DL_DELETE)],
        [InlineKeyboardButton("🔄 لینک‌های زاپاس", callback_data=CB_DL_BACKUP)],
        [InlineKeyboardButton("↩️ بازگشت", callback_data=CB_LINKS_DOWNLOAD)],
    ])

def vpn_menu_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🇮🇷 کانفیگ‌های ایران", callback_data=CB_VPN_IR)],
        [InlineKeyboardButton("🌍 کانفیگ‌های خارج", callback_data=CB_VPN_GLOBAL)],
    ]
    rows.append([InlineKeyboardButton("↩️ بازگشت", callback_data=CB_BACK_ADMIN_ROOT)])
    return InlineKeyboardMarkup(rows)

def _vpn_region_label(region: str) -> str:
    return "ایران" if region == "iran" else "خارج"

def vpn_region_kb(region: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("➕ افزودن کانفیگ", callback_data=f"vpn:add:{region}")],
        [InlineKeyboardButton("⭐️ انتخاب کانفیگ فعال", callback_data=f"vpn:select:{region}")],
        [InlineKeyboardButton("🗑 حذف کانفیگ", callback_data=f"vpn:remove:{region}")],
        [InlineKeyboardButton("🔄 سنجش اتصال", callback_data=f"vpn:ping:{region}")],
    ]
    if region == "iran":
        rows.append([InlineKeyboardButton("🎯 نسبت‌دادن کانفیگ به اکانت", callback_data="vpn:assign:iran")])
    rows.append([InlineKeyboardButton("↩️ بازگشت", callback_data=CB_ADMIN_VPN)])
    return InlineKeyboardMarkup(rows)

def _render_vpn_region(region: str) -> str:
    configs = vpn_load_configs(region)
    vpn_map = iranpaper_vpn_map()
    if not configs:
        return "هیچ کانفیگی ثبت نشده است."
    lines = []
    for cfg in configs:
        status = cfg.get("status")
        if status == "ok":
            icon = "🟢"
        elif status == "fail":
            icon = "🔴"
        else:
            icon = "⚪️"
        ping = f"{int(cfg.get('ping_ms') or 0)}ms" if cfg.get("ping_ms") else "—"
        active = " ⭐️" if cfg.get("active") else ""
        label = htmlmod.escape(cfg.get("label") or cfg.get("id"))
        assigned_slots = sorted([slot for slot, cid in vpn_map.items() if cid == cfg.get("id")], key=lambda s: str(s))
        assign_txt = f" | اکانت: {','.join(assigned_slots) or '—'}"
        lines.append(
            f"{icon} <b>{label}</b>{active} — پینگ: {ping}{assign_txt}\nID: <code>{cfg.get('id')}</code>"
        )
    return "\n\n".join(lines)

def _assign_config_kb(configs: List[Dict[str, Any]]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for idx, cfg in enumerate(configs, start=1):
        label = htmlmod.escape(cfg.get("label") or cfg.get("id"))
        rows.append([InlineKeyboardButton(f"{idx}. {label}", callback_data=f"vpn:assign:cfg:{idx}")])
    rows.append([InlineKeyboardButton("↩️ بازگشت", callback_data=CB_ADMIN_VPN)])
    return InlineKeyboardMarkup(rows)

def _assign_slot_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("1", callback_data="vpn:assign:slot:1"),
            InlineKeyboardButton("2", callback_data="vpn:assign:slot:2"),
            InlineKeyboardButton("3", callback_data="vpn:assign:slot:3"),
        ],
        [InlineKeyboardButton("↩️ بازگشت", callback_data=CB_ADMIN_VPN)],
    ])


# =========================
# منوی اکانت‌های IranPaper
# =========================
def _mask_email(email: str) -> str:
    if "@" not in email:
        return email
    name, domain = email.split("@", 1)
    if len(name) <= 2:
        return "***@" + domain
    return f"{name[0]}***{name[-1]}@{domain}"


def _render_accounts_text() -> str:
    accs = iranpaper_accounts_ordered()
    cfg_labels = {str(c.get("id")): (c.get("label") or c.get("id")) for c in vpn_load_configs("iran")}
    lines = []
    for acc in accs:
        slot = acc.get("slot")
        email_raw = acc.get("email") or ""
        email = _mask_email(email_raw) if email_raw else "—"
        active = "🟢 فعال" if acc.get("active") else "⚪️ غیرفعال"
        primary = "⭐️ اولویت" if acc.get("primary") else ""
        vpn_id = acc.get("vpn_id")
        vpn_label = cfg_labels.get(str(vpn_id), vpn_id) if vpn_id else "—"
        cred = "✅" if acc.get("has_cred") else "❌"
        lines.append(f"{slot}. {email} — {active} {primary} | VPN: {vpn_label} | کرِد: {cred}")
    return "\n".join(lines)


def accounts_menu_kb() -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for acc in iranpaper_accounts_ordered():
        slot = acc.get("slot")
        toggle_txt = f"{'✅' if acc.get('active') else '❌'} اکانت {slot}"
        primary_txt = f"{'⭐️' if acc.get('primary') else '☆'} اولویت {slot}"
        rows.append([
            InlineKeyboardButton(toggle_txt, callback_data=f"acc:toggle:{slot}"),
            InlineKeyboardButton(primary_txt, callback_data=f"acc:primary:{slot}"),
        ])
    rows.append([InlineKeyboardButton("↩️ بازگشت", callback_data=CB_BACK_ADMIN_ROOT)])
    return InlineKeyboardMarkup(rows)


def activation_kb(active: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ فعال‌سازی", callback_data="act:on")],
        [InlineKeyboardButton("⛔️ غیرفعال‌سازی", callback_data="act:off")],
        [InlineKeyboardButton("↩️ بازگشت", callback_data=CB_BACK_ADMIN_ROOT)],
    ])

# =========================
# متن‌ها
# =========================
def _mask_token(tok: Optional[str]) -> str:
    if not tok: return "—"
    return f"{htmlmod.escape(tok[:4])}…{htmlmod.escape(tok[-2:])}" if len(tok) > 6 else htmlmod.escape(tok)


async def _safe_edit(q, text: str, markup: Optional[InlineKeyboardMarkup] = None) -> None:
    try:
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    except BadRequest as exc:
        if "Message is not modified" in str(exc):
            return
        raise


def build_account_text(user: Dict[str, Any], admin_flag: bool) -> str:
    raw_uname = user.get("username")
    uname = f"@{htmlmod.escape(raw_uname)}" if raw_uname else "—"
    dois_count = db_count_dois(user["user_id"])
    role = "ادمین 👑" if admin_flag else "کاربر عادی"

    if user.get("plan_type"):
        price = user.get("plan_price")
        price_str = f"{price:,}".replace(",", "٬") + " تومان" if isinstance(price, int) else "—"
        plan_text = f"{htmlmod.escape(user.get('plan_label') or '—')} | وضعیت: {htmlmod.escape(user.get('plan_status') or '—')} | قیمت: {price_str}"
    else:
        plan_text = "— (هنوز انتخاب نشده)"

    wallet_balance = int(user.get("wallet_balance") or 0)
    wallet_text = f"{wallet_balance:,}".replace(",", "٬") + " تومان"

    email = user.get("email")
    email_verified = bool(user.get("email_verified"))
    if email:
        status = "✅" if email_verified else "⚠️ (تایید نشده)"
        email_line = f"{htmlmod.escape(email)} {status}"
    else:
        email_line = "— (تنظیم نشده، برای Unpaywall ضروری است)"
    delivery_method = user.get("delivery_method")
    delivery_name = "— (انتخاب نشده)" if delivery_method is None else ("ارسال در ربات" if delivery_method == "bot" else "ارسال از طریق ایمیل")
    warn = " ⚠️ (ایمیل تنظیم نشده)" if delivery_method == "email" and not email else ""

    return (
        "👤 <ب>حساب کاربری</ب>\n"
        f"• نام کاربری: {uname}\n"
        f"• نقش: {role}\n"
        f"• ایمیل: {email_line}\n"
        f"• روش ارسال: {delivery_name}{warn}\n"
        f"• پلن: {plan_text}\n"
        f"• موجودی کیف پول: {wallet_text}\n"
        f"• تعداد DOIهای ذخیره‌شده: {dois_count} 📚\n"
    ).replace("<ب>", "<b>").replace("</ب>", "</b>")
def _store_status_text() -> str:
    price_plag = _get_store_price(STORE_PRICE_PLAG_KEY, CFG.STORE_PLAGIARISM_PRICE)
    price_ai = _get_store_price(STORE_PRICE_PLAG_AI_KEY, CFG.STORE_PLAGIARISM_AI_PRICE)
    card = _get_store_card_number()
    card_display = _format_card_number(card) if card else "تنظیم نشده"
    group_id = _get_store_group_id()
    group_display = str(group_id) if group_id else "تنظیم نشده"
    price_plag_text = _format_price_toman(price_plag)
    price_ai_text = _format_price_toman(price_ai)
    if price_plag_text != "نامشخص":
        price_plag_text += " تومان"
    if price_ai_text != "نامشخص":
        price_ai_text += " تومان"
    return (
        "🛒 <b>فروشگاه</b>\n"
        f"• قیمت چک پلاژياریسم: {price_plag_text}\n"
        f"• قیمت چک پلاژياریسم و AI: {price_ai_text}\n"
        f"• شماره کارت:\n<code>{htmlmod.escape(card_display)}</code>\n"
        f"• گروه بررسی پرداخت: <code>{htmlmod.escape(group_display)}</code>\n"
    )

def build_token_text(token: str) -> str:
    return (
        "🔑 <b>توکن افزونهٔ کروم</b>\n"
        "این توکن را در صفحهٔ Options افزونه وارد کنید. اگر گم شد یا شک داری کسی دارد استفاده می‌کند، توکن جدید بساز.\n\n"
        f"<b>توکن شما:</b>\n<code>{htmlmod.escape(token)}</code>"
    )
def build_doi_control_text(buffer_count: int, *, status_lines: Optional[List[str]] = None) -> str:
    status_block = ""
    if status_lines:
        status_block = "\n".join(status_lines) + "\n\n"
    return (
        "📎 لطفاً DOI ارسال کنید.\n"
        "<b>هر چند تا خواستید DOI بفرستید؛ اما در هر پیام فقط یک DOI.</b>\n\n"
        f"{status_block}"
        f"🔢 تعداد DOIهای موقت: <b>{buffer_count}</b>\n"
        "وقتی تمام شد، دکمهٔ «پایان ارسال DOIها» را بزنید.\n"
        "برای لغو: /cancel"
    )

# =========================
# نمایش منو
# =========================
# === جایگزین کامل تابع قبلی شود ===
async def show_user_menu(
    update: Update,
    context: Optional[ContextTypes.DEFAULT_TYPE] = None,
    *,
    edit: bool = False,
    first_time: bool = False,
) -> None:
    """
    منوی کاربر عادی را نشان می‌دهد.
    اگر از Callback صدا زده شود، به‌طور خودکار edit=True در نظر گرفته می‌شود.
    """
    # اگر از دکمه (callback) آمده‌ایم و پارامتر edit صریحاً False است، آن را True کن
    if update.callback_query and not edit:
        edit = True

    text = WELCOME_TEXT
    if first_time:
        text += "\n\n⚠️ لطفاً اطلاعات کاربری خودتون رو کامل کنید."

    if edit and update.callback_query:
        q = update.callback_query
        await q.answer()
        await q.edit_message_text(text, reply_markup=main_menu_kb())
    else:
        if update.message:
            await update.message.reply_text(text, reply_markup=main_menu_kb())


async def show_admin_menu(update: Update, *, edit=False) -> None:
    text = "🛠 <b>پنل ادمین</b>\nیک گزینه را انتخاب کنید:"
    if edit and update.callback_query:
        q = update.callback_query; await q.answer()
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=admin_root_kb())
    else:
        if update.message:
            await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=admin_root_kb())

# =========================
# /start  و /help
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = ensure_user(update.effective_user.id, update.effective_user.username)
    logger.info("DBG | user_id=%s username=%s", update.effective_user.id, update.effective_user.username)

    first = not bool(user.get("seen_welcome"))
    if first: db_set_seen_welcome(user["user_id"])

    if is_admin(update):
        await show_admin_menu(update, edit=False)
    else:
        await show_user_menu(update, context, edit=False, first_time=first)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text("راهنما 👇", reply_markup=ReplyKeyboardRemove())
    if is_admin(update):
        await show_admin_menu(update, edit=False)
    else:
        await show_user_menu(update, context, edit=False)

# =========================
# شاخهٔ «لینک‌ها» (ادمین)
# =========================
def _get_scihub_links() -> str:
    raw = db_get_setting("SCI_HUB_LINKS") or ""
    links = [l.strip() for l in raw.splitlines() if l.strip()]
    return "\n".join(f"• {htmlmod.escape(l)}" for l in links) or "— هیچ لینکی ثبت نشده است."

async def on_menu_links(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer()
    logger.info("ui.on_menu_links | user=%s", update.effective_user.id if update.effective_user else None)
    await q.edit_message_text(
        "🔗 <b>لینک‌ها</b>\nیک گزینه را انتخاب کنید:",
        parse_mode=ParseMode.HTML,
        reply_markup=links_root_kb()
    )
def _get_dl_links() -> List[Dict[str, Any]]:
    raw = db_get_setting("DOWNLOAD_LINKS") or "[]"
    try:
        arr = json.loads(raw)
        if isinstance(arr, list):
            return [d for d in arr if isinstance(d, dict) and "url" in d]
    except Exception:
        pass
    return []

async def _ai_detect_provider(url: str) -> Tuple[str, float]:
    """
    با مدل Groq نام پایگاه را حدس می‌زند.
    خروجی: (label, confidence)
    """
    if not _HAS_GROQ_LOCAL or not CFG.GROQ_API_KEY:
        return "Unknown", 0.0

    client = AsyncGroq(api_key=CFG.GROQ_API_KEY)
    system = (
        "You are a classifier. Choose exactly ONE label "
        f"from {PROVIDER_LABELS}. Return STRICT JSON: "
        '{"label":"<one>","confidence":0..1}'
    )
    user = f"URL: {url}\nنام پایگاه علمی را فقط از روی دامنه تعیین کن."
    try:
        resp = await client.chat.completions.create(
            model=CFG.GROQ_MODEL,
            messages=[{"role":"system","content":system},
                      {"role":"user","content":user}],
            temperature=0,
            response_format={"type":"json_object"},
        )
        txt = resp.choices[0].message.content
        data = json.loads(txt)
        lab = data.get("label") or "Unknown"
        conf = float(data.get("confidence") or 0.0)
        if lab not in PROVIDER_LABELS:
            lab = "Other"
        return lab, conf
    except Exception as e:
        logger.warning("ai_provider_fail | url=%s err=%s", url, e)
        return "Unknown", 0.0

def _save_dl_links(arr: List[Dict[str, Any]]) -> None:
    db_set_setting("DOWNLOAD_LINKS", json.dumps(arr, ensure_ascii=False))

def _render_dl_list() -> str:
    """
    خروجی مثال:

        1. https://sciencedirect.com/... — 20/h [ScienceDirect]
        2. https://ieeexplore.ieee.org/... (زاپاس) — 15/h [IEEE Xplore]
        3. https://example.org/...  — 10/h        (Unknown)

    اگر فهرست خالی باشد، پیام پیش‌فرض برمی‌گرداند.
    """
    links = _get_dl_links()          # ← از settings می‌خوانَد
    if not links:
        return "— هنوز لینکی ثبت نشده است."

    lines: List[str] = []
    for i, link in enumerate(links, start=1):
        url  = htmlmod.escape(link["url"])
        mark = " (زاپاس)" if link.get("backup") else ""
        rate = f" — {link.get('rate')}/h" if link.get("rate") else ""

        prov = link.get("provider") or "Unknown"
        provider_tag = f" [{prov}]" if prov and prov != "Unknown" else ""


        lines.append(f"{i}. {url}{mark}{rate}{provider_tag}")

    return "\n".join(lines)


async def on_links_scihub(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer()
    await q.edit_message_text(
        "🌐 <b>لینک‌های Sci-Hub</b>\n"
        f"{_get_scihub_links()}\n\n"
        "با «✏️ تغییر» می‌توانید لیست را ویرایش کنید.",
        parse_mode=ParseMode.HTML,
        reply_markup=links_scihub_kb()
    )
async def on_links_download(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer()
    await q.edit_message_text(
        "⬇️ <b>لینک‌های دانلود</b>\n"
        f"{_render_dl_list()}\n\n"
        "می‌توانید «✏️ تغییر» را بزنید.",
        parse_mode=ParseMode.HTML,
        reply_markup=links_download_kb()
    )
async def on_dl_delete_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    await q.edit_message_text(
        "❌ <b>حذف لینک خاص</b>\n"
        f"{_render_dl_list()}\n\n"
        "شمارهٔ لینک مورد نظر را بفرستید.\n"
        "برای انصراف: /cancel",
        parse_mode=ParseMode.HTML,
        reply_markup=back_to_menu_kb()
    )
    return WAITING_DL_DELETE

async def receive_dl_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    idx_text = (update.message.text or "").strip()
    if not idx_text.isdigit():
        await update.message.reply_text("لطفاً فقط شماره بفرستید.", reply_markup=back_to_menu_kb())
        return WAITING_DL_DELETE

    idx = int(idx_text) - 1
    links = _get_dl_links()
    if not (0 <= idx < len(links)):
        await update.message.reply_text("شماره خارج از محدوده است.", reply_markup=back_to_menu_kb())
        return WAITING_DL_DELETE

    removed = links.pop(idx)
    _save_dl_links(links)

    # 🔻 پیام تأیید + فهرست به‌روز – در یک reply عادی
    txt_ok = (
        f"✅ حذف شد:\n{removed['url']}\n\n"
        "⬇️ <b>لینک‌های دانلود</b>\n"
        f"{_render_dl_list()}\n\n"
        "برای ویرایش دوباره «✏️ تغییر» را بزنید."
    )
    await update.message.reply_text(
        txt_ok,
        parse_mode=ParseMode.HTML,
        reply_markup=links_download_kb()
    )
    return ConversationHandler.END

async def on_dl_add_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    await q.edit_message_text(
        "➕ <b>اضافه کردن لینک دانلود</b>\n"
        "لینک را بفرستید.\n"
        "برای انصراف: /cancel",
        parse_mode=ParseMode.HTML,
        reply_markup=back_to_menu_kb()
    )
    return WAITING_DL_ADD

async def receive_dl_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    url = (update.message.text or "").strip()
    if not re.match(r"^https?://", url, flags=re.I):
        await update.message.reply_text("URL باید با http:// یا https:// شروع شود.", reply_markup=back_to_menu_kb())
        return WAITING_DL_ADD

    # ذخیره موقت URL
    context.user_data["pending_dl_url"] = url

    # ↪️ مرحلهٔ دوم: ظرفیت
    await update.message.reply_text(
        "حداکثر چند مقاله در ساعت می‌توان از این لینک دانلود کرد؟ (فقط عدد)",
        reply_markup=back_to_menu_kb()
    )
    return WAITING_DL_RATE

async def receive_dl_rate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ntext = (update.message.text or "").strip()
    if not ntext.isdigit():
        await update.message.reply_text("فقط عدد بفرستید (مثلاً 20).", reply_markup=back_to_menu_kb())
        return WAITING_DL_RATE

    rate = int(ntext)
    context.user_data["pending_dl_rate"] = rate

    url = context.user_data.get("pending_dl_url")
    if not url:
        await update.message.reply_text(
            "اطلاعات لینک در دسترس نیست. دوباره گزینهٔ «➕ اضافه کردن لینک» را انتخاب کنید.",
            reply_markup=links_download_kb()
        )
        return ConversationHandler.END
    await update.message.reply_text(
        f"لینک:\n{htmlmod.escape(url)}\nظرفیت: {rate} در ساعت\n\nتایید شود؟",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ تایید", callback_data="dl:add:confirm")],
            [InlineKeyboardButton("❌ تغییر", callback_data="dl:add:retry")],
        ])
    )
    return WAITING_DL_RATE   # تا وقتی دکمه را بزند

# ---------- تأییدِ افزودن لینک دانلود ---------- #
async def dl_add_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()

    url  = context.user_data.pop("pending_dl_url",  None)
    rate = context.user_data.pop("pending_dl_rate", None)
    if not url or rate is None:
        await q.answer("موردی برای ذخیره نیست.", show_alert=True)
        return ConversationHandler.END

    # --- تنها هوش مصنوعی ---
    provider, conf = await _ai_detect_provider(url)
    if conf < 0.40:          # آستانهٔ اطمینان (هر عددی که خواستید)
        provider = "Unknown"

    links = _get_dl_links()
    links.append({
        "url": url,
        "backup": False,
        "rate": rate,
        "provider": provider
    })
    _save_dl_links(links)

    await q.edit_message_text(
        "✅ لینک ذخیره شد.\n\n"
        "⬇️ <b>لینک‌های دانلود</b>\n"
        f"{_render_dl_list()}",
        parse_mode=ParseMode.HTML,
        reply_markup=links_download_kb()
    )
    return ConversationHandler.END



async def dl_add_retry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    await on_dl_add_entry(update, context)
    return WAITING_DL_ADD
async def on_dl_backup_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer()
    links = _get_dl_links()
    rows = [[InlineKeyboardButton(
        f"{i+1}. {'✅' if link.get('backup') else '⬜️'}",
        callback_data=f"dl:toggle:{i}"
    )] for i, link in enumerate(links)]
    rows.append([InlineKeyboardButton("↩️ بازگشت", callback_data=CB_DL_EDIT)])
    await q.edit_message_text(
        "<b>لینک‌های زاپاس</b>\nروی شماره‌ها بزنید تا وضعیت تغییر کند.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows)
    )

async def dl_backup_toggle_item(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer()
    m = re.match(r"dl:toggle:(\d+)", q.data or "")
    if not m:
        return
    idx = int(m.group(1))
    links = _get_dl_links()
    if 0 <= idx < len(links):
        links[idx]["backup"] = not links[idx].get("backup")
        _save_dl_links(links)
    await on_dl_backup_toggle(update, context)

async def on_dl_edit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer()
    await q.edit_message_text(
        "✏️ <b>ویرایش لینک‌های دانلود</b>\n"
        "گزینه‌ای را انتخاب کنید:",
        parse_mode=ParseMode.HTML,
        reply_markup=dl_edit_menu_kb()
    )

async def on_scihub_edit_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    await q.edit_message_text(
        "✏️ <b>به‌روزرسانی لینک‌های Sci-Hub</b>\n"
        "هر لینک را در یک خط ارسال کنید.\n"
        "برای انصراف: /cancel",
        parse_mode=ParseMode.HTML,
        reply_markup=back_to_menu_kb()
    )
    return WAITING_SCIHUB

async def receive_scihub_links(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = (update.message.text or "").strip()
    urls = [u.strip() for u in raw.splitlines() if u.strip()]

    bad = [u for u in urls if not re.match(r"^https?://", u, flags=re.I)]
    if bad:
        await update.message.reply_text(
            "❗️ این موارد URL معتبر نیستند:\n" + "\n".join(bad),
            reply_markup=back_to_menu_kb()
        )
        return WAITING_SCIHUB

    # ذخیره در settings
    db_set_setting("SCI_HUB_LINKS", "\n".join(urls))

    # پیام تأیید + نمایش فهرست به‌روز
    text = (
        "✅ لینک‌ها ذخیره شد.\n\n"
        "🌐 <b>لینک‌های Sci-Hub</b>\n"
        f"{_get_scihub_links()}\n\n"
        "با «✏️ تغییر» می‌توانید لیست را ویرایش کنید."
    )
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=links_scihub_kb()
    )
    return ConversationHandler.END


async def on_back_admin_root(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_admin_menu(update, edit=True)

async def on_menu_vpn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer()
    logger.info("ui.on_menu_vpn | user=%s", update.effective_user.id if update.effective_user else None)
    text = (
        "📡 <b>مدیریت کانفیگ‌های V2Ray</b>\n"
        f"• ایران: {len(vpn_load_configs('iran'))} کانفیگ\n"
        f"• خارج: {len(vpn_load_configs('global'))} کانفیگ\n\n"
        "روی یکی از گزینه‌ها بزنید تا فهرست همان بخش نمایش داده شود."
    )
    await _safe_edit(q, text, vpn_menu_kb())


async def on_menu_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer()
    logger.info("ui.on_menu_accounts | user=%s", update.effective_user.id if update.effective_user else None)
    text = "👥 <b>اکانت‌های IranPaper</b>\n" + _render_accounts_text()
    await _safe_edit(q, text, accounts_menu_kb())


async def on_menu_activation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer()
    logger.info("ui.on_menu_activation | user=%s", update.effective_user.id if update.effective_user else None)
    status = "فعال" if is_activation_on() else "غیرفعال"
    text = f"🔓 <b>وضعیت دانلود خودکار</b>\nوضعیت فعلی: <b>{status}</b>"
    await _safe_edit(q, text, activation_kb(is_activation_on()))

async def on_menu_charge_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    if not is_admin(update):
        await q.answer("دسترسی ندارید.", show_alert=True)
        return ConversationHandler.END
    text = (
        "💳 <b>شارژ حساب</b>\n"
        "ایمیل کاربر را وارد کنید.\n\n"
        "برای انصراف: /cancel"
    )
    context.user_data.pop("charge", None)
    await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())
    return WAITING_CHARGE_EMAIL


async def receive_charge_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    email = (update.message.text or "").strip()
    if not EMAIL_REGEX.match(email):
        await update.message.reply_text("❗️ فرمت ایمیل معتبر نیست. نمونه: user@example.com", reply_markup=back_to_menu_kb())
        return WAITING_CHARGE_EMAIL
    user = db_get_user_by_email(email)
    if not user:
        await update.message.reply_text(
            "❗️ کاربری با این ایمیل پیدا نشد. (کاربر باید یک‌بار در ربات ایمیلش را ثبت کرده باشد.)\n"
            "ایمیل دیگری وارد کنید یا /cancel",
            reply_markup=back_to_menu_kb(),
        )
        return WAITING_CHARGE_EMAIL
    context.user_data["charge"] = {"email": email}
    await update.message.reply_text("تعداد مقالهٔ <b>دارای هزینه</b> برای شارژ را وارد کنید (عدد).", parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())
    return WAITING_CHARGE_PAID


def _parse_nonneg_int(text: str) -> Optional[int]:
    s = _normalize_digits(text).replace(",", "").replace("٬", "").strip()
    if not s:
        return None
    try:
        n = int(s)
    except Exception:
        return None
    return n if n >= 0 else None


async def receive_charge_paid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    n = _parse_nonneg_int(update.message.text or "")
    if n is None:
        await update.message.reply_text("❗️ فقط عدد ۰ یا بزرگ‌تر وارد کنید.", reply_markup=back_to_menu_kb())
        return WAITING_CHARGE_PAID
    payload = context.user_data.get("charge") or {}
    payload["paid_add"] = n
    context.user_data["charge"] = payload
    await update.message.reply_text("تعداد مقالهٔ <b>رایگان</b> برای شارژ را وارد کنید (عدد).", parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())
    return WAITING_CHARGE_FREE


async def receive_charge_free(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    n = _parse_nonneg_int(update.message.text or "")
    if n is None:
        await update.message.reply_text("❗️ فقط عدد ۰ یا بزرگ‌تر وارد کنید.", reply_markup=back_to_menu_kb())
        return WAITING_CHARGE_FREE
    payload = context.user_data.get("charge") or {}
    email = str(payload.get("email") or "").strip()
    paid_add = int(payload.get("paid_add") or 0)
    free_add = n
    if not email:
        await update.message.reply_text("❗️ ابتدا ایمیل را وارد کنید.", reply_markup=back_to_menu_kb())
        return WAITING_CHARGE_EMAIL

    ok = db_add_quota_by_email(email, free_add=free_add, paid_add=paid_add)
    user = db_get_user_by_email(email) if ok else {}
    quota = db_get_quota_status(int(user.get("user_id") or 0)) if user else {}

    if not ok:
        await update.message.reply_text("❗️ شارژ انجام نشد (کاربر پیدا نشد).", reply_markup=back_to_menu_kb())
        return ConversationHandler.END

    await update.message.reply_text(
        "✅ شارژ انجام شد.\n"
        f"ایمیل: {email}\n"
        f"افزوده شد — رایگان: {free_add} | هزینه‌دار: {paid_add}\n"
        f"باقی‌مانده — رایگان: {quota.get('remaining_free','?')} | هزینه‌دار: {quota.get('remaining_paid','?')}",
        reply_markup=back_to_menu_kb(),
    )
    context.user_data.pop("charge", None)
    return ConversationHandler.END


async def on_activation_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer()
    if q.data == "act:on":
        set_activation(True)
        await q.answer("دانلود فعال شد", show_alert=False)
        # Warmup فوری (در پس‌زمینه و بدون بلاک کردن event loop)
        def _warm_scidir() -> None:
            try:
                asyncio.run(
                    warmup_accounts(
                        iranpaper_accounts_ordered(),
                        cfg=CFG,
                        build_chrome_driver=_build_chrome_driver,
                        ensure_v2ray_running=ensure_v2ray_running,
                        solve_recaptcha=_maybe_solve_recaptcha,
                        delay_first=(0, 2),
                    )
                )
            except Exception as exc:
                logger.warning("scidir_warmup_failed | err=%s", exc)

        def _warm_scihub() -> None:
            try:
                _get_scihub_driver()
            except Exception as exc:
                logger.warning("scihub_warmup_failed | err=%s", exc)

        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _warm_scidir)
        loop.run_in_executor(None, _warm_scihub)
    elif q.data == "act:off":
        set_activation(False)
        await q.answer("دانلود غیرفعال شد", show_alert=False)
    await on_menu_activation(update, context)


async def on_acc_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer()
    m = re.match(r"acc:toggle:(\d+)", q.data or "")
    if not m:
        return
    slot = int(m.group(1))
    accs = iranpaper_accounts_ordered()
    current = next((a for a in accs if a.get("slot") == slot), None)
    new_state = not bool(current and current.get("active"))
    iranpaper_set_active(slot, new_state)
    await on_menu_accounts(update, context)


async def on_acc_primary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer()
    m = re.match(r"acc:primary:(\d+)", q.data or "")
    if not m:
        return
    slot = int(m.group(1))
    iranpaper_set_active(slot, True)
    iranpaper_set_primary(slot)
    await on_menu_accounts(update, context)


async def _start_vpn_assign_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, slot_hint: Optional[int] = None) -> int:
    q = update.callback_query
    if q:
        await q.answer()
    configs = vpn_load_configs("iran")
    if not configs:
        if q:
            await q.edit_message_text("هیچ کانفیگ ایران ثبت نشده است. ابتدا یکی اضافه کنید.", reply_markup=back_to_menu_kb())
        return ConversationHandler.END
    context.user_data["vpn_assign_configs"] = configs
    context.user_data["vpn_assign_slot_fixed"] = slot_hint
    context.user_data.pop("vpn_assign_cfg_id", None)
    lines = [f"{idx}. {htmlmod.escape(c.get('label') or c.get('id'))}" for idx, c in enumerate(configs, start=1)]
    text = "🔗 یک کانفیگ ایران را انتخاب کنید:\n" + "\n".join(lines)
    if q:
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=_assign_config_kb(configs))
    return WAITING_VPN_ASSIGN_CFG


async def on_acc_vpn_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    m = re.match(r"(acc:vpn|vpn:acc):(\d+)", q.data or "")
    if not m:
        return ConversationHandler.END
    slot = int(m.group(2))
    return await _start_vpn_assign_flow(update, context, slot_hint=slot)


async def on_vpn_assign_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _start_vpn_assign_flow(update, context)


async def on_vpn_assign_choose_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    m = re.match(r"vpn:assign:cfg:(\d+)", q.data or "")
    configs: List[Dict[str, Any]] = context.user_data.get("vpn_assign_configs") or []
    if not m or not configs:
        return await _start_vpn_assign_flow(update, context, slot_hint=context.user_data.get("vpn_assign_slot_fixed"))
    idx = int(m.group(1)) - 1
    if idx < 0 or idx >= len(configs):
        return await _start_vpn_assign_flow(update, context, slot_hint=context.user_data.get("vpn_assign_slot_fixed"))
    cfg = configs[idx]
    cfg_id = cfg.get("id")
    cfg_label = cfg.get("label") or cfg_id
    context.user_data["vpn_assign_cfg_id"] = cfg_id
    context.user_data["vpn_assign_cfg_label"] = cfg_label
    slot_hint = context.user_data.get("vpn_assign_slot_fixed")
    if slot_hint:
        return await _finalize_vpn_assignment(update, context, int(slot_hint))
    text = (
        f"کانفیگ انتخاب‌شده: <b>{htmlmod.escape(str(cfg_label))}</b>\n"
        "عدد اکانت را انتخاب کنید (1 تا 3)."
    )
    await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=_assign_slot_kb())
    return WAITING_VPN_ASSIGN_SLOT


async def _finalize_vpn_assignment(update: Update, context: ContextTypes.DEFAULT_TYPE, slot: int) -> int:
    q = update.callback_query
    cfg_id = context.user_data.get("vpn_assign_cfg_id")
    cfg_label = context.user_data.get("vpn_assign_cfg_label", cfg_id)
    if not cfg_id:
        return await _start_vpn_assign_flow(update, context, slot_hint=context.user_data.get("vpn_assign_slot_fixed"))
    if slot not in {1, 2, 3}:
        if q:
            await q.answer("اسلات باید 1 تا 3 باشد.", show_alert=True)
        return WAITING_VPN_ASSIGN_SLOT
    iranpaper_set_vpn(int(slot), cfg_id)
    # پاک کردن state
    context.user_data.pop("vpn_assign_slot_fixed", None)
    context.user_data.pop("vpn_assign_cfg_id", None)
    context.user_data.pop("vpn_assign_cfg_label", None)
    success_text = (
        f"✅ کانفیگ <b>{htmlmod.escape(str(cfg_label))}</b> به اکانت <b>{slot}</b> متصل شد.\n\n"
        f"{_render_vpn_region('iran')}"
    )
    if q:
        await q.edit_message_text(success_text, parse_mode=ParseMode.HTML, reply_markup=vpn_menu_kb())
    return ConversationHandler.END


async def on_vpn_assign_choose_slot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    m = re.match(r"vpn:assign:slot:(\d+)", q.data or "")
    if not m:
        return ConversationHandler.END
    slot = int(m.group(1))
    return await _finalize_vpn_assignment(update, context, slot)

async def show_vpn_region(update: Update, region: str) -> None:
    text = (
        f"📡 <b>کانفیگ‌های {_vpn_region_label(region)}</b>\n\n"
        f"{_render_vpn_region(region)}\n\n"
        "برای عملیات بیشتر از دکمه‌های زیر استفاده کنید."
    )
    if update.callback_query:
        q = update.callback_query
        await q.answer()
        try:
            await _safe_edit(q, text, vpn_region_kb(region))
        except BadRequest as exc:
            if "Message is not modified" not in str(exc):
                raise
    elif update.message:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=vpn_region_kb(region))

async def on_vpn_config_entry(update: Update, context: ContextTypes.DEFAULT_TYPE, region: str) -> int:
    q = update.callback_query; await q.answer()
    context.user_data["vpn_region"] = region
    context.user_data["vpn_label"] = None
    await q.edit_message_text(
        f"📡 <b>افزودن کانفیگ {_vpn_region_label(region)}</b>\n"
        "ابتدا یک نام یا برچسب وارد کنید.",
        parse_mode=ParseMode.HTML,
        reply_markup=back_to_menu_kb()
    )
    return WAITING_VPN_LABEL

async def receive_vpn_label(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    label = (update.message.text or "").strip()
    if not label:
        await update.message.reply_text("نام معتبر نیست.", reply_markup=back_to_menu_kb())
        return WAITING_VPN_LABEL
    region = context.user_data.get("vpn_region", "iran")
    normalized = label.lower().strip()
    if normalized.startswith(("vless://", "vmess://", "trojan://", "ss://")) or normalized.startswith("{"):
        entry = vpn_add_config(region, "", label)
        await update.message.reply_text(
            f"✅ کانفیگ ذخیره شد و شناسهٔ آن <code>{entry['id']}</code> است.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_to_menu_kb()
        )
        context.user_data.pop("vpn_label", None)
        await show_vpn_region(update, region)
        return ConversationHandler.END
    context.user_data["vpn_label"] = label
    await update.message.reply_text("حالا متن کانفیگ (لینک یا JSON) را ارسال کنید.", reply_markup=back_to_menu_kb())
    return WAITING_VPN_CONFIG

async def receive_vpn_config(update: Update, context: ContextTypes.DEFAULT_TYPE, region: Optional[str] = None) -> int:
    region = region or context.user_data.get("vpn_region") or "iran"
    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("متن کانفیگ خالی است.", reply_markup=back_to_menu_kb())
        return WAITING_VPN_CONFIG
    label = context.user_data.get("vpn_label") or "Config"
    entry = vpn_add_config(region, label, text)
    await update.message.reply_text(
        f"✅ کانفیگ ذخیره شد و شناسهٔ آن <code>{entry['id']}</code> است.",
        parse_mode=ParseMode.HTML,
        reply_markup=back_to_menu_kb()
    )
    context.user_data.pop("vpn_region", None)
    context.user_data.pop("vpn_label", None)
    await show_vpn_region(update, region)
    return ConversationHandler.END

async def on_vpn_config_ir_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await on_vpn_config_entry(update, context, "iran")

async def on_vpn_config_global_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await on_vpn_config_entry(update, context, "global")

async def on_vpn_region_ir(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_vpn_region(update, "iran")

async def on_vpn_region_global(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_vpn_region(update, "global")

async def on_vpn_select_entry(update: Update, context: ContextTypes.DEFAULT_TYPE, region: str) -> int:
    q = update.callback_query; await q.answer()
    context.user_data["vpn_region"] = region
    await q.edit_message_text(
        "شناسهٔ کانفیگ مورد نظر را ارسال کنید تا فعال شود.",
        parse_mode=ParseMode.HTML,
        reply_markup=back_to_menu_kb()
    )
    return WAITING_VPN_SELECT

async def receive_vpn_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cfg_id = (update.message.text or "").strip()
    region = context.user_data.get("vpn_region", "iran")
    if not vpn_set_active(region, cfg_id):
        await update.message.reply_text("شناسه یافت نشد.", reply_markup=back_to_menu_kb())
        return WAITING_VPN_SELECT
    await update.message.reply_text("✅ کانفیگ فعال شد.", reply_markup=back_to_menu_kb())
    context.user_data.pop("vpn_region", None)
    await show_vpn_region(update, region)
    return ConversationHandler.END

async def on_vpn_remove_entry(update: Update, context: ContextTypes.DEFAULT_TYPE, region: str) -> int:
    q = update.callback_query; await q.answer()
    context.user_data["vpn_region"] = region
    await q.edit_message_text(
        "شناسهٔ کانفیگی که باید حذف شود را ارسال کنید.",
        parse_mode=ParseMode.HTML,
        reply_markup=back_to_menu_kb()
    )
    return WAITING_VPN_DELETE

async def receive_vpn_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cfg_id = (update.message.text or "").strip()
    region = context.user_data.get("vpn_region", "iran")
    if not vpn_remove_config(region, cfg_id):
        await update.message.reply_text("شناسه یافت نشد.", reply_markup=back_to_menu_kb())
        return WAITING_VPN_DELETE
    await update.message.reply_text("🗑 کانفیگ حذف شد.", reply_markup=back_to_menu_kb())
    context.user_data.pop("vpn_region", None)
    await show_vpn_region(update, region)
    return ConversationHandler.END

async def on_vpn_ping(update: Update, context: ContextTypes.DEFAULT_TYPE, region: str) -> None:
    q = update.callback_query
    ok, total = vpn_ping_all(region)
    await q.answer(text=f"نتیجهٔ تست: {ok}/{total}", show_alert=True)
    await show_vpn_region(update, region)

async def on_vpn_ping_ir(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await on_vpn_ping(update, context, "iran")

async def on_vpn_ping_global(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await on_vpn_ping(update, context, "global")

async def on_vpn_select_ir(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await on_vpn_select_entry(update, context, "iran")

async def on_vpn_select_global(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await on_vpn_select_entry(update, context, "global")

async def on_vpn_remove_ir(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await on_vpn_remove_entry(update, context, "iran")

async def on_vpn_remove_global(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await on_vpn_remove_entry(update, context, "global")



# =========================
# هندلرها
# =========================
async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit: bool = False, first_time_note: bool = False) -> None:
    """
    فقط نقش دروازه را دارد: بسته به ادمین بودن، یکی از دو منوی زیر را نشان می‌دهد.
    """
    if is_admin(update):
        await show_admin_menu(update, edit=edit)
    else:
        await show_user_menu(update, context, edit=edit, first_time=first_time_note)

async def send_main_menu_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if is_admin(update):
        await context.bot.send_message(
            chat_id,
            "منوی مدیریت:",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_root_kb(),
        )
    else:
        await context.bot.send_message(chat_id, WELCOME_TEXT, reply_markup=main_menu_kb())

async def on_download_link_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    await q.answer()
    with contextlib.suppress(Exception):
        await q.edit_message_reply_markup(reply_markup=back_to_menu_kb())


async def send_account_view_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ensure_user(update.effective_user.id, update.effective_user.username)
    await show_profile_card(update, context, include_delivery=True)




# ---- حساب کاربری و توکن
async def on_menu_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer()
    ensure_user(update.effective_user.id, update.effective_user.username)
    await show_profile_card(update, context, include_delivery=True)

async def on_account_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer()
    uid = update.effective_user.id
    tok = db_get_or_create_token(uid)
    text = build_token_text(tok)
    await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=token_menu_kb())

async def on_token_regen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer("توکن جدید ساخته شد")
    uid = update.effective_user.id
    new_tok = db_set_new_token(uid)
    text = (
        "⚠️ <b>هشدار:</b> توکن قبلی دیگر معتبر نیست و اگر در افزونه ذخیره شده بود باید این توکن جدید را جایگزین کنید.\n\n"
        + build_token_text(new_tok)
    )
    await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=token_menu_kb())

# ---- ایمیل
async def on_account_email_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    user = ensure_user(update.effective_user.id, update.effective_user.username)
    cur = user.get("email")
    cur_line = f"ایمیل فعلی: {htmlmod.escape(cur)}" if cur else "ایمیل فعلی: —"
    text = ("✉️ <b>ارسال ایمیل</b>\n"
            f"{cur_line}\n\n"
            "ایمیل جدید خود را ارسال کنید.\n"
            "برای انصراف: /cancel")
    await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())
    context.user_data.pop("pending_email", None)
    return WAITING_FOR_EMAIL

async def receive_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    email = (update.message.text or "").strip()
    if not EMAIL_REGEX.match(email):
        await update.message.reply_text("❗️ فرمت ایمیل معتبر نیست. نمونه: user@example.com", reply_markup=back_to_menu_kb())
        return WAITING_FOR_EMAIL
    user = ensure_user(update.effective_user.id, update.effective_user.username)
    result = request_email_verification(email, user_id=int(user.get("user_id")))
    if not result.get("ok"):
        if result.get("error") == "rate_limited":
            retry_after = int(result.get("retry_after") or 0)
            context.user_data["pending_email"] = email
            await update.message.reply_text(
                f"⏳ درخواست قبلی ثبت شده است. لطفاً {retry_after} ثانیه دیگر برای ارسال مجدد صبر کنید.",
                reply_markup=back_to_menu_kb(),
            )
            return WAITING_FOR_EMAIL_CODE
        await update.message.reply_text(
            "⚠️ ارسال کد تایید با خطا مواجه شد. لطفاً دوباره تلاش کنید.",
            reply_markup=back_to_menu_kb(),
        )
        return WAITING_FOR_EMAIL
    context.user_data["pending_email"] = email
    await update.message.reply_text(_email_code_rules_text(), parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())
    return WAITING_FOR_EMAIL_CODE

async def receive_email_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    code = _normalize_digits(update.message.text or "").strip()
    if not _valid_email_code(code):
        await update.message.reply_text(
            "❗️ رمز نامعتبر است. لطفاً دوباره وارد کنید.\n\n" + _email_code_rules_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=back_to_menu_kb(),
        )
        return WAITING_FOR_EMAIL_CODE

    pending_email = (context.user_data.get("pending_email") or "").strip()
    if not pending_email or not EMAIL_REGEX.match(pending_email):
        await update.message.reply_text(
            "❗️ ابتدا ایمیل را ارسال کنید.\n\nایمیل جدید خود را ارسال کنید.",
            reply_markup=back_to_menu_kb(),
        )
        return WAITING_FOR_EMAIL

    result = verify_email_code(pending_email, code)
    if not result.get("ok"):
        err = result.get("error")
        if err == "invalid_code":
            attempts_left = int(result.get("attempts_left") or 0)
            await update.message.reply_text(
                f"❗️ کد اشتباه است. {attempts_left} تلاش باقی مانده است.",
                reply_markup=back_to_menu_kb(),
            )
            return WAITING_FOR_EMAIL_CODE
        if err == "too_many_attempts":
            context.user_data.pop("pending_email", None)
            await update.message.reply_text(
                "⚠️ تعداد تلاش‌ها بیش از حد مجاز است. لطفاً دوباره درخواست کد بدهید.",
                reply_markup=back_to_menu_kb(),
            )
            return WAITING_FOR_EMAIL
        if err == "expired":
            context.user_data.pop("pending_email", None)
            await update.message.reply_text(
                "⌛️ کد منقضی شده است. لطفاً دوباره درخواست کد بدهید.",
                reply_markup=back_to_menu_kb(),
            )
            return WAITING_FOR_EMAIL
        if err == "already_verified":
            context.user_data.pop("pending_email", None)
            await update.message.reply_text(
                "✅ ایمیل قبلاً تایید شده است.",
                reply_markup=back_to_menu_kb(),
            )
            await send_account_view_message(update, context)
            return ConversationHandler.END
        await update.message.reply_text(
            "⚠️ تایید ایمیل با خطا مواجه شد. لطفاً دوباره تلاش کنید.",
            reply_markup=back_to_menu_kb(),
        )
        return WAITING_FOR_EMAIL_CODE

    context.user_data.pop("pending_email", None)
    await update.message.reply_text("✅ ایمیل با موفقیت تایید شد.", reply_markup=back_to_menu_kb())
    await send_account_view_message(update, context)
    return ConversationHandler.END

# ---- روش ارسال
async def on_account_delivery(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer()
    extra = f"{CFG.EXTRA_EMAIL_DELIVERY_FEE:,}".replace(",", "٬")
    text = ("📦 <b>روش ارسال</b>\n"
            "شما می\u200cتونید دو روش ارسال مقاله داشته باشید:\n"
            "• ارسال در ربات\n"
            "• ارسال از طریق ایمیل\n\n"
            "ℹ️ در <b>پلن معمولی</b> برای دریافت از طریق ایمیل، "
            f"<b>{extra} تومان</b> هزینه\u200cی اضافه دریافت می\u200cشود.\n"
            "روش مورد نظر را انتخاب کنید:")
    await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=delivery_menu_kb())

async def set_delivery_bot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer("روش ارسال: ربات")
    user = ensure_user(update.effective_user.id, update.effective_user.username)
    db_set_delivery(user["user_id"], "bot")
    await show_profile_card(update, context, include_delivery=True)

async def set_delivery_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer("روش ارسال: ایمیل")
    user = ensure_user(update.effective_user.id, update.effective_user.username)
    db_set_delivery(user["user_id"], "email")
    await show_profile_card(update, context, include_delivery=True)

# ---- اشتراک/پلن‌ها
def compute_price_with_delivery(user: Dict[str, Any], base_price: int, plan_type: str) -> Tuple[int, bool]:
    add = False
    if plan_type.startswith("normal") and user.get("delivery_method") == "email":
        base_price += CFG.EXTRA_EMAIL_DELIVERY_FEE; add = True
    return base_price, add


def plan_final_price(user: Dict[str, Any], plan_type: str) -> Tuple[int, bool]:
    base_price = _plan_base_price(plan_type)
    if base_price <= 0:
        return 0, False
    return compute_price_with_delivery(user, base_price, plan_type)


def _activate_plan(user_id: int, plan_type: str) -> Dict[str, Any]:
    user = db_get_user(int(user_id))
    label = (user.get("plan_label") if user else "") or _plan_label(plan_type)
    price_val = user.get("plan_price") if user else None
    price = int(price_val) if isinstance(price_val, int) else _plan_base_price(plan_type)
    note = (user.get("plan_note") if user else "") or _plan_note(plan_type)
    db_set_plan(int(user_id), plan_type, label, price, PLAN_STATUS_ACTIVE, note)

    duration_days = _plan_duration_days(plan_type)
    started_at = _now_ts()
    expires_at = started_at + (duration_days * 86400) if duration_days > 0 else 0
    if duration_days > 0:
        db_set_plan_period(int(user_id), started_at=started_at, expires_at=expires_at)

    if _plan_is_unlimited(plan_type):
        db_set_doi_quota(int(user_id), limit=0, used=0)
        doi_limit = 0
        doi_unlimited = True
    else:
        doi_limit = _plan_doi_limit(plan_type)
        if doi_limit > 0:
            db_set_doi_quota(int(user_id), limit=doi_limit, used=0)
        doi_unlimited = False

    daily_limit = _plan_daily_limit(plan_type)
    if daily_limit > 0:
        db_set_doi_daily_quota(int(user_id), limit=daily_limit, used=0, day_key=_today_key())
    else:
        db_set_doi_daily_quota(int(user_id), limit=0, used=0, day_key=0)

    quota_add = _plan_quota_paid(plan_type)
    if quota_add > 0:
        db_add_quota(int(user_id), paid_add=quota_add)
    return {
        "label": label,
        "price": price,
        "quota_add": quota_add,
        "plan_type": plan_type,
        "doi_limit": doi_limit,
        "doi_unlimited": doi_unlimited,
        "daily_limit": daily_limit,
        "expires_at": expires_at,
    }


def _reject_plan(user_id: int, plan_type: str) -> None:
    user = db_get_user(int(user_id))
    label = (user.get("plan_label") if user else "") or _plan_label(plan_type)
    price_val = user.get("plan_price") if user else None
    price = int(price_val) if isinstance(price_val, int) else _plan_base_price(plan_type)
    note = (user.get("plan_note") if user else "") or _plan_note(plan_type)
    db_set_plan(int(user_id), plan_type, label, price, "رد شد", note)

async def on_menu_topup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer()
    extra = f"{CFG.EXTRA_EMAIL_DELIVERY_FEE:,}".replace(",", "٬")
    text = (
        "💳 <b>خرید اشتراک</b>\n\n"
        "🧰 <b>پلن معمولی</b>\n"
        "• ۴۰ مقاله — ۲۴۰٬۰۰۰ تومان\n"
        "• ۱۰۰ مقاله — ۵۰۰٬۰۰۰ تومان\n"
        f"⏳ اعتبار: ۱ ساله | (ارسال از طریق ایمیل: +{extra} تومان)\n\n"
        "⭐️ <b>پلن اشتراک پریمیوم</b>\n"
        "• دانلود نامحدود (۱۵ مقاله در روز)\n"
        "قیمت‌ها:\n"
        "• ۱ ماه — ۲۴۰٬۰۰۰ تومان\n"
        "• ۳ ماه — ۶۰۰٬۰۰۰ تومان\n"
        "محدودیت: ۱۵ مقاله در روز"
    )
    await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=topup_menu_keyboard())


async def on_menu_wallet_topup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q:
        return ConversationHandler.END
    await q.answer()
    user = ensure_user(update.effective_user.id, update.effective_user.username)
    balance = int(user.get("wallet_balance") or 0)
    balance_text = f"{balance:,}".replace(",", "٬")
    text = (
        "💰 <b>شارژ کیف پول</b>\n"
        f"موجودی فعلی: <b>{balance_text} تومان</b>\n\n"
        "مبلغ شارژ را وارد کنید (تومان):"
    )
    await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())
    return WAITING_WALLET_TOPUP_AMOUNT


async def receive_wallet_topup_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    amount = _parse_amount(update.message.text or "")
    if amount is None:
        await update.message.reply_text("❗️ فقط مبلغ معتبر وارد کنید (عدد).", reply_markup=back_to_menu_kb())
        return WAITING_WALLET_TOPUP_AMOUNT
    return await _start_manual_payment(
        update,
        context,
        product_key=WALLET_TOPUP_PRODUCT,
        amount=amount,
        product_label="شارژ کیف پول",
        total_amount=amount,
        wallet_used=0,
    )

def set_pending_plan(ctx_ud: Dict[str, Any], label: str, ptype: str, base_price: int, note: str) -> Tuple[int, bool]:
    ctx_ud["pending_plan"] = {"type": ptype, "label": label, "base_price": base_price, "note": note}
    user = db_get_user(ctx_ud["user_id"])
    final_price, added = compute_price_with_delivery(user, base_price, ptype)
    return final_price, added

async def on_plan_normal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer()
    extra = f"{CFG.EXTRA_EMAIL_DELIVERY_FEE:,}".replace(",", "٬")
    text = ("🧰 <b>پلن معمولی</b>\n"
            "یکی از گزینه‌ها را انتخاب کنید:\n"
            "• ۴۰ مقاله — ۲۴۰٬۰۰۰ تومان\n"
            "• ۱۰۰ مقاله — ۵۰۰٬۰۰۰ تومان\n"
            f"⏳ اعتبار: ۱ ساله | (ارسال ایمیل: +{extra} تومان)")
    await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=normal_subplan_keyboard())

async def on_plan_premium(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer()
    text = ("⭐️ <b>پلن اشتراک پریمیوم</b>\n"
            "مدت اشتراک را انتخاب کنید:\n"
            "محدودیت: ۱۵ مقاله در روز")
    await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=premium_subplan_keyboard())

async def on_select_normal_40(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer()
    context.user_data["user_id"] = update.effective_user.id
    price, added = set_pending_plan(context.user_data, "🧰 پلن معمولی — ۴۰ مقاله (اعتبار ۱ سال)", "normal_40", 240000, "اعتبار ۱ ساله")
    price_str = f"{price:,}".replace(",", "٬"); extra_line = "\n• شامل ۱۰٬۰۰۰ تومان هزینهٔ اضافه برای ارسال از طریق ایمیل" if added else ""
    await q.edit_message_text("🧰 <b>پلن معمولی (۴۰ مقاله)</b>\n"
                              f"• قیمت نهایی فعلی: {price_str} تومان{extra_line}\n"
                              "• اعتبار: ۱ سال\n\n"
                              "اگر موافقی، «تایید پلن» را بزن.",
                              parse_mode=ParseMode.HTML, reply_markup=confirm_keyboard())

async def on_select_normal_100(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer()
    context.user_data["user_id"] = update.effective_user.id
    price, added = set_pending_plan(context.user_data, "🧰 پلن معمولی — ۱۰۰ مقاله (اعتبار ۱ سال)", "normal_100", 500_000, "اعتبار ۱ ساله")
    price_str = f"{price:,}".replace(",", "٬"); extra_line = "\n• شامل ۱۰٬۰۰۰ تومان هزینهٔ اضافه برای ارسال از طریق ایمیل" if added else ""
    await q.edit_message_text("🧰 <b>پلن معمولی (۱۰۰ مقاله)</b>\n"
                              f"• قیمت نهایی فعلی: {price_str} تومان{extra_line}\n"
                              "• اعتبار: ۱ سال\n\n"
                              "اگر موافقی، «تایید پلن» را بزن.",
                              parse_mode=ParseMode.HTML, reply_markup=confirm_keyboard())

async def on_select_premium_1m(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer()
    context.user_data["user_id"] = update.effective_user.id
    set_pending_plan(context.user_data, "⭐️ پریمیوم — ۱ ماه (نامحدود با سقف ۱۵ در روز)", "premium_1m", 240000, "دانلود نامحدود (۱۵ در روز)")
    await q.edit_message_text(
        "⭐️ <b>پلن پریمیوم (۱ ماه)</b>\n"
        "• مبلغ: ۲۴۰٬۰۰۰ تومان\n"
        "• دانلود نامحدود (۱۵ مقاله در روز)\n\n"
        "لطفاً برای ادامه، تایید کنید.",
        parse_mode=ParseMode.HTML, reply_markup=confirm_keyboard()
    )

async def on_select_premium_3m(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer()
    context.user_data["user_id"] = update.effective_user.id
    set_pending_plan(context.user_data, "⭐️ پریمیوم — ۳ ماه (نامحدود با سقف ۱۵ در روز)", "premium_3m", 600000, "دانلود نامحدود (۱۵ در روز)")
    await q.edit_message_text(
        "⭐️ <b>پلن پریمیوم (۳ ماه)</b>\n"
        "• مبلغ: ۶۰۰٬۰۰۰ تومان\n"
        "• دانلود نامحدود (۱۵ مقاله در روز)\n\n"
        "لطفاً برای ادامه، تایید کنید.",
        parse_mode=ParseMode.HTML, reply_markup=confirm_keyboard()
    )

async def on_confirm_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer()
    context.user_data.pop("resume_plan_after_email", None)
    uid = update.effective_user.id
    pending = context.user_data.get("pending_plan")
    if not pending:
        await q.edit_message_text("❗️ پلنی برای تایید انتخاب نشده است.", reply_markup=topup_menu_keyboard())
        return
    user = db_get_user(uid)
    if not user.get("delivery_chosen"):
        context.user_data["resume_plan_after_email"] = True
        warn = ("⚠️ ابتدا «روش ارسال» خود را مشخص کنید.\n"
                "از منوی حساب کاربری می‌توانید «ارسال در ربات» یا «ارسال از طریق ایمیل» را انتخاب کنید.\n"
                "در پلن معمولی، انتخاب ایمیل شامل هزینهٔ اضافه می‌شود.")
        await q.edit_message_text(warn, parse_mode=ParseMode.HTML, reply_markup=account_menu_kb())
        return
    base_price = pending["base_price"]
    final_price, added = compute_price_with_delivery(user, base_price, pending["type"])
    db_set_plan(uid, pending["type"], pending["label"], final_price, "در انتظار پرداخت", pending.get("note", ""))

    price_str = f"{final_price:,}".replace(",", "٬") + " تومان"
    extra_line = "\n• شامل ۱۰٬۰۰۰ تومان هزینهٔ اضافه بابت روش ایمیل" if added else ""
    text = ("✅ <b>پلن شما ثبت شد (در انتظار پرداخت)</b>\n"
            f"{(pending['label'])}\n"
            f"• قیمت: {price_str}{extra_line}\n"
            "برای پرداخت یکی از گزینه‌های زیر را انتخاب کنید:")
    await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=plan_payment_kb(pending["type"]))


async def on_plan_continue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    return await on_confirm_plan(update, context)


async def on_plan_pay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q:
        return ConversationHandler.END
    await q.answer()

    data = q.data or ""
    if not data.startswith(CB_PLAN_PAY_PREFIX):
        return ConversationHandler.END
    plan_type = data[len(CB_PLAN_PAY_PREFIX):]
    if plan_type not in PLAN_PRODUCTS:
        return ConversationHandler.END

    user = db_get_user(update.effective_user.id)
    if not user or user.get("plan_type") != plan_type or user.get("plan_status") != "در انتظار پرداخت":
        await context.bot.send_message(
            update.effective_chat.id,
            "ابتدا پلن خود را از بخش «خرید اشتراک» انتخاب و تایید کنید.",
        )
        return ConversationHandler.END

    final_price, _added = plan_final_price(user, plan_type)
    if final_price <= 0:
        await context.bot.send_message(update.effective_chat.id, "❗️ مبلغ پلن نامعتبر است.")
        return ConversationHandler.END

    return await _start_manual_payment(
        update,
        context,
        product_key=_plan_product_key(plan_type),
        amount=final_price,
        product_label=_plan_label(plan_type),
        total_amount=final_price,
        wallet_used=0,
    )


async def on_plan_wallet_pay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q:
        return ConversationHandler.END
    await q.answer()

    data = q.data or ""
    if not data.startswith(CB_PLAN_WALLET_PREFIX):
        return ConversationHandler.END
    plan_type = data[len(CB_PLAN_WALLET_PREFIX):]
    if plan_type not in PLAN_PRODUCTS:
        return ConversationHandler.END

    user = db_get_user(update.effective_user.id)
    if not user or user.get("plan_type") != plan_type or user.get("plan_status") != "در انتظار پرداخت":
        await context.bot.send_message(
            update.effective_chat.id,
            "ابتدا پلن خود را از بخش «خرید اشتراک» انتخاب و تایید کنید.",
        )
        return ConversationHandler.END

    total_price, _added = plan_final_price(user, plan_type)
    if total_price <= 0:
        await context.bot.send_message(update.effective_chat.id, "❗️ مبلغ پلن نامعتبر است.")
        return ConversationHandler.END

    card_number = _get_store_card_number()
    open_state = await _handle_open_payment_request(
        update,
        context,
        user_id=int(user.get("user_id")),
        fallback_product_key=_plan_product_key(plan_type),
        fallback_amount=total_price,
        fallback_card=card_number,
    )
    if open_state is not None:
        return open_state

    balance = int(user.get("wallet_balance") or 0)
    wallet_used = min(balance, int(total_price))
    remaining = int(total_price) - wallet_used

    if remaining > 0:
        group_id = _get_store_group_id()
        if not card_number or not group_id:
            await context.bot.send_message(
                update.effective_chat.id,
                "⚠️ پرداخت برای این سرویس در حال حاضر فعال نیست. لطفاً بعداً دوباره تلاش کنید.",
            )
            return ConversationHandler.END

    if wallet_used > 0:
        db_add_wallet_balance(int(user.get("user_id")), -wallet_used)

    if remaining <= 0:
        summary = _activate_plan(int(user.get("user_id")), plan_type)
        balance_after = int(db_get_user(int(user.get("user_id"))).get("wallet_balance") or 0)
        balance_text = f"{balance_after:,}".replace(",", "٬")
        quota_add = summary.get("quota_add") or 0
        if summary.get("doi_unlimited"):
            daily_limit = int(summary.get("daily_limit") or 0)
            if daily_limit > 0:
                quota_line = f"\n• سهمیه DOI: نامحدود (سقف {daily_limit} در روز)"
            else:
                quota_line = "\n• سهمیه DOI: نامحدود"
        else:
            quota_line = f"\n• سهمیه DOI: {quota_add}" if quota_add else ""
        await context.bot.send_message(
            update.effective_chat.id,
            "✅ پرداخت با کیف پول انجام شد و اشتراک شما فعال گردید.\n"
            f"• پلن: {summary.get('label')}\n"
            f"• موجودی فعلی کیف پول: {balance_text} تومان{quota_line}",
            reply_markup=back_to_menu_kb(),
        )
        return ConversationHandler.END

    return await _start_manual_payment(
        update,
        context,
        product_key=_plan_product_key(plan_type),
        amount=remaining,
        product_label=_plan_label(plan_type),
        total_amount=int(total_price),
        wallet_used=wallet_used,
        refund_wallet_user_id=int(user.get("user_id")),
        refund_wallet_amount=wallet_used,
    )

# ---- DOI Conversation
async def enter_doi_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = ensure_user(update.effective_user.id, update.effective_user.username)
    q = update.callback_query; await q.answer()
    access = _doi_access_status(user)
    if not access.get("ok"):
        await q.edit_message_text(_doi_block_message(access), reply_markup=back_to_menu_kb())
        return ConversationHandler.END
    context.user_data["doi_buffer"] = []
    sent = await q.edit_message_text(
        build_doi_control_text(0, status_lines=_doi_status_lines(access, 0)),
        reply_markup=doi_control_kb(),
        parse_mode=ParseMode.HTML,
    )
    context.user_data["doi_ctrl"] = (sent.chat_id, sent.message_id)
    return WAITING_FOR_DOI

async def _update_doi_ctrl(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    msg_id: int,
    count: int,
    status_lines: Optional[List[str]] = None,
) -> None:
    try:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id,
            text=build_doi_control_text(count, status_lines=status_lines),
            parse_mode=ParseMode.HTML, reply_markup=doi_control_kb())
    except Exception as e:
        logger.warning("ctrl_update_fail: %s", e)

async def receive_doi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = ensure_user(update.effective_user.id, update.effective_user.username)
    text = (update.message.text or "").strip()
    found = DOI_REGEX.findall(text)
    if len(found) != 1:
        await update.message.reply_text("❗️ هر پیام فقط یک DOI معتبر بفرستید.", reply_markup=doi_control_kb())
        return WAITING_FOR_DOI
    doi = normalize_doi(found[0])
    if not doi:
        await update.message.reply_text("❗️ DOI معتبر نیست.", reply_markup=doi_control_kb())
        return WAITING_FOR_DOI
    access = _doi_access_status(user)
    if not access.get("ok"):
        await update.message.reply_text(_doi_block_message(access), reply_markup=back_to_menu_kb())
        context.user_data["doi_buffer"] = []
        context.user_data.pop("doi_ctrl", None)
        return ConversationHandler.END
    buf: List[str] = context.user_data.get("doi_buffer", [])
    if doi in buf:                                  # ← جلوگیری از تکرار
        await update.message.reply_text(
            "این DOI قبلاً ثبت شده است. مورد دیگری بفرستید.",
            reply_markup=doi_control_kb()
        )
        return WAITING_FOR_DOI
    if not access.get("unlimited"):
        remaining = int(access.get("remaining") or 0)
        if remaining <= len(buf):
            await update.message.reply_text(
                "سهمیهٔ شما تکمیل شده است. لطفاً روی «پایان ارسال DOIها» بزنید.",
                reply_markup=doi_control_kb(),
            )
            return WAITING_FOR_DOI
    else:
        daily_limit = int(access.get("daily_limit") or 0)
        if daily_limit > 0:
            daily_remaining = int(access.get("daily_remaining") or 0)
            if daily_remaining <= len(buf):
                await update.message.reply_text(
                    "سهمیه امروز شما تکمیل شده است. لطفاً روی «پایان ارسال DOIها» بزنید.",
                    reply_markup=doi_control_kb(),
                )
                return WAITING_FOR_DOI

    buf.append(doi); context.user_data["doi_buffer"] = buf
    await update.message.reply_text(f"✅ DOI ثبت شد:\n{doi}")
    ctrl = context.user_data.get("doi_ctrl")
    if ctrl:
        await _update_doi_ctrl(context, ctrl[0], ctrl[1], len(buf), _doi_status_lines(access, len(buf)))
    return WAITING_FOR_DOI

async def finish_doi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    user = ensure_user(update.effective_user.id, update.effective_user.username)
    buf: List[str] = context.user_data.get("doi_buffer", [])
    if not buf:
        await q.edit_message_text("هیچ DOI ای ثبت نشده است. لطفاً DOI بفرستید.", reply_markup=doi_control_kb())
        return WAITING_FOR_DOI
    access = _doi_access_status(user)
    if not access.get("ok"):
        await q.edit_message_text(_doi_block_message(access), reply_markup=back_to_menu_kb())
        context.user_data["doi_buffer"] = []
        context.user_data.pop("doi_ctrl", None)
        return ConversationHandler.END

    skipped = 0
    if not access.get("unlimited"):
        remaining = int(access.get("remaining") or 0)
        if remaining <= 0:
            await q.edit_message_text(_doi_block_message({"reason": "quota_exhausted"}), reply_markup=back_to_menu_kb())
            context.user_data["doi_buffer"] = []
            context.user_data.pop("doi_ctrl", None)
            return ConversationHandler.END
        if len(buf) > remaining:
            skipped = len(buf) - remaining
            buf = buf[:remaining]
    else:
        daily_limit = int(access.get("daily_limit") or 0)
        if daily_limit > 0:
            daily_remaining = int(access.get("daily_remaining") or 0)
            if daily_remaining <= 0:
                await q.edit_message_text(_doi_block_message({"reason": "daily_exhausted"}), reply_markup=back_to_menu_kb())
                context.user_data["doi_buffer"] = []
                context.user_data.pop("doi_ctrl", None)
                return ConversationHandler.END
            if len(buf) > daily_remaining:
                skipped = len(buf) - daily_remaining
                buf = buf[:daily_remaining]

    inserted = db_add_dois(user["user_id"], buf)
    if not access.get("unlimited"):
        db_inc_doi_quota_used(int(user["user_id"]), inserted)
    else:
        daily_limit = int(access.get("daily_limit") or 0)
        if daily_limit > 0:
            db_inc_doi_daily_used(int(user["user_id"]), inserted, day_key=_today_key())
    context.user_data["doi_buffer"] = []; context.user_data.pop("doi_ctrl", None)

    skipped_line = f"\n⚠️ تعداد DOIهای تکراری/نامعتبر حذف شد: <b>{skipped}</b>" if skipped else ""
    await q.edit_message_text(
        f"✅ ارسال DOIها انجام شد.\nتعداد ثبت شده: <b>{inserted}</b>\n\n"
        f"به زودی فایل/پی دی اف ها برای شما ارسال می شود{skipped_line}",
        parse_mode=ParseMode.HTML,
        reply_markup=back_to_menu_kb()
    )

    chat_id = update.effective_chat.id
    # Run DOI processing in background so UI stays responsive.
    active_tasks: List[asyncio.Task] = context.user_data.setdefault("active_doi_tasks", [])
    task = asyncio.create_task(process_dois_batch(user["user_id"], buf, chat_id, context.bot))
    active_tasks.append(task)

    def _on_done(t: asyncio.Task) -> None:
        with contextlib.suppress(ValueError):
            active_tasks.remove(t)
        try:
            exc = t.exception()
        except asyncio.CancelledError:
            return
        if exc:
            logger.error("doi_batch_failed | chat_id=%s err=%s", chat_id, exc)

            async def _notify_failure() -> None:
                with contextlib.suppress(Exception):
                    await context.bot.send_message(
                        chat_id,
                        "در پردازش DOIها خطایی رخ داد. لطفاً کمی بعد دوباره تلاش کنید."
                    )

            asyncio.create_task(_notify_failure())

    task.add_done_callback(_on_done)

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message:
        await update.message.reply_text("عملیات لغو شد. ✅", reply_markup=back_to_menu_kb())
    context.user_data["doi_buffer"] = []; context.user_data.pop("doi_ctrl", None)
    context.user_data.pop("pending_email", None)
    context.user_data.pop("charge", None)
    return ConversationHandler.END

# ---- بازگشت‌ها و متفرقه
async def on_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer()
    await q.edit_message_text("بازگشت به انتخاب پلن:", reply_markup=topup_menu_keyboard())

async def on_back_root(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["doi_buffer"] = []; context.user_data.pop("doi_ctrl", None)
    await show_main_menu(update, context, edit=True)
    return ConversationHandler.END

async def on_menu_root(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["doi_buffer"] = []; context.user_data.pop("doi_ctrl", None)
    await show_main_menu(update, context, edit=True)
    return ConversationHandler.END

# --- متنی که هیچ Conversation یا Callback برایش پیدا نشد ---
# --- متنی که هیچ Conversation یا Callback برایش پیدا نشد ---
async def fallback_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    اگر کاربر یک DOI معتبر خارج از کانورسیشن بفرستد، ربات به‌طور خودکار
    حالت دریافت DOI را فعال می‌کند و همان DOI را به بافر اضافه می‌کند.
    برای سایر متن‌های نامرتبط فقط یک یادآوری ساده می‌فرستد.
    """
    if not update.message:
        return
    if (context.user_data.get("email_ui") or {}).get("active"):
        return

    text = (update.message.text or "").strip()
    found = DOI_REGEX.findall(text)

    # --- ۱) اگر دقیقاً یک DOI پیدا شد → کانورسیشن را خودکار شروع کن
    if len(found) == 1:
        doi = normalize_doi(found[0])
        if not doi:
            return
        user = ensure_user(update.effective_user.id, update.effective_user.username)
        access = _doi_access_status(user)
        if not access.get("ok"):
            await update.message.reply_text(_doi_block_message(access), reply_markup=back_to_menu_kb())
            return
        # Ensure DOI control message exists for direct DOI entry.
        ctrl = context.user_data.get("doi_ctrl")

        # Create control message if missing.
        if not ctrl:
            sent = await update.message.reply_text(
                build_doi_control_text(0, status_lines=_doi_status_lines(access, 0)),
                reply_markup=doi_control_kb(),
                parse_mode=ParseMode.HTML
            )
            context.user_data["doi_ctrl"] = (sent.chat_id, sent.message_id)
            context.user_data["doi_buffer"] = []

        # Mirror receive_doi buffer behavior.
        buf: List[str] = context.user_data.get("doi_buffer", [])
        if doi in buf:          # Skip duplicates.
            return

        if not access.get("unlimited"):
            remaining = int(access.get("remaining") or 0)
            if remaining <= len(buf):
                await update.message.reply_text(
                    "سهمیهٔ شما تکمیل شده است. لطفاً روی «پایان ارسال DOIها» بزنید.",
                    reply_markup=doi_control_kb(),
                )
                return

        else:
            daily_remaining = int(access.get("daily_remaining") or 0)
            if daily_remaining <= len(buf):
                await update.message.reply_text(
                    "سهمیه امروز شما تکمیل شده است. لطفاً روی «پایان ارسال DOIها» بزنید.",
                    reply_markup=doi_control_kb(),
                )
                return

        buf.append(doi)
        context.user_data["doi_buffer"] = buf

        # Update control message (if any).
        ctrl = context.user_data.get("doi_ctrl")
        if ctrl:
            await _update_doi_ctrl(context, ctrl[0], ctrl[1], len(buf), _doi_status_lines(access, len(buf)))

        logger.debug("auto_doi_add | uid=%s doi=%s", update.effective_user.id, doi)
        return
# --- ۲) متن نامرتبط → یادآوری ساده
    return



def log_ctx(u: Update) -> str:
    return f"Update(update_id={u.update_id}, user={u.effective_user.username if u.effective_user else 'N/A'})"

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        u = update if isinstance(update, Update) else None
        meta = log_ctx(u) if u else "update=None"
    except Exception:
        meta = "meta_build_failed"
    logger.exception("Exception occurred | %s", meta, exc_info=context.error)

# =========================
# راه‌اندازی
# =========================


async def on_scinet_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not scinet_complete_active_request:
        if update.callback_query:
            await update.callback_query.answer("این قابلیت فعال نیست.", show_alert=True)
        return

    q = update.callback_query
    if not q:
        return

    await q.answer("در حال آزاد کردن…", show_alert=False)
    result = await scinet_complete_active_request()

    with contextlib.suppress(Exception):
        await q.edit_message_reply_markup(reply_markup=None)

    try:
        await q.message.reply_text(result)  # type: ignore[arg-type]
    except Exception:
        with contextlib.suppress(Exception):
            await context.bot.send_message(chat_id=CFG.SCINET_GROUP_CHAT_ID, text=result)

def build_app() -> Application:
    if not CFG.TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN env var is missing")

    # اجبار به HTTP/1.1
    # تایم‌اوت‌های پیش‌فرض (۵ ثانیه) در بعضی شبکه‌ها باعث Timeout در `getMe` می‌شود و
    # می‌تواند Bot را نیمه‌初始化 کند. اینجا کمی بزرگ‌تر می‌گذاریم.
    req = HTTPXRequest(
        http_version="1.1",
        connect_timeout=20.0,
        read_timeout=20.0,
        write_timeout=20.0,
        pool_timeout=10.0,
    )

    async def _post_init(application: Application) -> None:
        # تضمین اینکه application.bot.bot (نتیجه‌ی getMe) واقعاً cache شده است
        # تا در Application.start برای نام task به مشکل نخوریم.
        try:
            _ = application.bot.bot
        except RuntimeError:
            await application.bot.get_me()

        if start_api_server:
            try:
                runner = await start_api_server(bot=application.bot)
                application.bot_data["api_runner"] = runner
                if runner:
                    logger.info("api_server_started | host=%s port=%s", CFG.API_HOST, CFG.API_PORT)
            except Exception as exc:
                logger.warning("api_server_start_failed | err=%s", exc)

    async def _post_shutdown(application: Application) -> None:
        if stop_api_server:
            runner = application.bot_data.get("api_runner")
            try:
                await stop_api_server(runner)
            except Exception as exc:
                logger.warning("api_server_stop_failed | err=%s", exc)

    try:
        from telegram.ext import AIORateLimiter
        builder = (
            Application.builder()
            .token(CFG.TOKEN)
            .request(req)                  # ←‌ این خط
            .rate_limiter(AIORateLimiter())
            .post_init(_post_init)
            .post_shutdown(_post_shutdown)
        )
    except Exception:
        # اگر AIORateLimiter در دسترس نبود هم request(req) را حفظ کن
        builder = (
            Application.builder()
            .token(CFG.TOKEN)
            .request(req)                  # ←‌ فراموش نشود
            .post_init(_post_init)
            .post_shutdown(_post_shutdown)
        )
    app = builder.build()

    doi_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(enter_doi_flow, pattern=f"^{CB_MENU_SEND_DOI}$")],
        states={
            WAITING_FOR_DOI: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_doi),
                CallbackQueryHandler(finish_doi, pattern=f"^{CB_DOI_FINISH}$"),
                CallbackQueryHandler(on_menu_root, pattern=f"^{CB_MENU_ROOT}$"),
                CallbackQueryHandler(enter_doi_flow, pattern=f"^{CB_MENU_SEND_DOI}$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="doi_conversation",
        persistent=False,
        block=True,                       # ← اینجا
    )
 
    
    email_conv = build_email_verification_conversation()

    scihub_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(on_scihub_edit_entry, pattern=f"^{CB_SCIHUB_EDIT}$")],
        states={
            WAITING_SCIHUB: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_scihub_links)
            ]
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
        name="scihub_conv",
        persistent=False,
        block=True,
    )
    dl_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(on_dl_delete_entry, pattern=f"^{CB_DL_DELETE}$"),
            CallbackQueryHandler(on_dl_add_entry,    pattern=f"^{CB_DL_ADD}$"),
        ],
        states={
            # ---- حذف لینک خاص ----
            WAITING_DL_DELETE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_dl_delete),
                CallbackQueryHandler(on_dl_delete_entry, pattern=f"^{CB_DL_DELETE}$"),
                CallbackQueryHandler(on_dl_edit_menu,    pattern=f"^{CB_DL_EDIT}$"),
                CallbackQueryHandler(on_links_download,  pattern=f"^{CB_LINKS_DOWNLOAD}$"),
            ],

            # ---- مرحلهٔ ۱: دریافت URL ----
            WAITING_DL_ADD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_dl_add),
                CallbackQueryHandler(on_dl_edit_menu,    pattern=f"^{CB_DL_EDIT}$"),
                CallbackQueryHandler(on_links_download,  pattern=f"^{CB_LINKS_DOWNLOAD}$"),
            ],

            # ---- مرحلهٔ ۲: دریافت ظرفیت ----
            WAITING_DL_RATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_dl_rate),
                CallbackQueryHandler(dl_add_confirm, pattern="^dl:add:confirm$"),
                CallbackQueryHandler(dl_add_retry,   pattern="^dl:add:retry$"),
                CallbackQueryHandler(on_dl_edit_menu,    pattern=f"^{CB_DL_EDIT}$"),
                CallbackQueryHandler(on_links_download,  pattern=f"^{CB_LINKS_DOWNLOAD}$"),
                CallbackQueryHandler(on_dl_delete_entry, pattern=f"^{CB_DL_DELETE}$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="dl_conv",
        persistent=False,
        block=True,
    )

    payment_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(on_plagiarism_pay, pattern=r"^plag:pay:"),
            CallbackQueryHandler(on_plagiarism_wallet, pattern=r"^plag:wallet:"),
            CallbackQueryHandler(on_plan_pay, pattern=r"^plan:pay:"),
            CallbackQueryHandler(on_plan_wallet_pay, pattern=r"^plan:wallet:"),
            CallbackQueryHandler(on_menu_wallet_topup, pattern=f"^{CB_MENU_WALLET_TOPUP}$"),
        ],
        states={
            WAITING_WALLET_TOPUP_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_wallet_topup_amount),
                CallbackQueryHandler(on_menu_root, pattern=f"^{CB_MENU_ROOT}$"),
            ],
            WAITING_PAYMENT_RECEIPT: [
                MessageHandler(filters.PHOTO | filters.Document.ALL, receive_payment_receipt),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_payment_receipt_invalid),
                CallbackQueryHandler(on_menu_root, pattern=f"^{CB_MENU_ROOT}$"),
                CallbackQueryHandler(on_menu_plagiarism, pattern=f"^{CB_MENU_PLAGIARISM}$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="payment_conv",
        persistent=False,
        block=True,
    )

    submit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(on_plagiarism_submit_entry, pattern=r"^plag:submit:\d+$")],
        states={
            WAITING_PLAGIARISM_SUBMIT: [
                MessageHandler(filters.PHOTO | filters.Document.ALL | (filters.TEXT & ~filters.COMMAND), receive_plagiarism_submission),
                CallbackQueryHandler(on_menu_root, pattern=f"^{CB_MENU_ROOT}$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="plagiarism_submit_conv",
        persistent=False,
        block=True,
    )

    vpn_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(on_vpn_config_ir_entry, pattern=r"^vpn:add:iran$"),
            CallbackQueryHandler(on_vpn_config_global_entry, pattern=r"^vpn:add:global$"),
            CallbackQueryHandler(on_vpn_select_ir, pattern=r"^vpn:select:iran$"),
            CallbackQueryHandler(on_vpn_select_global, pattern=r"^vpn:select:global$"),
            CallbackQueryHandler(on_vpn_remove_ir, pattern=r"^vpn:remove:iran$"),
            CallbackQueryHandler(on_vpn_remove_global, pattern=r"^vpn:remove:global$"),
            CallbackQueryHandler(on_vpn_assign_entry, pattern=r"^vpn:assign:iran$"),
            CallbackQueryHandler(on_acc_vpn_entry, pattern=r"^(acc:vpn|vpn:acc):\d+$"),
        ],
        states={
            WAITING_VPN_LABEL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_vpn_label),
                CallbackQueryHandler(on_menu_vpn, pattern=f"^{CB_BACK_ADMIN_ROOT}$"),
            ],
            WAITING_VPN_CONFIG: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_vpn_config),
                CallbackQueryHandler(on_menu_vpn, pattern=f"^{CB_BACK_ADMIN_ROOT}$"),
            ],
            WAITING_VPN_SELECT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_vpn_select),
                CallbackQueryHandler(on_menu_vpn, pattern=f"^{CB_BACK_ADMIN_ROOT}$"),
            ],
            WAITING_VPN_DELETE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_vpn_delete),
                CallbackQueryHandler(on_menu_vpn, pattern=f"^{CB_BACK_ADMIN_ROOT}$"),
            ],
            WAITING_VPN_ASSIGN_CFG: [
                CallbackQueryHandler(on_vpn_assign_choose_config, pattern=r"^vpn:assign:cfg:\d+$"),
                CallbackQueryHandler(on_menu_vpn, pattern=f"^{CB_BACK_ADMIN_ROOT}$"),
            ],
            WAITING_VPN_ASSIGN_SLOT: [
                CallbackQueryHandler(on_vpn_assign_choose_slot, pattern=r"^vpn:assign:slot:\d+$"),
                CallbackQueryHandler(on_menu_vpn, pattern=f"^{CB_BACK_ADMIN_ROOT}$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="vpn_conv",
        persistent=False,
        block=True,
    )

    charge_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(on_menu_charge_entry, pattern=f"^{CB_ADMIN_CHARGE}$")],
        states={
            WAITING_CHARGE_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_charge_email)],
            WAITING_CHARGE_PAID: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_charge_paid)],
            WAITING_CHARGE_FREE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_charge_free)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="charge_conv",
        persistent=False,
        block=True,
    )

    store_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(on_store_set_price_plag, pattern=f"^{CB_STORE_SET_PRICE_PLAG}$"),
            CallbackQueryHandler(on_store_set_price_ai, pattern=f"^{CB_STORE_SET_PRICE_AI}$"),
            CallbackQueryHandler(on_store_set_card, pattern=f"^{CB_STORE_SET_CARD}$"),
            CallbackQueryHandler(on_store_set_group, pattern=f"^{CB_STORE_SET_GROUP}$"),
        ],
        states={
            WAITING_STORE_PRICE_PLAG: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_store_price_plag),
                CallbackQueryHandler(on_menu_store, pattern=f"^{CB_ADMIN_STORE}$"),
                CallbackQueryHandler(on_back_admin_root, pattern=f"^{CB_BACK_ADMIN_ROOT}$"),
            ],
            WAITING_STORE_PRICE_AI: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_store_price_ai),
                CallbackQueryHandler(on_menu_store, pattern=f"^{CB_ADMIN_STORE}$"),
                CallbackQueryHandler(on_back_admin_root, pattern=f"^{CB_BACK_ADMIN_ROOT}$"),
            ],
            WAITING_STORE_CARD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_store_card),
                CallbackQueryHandler(on_menu_store, pattern=f"^{CB_ADMIN_STORE}$"),
                CallbackQueryHandler(on_back_admin_root, pattern=f"^{CB_BACK_ADMIN_ROOT}$"),
            ],
            WAITING_STORE_GROUP: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_store_group),
                CallbackQueryHandler(on_menu_store, pattern=f"^{CB_ADMIN_STORE}$"),
                CallbackQueryHandler(on_back_admin_root, pattern=f"^{CB_BACK_ADMIN_ROOT}$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="store_conv",
        persistent=False,
        block=True,
    )

    app.add_handler(dl_conv, group=0)
    app.add_handler(payment_conv, group=0)
    app.add_handler(submit_conv, group=0)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("cancel", cancel))

    app.add_handler(doi_conv,   group=0)
    app.add_handler(email_conv, group=0)
    app.add_handler(scihub_conv, group=0)
    app.add_handler(vpn_conv, group=0)
    app.add_handler(charge_conv, group=0)
    app.add_handler(store_conv, group=0)
    app.add_handler(CallbackQueryHandler(on_links_download,    pattern=f"^{CB_LINKS_DOWNLOAD}$"))
    app.add_handler(CallbackQueryHandler(on_dl_edit_menu,      pattern=f"^{CB_DL_EDIT}$"))
    app.add_handler(CallbackQueryHandler(on_dl_backup_toggle,  pattern=f"^{CB_DL_BACKUP}$"))
    app.add_handler(CallbackQueryHandler(dl_backup_toggle_item, pattern=r"^dl:toggle:\d+$"))
    app.add_handler(CallbackQueryHandler(on_download_link_done, pattern=f"^{CB_DL_DONE}$"))
    app.add_handler(CallbackQueryHandler(on_menu_plagiarism, pattern=f"^{CB_MENU_PLAGIARISM}$"))
    app.add_handler(CallbackQueryHandler(on_plagiarism_product, pattern=r"^plag:(only|ai)$"))
    app.add_handler(CallbackQueryHandler(on_payment_approve, pattern=r"^pay:approve:\d+$"))
    app.add_handler(CallbackQueryHandler(on_payment_reject, pattern=r"^pay:reject:\d+$"))
    app.add_handler(CallbackQueryHandler(on_payment_cancel, pattern=r"^pay:cancel:\d+$"))
    app.add_handler(CallbackQueryHandler(on_payment_done, pattern=f"^{CB_PAYMENT_DONE}$"))
    app.add_handler(CallbackQueryHandler(on_menu_store, pattern=f"^{CB_ADMIN_STORE}$"))


    app.add_handler(CallbackQueryHandler(on_menu_account, pattern=f"^{CB_MENU_ACCOUNT}$"))
    app.add_handler(CallbackQueryHandler(on_account_delivery, pattern=f"^{CB_ACCOUNT_DELIVERY}$"))
    app.add_handler(CallbackQueryHandler(on_account_token, pattern=f"^{CB_ACCOUNT_TOKEN}$"))
    app.add_handler(CallbackQueryHandler(on_token_regen, pattern=f"^{CB_TOKEN_REGEN}$"))
    app.add_handler(CallbackQueryHandler(set_delivery_bot, pattern=f"^{CB_DELIVERY_BOT}$"))
    app.add_handler(CallbackQueryHandler(set_delivery_email, pattern=f"^{CB_DELIVERY_EMAIL}$"))

    app.add_handler(CallbackQueryHandler(on_menu_topup, pattern=f"^{CB_MENU_TOPUP}$"))
    app.add_handler(CallbackQueryHandler(on_plan_normal, pattern=f"^{CB_PLAN_NORMAL}$"))
    app.add_handler(CallbackQueryHandler(on_plan_premium, pattern=f"^{CB_PLAN_PREMIUM}$"))
    app.add_handler(CallbackQueryHandler(on_select_normal_40, pattern=f"^{CB_NORMAL_40}$"))
    app.add_handler(CallbackQueryHandler(on_select_normal_100, pattern=f"^{CB_NORMAL_100}$"))
    app.add_handler(CallbackQueryHandler(on_select_premium_1m, pattern=f"^{CB_PREMIUM_1M}$"))
    app.add_handler(CallbackQueryHandler(on_select_premium_3m, pattern=f"^{CB_PREMIUM_3M}$"))
    app.add_handler(CallbackQueryHandler(on_confirm_plan, pattern=f"^{CB_CONFIRM}$"))
    app.add_handler(CallbackQueryHandler(on_plan_continue, pattern=f"^{CB_PLAN_CONTINUE}$"))
    app.add_handler(CallbackQueryHandler(on_back, pattern=f"^{CB_BACK}$"))
    app.add_handler(CallbackQueryHandler(on_back_root, pattern=f"^{CB_BACK_ROOT}$"))
    app.add_handler(CallbackQueryHandler(on_menu_root, pattern=f"^{CB_MENU_ROOT}$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_payment_reject_reason, block=False), group=1)
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_text),
        group=99
    )    
    
    app.add_handler(CallbackQueryHandler(on_menu_links,      pattern=f"^{CB_ADMIN_LINKS}$"))
    app.add_handler(CallbackQueryHandler(on_menu_vpn,        pattern=f"^{CB_ADMIN_VPN}$"))
    app.add_handler(CallbackQueryHandler(on_vpn_region_ir,   pattern=f"^{CB_VPN_IR}$"))
    app.add_handler(CallbackQueryHandler(on_vpn_region_global, pattern=f"^{CB_VPN_GLOBAL}$"))
    app.add_handler(CallbackQueryHandler(on_vpn_ping_ir,     pattern=r"^vpn:ping:iran$"))
    app.add_handler(CallbackQueryHandler(on_vpn_ping_global, pattern=r"^vpn:ping:global$"))
    app.add_handler(CallbackQueryHandler(on_links_scihub,    pattern=f"^{CB_LINKS_SCIHUB}$"))
    app.add_handler(CallbackQueryHandler(on_menu_accounts,   pattern=f"^{CB_ADMIN_ACCOUNTS}$"))
    app.add_handler(CallbackQueryHandler(on_acc_toggle,      pattern=r"^acc:toggle:\d+$"))
    app.add_handler(CallbackQueryHandler(on_acc_primary,     pattern=r"^acc:primary:\d+$"))
    app.add_handler(CallbackQueryHandler(on_menu_activation, pattern=f"^{CB_ADMIN_ACTIVATION}$"))
    app.add_handler(CallbackQueryHandler(on_activation_toggle, pattern=r"^act:(on|off)$"))

    app.add_handler(CallbackQueryHandler(on_back_admin_root, pattern=f"^{CB_BACK_ADMIN_ROOT}$"))
    app.add_handler(CallbackQueryHandler(show_user_menu,     pattern=f"^{CB_ADMIN_USER_MENU}$"))
    if scinet_complete_active_request:
        app.add_handler(CallbackQueryHandler(on_scinet_done, pattern=f"^{SCINET_DONE_CALLBACK}$"))
    app.add_error_handler(error_handler)
    return app

def _payment_amount_display(amount: int) -> str:
    amount_text = _format_price_toman(amount)
    return f"{amount_text} تومان" if amount_text != "نامشخص" else "نامشخص"


def _product_label(product_key: str) -> str:
    if product_key == WALLET_TOPUP_PRODUCT:
        return "شارژ کیف پول"
    product = PLAGIARISM_PRODUCTS.get(product_key)
    if product:
        return product["label"]
    plan_type = _plan_type_from_product_key(product_key)
    if plan_type:
        return _plan_label(plan_type)
    return "نامشخص"


def _product_price(product_key: str) -> int:
    product = PLAGIARISM_PRODUCTS.get(product_key)
    if not product:
        return 0
    default = CFG.STORE_PLAGIARISM_PRICE if product_key == PLAGIARISM_PRODUCT else CFG.STORE_PLAGIARISM_AI_PRICE
    return _get_store_price(product["price_key"], default)


def _build_payment_instruction_text(
    product_label: str,
    amount: int,
    card_number: str,
    payment_id: int,
    payment_code: str,
    *,
    total_amount: Optional[int] = None,
    wallet_used: int = 0,
) -> str:
    amount_display = _payment_amount_display(amount)
    total_value = int(total_amount) if total_amount is not None else int(amount)
    total_display = _payment_amount_display(total_value)
    wallet_used = int(wallet_used or 0)
    wallet_display = _payment_amount_display(wallet_used) if wallet_used > 0 else ""
    card_display = _format_card_number(card_number) if card_number else "—"
    if wallet_used > 0 and total_value > 0:
        price_lines = (
            f"مبلغ کل: {total_display}\n"
            f"پرداخت از کیف پول: {wallet_display}\n"
            f"مبلغ قابل پرداخت: {amount_display}\n"
        )
        pay_line = f"برای تکمیل خرید لطفا مبلغ باقی‌مانده {amount_display} را به شماره کارت زیر پرداخت کنید و سپس تصویر رسید خود را ارسال کنید."
    else:
        price_lines = f"مبلغ: {amount_display}\n"
        pay_line = f"برای خرید این محصول لطفا مبلغ {amount_display} را به شماره کارت زیر پرداخت کنید و سپس تصویر رسید خود را ارسال کنید."
    return (
        "🧾 <b>راهنمای پرداخت</b>\n"
        f"محصول: {htmlmod.escape(product_label)}\n"
        f"{price_lines}"
        f"شماره کارت:\n<code>{htmlmod.escape(card_display)}</code>\n"
        f"شماره پرداخت: <code>{payment_id}</code>\n"
        f"کد پرداخت: <code>{htmlmod.escape(payment_code)}</code>\n\n"
        f"{pay_line}"
    )


async def _handle_open_payment_request(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: int,
    fallback_product_key: str,
    fallback_amount: int,
    fallback_card: str,
) -> Optional[int]:
    open_req = db_get_open_payment_request(int(user_id))
    if not open_req:
        return None
    open_product = (open_req.get("product_key") or "").strip()
    if open_product and open_product != fallback_product_key:
        return None
    status = open_req.get("status")
    label = _product_label(open_req.get("product_key") or fallback_product_key)
    chat_id = update.effective_chat.id
    if status == PAYMENT_STATUS_PENDING:
        await context.bot.send_message(
            chat_id,
            f"رسید پرداخت قبلی برای «{label}» در حال بررسی است. کد پرداخت: {open_req.get('payment_code','—')}",
        )
        return ConversationHandler.END
    if status == PAYMENT_STATUS_AWAITING:
        payment_id = int(open_req.get("id") or 0)
        context.user_data[PENDING_PAYMENT_KEY] = payment_id
        context.user_data[PENDING_PAYMENT_PRODUCT_KEY] = open_req.get("product_key") or fallback_product_key
        amount_val = int(open_req.get("amount") or fallback_amount)
        total_amount_val = int(open_req.get("total_amount") or amount_val)
        wallet_used = int(open_req.get("wallet_used") or 0)
        text = _build_payment_instruction_text(
            label,
            amount_val,
            open_req.get("card_number") or fallback_card,
            payment_id,
            str(open_req.get("payment_code") or ""),
            total_amount=total_amount_val,
            wallet_used=wallet_used,
        )
        await context.bot.send_message(
            chat_id,
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=payment_cancel_kb(payment_id),
        )
        return WAITING_PAYMENT_RECEIPT
    return ConversationHandler.END


async def _start_manual_payment(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    product_key: str,
    amount: int,
    product_label: Optional[str] = None,
    total_amount: Optional[int] = None,
    wallet_used: int = 0,
    refund_wallet_user_id: Optional[int] = None,
    refund_wallet_amount: int = 0,
) -> int:
    user = update.effective_user
    if not user:
        return ConversationHandler.END
    chat_id = update.effective_chat.id
    group_id = _get_store_group_id()
    card_number = _get_store_card_number()
    if amount <= 0 or not card_number or not group_id:
        await context.bot.send_message(
            chat_id,
            "⚠️ پرداخت برای این سرویس در حال حاضر فعال نیست. لطفاً بعداً دوباره تلاش کنید.",
        )
        if refund_wallet_user_id and refund_wallet_amount > 0:
            db_add_wallet_balance(int(refund_wallet_user_id), int(refund_wallet_amount))
        return ConversationHandler.END

    open_state = await _handle_open_payment_request(
        update,
        context,
        user_id=int(user.id),
        fallback_product_key=product_key,
        fallback_amount=amount,
        fallback_card=card_number,
    )
    if open_state is not None:
        return open_state

    req = db_create_payment_request(
        int(user.id),
        user.username,
        int(chat_id),
        product_key,
        int(amount),
        card_number,
        total_amount=total_amount if total_amount is not None else int(amount),
        wallet_used=int(wallet_used or 0),
    )
    payment_id = int(req.get("id") or 0)
    if not payment_id:
        if refund_wallet_user_id and refund_wallet_amount > 0:
            db_add_wallet_balance(int(refund_wallet_user_id), int(refund_wallet_amount))
        await context.bot.send_message(chat_id, "❗️ ثبت پرداخت با خطا مواجه شد. لطفاً دوباره تلاش کنید.")
        return ConversationHandler.END

    context.user_data[PENDING_PAYMENT_KEY] = payment_id
    context.user_data[PENDING_PAYMENT_PRODUCT_KEY] = product_key
    label = product_label or _product_label(product_key)
    text = _build_payment_instruction_text(
        label,
        int(amount),
        card_number,
        payment_id,
        str(req.get("payment_code") or ""),
        total_amount=total_amount if total_amount is not None else int(amount),
        wallet_used=int(wallet_used or 0),
    )
    await context.bot.send_message(
        chat_id,
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=payment_cancel_kb(payment_id),
    )
    return WAITING_PAYMENT_RECEIPT


async def _send_plagiarism_submit_prompt(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    chat_id: int,
    payment_id: int,
    payment_code: str,
) -> None:
    text = (
        "✅ پرداخت شما تایید شد.\n"
        f"شماره پرداخت: {payment_id}\n"
        f"کد پرداخت: {payment_code}\n"
        "برای ادامه روند، روی دکمهٔ ارسال فایل/متن بزنید."
    )
    reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 ارسال فایل/متن برای بررسی", callback_data=f"{CB_PLAGIARISM_SUBMIT_PREFIX}{payment_id}")],
        [InlineKeyboardButton("↩️ منوی اصلی", callback_data=CB_MENU_ROOT)],
    ])
    with contextlib.suppress(Exception):
        await context.bot.send_message(int(chat_id), text, reply_markup=reply_markup)

async def on_menu_plagiarism(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    await q.answer()
    text = "🧪 <b>انتخاب سرویس بررسی</b>\nیکی از گزینه‌های زیر را انتخاب کنید:"
    await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=plagiarism_menu_kb())
    return ConversationHandler.END


async def on_plagiarism_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    await q.answer()
    data = q.data or ""
    product_key = PLAGIARISM_PRODUCT if data == CB_PLAGIARISM_ONLY else PLAGIARISM_AI_PRODUCT
    label = _product_label(product_key)
    price = _product_price(product_key)
    price_display = _payment_amount_display(price)
    text = (
        "🔒 <b>امنیت و محرمانگی</b>\n"
        "اطلاعات شما محرمانه است و فقط برای بررسی استفاده می‌شود.\n"
        "هیچ داده‌ای بدون اجازه شما منتشر یا با شخص ثالثی به اشتراک گذاشته نمی‌شود.\n\n"
        f"💰 قیمت: <b>{price_display}</b>\n"
    )
    await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=plagiarism_product_kb(product_key))


async def on_plagiarism_pay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q:
        return ConversationHandler.END
    await q.answer()

    data = q.data or ""
    if not data.startswith(CB_PLAGIARISM_PAY_PREFIX):
        return ConversationHandler.END
    product_key = data[len(CB_PLAGIARISM_PAY_PREFIX):]
    if product_key not in PLAGIARISM_PRODUCTS:
        return ConversationHandler.END
    price = _product_price(product_key)
    return await _start_manual_payment(
        update,
        context,
        product_key=product_key,
        amount=price,
        product_label=_product_label(product_key),
        total_amount=price,
        wallet_used=0,
    )


async def on_plagiarism_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q:
        return ConversationHandler.END
    await q.answer()

    data = q.data or ""
    if not data.startswith(CB_PLAGIARISM_WALLET_PREFIX):
        return ConversationHandler.END
    product_key = data[len(CB_PLAGIARISM_WALLET_PREFIX):]
    if product_key not in PLAGIARISM_PRODUCTS:
        return ConversationHandler.END

    user = update.effective_user
    if not user:
        return ConversationHandler.END

    price = _product_price(product_key)
    if price <= 0:
        await context.bot.send_message(
            update.effective_chat.id,
            "⚠️ پرداخت برای این سرویس در حال حاضر فعال نیست. لطفاً بعداً دوباره تلاش کنید.",
        )
        return ConversationHandler.END

    card_number = _get_store_card_number()
    open_state = await _handle_open_payment_request(
        update,
        context,
        user_id=int(user.id),
        fallback_product_key=product_key,
        fallback_amount=price,
        fallback_card=card_number,
    )
    if open_state is not None:
        return open_state

    db_user = db_get_user(int(user.id))
    balance = int(db_user.get("wallet_balance") or 0) if db_user else 0
    wallet_used = min(balance, int(price))
    remaining = int(price) - wallet_used

    if remaining > 0:
        group_id = _get_store_group_id()
        if not card_number or not group_id:
            await context.bot.send_message(
                update.effective_chat.id,
                "⚠️ پرداخت برای این سرویس در حال حاضر فعال نیست. لطفاً بعداً دوباره تلاش کنید.",
            )
            return ConversationHandler.END

    if wallet_used > 0:
        db_add_wallet_balance(int(user.id), -wallet_used)

    if remaining <= 0:
        req = db_create_payment_request(
            int(user.id),
            user.username,
            int(update.effective_chat.id),
            product_key,
            int(price),
            "",
            total_amount=int(price),
            wallet_used=int(price),
        )
        payment_id = int(req.get("id") or 0)
        if not payment_id:
            if wallet_used > 0:
                db_add_wallet_balance(int(user.id), wallet_used)
            await context.bot.send_message(update.effective_chat.id, "❗️ ثبت پرداخت با خطا مواجه شد. لطفاً دوباره تلاش کنید.")
            return ConversationHandler.END
        db_set_payment_status(payment_id, PAYMENT_STATUS_APPROVED)
        await _send_plagiarism_submit_prompt(
            context,
            chat_id=int(update.effective_chat.id),
            payment_id=payment_id,
            payment_code=str(req.get("payment_code") or "—"),
        )
        return ConversationHandler.END

    return await _start_manual_payment(
        update,
        context,
        product_key=product_key,
        amount=remaining,
        product_label=_product_label(product_key),
        total_amount=int(price),
        wallet_used=wallet_used,
        refund_wallet_user_id=int(user.id),
        refund_wallet_amount=wallet_used,
    )


async def on_plagiarism_submit_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q:
        return ConversationHandler.END
    await q.answer()

    data = q.data or ""
    if not data.startswith(CB_PLAGIARISM_SUBMIT_PREFIX):
        return ConversationHandler.END
    try:
        payment_id = int(data.split(":")[-1])
    except Exception:
        return ConversationHandler.END

    rec = db_get_payment_request(payment_id)
    if not rec or rec.get("status") != PAYMENT_STATUS_APPROVED:
        await q.edit_message_text("پرداخت تایید نشده است.", reply_markup=back_to_menu_kb())
        return ConversationHandler.END

    if update.effective_user and int(rec.get("user_id") or 0) != int(update.effective_user.id):
        await q.answer("دسترسی ندارید.", show_alert=True)
        return ConversationHandler.END

    context.user_data[PENDING_SUBMISSION_KEY] = payment_id
    await q.edit_message_text("لطفاً فایل یا متن خود را برای بررسی ارسال کنید.", reply_markup=back_to_menu_kb())
    return WAITING_PLAGIARISM_SUBMIT


async def receive_plagiarism_submission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END

    payment_id = context.user_data.get(PENDING_SUBMISSION_KEY)
    if not payment_id:
        return ConversationHandler.END

    rec = db_get_payment_request(int(payment_id))
    if not rec or rec.get("status") != PAYMENT_STATUS_APPROVED:
        await update.message.reply_text("پرداخت تایید نشده است.", reply_markup=back_to_menu_kb())
        context.user_data.pop(PENDING_SUBMISSION_KEY, None)
        return ConversationHandler.END

    if update.effective_user and int(rec.get("user_id") or 0) != int(update.effective_user.id):
        await update.message.reply_text("دسترسی ندارید.")
        context.user_data.pop(PENDING_SUBMISSION_KEY, None)
        return ConversationHandler.END

    group_id = _get_store_group_id()
    if not group_id:
        await update.message.reply_text("گروه بررسی تنظیم نشده است. لطفاً با پشتیبانی تماس بگیرید.")
        return ConversationHandler.END

    user = update.effective_user
    username = f"@{user.username}" if user and user.username else ""
    full_name = user.full_name if user else "—"
    product_label = _product_label(rec.get("product_key") or "")
    caption = (
        "📄 ارسال برای بررسی\n"
        f"شماره پرداخت: {rec.get('id')}\n"
        f"کد پرداخت: {rec.get('payment_code','—')}\n"
        f"محصول: {product_label}\n"
        f"کاربر: {full_name} {username}\n"
        f"شناسه کاربر: {rec.get('user_id','—')}"
    )

    try:
        if update.message.photo:
            await context.bot.send_photo(group_id, update.message.photo[-1].file_id, caption=caption)
        elif update.message.document:
            await context.bot.send_document(group_id, update.message.document.file_id, caption=caption)
        elif update.message.text:
            await context.bot.send_message(group_id, f"{caption}\n\nمتن ارسالی:\n{update.message.text}")
        else:
            await update.message.reply_text("لطفاً فایل یا متن ارسال کنید.")
            return WAITING_PLAGIARISM_SUBMIT
    except Exception:
        pass

    await update.message.reply_text("✅ ارسال شما ثبت شد و در حال بررسی است.")
    context.user_data.pop(PENDING_SUBMISSION_KEY, None)
    return ConversationHandler.END


async def receive_payment_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END

    payment_id = context.user_data.get(PENDING_PAYMENT_KEY)
    if not payment_id:
        await update.message.reply_text("ابتدا پرداخت را از طریق دکمهٔ پرداخت شروع کنید.")
        return ConversationHandler.END

    rec = db_get_payment_request(int(payment_id))
    if not rec:
        await update.message.reply_text("❗️ اطلاعات پرداخت یافت نشد.")
        context.user_data.pop(PENDING_PAYMENT_KEY, None)
        return ConversationHandler.END

    if rec.get("status") != PAYMENT_STATUS_AWAITING:
        await update.message.reply_text("این پرداخت قبلاً ثبت شده است.")
        context.user_data.pop(PENDING_PAYMENT_KEY, None)
        return ConversationHandler.END

    file_id = ""
    file_unique_id = ""
    if update.message.photo:
        photo = update.message.photo[-1]
        file_id = photo.file_id
        file_unique_id = photo.file_unique_id
    elif update.message.document:
        doc = update.message.document
        file_id = doc.file_id
        file_unique_id = doc.file_unique_id
    else:
        await update.message.reply_text("لطفاً تصویر یا فایل رسید را ارسال کنید.")
        return WAITING_PAYMENT_RECEIPT

    db_update_payment_receipt(
        int(payment_id),
        file_id=file_id,
        file_unique_id=file_unique_id,
        message_id=int(update.message.message_id),
    )

    group_id = _get_store_group_id()
    if not group_id:
        await update.message.reply_text("⚠️ گروه بررسی پرداخت تنظیم نشده است. لطفاً با پشتیبانی تماس بگیرید.")
        return ConversationHandler.END

    product_label = _product_label(rec.get("product_key") or "")
    amount_value = int(rec.get("amount") or 0)
    total_value = int(rec.get("total_amount") or amount_value)
    wallet_used = int(rec.get("wallet_used") or 0)
    amount_display = _payment_amount_display(amount_value)
    total_display = _payment_amount_display(total_value)
    wallet_display = _payment_amount_display(wallet_used) if wallet_used > 0 else ""
    if wallet_used > 0 and total_value > 0:
        amount_lines = (
            f"مبلغ کل: {total_display}\n"
            f"پرداخت از کیف پول: {wallet_display}\n"
            f"مبلغ قابل پرداخت: {amount_display}\n"
        )
    else:
        amount_lines = f"مبلغ: {amount_display}\n"
    card_display = _format_card_number(rec.get("card_number") or "") or "—"
    user = update.effective_user
    username = f"@{user.username}" if user and user.username else ""
    full_name = user.full_name if user else "—"
    caption = (
        "🧾 رسید پرداخت\n"
        f"شماره پرداخت: {rec.get('id')}\n"
        f"کد پرداخت: {rec.get('payment_code','—')}\n"
        f"محصول: {product_label}\n"
        f"{amount_lines}"
        f"شماره کارت:\n{card_display}\n"
        f"کاربر: {full_name} {username}\n"
        f"شناسه کاربر: {rec.get('user_id','—')}\n"
        f"چت کاربر: {rec.get('chat_id','—')}"
    )

    review_msg = None
    try:
        if update.message.photo:
            review_msg = await context.bot.send_photo(
                chat_id=group_id,
                photo=file_id,
                caption=caption,
                reply_markup=payment_review_kb(int(payment_id)),
            )
        else:
            review_msg = await context.bot.send_document(
                chat_id=group_id,
                document=file_id,
                caption=caption,
                reply_markup=payment_review_kb(int(payment_id)),
            )
    except Exception:
        pass

    if review_msg:
        db_set_payment_review_message(int(payment_id), int(review_msg.chat_id), int(review_msg.message_id))

    await update.message.reply_text(
        f"✅ رسید دریافت شد و در حال بررسی است.\nکد پرداخت: {rec.get('payment_code','—')}",
        reply_markup=back_to_menu_kb(),
    )
    context.user_data.pop(PENDING_PAYMENT_KEY, None)
    context.user_data.pop(PENDING_PAYMENT_PRODUCT_KEY, None)
    return ConversationHandler.END


async def receive_payment_receipt_invalid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message:
        await update.message.reply_text("لطفاً فقط تصویر رسید را ارسال کنید.")
    return WAITING_PAYMENT_RECEIPT


async def on_payment_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    await q.answer("این پرداخت قبلاً بررسی شده است.", show_alert=False)


async def on_payment_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    await q.answer()

    data = q.data or ""
    payment_id = int(data.split(":")[-1]) if data.startswith(CB_PAYMENT_CANCEL_PREFIX) else 0
    if not payment_id:
        return

    rec = db_get_payment_request(payment_id)
    if not rec:
        await q.answer("پرداخت یافت نشد.", show_alert=True)
        return

    status = rec.get("status")
    if status == PAYMENT_STATUS_PENDING:
        await q.answer("رسید شما ثبت شده و در حال بررسی است.", show_alert=True)
        return
    if status == PAYMENT_STATUS_APPROVED:
        await q.answer("پرداخت قبلاً تایید شده است.", show_alert=True)
        return
    if status == PAYMENT_STATUS_REJECTED:
        await q.answer("پرداخت قبلاً رد شده است.", show_alert=True)
        return
    if status != PAYMENT_STATUS_AWAITING:
        await q.answer("وضعیت پرداخت نامعتبر است.", show_alert=True)
        return

    db_set_payment_status(payment_id, "cancelled")
    context.user_data.pop(PENDING_PAYMENT_KEY, None)
    context.user_data.pop(PENDING_PAYMENT_PRODUCT_KEY, None)
    await q.edit_message_text("پرداخت لغو شد.", reply_markup=back_to_menu_kb())


async def on_payment_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    await q.answer()
    if not is_admin(update):
        await q.answer("دسترسی ندارید.", show_alert=True)
        return

    data = q.data or ""
    payment_id = int(data.split(":")[-1]) if data.startswith(CB_PAYMENT_APPROVE_PREFIX) else 0
    if not payment_id:
        return

    rec = db_get_payment_request(payment_id)
    if not rec:
        await q.answer("پرداخت یافت نشد.", show_alert=True)
        return

    status = rec.get("status")
    if status == PAYMENT_STATUS_APPROVED:
        await q.answer("قبلاً تایید شده است.", show_alert=True)
        return
    if status == PAYMENT_STATUS_REJECTED:
        await q.answer("قبلاً رد شده است.", show_alert=True)
        return
    if status != PAYMENT_STATUS_PENDING:
        await q.answer("وضعیت پرداخت نامعتبر است.", show_alert=True)
        return

    db_set_payment_status(payment_id, PAYMENT_STATUS_APPROVED, admin_id=q.from_user.id if q.from_user else None)
    with contextlib.suppress(Exception):
        await q.edit_message_reply_markup(reply_markup=payment_review_done_kb("✅ تایید شد"))

    product_key = rec.get("product_key") or ""
    plan_type = _plan_type_from_product_key(product_key)
    if product_key in PLAGIARISM_PRODUCTS:
        await _send_plagiarism_submit_prompt(
            context,
            chat_id=int(rec.get("chat_id") or 0),
            payment_id=payment_id,
            payment_code=str(rec.get("payment_code") or "—"),
        )
        return

    if product_key == WALLET_TOPUP_PRODUCT:
        credit_amount = int(rec.get("total_amount") or rec.get("amount") or 0)
        if credit_amount > 0:
            db_add_wallet_balance(int(rec.get("user_id") or 0), credit_amount)
        balance_after = db_get_user(int(rec.get("user_id") or 0)).get("wallet_balance") or 0
        balance_text = f"{int(balance_after):,}".replace(",", "٬")
        amount_display = _payment_amount_display(credit_amount)
        text = (
            "✅ شارژ کیف پول شما تایید شد.\n"
            f"مبلغ: {amount_display}\n"
            f"موجودی فعلی: {balance_text} تومان"
        )
        with contextlib.suppress(Exception):
            await context.bot.send_message(int(rec.get("chat_id") or 0), text, reply_markup=back_to_menu_kb())
        return

    if plan_type:
        summary = _activate_plan(int(rec.get("user_id") or 0), plan_type)
        quota_add = summary.get("quota_add") or 0
        if summary.get("doi_unlimited"):
            daily_limit = int(summary.get("daily_limit") or 0)
            if daily_limit > 0:
                quota_line = f"\n• سهمیه DOI: نامحدود (سقف {daily_limit} در روز)"
            else:
                quota_line = "\n• سهمیه DOI: نامحدود"
        else:
            quota_line = f"\n• سهمیه DOI: {quota_add}" if quota_add else ""
        text = (
            "✅ پرداخت شما تایید شد و اشتراک شما فعال گردید.\n"
            f"• پلن: {summary.get('label')}{quota_line}"
        )
        with contextlib.suppress(Exception):
            await context.bot.send_message(int(rec.get("chat_id") or 0), text, reply_markup=back_to_menu_kb())
        return

    text = (
        "✅ پرداخت شما تایید شد.\n"
        f"شماره پرداخت: {payment_id}\n"
        f"کد پرداخت: {rec.get('payment_code','—')}"
    )
    with contextlib.suppress(Exception):
        await context.bot.send_message(int(rec.get("chat_id") or 0), text)


async def on_payment_reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    await q.answer()
    if not is_admin(update):
        await q.answer("دسترسی ندارید.", show_alert=True)
        return

    data = q.data or ""
    payment_id = int(data.split(":")[-1]) if data.startswith(CB_PAYMENT_REJECT_PREFIX) else 0
    if not payment_id:
        return

    rec = db_get_payment_request(payment_id)
    if not rec:
        await q.answer("پرداخت یافت نشد.", show_alert=True)
        return

    status = rec.get("status")
    if status == PAYMENT_STATUS_APPROVED:
        await q.answer("قبلاً تایید شده است.", show_alert=True)
        return
    if status == PAYMENT_STATUS_REJECTED:
        await q.answer("قبلاً رد شده است.", show_alert=True)
        return
    if status != PAYMENT_STATUS_PENDING:
        await q.answer("وضعیت پرداخت نامعتبر است.", show_alert=True)
        return

    context.user_data[PENDING_REJECT_KEY] = payment_id
    context.user_data[PENDING_REJECT_MSG_KEY] = (q.message.chat_id if q.message else None, q.message.message_id if q.message else None)
    await q.message.reply_text("لطفاً دلیل رد پرداخت را ارسال کنید.")


async def on_payment_reject_reason(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not is_admin(update):
        return

    payment_id = context.user_data.get(PENDING_REJECT_KEY)
    if not payment_id:
        return

    reason = (update.message.text or "").strip()
    if not reason:
        await update.message.reply_text("دلیل رد را ارسال کنید.")
        return

    rec = db_get_payment_request(int(payment_id))
    if not rec:
        context.user_data.pop(PENDING_REJECT_KEY, None)
        return

    if rec.get("status") != PAYMENT_STATUS_PENDING:
        context.user_data.pop(PENDING_REJECT_KEY, None)
        return

    db_set_payment_status(int(payment_id), PAYMENT_STATUS_REJECTED, admin_id=update.effective_user.id, admin_reason=reason)
    plan_type = _plan_type_from_product_key(rec.get("product_key") or "")
    if plan_type:
        _reject_plan(int(rec.get("user_id") or 0), plan_type)

    wallet_used = int(rec.get("wallet_used") or 0)
    refund_line = ""
    if wallet_used > 0:
        db_add_wallet_balance(int(rec.get("user_id") or 0), wallet_used)
        refund_line = f"\nمبلغ بازگشت به کیف پول: {_payment_amount_display(wallet_used)}"
    msg_info = context.user_data.pop(PENDING_REJECT_MSG_KEY, None)
    context.user_data.pop(PENDING_REJECT_KEY, None)
    if msg_info and msg_info[0] and msg_info[1]:
        try:
            await context.bot.edit_message_reply_markup(chat_id=msg_info[0], message_id=msg_info[1], reply_markup=None)
        except Exception:
            pass

    user_text = (
        "❌ پرداخت شما رد شد.\n"
        f"دلیل: {reason}\n"
        f"شماره پرداخت: {payment_id}\n"
        f"کد پرداخت: {rec.get('payment_code','—')}{refund_line}"
    )
    try:
        await context.bot.send_message(int(rec.get("chat_id") or 0), user_text)
    except Exception:
        pass

    await update.message.reply_text("❌ رد پرداخت ثبت شد.")


async def on_menu_store(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    await q.answer()
    if not is_admin(update):
        await q.edit_message_text("دسترسی ندارید.")
        return
    await q.edit_message_text(_store_status_text(), parse_mode=ParseMode.HTML, reply_markup=store_menu_kb())


async def on_store_set_price_plag(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q:
        return ConversationHandler.END
    await q.answer()
    if not is_admin(update):
        await q.edit_message_text("دسترسی ندارید.")
        return ConversationHandler.END
    await q.edit_message_text("قیمت چک پلاژياریسم را وارد کنید (عدد).", reply_markup=store_back_kb())
    return WAITING_STORE_PRICE_PLAG


async def receive_store_price_plag(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip().replace(",", "") if update.message else ""
    try:
        val = int(text)
        if val < 0:
            raise ValueError
    except Exception:
        await update.message.reply_text("لطفاً فقط عدد معتبر وارد کنید.", reply_markup=store_back_kb())
        return WAITING_STORE_PRICE_PLAG

    db_set_setting(STORE_PRICE_PLAG_KEY, str(val))
    await update.message.reply_text(_store_status_text(), parse_mode=ParseMode.HTML, reply_markup=store_menu_kb())
    return ConversationHandler.END


async def on_store_set_price_ai(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q:
        return ConversationHandler.END
    await q.answer()
    if not is_admin(update):
        await q.edit_message_text("دسترسی ندارید.")
        return ConversationHandler.END
    await q.edit_message_text("قیمت چک پلاژياریسم و AI را وارد کنید (عدد).", reply_markup=store_back_kb())
    return WAITING_STORE_PRICE_AI


async def receive_store_price_ai(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip().replace(",", "") if update.message else ""
    try:
        val = int(text)
        if val < 0:
            raise ValueError
    except Exception:
        await update.message.reply_text("لطفاً فقط عدد معتبر وارد کنید.", reply_markup=store_back_kb())
        return WAITING_STORE_PRICE_AI

    db_set_setting(STORE_PRICE_PLAG_AI_KEY, str(val))
    await update.message.reply_text(_store_status_text(), parse_mode=ParseMode.HTML, reply_markup=store_menu_kb())
    return ConversationHandler.END


async def on_store_set_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q:
        return ConversationHandler.END
    await q.answer()
    if not is_admin(update):
        await q.edit_message_text("دسترسی ندارید.")
        return ConversationHandler.END
    await q.edit_message_text("شماره کارت ۱۶ رقمی را وارد کنید.", reply_markup=store_back_kb())
    return WAITING_STORE_CARD


async def receive_store_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip() if update.message else ""
    digits = re.sub(r"\D", "", text)
    if len(digits) != 16:
        await update.message.reply_text("شماره کارت معتبر نیست (باید ۱۶ رقم باشد).", reply_markup=store_back_kb())
        return WAITING_STORE_CARD

    db_set_setting(STORE_CARD_KEY, digits)
    await update.message.reply_text(_store_status_text(), parse_mode=ParseMode.HTML, reply_markup=store_menu_kb())
    return ConversationHandler.END


async def on_store_set_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if not q:
        return ConversationHandler.END
    await q.answer()
    if not is_admin(update):
        await q.edit_message_text("دسترسی ندارید.")
        return ConversationHandler.END
    await q.edit_message_text("شناسه گروه بررسی پرداخت را وارد کنید (مثلاً -1001234567890) یا clear برای حذف.", reply_markup=store_back_kb())
    return WAITING_STORE_GROUP


async def receive_store_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip() if update.message else ""
    if text.lower() == "clear":
        db_set_setting(STORE_GROUP_KEY, "")
        await update.message.reply_text(_store_status_text(), parse_mode=ParseMode.HTML, reply_markup=store_menu_kb())
        return ConversationHandler.END

    try:
        val = int(text)
    except Exception:
        await update.message.reply_text("شناسه معتبر نیست.", reply_markup=store_back_kb())
        return WAITING_STORE_GROUP

    db_set_setting(STORE_GROUP_KEY, str(val))
    await update.message.reply_text(_store_status_text(), parse_mode=ParseMode.HTML, reply_markup=store_menu_kb())
    return ConversationHandler.END


def main() -> None:
    logger.info("=== Bot starting ===")
    db_init(); groq_health_check_sync()

    global ensure_scinet_session, scinet_monitor_cycle, scinet_complete_active_request

    if ensure_scinet_session:
        try:
            ensure_scinet_session()
        except Exception as exc:
            logger.warning("scinet_autologin_failed | err=%s", exc)
            ensure_scinet_session = None
            scinet_monitor_cycle = None
            scinet_complete_active_request = None

    app = build_app()

    async def _scidir_warm(context: CallbackContext) -> None:
        if not is_activation_on():
            return
        def _run() -> None:
            try:
                asyncio.run(
                    warmup_accounts(
                        iranpaper_accounts_ordered(),
                        cfg=CFG,
                        build_chrome_driver=_build_chrome_driver,
                        ensure_v2ray_running=ensure_v2ray_running,
                        solve_recaptcha=_maybe_solve_recaptcha,
                    )
                )
            except Exception as exc:
                logger.warning("scidir_warmup_failed | err=%s", exc)
        asyncio.get_running_loop().run_in_executor(None, _run)

    async def _scihub_warm(context: CallbackContext) -> None:
        if not is_activation_on():
            return
        def _run() -> None:
            try:
                _get_scihub_driver()
            except Exception as exc:
                logger.warning("scihub_warmup_failed | err=%s", exc)
        asyncio.get_running_loop().run_in_executor(None, _run)

    if scinet_monitor_cycle:
        async def _scinet_job(context: CallbackContext) -> None:
            await scinet_monitor_cycle(context.bot)

        try:
            if app.job_queue:
                app.job_queue.run_repeating(
                    _scinet_job,
                    interval=2,
                    first=2,
                    name="scinet_monitor",
                )
        except Exception as exc:
            logger.warning("scinet_monitor_schedule_failed | err=%s", exc)

    try:
        delay = random.uniform(30, 60)
        if app.job_queue:
            app.job_queue.run_once(_scidir_warm, when=delay, name="scidir_warmup")
            app.job_queue.run_once(_scihub_warm, when=delay, name="scihub_warmup")
    except Exception as exc:
        logger.warning("warmup_schedule_failed | err=%s", exc)

    app.run_polling(
        allowed_updates=["message", "callback_query", "my_chat_member"],
        drop_pending_updates=True
    )

if __name__ == "__main__":
    main()

