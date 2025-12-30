# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from downloadmain import (
    CFG,
    db_cleanup_download_links,
    db_get_download_link,
    db_get_setting,
    db_init,
    db_mark_download_link_used,
    db_set_setting,
)

ADMIN_KEY = "DOWNLOAD_BOT_ADMINS"
BLOCK_KEY = "DOWNLOAD_BOT_BLOCKED"
CONFIG_KEY = "DOWNLOAD_BOT_CONFIG"
CHANNELS_KEY = "DOWNLOAD_BOT_REQUIRED_CHANNELS"
CHANNEL_LINKS_KEY = "DOWNLOAD_BOT_REQUIRED_CHANNEL_LINKS"

_BOT_ID: Optional[int] = None

ADMIN_ACTION_KEY = "admin_action"

CB_ADMIN_MENU = "admin:menu"
CB_ADMIN_ADD = "admin:add"
CB_ADMIN_USERS = "admin:users"
CB_ADMIN_SETTINGS = "admin:settings"
CB_ADMIN_BLOCK = "admin:block"
CB_ADMIN_UNBLOCK = "admin:unblock"
CB_ADMIN_LIST_BLOCKED = "admin:list_blocked"
CB_ADMIN_SET_DELAY = "admin:set_delay"
CB_ADMIN_SET_CHANNELS = "admin:set_channels"
CB_ADMIN_SET_CHANNEL_LINKS = "admin:set_channel_links"
CB_ADMIN_TOGGLE_COUNTDOWN = "admin:toggle_countdown"
CB_ADMIN_TOGGLE_DELETE_FILE = "admin:toggle_delete_file"
CB_ADMIN_TOGGLE_REQUIRE_SAME = "admin:toggle_require_same"
CB_ADMIN_TOGGLE_ENFORCE_CHANNELS = "admin:toggle_enforce_channels"

CB_JOIN_CHECK_PREFIX = "join_check:"


def _parse_csv(raw: str) -> List[str]:
    if not raw:
        return []
    parts = re.split(r"[,\n]+", raw)
    return [p.strip() for p in parts if p.strip()]


def _required_channels_raw() -> str:
    raw = db_get_setting(CHANNELS_KEY)
    if raw is None:
        return CFG.DOWNLOAD_REQUIRED_CHANNELS
    return raw


def _required_channel_links_raw() -> str:
    raw = db_get_setting(CHANNEL_LINKS_KEY)
    if raw is None:
        return CFG.DOWNLOAD_REQUIRED_CHANNEL_LINKS
    return raw


def _required_channels() -> List[str]:
    return _parse_csv(_required_channels_raw())


def _required_channel_links() -> List[str]:
    return _parse_csv(_required_channel_links_raw())


def _channel_ref_for_check(item: str) -> Optional[Any]:
    val = item.strip()
    if not val:
        return None
    if val.startswith("https://t.me/"):
        val = val.split("https://t.me/", 1)[1].strip("/")
    if val.startswith("@"):
        return val
    if val.startswith("-") or val.isdigit():
        try:
            return int(val)
        except Exception:
            return val
    return f"@{val}"


def _channel_link_for_display(item: str) -> str:
    if item.startswith("http://") or item.startswith("https://"):
        return item
    name = item.lstrip("@").strip()
    return f"https://t.me/{name}"


def _normalize_join_link(value: str) -> str:
    val = (value or "").strip()
    if not val:
        return ""
    if val.startswith("http://") or val.startswith("https://"):
        return val
    if val.startswith("t.me/") or val.startswith("telegram.me/"):
        return f"https://{val}"
    if val.startswith("@"):
        return f"https://t.me/{val.lstrip('@')}"
    return f"https://t.me/{val}"

def _is_numeric_channel(value: str) -> bool:
    return bool(re.fullmatch(r"-?\d+", value.strip()))


def _has_numeric_channels(values: List[str]) -> bool:
    return any(_is_numeric_channel(v) for v in values)


