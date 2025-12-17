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
    db_set_plan, db_count_dois, db_add_dois, db_get_or_create_token, db_set_new_token,
    db_get_setting, db_set_setting,
    normalize_doi,
    save_user_email_code,
    db_add_quota_by_email, db_get_user_by_email, db_get_quota_status,
    vpn_load_configs, vpn_add_config, vpn_remove_config, vpn_set_active, vpn_ping_all,
    _get_scihub_driver, _build_chrome_driver, _maybe_solve_recaptcha,
    process_dois_batch, groq_health_check_sync, ensure_v2ray_running,
    iranpaper_accounts_ordered, iranpaper_set_active, iranpaper_set_primary, iranpaper_set_vpn,
    set_activation, is_activation_on, iranpaper_vpn_map,
)
from downloaders.sciencedirect import warmup_accounts
from telegram.request import HTTPXRequest
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
WELCOME_TEXT: Final[str] = "👋 به ربات doi خوش اومدید"

# --- کاربر عادی
CB_MENU_SEND_DOI   = "menu:send_doi"
CB_MENU_ACCOUNT    = "menu:account"
CB_MENU_TOPUP      = "menu:topup"
CB_MENU_ROOT       = "menu:root"

# --- پنل ادمین
CB_ADMIN_USER_MENU = "admin:user_menu"   # منوی کاربر عادی
CB_ADMIN_LINKS     = "admin:links"       # شاخهٔ «لینک‌ها»
CB_ADMIN_VPN       = "admin:vpn"
CB_ADMIN_ACCOUNTS  = "admin:accounts"
CB_ADMIN_ACTIVATION= "admin:activation"
CB_ADMIN_CHARGE    = "admin:charge"
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
DOI_REGEX   = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Z0-9]+)\b", re.IGNORECASE)
EMAIL_REGEX = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
# -----------------



PROVIDER_LABELS = [
    "ScienceDirect", "SpringerLink", "Wiley", "ACS",
    "Taylor & Francis", "IEEE", "Other"
]

def _valid_email(s: Optional[str]) -> bool:
    return bool(s and EMAIL_REGEX.match(s))

def _valid_email_code(code: str) -> bool:
    c = (code or "").strip()
    if len(c) != 6:
        return False
    if not c.isalnum():
        return False
    letters = sum(1 for ch in c if ch.isalpha())
    digits = sum(1 for ch in c if ch.isdigit())
    return letters == 1 and digits == 5

def _email_code_rules_text() -> str:
    return (
        "🔐 <b>رمز ۶ کاراکتری</b>\n"
        "لطفاً یک رمز <b>۶ کاراکتری</b> بفرستید که:\n"
        "• دقیقاً <b>۵ رقم</b> داشته باشد\n"
        "• دقیقاً <b>۱ حرف انگلیسی</b> داشته باشد\n"
        "• فقط از حروف انگلیسی و اعداد استفاده شود (بدون فاصله)\n"
        "نمونه: <code>12A345</code>\n\n"
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

# =========================
# Keyboards
# =========================
# -- کاربر عادی
def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📎 ارسال doi", callback_data=CB_MENU_SEND_DOI)],
        [InlineKeyboardButton("👤 حساب کاربری", callback_data=CB_MENU_ACCOUNT)],
        [InlineKeyboardButton("💳 شارژ حساب کاربری", callback_data=CB_MENU_TOPUP)],
    ])

# -- پنل ادمین
def admin_root_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("منوی اصلی", callback_data=CB_ADMIN_USER_MENU)],
        [InlineKeyboardButton("🔗 لینک‌ها",  callback_data=CB_ADMIN_LINKS)],
        [InlineKeyboardButton("📡 کانفیگ V2Ray", callback_data=CB_ADMIN_VPN)],
        [InlineKeyboardButton("👥 اکانت‌ها (IranPaper)", callback_data=CB_ADMIN_ACCOUNTS)],
        [InlineKeyboardButton("🔓 فعال‌سازی دانلود", callback_data=CB_ADMIN_ACTIVATION)],
        [InlineKeyboardButton("💳 شارژ حساب", callback_data=CB_ADMIN_CHARGE)],
    ])