def _parse_numeric_channels(raw: str) -> tuple[List[str], List[str]]:
    parts = _parse_csv(raw)
    valid: List[str] = []
    invalid: List[str] = []
    for item in parts:
        if _is_numeric_channel(item):
            valid.append(item.strip())
        else:
            invalid.append(item.strip())
    return valid, invalid


async def _get_bot_id(bot) -> Optional[int]:
    global _BOT_ID
    if _BOT_ID:
        return _BOT_ID
    try:
        me = await bot.get_me()
        _BOT_ID = int(me.id)
        return _BOT_ID
    except Exception:
        return None


async def _bot_is_admin_in_channel(bot, ref: Any) -> bool:
    bot_id = await _get_bot_id(bot)
    if not bot_id:
        return False
    try:
        member = await bot.get_chat_member(ref, bot_id)
        status = str(getattr(member, "status", "")).lower()
        return status in {"administrator", "creator"}
    except Exception:
        return False


def _load_id_list(key: str) -> List[int]:
    raw = db_get_setting(key) or "[]"
    try:
        data = json.loads(raw)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out: List[int] = []
    for item in data:
        try:
            out.append(int(item))
        except Exception:
            continue
    return out


def _save_id_list(key: str, values: List[int]) -> None:
    payload = json.dumps(sorted(set(values)))
    db_set_setting(key, payload)


def _get_admins() -> Set[int]:
    admins = set(_load_id_list(ADMIN_KEY))
    if CFG.ADMIN_USER_ID:
        admins.add(int(CFG.ADMIN_USER_ID))
    return admins


def _is_admin(user_id: Optional[int]) -> bool:
    if not user_id:
        return False
    return int(user_id) in _get_admins()


def _get_blocked() -> Set[int]:
    return set(_load_id_list(BLOCK_KEY))


def _load_config() -> Dict[str, Any]:
    channels = _required_channels()
    cfg = {
        "delete_after_s": int(CFG.DOWNLOAD_DELETE_DELAY_S),
        "countdown": bool(CFG.DOWNLOAD_COUNTDOWN_ENABLED),
        "delete_file": bool(CFG.DOWNLOAD_LINK_DELETE_ON_SEND),
        "require_same_user": bool(CFG.DOWNLOAD_LINK_REQUIRE_SAME_USER),
        "enforce_channels": bool(CFG.DOWNLOAD_CHANNELS_ENFORCED and bool(channels)),
    }
    raw = db_get_setting(CONFIG_KEY)
    if not raw:
        return cfg
    try:
        data = json.loads(raw)
    except Exception:
        return cfg
    if isinstance(data, dict):
        cfg.update({k: data.get(k, v) for k, v in cfg.items()})
    return cfg


def _save_config(cfg: Dict[str, Any]) -> None:
    payload = json.dumps(cfg, ensure_ascii=False)
    db_set_setting(CONFIG_KEY, payload)


def _admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("افزودن ادمین", callback_data=CB_ADMIN_ADD)],
            [InlineKeyboardButton("مدیریت کاربران", callback_data=CB_ADMIN_USERS)],
            [InlineKeyboardButton("تنظیمات ربات", callback_data=CB_ADMIN_SETTINGS)],
        ]
    )


def _admin_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("بازگشت به منوی اصلی", callback_data=CB_ADMIN_MENU)]]
    )


def _admin_users_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("مسدود کردن کاربر", callback_data=CB_ADMIN_BLOCK)],
            [InlineKeyboardButton("رفع مسدودیت کاربر", callback_data=CB_ADMIN_UNBLOCK)],
            [InlineKeyboardButton("لیست کاربران مسدود", callback_data=CB_ADMIN_LIST_BLOCKED)],
            [InlineKeyboardButton("بازگشت به منوی اصلی", callback_data=CB_ADMIN_MENU)],
        ]
    )


def _admin_settings_kb(cfg: Dict[str, Any]) -> InlineKeyboardMarkup:
    countdown = "روشن" if cfg.get("countdown") else "خاموش"
    delete_file = "روشن" if cfg.get("delete_file") else "خاموش"
    require_same = "روشن" if cfg.get("require_same_user") else "خاموش"
    enforce = "روشن" if cfg.get("enforce_channels") else "خاموش"
    delay = int(cfg.get("delete_after_s") or 0)
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"شمارش معکوس: {countdown}", callback_data=CB_ADMIN_TOGGLE_COUNTDOWN)],
            [InlineKeyboardButton(f"حذف فایل بعد ارسال: {delete_file}", callback_data=CB_ADMIN_TOGGLE_DELETE_FILE)],
            [InlineKeyboardButton(f"الزام کاربر اصلی: {require_same}", callback_data=CB_ADMIN_TOGGLE_REQUIRE_SAME)],
            [InlineKeyboardButton(f"الزام عضویت کانال‌ها: {enforce}", callback_data=CB_ADMIN_TOGGLE_ENFORCE_CHANNELS)],
            [InlineKeyboardButton("تنظیم کانال‌های اجباری", callback_data=CB_ADMIN_SET_CHANNELS)],
            [InlineKeyboardButton("تنظیم لینک کانال‌ها", callback_data=CB_ADMIN_SET_CHANNEL_LINKS)],
            [InlineKeyboardButton(f"زمان حذف: {delay} ثانیه", callback_data=CB_ADMIN_SET_DELAY)],
            [InlineKeyboardButton("بازگشت به منوی اصلی", callback_data=CB_ADMIN_MENU)],
        ]
    )


def _join_check_kb(token: str, join_links: Optional[List[str]] = None) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    links = join_links or []
    total = len(links)
    base_title = "عضویت در کانال"
    for idx, link in enumerate(links, start=1):
        label = base_title if total == 1 else f"{base_title} {idx}"
        rows.append([InlineKeyboardButton(label, url=link)])
    rows.append([InlineKeyboardButton("بررسی عضویت ✅", callback_data=f"{CB_JOIN_CHECK_PREFIX}{token}")])
    return InlineKeyboardMarkup(rows)


async def _check_required_channels(bot, user_id: int) -> tuple[List[str], List[str]]:
    missing: List[str] = []
    not_admin: List[str] = []
    channels = _required_channels()
    for item in channels:
        ref = _channel_ref_for_check(item)
        if not ref:
            continue
        if not await _bot_is_admin_in_channel(bot, ref):
            not_admin.append(item)
            continue
        try:
            member = await bot.get_chat_member(ref, user_id)
            status = str(getattr(member, "status", "")).lower()
            if status in {"left", "kicked"}:
                missing.append(item)
        except BadRequest:
            missing.append(item)
        except Exception:
            missing.append(item)
    return missing, not_admin


async def _send_document_with_retry(
    bot,
    chat_id: int,
    file_path: Path,
    caption: str,
    *,
    tries: int = 3,
    timeout: int = 240,
):
    for i in range(1, tries + 1):
        try:
            await bot.send_chat_action(chat_id=chat_id, action="upload_document")
            with open(file_path, "rb") as f:
                msg = await bot.send_document(
                    chat_id,
                    document=f,
                    filename=file_path.name,
                    caption=caption,
                    read_timeout=timeout,
                )
            return msg
        except Exception:
            await asyncio.sleep(2 ** i)
    return None


async def _countdown_and_cleanup(
    bot,
    chat_id: int,
    doc_message_id: int,
    countdown_message_id: Optional[int],
    file_path: Path,
    *,
    delay_s: int,
    delete_file: bool,
):
    remaining = max(0, int(delay_s))
    while remaining > 0:
        if countdown_message_id:
            try:
                await bot.edit_message_text(
                    f"حذف در {remaining} ثانیه...",
                    chat_id=chat_id,
                    message_id=countdown_message_id,
                )
            except Exception:
                pass
        await asyncio.sleep(1)
        remaining -= 1
    try:
        await bot.delete_message(chat_id=chat_id, message_id=doc_message_id)
    except Exception:
        pass
    if countdown_message_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=countdown_message_id)
        except Exception:
            pass
    if delete_file:
        try:
            file_path.unlink()
        except Exception:
            pass