# -- شاخهٔ «لینک‌ها»
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
        [InlineKeyboardButton("✉️ ارسال ایمیل", callback_data=CB_ACCOUNT_EMAIL)],
        [InlineKeyboardButton("📦 روش ارسال", callback_data=CB_ACCOUNT_DELIVERY)],
        [InlineKeyboardButton("🔑 توکن افزونه", callback_data=CB_ACCOUNT_TOKEN)],
        [InlineKeyboardButton("↩️ بازگشت به منوی اصلی", callback_data=CB_MENU_ROOT)],
    ])

def token_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔁 ساخت توکن جدید", callback_data=CB_TOKEN_REGEN)],
        [InlineKeyboardButton("↩️ بازگشت به حساب کاربری", callback_data=CB_MENU_ACCOUNT)],
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
    ])

def premium_subplan_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("۱ ماه — ۲۴۰٬۰۰۰ تومان", callback_data=CB_PREMIUM_1M)],
        [InlineKeyboardButton("۳ ماه — ۶۰۰٬۰۰۰ تومان", callback_data=CB_PREMIUM_3M)],
        [InlineKeyboardButton("↩️ بازگشت", callback_data=CB_BACK)],
    ])

def confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تایید پلن", callback_data=CB_CONFIRM)],
        [InlineKeyboardButton("↩️ بازگشت", callback_data=CB_BACK)],
    ])

def payment_keyboard(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 پرداخت", url=url)],
        [InlineKeyboardButton("↩️ بازگشت به انتخاب پلن", callback_data=CB_BACK)],
    ])

def delivery_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📲 ارسال در ربات", callback_data=CB_DELIVERY_BOT)],
        [InlineKeyboardButton("📧 ارسال از طریق ایمیل", callback_data=CB_DELIVERY_EMAIL)],
        [InlineKeyboardButton("↩️ بازگشت به حساب کاربری", callback_data=CB_MENU_ACCOUNT)],
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
    cfg_labels = {str(c.get("id")): (c.get("label") or c.get("id")) for c in vpn_load_configs("iran")}
    rows = [
        [InlineKeyboardButton("🇮🇷 کانفیگ‌های ایران", callback_data=CB_VPN_IR)],
        [InlineKeyboardButton("🌍 کانفیگ‌های خارج", callback_data=CB_VPN_GLOBAL)],
    ]
    # وضعیت VPN هر اکانت IranPaper
    for acc in iranpaper_accounts_ordered():
        slot = acc.get("slot")
        vpn_id = acc.get("vpn_id")
        vpn_label = cfg_labels.get(str(vpn_id), vpn_id) if vpn_id else "—"
        rows.append([
            InlineKeyboardButton(f"🛡 VPN{slot}: {vpn_label}", callback_data=f"vpn:acc:{slot}")
        ])
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

    email = user.get("email")
    email_line = f"{htmlmod.escape(email)} ✅" if email else "— (تنظیم نشده، برای Unpaywall ضروری است)"
    delivery_method = user.get("delivery_method")
    delivery_name = "— (انتخاب نشده)" if delivery_method is None else ("ارسال در ربات" if delivery_method == "bot" else "ارسال از طریق ایمیل")
    warn = " ⚠️ (ایمیل تنظیم نشده)" if delivery_method == "email" and not email else ""

    token_short = _mask_token(user.get("user_token"))
    token_hint = " (از منوی «توکن افزونه» بگیرید)" if token_short == "—" else ""

    return (
        "👤 <ب>حساب کاربری</ب>\n"
        f"• نام کاربری: {uname}\n"
        f"• نقش: {role}\n"
        f"• ایمیل: {email_line}\n"
        f"• روش ارسال: {delivery_name}{warn}\n"
        f"• پلن: {plan_text}\n"
        f"• تعداد DOIهای ذخیره‌شده: {dois_count} 📚\n"
        f"• توکن افزونه: {token_short}{token_hint}"
    ).replace("<ب>", "<b>").replace("</ب>", "</b>")
def build_token_text(token: str) -> str:
    return (
        "🔑 <b>توکن افزونهٔ کروم</b>\n"
        "این توکن را در صفحهٔ Options افزونه وارد کنید. اگر گم شد یا شک داری کسی دارد استفاده می‌کند، توکن جدید بساز.\n\n"
        f"<b>توکن شما:</b>\n<code>{htmlmod.escape(token)}</code>"
    )
def build_doi_control_text(buffer_count: int) -> str:
    return (
        "📎 لطفاً DOI ارسال کنید.\n"
        "<b>هر چند تا خواستید DOI بفرستید؛ اما در هر پیام فقط یک DOI.</b>\n\n"
        f"🔢 تعداد DOIهای موقت: <b>{buffer_count}</b>\n"
        "وقتی تمام شد، دکمهٔ «پایان ارسال DOIها» را بزنید.\n"
        "برای خروج: /cancel"
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

    if update.message:
        await update.message.reply_text("کیبورد کناری حذف شد ✅", reply_markup=ReplyKeyboardRemove())
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
    s = (text or "").strip()
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


async def send_account_view_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = ensure_user(update.effective_user.id, update.effective_user.username)
    text = build_account_text(user, is_admin(update))
    await context.bot.send_message(update.effective_chat.id, text, parse_mode=ParseMode.HTML, reply_markup=account_menu_kb())




# ---- حساب کاربری و توکن
async def on_menu_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer()
    user = ensure_user(update.effective_user.id, update.effective_user.username)
    text = build_account_text(user, is_admin(update))
    await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=account_menu_kb())

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
    context.user_data["pending_email"] = email
    await update.message.reply_text(_email_code_rules_text(), parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb())
    return WAITING_FOR_EMAIL_CODE

async def receive_email_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    code = (update.message.text or "").strip()
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

    user = ensure_user(update.effective_user.id, update.effective_user.username)
    db_set_email(user["user_id"], pending_email)
    save_user_email_code(user["user_id"], update.effective_user.username, pending_email, code)
    context.user_data.pop("pending_email", None)

    await update.message.reply_text("✅ ایمیل و رمز با موفقیت ثبت شد.", reply_markup=back_to_menu_kb())
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
    text = build_account_text(db_get_user(user["user_id"]), is_admin(update))
    await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=account_menu_kb())

async def set_delivery_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer("روش ارسال: ایمیل")
    user = ensure_user(update.effective_user.id, update.effective_user.username)
    db_set_delivery(user["user_id"], "email")
    text = build_account_text(db_get_user(user["user_id"]), is_admin(update))
    await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=account_menu_kb())

# ---- شارژ/پلن‌ها
def compute_price_with_delivery(user: Dict[str, Any], base_price: int, plan_type: str) -> Tuple[int, bool]:
    add = False
    if plan_type.startswith("normal") and user.get("delivery_method") == "email":
        base_price += CFG.EXTRA_EMAIL_DELIVERY_FEE; add = True
    return base_price, add

async def on_menu_topup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer()
    extra = f"{CFG.EXTRA_EMAIL_DELIVERY_FEE:,}".replace(",", "٬")
    text = ("💳 <b>شارژ حساب کاربری</b>\n\n"
            "🧰 <b>پلن معمولی</b>\n"
            "• ۴۰ مقاله — ۲۴۰٬۰۰۰ تومان\n"
            "• ۱۰۰ مقاله — ۵۰۰٬۰۰۰ تومان\n"
            f"⏳ اعتبار: ۱ ساله | (ارسال از طریق ایمیل: +{extra} تومان)\n\n"
            "⭐️ <b>پلن اشتراک پریمیوم</b>\n"
            "• دانلود نامحدود (محدودیت روزانه ۱۵ مقاله)\n"
            "قیمت‌ها:\n"
            "• ۱ ماه — ۲۴۰٬۰۰۰ تومان\n"
            "• ۳ ماه — ۶۰۰٬۰۰۰ تومان\n"
            "⏳ اعتبار: بر اساس مدت اشتراک")
    await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=topup_menu_keyboard())

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
    text = ("⭐️ <b>پلن اشتراکی پریمیوم</b>\n"
            "مدت اشتراک را انتخاب کنید:\n"
            "• ۱ ماه — ۲۴۰٬۰۰۰ تومان\n"
            "• ۳ ماه — ۶۰۰٬۰۰۰ تومان\n"
            "محدودیت: حداکثر ۱۵ مقاله در روز")
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
    set_pending_plan(context.user_data, "⭐️ پریمیوم — ۱ ماه (۱۵ مقاله در روز)", "premium_1m", 240000, "۱۵ مقاله/روز")
    await q.edit_message_text("⭐️ <b>پلن پریمیوم (۱ ماه)</b>\n"
                              "• قیمت: ۲۴۰٬۰۰۰ تومان\n"
                              "• محدودیت: ۱۵ مقاله در روز\n\n"
                              "اگر موافقی، «تایید پلن» را بزن.",
                              parse_mode=ParseMode.HTML, reply_markup=confirm_keyboard())