async def _deliver_file(update: Update, context: ContextTypes.DEFAULT_TYPE, token: str) -> None:
    rec = db_get_download_link(token)
    if not rec:
        await update.effective_message.reply_text("این لینک دانلود نامعتبر یا منقضی است.")
        return

    now = int(time.time())
    expires_at = int(rec.get("expires_at") or 0)
    if expires_at and expires_at < now:
        await update.effective_message.reply_text("این لینک دانلود منقضی شده است.")
        return

    if rec.get("used_at"):
        await update.effective_message.reply_text("این لینک قبلا استفاده شده است.")
        return

    cfg = _load_config()
    if cfg.get("require_same_user") and not _is_admin(update.effective_user.id if update.effective_user else None):
        owner_id = int(rec.get("user_id") or 0)
        if owner_id and update.effective_user and int(update.effective_user.id) != owner_id:
            await update.effective_message.reply_text("این لینک برای حساب شما معتبر نیست.")
            return

    fpath = Path(str(rec.get("file_path") or ""))
    if not fpath.exists():
        await update.effective_message.reply_text("فایل روی سرور پیدا نشد.")
        return

    msg = await _send_document_with_retry(
        context.bot,
        update.effective_chat.id,
        fpath,
        caption="فایل شما آماده است.",
        tries=3,
        timeout=240,
    )
    if not msg:
        await update.effective_message.reply_text("ارسال فایل ناموفق بود. لطفا دوباره تلاش کنید.")
        return

    db_mark_download_link_used(token, used_by=update.effective_user.id if update.effective_user else None)

    delete_after_s = int(cfg.get("delete_after_s") or 0)
    if delete_after_s > 0:
        countdown_id = None
        if cfg.get("countdown"):
            try:
                m = await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"حذف در {delete_after_s} ثانیه...",
                )
                countdown_id = m.message_id
            except Exception:
                countdown_id = None
        asyncio.create_task(
            _countdown_and_cleanup(
                context.bot,
                update.effective_chat.id,
                msg.message_id,
                countdown_id,
                fpath,
                delay_s=delete_after_s,
                delete_file=bool(cfg.get("delete_file")),
            )
        )


async def _send_join_prompt(update: Update, token: str, missing: List[str]) -> None:
    text, join_links = _build_join_prompt(missing)
    await update.effective_message.reply_text(
        text,
        reply_markup=_join_check_kb(token, join_links),
    )


def _build_join_prompt(missing: List[str]) -> tuple[str, List[str]]:
    channels = _required_channels()
    links = _required_channel_links()
    links_valid = bool(links) and len(links) == len(channels)

    join_links: List[str] = []
    if links:
        if links_valid:
            missing_set = set(missing)
            join_links = [link for ch, link in zip(channels, links) if ch in missing_set]
            if not join_links:
                join_links = list(links)
        else:
            join_links = list(links)
    else:
        join_links = [_channel_link_for_display(ch) for ch in missing if not _is_numeric_channel(ch)]

    seen: Set[str] = set()
    cleaned: List[str] = []
    for link in join_links:
        normalized = _normalize_join_link(link)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(normalized)

    lines = ["لطفا ابتدا عضو کانال‌های زیر شوید:"]
    if not links_valid and _has_numeric_channels(missing):
        lines.append("برای کانال‌های خصوصی، لینک عضویت را از منوی مدیریت تنظیم کنید.")
    lines.append("بعد از عضویت، دکمه زیر را بزنید.")
    return "\n".join(lines), cleaned


async def _edit_or_alert(
    q,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup],
    *,
    alert_text: str,
) -> None:
    try:
        await q.edit_message_text(text, reply_markup=reply_markup)
        await q.answer()
    except BadRequest:
        await q.answer(alert_text, show_alert=True)
    except Exception:
        await q.answer(alert_text, show_alert=True)


async def _send_admin_required_prompt(update: Update, channels: List[str]) -> None:
    lines = ["برای بررسی عضویت، ربات باید ادمین این کانال‌ها باشد:"]
    for ch in channels:
        lines.append(f"- {ch}")
    lines.append("ابتدا ربات را ادمین کنید و دوباره تلاش کنید.")
    await update.effective_message.reply_text("\n".join(lines))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    user_id = update.effective_user.id if update.effective_user else None
    if user_id and (user_id in _get_blocked()) and not _is_admin(user_id):
        await update.message.reply_text("دسترسی ندارید.")
        return

    token = _parse_start_token(context)
    if not token:
        if _is_admin(user_id):
            await update.message.reply_text("منوی مدیریت:", reply_markup=_admin_menu_kb())
        else:
            await update.message.reply_text("لطفا لینک دانلود را از ربات اصلی باز کنید و /start بزنید.")
        return

    cfg = _load_config()
    if cfg.get("enforce_channels"):
        channels = _required_channels()
        if channels and not _is_admin(user_id):
            missing, not_admin = await _check_required_channels(context.bot, int(user_id))
            if not_admin:
                await _send_admin_required_prompt(update, not_admin)
                return
            if missing:
                await _send_join_prompt(update, token, missing)
                return

    await _deliver_file(update, context, token)
    if _is_admin(user_id):
        await update.message.reply_text("منوی مدیریت:", reply_markup=_admin_menu_kb())