async def on_select_premium_3m(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer()
    context.user_data["user_id"] = update.effective_user.id
    set_pending_plan(context.user_data, "⭐️ پریمیوم — ۳ ماه (۱۵ مقاله در روز)", "premium_3m", 600000, "۱۵ مقاله/روز")
    await q.edit_message_text("⭐️ <b>پلن پریمیوم (۳ ماه)</b>\n"
                              "• قیمت: ۶۰۰٬۰۰۰ تومان\n"
                              "• محدودیت: ۱۵ مقاله در روز\n\n"
                              "اگر موافقی، «تایید پلن» را بزن.",
                              parse_mode=ParseMode.HTML, reply_markup=confirm_keyboard())

async def on_confirm_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query; await q.answer()
    uid = update.effective_user.id
    pending = context.user_data.get("pending_plan")
    if not pending:
        await q.edit_message_text("❗️ پلنی برای تایید انتخاب نشده است.", reply_markup=topup_menu_keyboard())
        return
    user = db_get_user(uid)
    if not user.get("delivery_chosen"):
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
            "برای پرداخت روی دکمهٔ زیر بزنید:")
    await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=payment_keyboard(CFG.ZARINPAL_URL))

# ---- DOI Conversation
async def enter_doi_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ensure_user(update.effective_user.id, update.effective_user.username)
    q = update.callback_query; await q.answer()
    context.user_data["doi_buffer"] = []
    sent = await q.edit_message_text(build_doi_control_text(0), reply_markup=doi_control_kb(), parse_mode=ParseMode.HTML)
    context.user_data["doi_ctrl"] = (sent.chat_id, sent.message_id)
    return WAITING_FOR_DOI

async def _update_doi_ctrl(context: ContextTypes.DEFAULT_TYPE, chat_id: int, msg_id: int, count: int) -> None:
    try:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=msg_id,
            text=build_doi_control_text(count), parse_mode=ParseMode.HTML, reply_markup=doi_control_kb())
    except Exception as e:
        logger.warning("ctrl_update_fail: %s", e)

async def receive_doi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = (update.message.text or "").strip()
    found = DOI_REGEX.findall(text)
    if len(found) != 1:
        await update.message.reply_text("❗️ هر پیام فقط یک DOI معتبر بفرستید.", reply_markup=doi_control_kb())
        return WAITING_FOR_DOI
    doi = normalize_doi(found[0])
    if not doi:
        await update.message.reply_text("❗️ DOI معتبر نیست.", reply_markup=doi_control_kb())
        return WAITING_FOR_DOI
    buf: List[str] = context.user_data.get("doi_buffer", [])
    if doi in buf:                                  # ← جلوگیری از تکرار
        await update.message.reply_text(
            "این DOI قبلاً ثبت شده است. مورد دیگری بفرستید.",
            reply_markup=doi_control_kb()
        )
        return WAITING_FOR_DOI
    buf.append(doi); context.user_data["doi_buffer"] = buf
    await update.message.reply_text(f"✅ DOI ثبت شد:\n{doi}")
    ctrl = context.user_data.get("doi_ctrl")
    if ctrl:
        await _update_doi_ctrl(context, ctrl[0], ctrl[1], len(buf))
    return WAITING_FOR_DOI