def _parse_start_token(context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    args = getattr(context, "args", None) or []
    if not args:
        return None
    token = str(args[0] or "").strip()
    return token or None


async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    user_id = update.effective_user.id if update.effective_user else None
    if not _is_admin(user_id):
        await update.message.reply_text(f"دسترسی ندارید. شناسه عددی شما: {user_id}")
        return
    context.user_data.pop(ADMIN_ACTION_KEY, None)
    await update.message.reply_text("منوی مدیریت:", reply_markup=_admin_menu_kb())


async def on_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    await q.answer()

    user_id = q.from_user.id if q.from_user else None
    if not _is_admin(user_id):
        await q.edit_message_text("دسترسی ندارید.")
        return

    data = q.data or ""
    context.user_data.pop(ADMIN_ACTION_KEY, None)

    if data == CB_ADMIN_MENU:
        await q.edit_message_text("منوی مدیریت:", reply_markup=_admin_menu_kb())
        return
    if data == CB_ADMIN_ADD:
        context.user_data[ADMIN_ACTION_KEY] = "add_admin"
        await q.edit_message_text(
            "شناسه عددی تلگرام را برای افزودن ادمین ارسال کنید.",
            reply_markup=_admin_back_kb(),
        )
        return
    if data == CB_ADMIN_USERS:
        await q.edit_message_text("مدیریت کاربران:", reply_markup=_admin_users_kb())
        return
    if data == CB_ADMIN_BLOCK:
        context.user_data[ADMIN_ACTION_KEY] = "block_user"
        await q.edit_message_text(
            "شناسه عددی تلگرام را برای مسدود کردن ارسال کنید.",
            reply_markup=_admin_back_kb(),
        )
        return
    if data == CB_ADMIN_UNBLOCK:
        context.user_data[ADMIN_ACTION_KEY] = "unblock_user"
        await q.edit_message_text(
            "شناسه عددی تلگرام را برای رفع مسدودیت ارسال کنید.",
            reply_markup=_admin_back_kb(),
        )
        return
    if data == CB_ADMIN_LIST_BLOCKED:
        blocked = sorted(_get_blocked())
        text = "کاربران مسدود:\n" + ("\n".join(str(x) for x in blocked) if blocked else "هیچ‌کدام")
        await q.edit_message_text(text, reply_markup=_admin_users_kb())
        return
    if data == CB_ADMIN_SETTINGS:
        cfg = _load_config()
        await q.edit_message_text("تنظیمات ربات:", reply_markup=_admin_settings_kb(cfg))
        return
    if data == CB_ADMIN_SET_DELAY:
        context.user_data[ADMIN_ACTION_KEY] = "set_delay"
        await q.edit_message_text(
            "زمان حذف را به ثانیه ارسال کنید (مثلا 60).",
            reply_markup=_admin_back_kb(),
        )
        return
    if data == CB_ADMIN_SET_CHANNELS:
        context.user_data[ADMIN_ACTION_KEY] = "set_channels"
        await q.edit_message_text(
            "شناسه عددی کانال‌ها را با کاما یا خط جدید ارسال کنید.\n"
            "مثال: -1001234567890\n"
            "برای حذف همه، کلمه clear را ارسال کنید.",
            reply_markup=_admin_back_kb(),
        )
        return
    if data == CB_ADMIN_SET_CHANNEL_LINKS:
        context.user_data[ADMIN_ACTION_KEY] = "set_channel_links"
        await q.edit_message_text(
            "لینک‌های عضویت کانال‌ها را با کاما یا خط جدید ارسال کنید.\n"
            "تعداد لینک‌ها باید با تعداد کانال‌ها برابر باشد.\n"
            "برای حذف همه، کلمه clear را ارسال کنید.",
            reply_markup=_admin_back_kb(),
        )
        return

    cfg = _load_config()
    if data == CB_ADMIN_TOGGLE_COUNTDOWN:
        cfg["countdown"] = not bool(cfg.get("countdown"))
    elif data == CB_ADMIN_TOGGLE_DELETE_FILE:
        cfg["delete_file"] = not bool(cfg.get("delete_file"))
    elif data == CB_ADMIN_TOGGLE_REQUIRE_SAME:
        cfg["require_same_user"] = not bool(cfg.get("require_same_user"))
    elif data == CB_ADMIN_TOGGLE_ENFORCE_CHANNELS:
        cfg["enforce_channels"] = not bool(cfg.get("enforce_channels"))
    else:
        return

    _save_config(cfg)
    await q.edit_message_text("تنظیمات ربات:", reply_markup=_admin_settings_kb(cfg))


async def on_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    user_id = update.effective_user.id if update.effective_user else None
    if not _is_admin(user_id):
        return

    action = context.user_data.get(ADMIN_ACTION_KEY)
    if not action:
        return

    text = (update.message.text or "").strip()
    context.user_data.pop(ADMIN_ACTION_KEY, None)

    if not text:
        await update.message.reply_text("ورودی خالی است.", reply_markup=_admin_back_kb())
        return

    if action == "add_admin":
        try:
            new_id = int(text)
        except Exception:
            await update.message.reply_text("شناسه عددی نامعتبر است.", reply_markup=_admin_back_kb())
            return
        admins = _get_admins()
        admins.add(new_id)
        _save_id_list(ADMIN_KEY, list(admins))
        await update.message.reply_text(f"ادمین اضافه شد: {new_id}", reply_markup=_admin_menu_kb())
        return

    if action in {"block_user", "unblock_user"}:
        try:
            target_id = int(text)
        except Exception:
            await update.message.reply_text("شناسه عددی نامعتبر است.", reply_markup=_admin_back_kb())
            return
        blocked = _get_blocked()
        if action == "block_user":
            blocked.add(target_id)
            _save_id_list(BLOCK_KEY, list(blocked))
            await update.message.reply_text(f"کاربر مسدود شد: {target_id}", reply_markup=_admin_users_kb())
            return
        if target_id in blocked:
            blocked.remove(target_id)
            _save_id_list(BLOCK_KEY, list(blocked))
        await update.message.reply_text(f"مسدودیت کاربر برداشته شد: {target_id}", reply_markup=_admin_users_kb())
        return

    if action == "set_channels":
        if text.lower() == "clear":
            db_set_setting(CHANNELS_KEY, "")
            await update.message.reply_text("لیست کانال‌های اجباری پاک شد.", reply_markup=_admin_settings_kb(_load_config()))
            return
        valid, invalid = _parse_numeric_channels(text)
        if invalid:
            await update.message.reply_text(
                "فقط شناسه عددی کانال مجاز است.\n"
                f"نامعتبرها: {', '.join(invalid)}",
                reply_markup=_admin_back_kb(),
            )
            return
        db_set_setting(CHANNELS_KEY, ",".join(valid))
        await update.message.reply_text(
            f"کانال‌های اجباری تنظیم شد: {', '.join(valid) if valid else 'خالی'}",
            reply_markup=_admin_settings_kb(_load_config()),
        )
        return

    if action == "set_channel_links":
        if text.lower() == "clear":
            db_set_setting(CHANNEL_LINKS_KEY, "")
            await update.message.reply_text("لیست لینک کانال‌ها پاک شد.", reply_markup=_admin_settings_kb(_load_config()))
            return
        links = _parse_csv(text)
        db_set_setting(CHANNEL_LINKS_KEY, ",".join(links))
        warn = ""
        channels = _required_channels()
        if channels and len(links) != len(channels):
            warn = f"\nهشدار: {len(links)} لینک برای {len(channels)} کانال ثبت شد."
        await update.message.reply_text(
            "لینک کانال‌ها تنظیم شد." + warn,
            reply_markup=_admin_settings_kb(_load_config()),
        )
        return

    if action == "set_delay":
        try:
            val = int(text)
        except Exception:
            await update.message.reply_text("عدد نامعتبر است.", reply_markup=_admin_back_kb())
            return
        val = max(0, val)
        cfg = _load_config()
        cfg["delete_after_s"] = val
        _save_config(cfg)
        await update.message.reply_text(f"زمان حذف روی {val} ثانیه تنظیم شد.", reply_markup=_admin_settings_kb(cfg))
        return


async def on_join_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q:
        return
    token = (q.data or "")[len(CB_JOIN_CHECK_PREFIX):].strip()
    if not token:
        await q.answer()
        return
    user_id = q.from_user.id if q.from_user else None
    cfg = _load_config()
    if cfg.get("enforce_channels") and user_id and not _is_admin(user_id):
        missing, not_admin = await _check_required_channels(context.bot, int(user_id))
        if not_admin:
            await _edit_or_alert(
                q,
                "ربات باید در کانال‌های تعیین‌شده ادمین باشد.",
                None,
                alert_text="ابتدا ربات را ادمین کنید و دوباره تلاش کنید.",
            )
            return
        if missing:
            text, join_links = _build_join_prompt(missing)
            await _edit_or_alert(
                q,
                text,
                _join_check_kb(token, join_links),
                alert_text="هنوز عضو همه کانال‌های لازم نیستید.",
            )
            return
    await q.answer()
    await _deliver_file(update, context, token)


async def _cleanup_job(context: CallbackContext) -> None:
    try:
        db_cleanup_download_links()
    except Exception:
        pass


def main() -> None:
    if not CFG.DOWNLOAD_BOT_TOKEN:
        raise RuntimeError("DOWNLOAD_BOT_TOKEN env var is missing")

    db_init()
    app = Application.builder().token(CFG.DOWNLOAD_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_menu))
    app.add_handler(CallbackQueryHandler(on_join_check, pattern=f"^{CB_JOIN_CHECK_PREFIX}"))
    app.add_handler(CallbackQueryHandler(on_admin_callback, pattern="^admin:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_admin_input))

    if app.job_queue:
        app.job_queue.run_repeating(_cleanup_job, interval=3600, first=120, name="download_cleanup")

    app.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=True)


if __name__ == "__main__":
    main()