async def finish_doi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    user = ensure_user(update.effective_user.id, update.effective_user.username)
    buf: List[str] = context.user_data.get("doi_buffer", [])
    if not buf:
        await q.edit_message_text("هیچ DOI موجود نیست. ابتدا DOI بفرستید.", reply_markup=doi_control_kb())
        return WAITING_FOR_DOI

    inserted = db_add_dois(user["user_id"], buf)
    context.user_data["doi_buffer"] = []; context.user_data.pop("doi_ctrl", None)

    await q.edit_message_text(
        f"✅ ارسال DOIها تمام شد.\nتعداد ذخیره‌شده: <b>{inserted}</b>\n\n"
        "🔎 در حال واکشی عنوان/سال، تعیین دسته‌بندی با هوش مصنوعی، و تلاش برای ارسال PDF…",
        parse_mode=ParseMode.HTML,
        reply_markup=back_to_menu_kb()
    )

    chat_id = update.effective_chat.id
    # پردازش موازی در پس‌زمینه به‌همراه رهگیری خطا
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
                        "❗️ پردازش DOI با خطا مواجه شد. دوباره تلاش کنید یا با پشتیبانی تماس بگیرید."
                    )

            asyncio.create_task(_notify_failure())

    task.add_done_callback(_on_done)

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message:
        await update.message.reply_text("عملیات لغو شد. ✅", reply_markup=ReplyKeyboardRemove())
    context.user_data["doi_buffer"] = []; context.user_data.pop("doi_ctrl", None)
    context.user_data.pop("pending_email", None)
    context.user_data.pop("charge", None)
    await show_main_menu(update, context, edit=False)
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

    text = (update.message.text or "").strip()
    found = DOI_REGEX.findall(text)

    # --- ۱) اگر دقیقاً یک DOI پیدا شد → کانورسیشن را خودکار شروع کن
    if len(found) == 1:
        doi = normalize_doi(found[0])
        if not doi:
            return
        # اگر قبلاً در همین چت کنترل DOI داریم، از همان استفاده کن
        ctrl = context.user_data.get("doi_ctrl")

        # اگر کنترل موجود نیست، پیام کنترل جدید بفرست
        if not ctrl:
            sent = await update.message.reply_text(
                build_doi_control_text(0),
                reply_markup=doi_control_kb(),
                parse_mode=ParseMode.HTML
            )
            context.user_data["doi_ctrl"] = (sent.chat_id, sent.message_id)
            context.user_data["doi_buffer"] = []

        # DOI را مثل receive_doi پردازش کن
        buf: List[str] = context.user_data.get("doi_buffer", [])
        if doi in buf:          # ← تکراری است؛ کاری نکن
            return

        buf.append(doi)
        context.user_data["doi_buffer"] = buf


        # آپدیت شمارنده در پیام کنترل
        ctrl = context.user_data.get("doi_ctrl")
        if ctrl:
            await _update_doi_ctrl(context, ctrl[0], ctrl[1], len(buf))

        # پیام جداگانه لازم نیست؛ لاگ برای دیباگ
        logger.debug("auto_doi_add | uid=%s doi=%s", update.effective_user.id, doi)
        return  # 👈 هیچ پیام دیگری نفرست

    # --- ۲) متن نامرتبط → یادآوری ساده
    await update.message.reply_text(
        "از دکمه‌های زیر همین پیام استفاده کن. 🙂",
        reply_markup=ReplyKeyboardRemove()
    )



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
 
    
    email_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(on_account_email_entry, pattern=f"^{CB_ACCOUNT_EMAIL}$")],
        states={
            WAITING_FOR_EMAIL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_email),
                CallbackQueryHandler(on_menu_root, pattern=f"^{CB_MENU_ROOT}$"),
            ],
            WAITING_FOR_EMAIL_CODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_email_code),
                CallbackQueryHandler(on_menu_root, pattern=f"^{CB_MENU_ROOT}$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="email_conversation",
        persistent=False,
        block=True,
    )

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

    app.add_handler(dl_conv, group=0)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("cancel", cancel))

    app.add_handler(doi_conv,   group=0)
    app.add_handler(email_conv, group=0)
    app.add_handler(scihub_conv, group=0)
    app.add_handler(vpn_conv, group=0)
    app.add_handler(charge_conv, group=0)
    app.add_handler(CallbackQueryHandler(on_links_download,    pattern=f"^{CB_LINKS_DOWNLOAD}$"))
    app.add_handler(CallbackQueryHandler(on_dl_edit_menu,      pattern=f"^{CB_DL_EDIT}$"))
    app.add_handler(CallbackQueryHandler(on_dl_backup_toggle,  pattern=f"^{CB_DL_BACKUP}$"))
    app.add_handler(CallbackQueryHandler(dl_backup_toggle_item, pattern=r"^dl:toggle:\d+$"))


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
    app.add_handler(CallbackQueryHandler(on_back, pattern=f"^{CB_BACK}$"))
    app.add_handler(CallbackQueryHandler(on_back_root, pattern=f"^{CB_BACK_ROOT}$"))
    app.add_handler(CallbackQueryHandler(on_menu_root, pattern=f"^{CB_MENU_ROOT}$"))
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
