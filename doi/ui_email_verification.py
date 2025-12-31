# -*- coding: utf-8 -*-
from __future__ import annotations

import html as htmlmod
import logging
import re
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    ConversationHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from downloadmain import db_get_user
from downloadmain import request_email_verification as _request_email_verification
from downloadmain import verify_email_code as _verify_email_code

logger = logging.getLogger(__name__)

# Callback data (match mainbot handlers where needed)
CB_MENU_ROOT = "menu:root"
CB_ACCOUNT_DELIVERY = "account:delivery"

CB_EMAIL_VERIFY = "email:verify"
CB_EMAIL_CHANGE = "email:change"
CB_EMAIL_RESEND = "email:resend"
CB_EMAIL_BACK = "email:back"
CB_EMAIL_COOLDOWN = "email:cooldown"

WAITING_EMAIL: int = 1
WAITING_CODE: int = 2

EMAIL_REGEX = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def get_user_status(user_id: int) -> Dict[str, Any]:
    user = db_get_user(int(user_id))
    return {
        "email": user.get("email"),
        "email_verified": bool(user.get("email_verified")),
        "plan_type": user.get("plan_type"),
        "plan_label": user.get("plan_label"),
        "plan_status": user.get("plan_status"),
        "plan_expires_at": int(user.get("plan_expires_at") or 0),
        "doi_quota_limit": int(user.get("doi_quota_limit") or 0),
        "doi_quota_used": int(user.get("doi_quota_used") or 0),
        "doi_daily_limit": int(user.get("doi_daily_limit") or 0),
        "doi_daily_used": int(user.get("doi_daily_used") or 0),
        "doi_daily_day": int(user.get("doi_daily_day") or 0),
    }


def request_email_verification(user_id: int, email: str) -> Dict[str, Any]:
    return _request_email_verification(email, user_id=user_id)


def verify_email_code(user_id: int, email: str, code: str) -> Dict[str, Any]:
    res = _verify_email_code(email, code)
    if res.get("ok"):
        target_id = res.get("user_id")
        if target_id and int(target_id) != int(user_id):
            return {"ok": False, "error": "user_mismatch"}
    return res


def _ui_state(context: ContextTypes.DEFAULT_TYPE) -> Dict[str, Any]:
    return context.user_data.setdefault("email_ui", {})


def _set_ui_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int) -> None:
    _ui_state(context).update({"chat_id": chat_id, "message_id": message_id})


def _get_ui_message(context: ContextTypes.DEFAULT_TYPE) -> Optional[Tuple[int, int]]:
    data = _ui_state(context)
    if data.get("chat_id") and data.get("message_id"):
        return int(data["chat_id"]), int(data["message_id"])
    return None


def _set_ui_active(context: ContextTypes.DEFAULT_TYPE, active: bool) -> None:
    data = _ui_state(context)
    if active:
        data["active"] = True
    else:
        data.pop("active", None)


def _today_key() -> int:
    return int(datetime.now().strftime("%Y%m%d"))


def _format_expiry_date(ts: int) -> str:
    if not ts:
        return "?"
    return datetime.fromtimestamp(int(ts)).strftime("%Y/%m/%d")


def _remaining_days(ts: int) -> int:
    if not ts:
        return 0
    now = int(datetime.now().timestamp())
    if ts <= now:
        return 0
    return int((ts - now + 86399) // 86400)


def _card(title: str, lines: list[str], hints: list[str]) -> str:
    body = "\n".join(lines)
    hint = "\n".join(hints)
    if hint:
        return f"{title}\n{body}\n\n{hint}"
    return f"{title}\n{body}"


def _profile_card(status: Dict[str, Any]) -> str:
    email = status.get("email")
    verified = bool(status.get("email_verified"))
    email_line = f"{htmlmod.escape(email)}" if email else "—"
    status_line = "تایید شده ✅" if verified else "تایید نشده ❌"

    lines = [
        f"• ایمیل: {email_line}",
        f"• وضعیت ایمیل: {status_line}",
    ]

    plan_type = status.get("plan_type")
    plan_label = status.get("plan_label") or "—"
    plan_status = status.get("plan_status") or "—"
    expires_at = int(status.get("plan_expires_at") or 0)
    display_status = plan_status
    if expires_at and _remaining_days(expires_at) == 0 and plan_status == "فعال":
        display_status = "منقضی"

    if plan_type:
        lines.append(f"• اشتراک فعال: {htmlmod.escape(str(plan_label))}")
        lines.append(f"• وضعیت اشتراک: {htmlmod.escape(str(display_status))}")
        if expires_at:
            days_left = _remaining_days(expires_at)
            lines.append(f"• تاریخ انقضا: {_format_expiry_date(expires_at)} | روزهای باقی مانده: {days_left} روز")

        limit = int(status.get("doi_quota_limit") or 0)
        used = int(status.get("doi_quota_used") or 0)
        if limit > 0:
            remaining = max(0, limit - used)
            lines.append(f"• سهمیه DOI: {remaining} از {limit}")

        daily_limit = int(status.get("doi_daily_limit") or 0)
        if daily_limit > 0:
            daily_used = int(status.get("doi_daily_used") or 0)
            day_key = int(status.get("doi_daily_day") or 0)
            if day_key != _today_key():
                daily_used = 0
            daily_remaining = max(0, daily_limit - daily_used)
            lines.append(f"• سهمیه امروز: {daily_remaining} از {daily_limit}")
    else:
        lines.append("• اشتراک فعال: ندارد")

    hints = [
        "برای فعال سازی اشتراک، از منوی خرید استفاده کنید.",
    ]
    return _card("👤 <b>حساب کاربری</b>", lines, hints)


def _email_entry_card(current_email: Optional[str]) -> str:
    cur = htmlmod.escape(current_email) if current_email else "—"
    lines = [
        f"• ایمیل فعلی: {cur}",
    ]
    hints = [
        "ایمیل جدید خود را وارد کنید:",
        "نمونه: <code>user@example.com</code>",
    ]
    return _card("✉️ <b>ورود ایمیل</b>", lines, hints)


def _code_sent_card(email: str, expires_in: int, cooldown: int) -> str:
    email_line = htmlmod.escape(email)
    lines = [
        f"• ایمیل: {email_line}",
        f"• اعتبار کد: {expires_in // 60} دقیقه",
    ]
    hints = ["کد ۶ رقمی ارسال شد. در صورت نیاز، ارسال مجدد را بزنید."]
    if cooldown > 0:
        hints.append(f"⏳ ارسال مجدد پس از {cooldown} ثانیه")
    return _card("📨 <b>کد ارسال شد</b>", lines, hints)


def _code_entry_card(email: str, expires_in: int) -> str:
    email_line = htmlmod.escape(email)
    lines = [
        f"• ایمیل: {email_line}",
        f"• اعتبار کد: {expires_in // 60} دقیقه",
    ]
    hints = ["کد ۶ رقمی را وارد کنید."]
    return _card("🔐 <b>ورود کد ۶ رقمی</b>", lines, hints)


def _result_card(ok: bool, message: str, email: Optional[str] = None) -> str:
    title = "✅ <b>موفقیت</b>" if ok else "⚠️ <b>مشکل</b>"
    lines = [htmlmod.escape(message)]
    if email:
        lines.insert(0, f"• ایمیل: {htmlmod.escape(email)}")
    return _card(title, lines, [])


def build_menu_keyboard(status: Dict[str, Any], *, include_delivery: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if status.get("email_verified"):
        rows.append([InlineKeyboardButton("✏️ تغییر ایمیل", callback_data=CB_EMAIL_CHANGE)])
    else:
        rows.append([InlineKeyboardButton("✅ تایید ایمیل", callback_data=CB_EMAIL_VERIFY)])
    if include_delivery:
        rows.append([InlineKeyboardButton("📦 روش ارسال", callback_data=CB_ACCOUNT_DELIVERY)])
    rows.append([InlineKeyboardButton("🏠 منو", callback_data=CB_MENU_ROOT)])
    return InlineKeyboardMarkup(rows)


def build_verify_keyboard(*, cooldown: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if cooldown > 0:
        rows.append([InlineKeyboardButton(f"⏳ ارسال مجدد ({cooldown}ث)", callback_data=CB_EMAIL_COOLDOWN)])
    else:
        rows.append([InlineKeyboardButton("🔄 ارسال مجدد", callback_data=CB_EMAIL_RESEND)])
    rows.append([InlineKeyboardButton("✏️ تغییر ایمیل", callback_data=CB_EMAIL_CHANGE)])
    rows.append([InlineKeyboardButton("⬅️ بازگشت", callback_data=CB_EMAIL_BACK)])
    return InlineKeyboardMarkup(rows)


def _cooldown_seconds(result: Dict[str, Any]) -> int:
    return int(result.get("cooldown_seconds") or result.get("retry_after") or 0)


async def _edit_or_send(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    keyboard: InlineKeyboardMarkup,
) -> None:
    q = update.callback_query
    if q and q.message:
        _set_ui_message(context, q.message.chat_id, q.message.message_id)
        try:
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
            return
        except BadRequest as exc:
            if "Message is not modified" not in str(exc):
                logger.warning("edit_failed: %s", exc)
    stored = _get_ui_message(context)
    if stored:
        chat_id, msg_id = stored
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
            return
        except Exception as exc:
            logger.warning("edit_failed_fallback: %s", exc)
    sent = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )
    _set_ui_message(context, sent.chat_id, sent.message_id)


async def _send_new(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    keyboard: InlineKeyboardMarkup,
) -> None:
    if update.message:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        return
    q = update.callback_query
    if q and q.message:
        await q.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        return
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def show_profile_card(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    include_delivery: bool = False,
) -> None:
    status = get_user_status(update.effective_user.id)
    text = _profile_card(status)
    kb = build_menu_keyboard(status, include_delivery=include_delivery)
    await _edit_or_send(update, context, text, kb)


async def on_email_verify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.callback_query:
        await update.callback_query.answer()
    status = get_user_status(update.effective_user.id)
    text = _email_entry_card(status.get("email"))
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ بازگشت", callback_data=CB_EMAIL_BACK)]])
    _set_ui_active(context, True)
    await _send_new(update, context, text, kb)
    return WAITING_EMAIL


async def on_email_change(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await on_email_verify(update, context)


async def on_email_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _set_ui_active(context, False)
    await show_profile_card(update, context, include_delivery=True)
    return ConversationHandler.END


async def on_email_cooldown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if q:
        await q.answer("لطفاً کمی بعد دوباره تلاش کنید.", show_alert=True)
    return WAITING_CODE


async def receive_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return WAITING_EMAIL
    email = (update.message.text or "").strip()
    if not EMAIL_REGEX.match(email):
        text = _result_card(False, "فرمت ایمیل معتبر نیست. نمونه: user@example.com")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ بازگشت", callback_data=CB_EMAIL_BACK)]])
        await _send_new(update, context, text, kb)
        return WAITING_EMAIL

    res = request_email_verification(update.effective_user.id, email)
    if not res.get("ok"):
        if res.get("error") == "rate_limited":
            cooldown = _cooldown_seconds(res)
            expires_in = int(res.get("expires_in") or 600)
            context.user_data["pending_email"] = email
            text = _code_sent_card(email, expires_in, cooldown)
            kb = build_verify_keyboard(cooldown=cooldown)
            await _send_new(update, context, text, kb)
            return WAITING_CODE
        text = _result_card(False, "ارسال کد با خطا مواجه شد. لطفاً دوباره تلاش کنید.")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ بازگشت", callback_data=CB_EMAIL_BACK)]])
        await _send_new(update, context, text, kb)
        return WAITING_EMAIL

    expires_in = int(res.get("expires_in") or 600)
    cooldown = _cooldown_seconds(res)
    context.user_data["pending_email"] = email
    text = _code_sent_card(email, expires_in, cooldown)
    kb = build_verify_keyboard(cooldown=cooldown)
    await _send_new(update, context, text, kb)
    return WAITING_CODE


async def on_email_resend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    if q:
        await q.answer()
    email = (context.user_data.get("pending_email") or "").strip()
    if not email:
        return await on_email_verify(update, context)
    res = request_email_verification(update.effective_user.id, email)
    if not res.get("ok") and res.get("error") != "rate_limited":
        text = _result_card(False, "ارسال مجدد با خطا مواجه شد. لطفاً دوباره تلاش کنید.")
        kb = build_verify_keyboard(cooldown=_cooldown_seconds(res))
        await _send_new(update, context, text, kb)
        return WAITING_CODE
    expires_in = int(res.get("expires_in") or 600)
    cooldown = _cooldown_seconds(res)
    text = _code_sent_card(email, expires_in, cooldown)
    kb = build_verify_keyboard(cooldown=cooldown)
    await _send_new(update, context, text, kb)
    return WAITING_CODE


async def receive_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return WAITING_CODE
    email = (context.user_data.get("pending_email") or "").strip()
    code = (update.message.text or "").strip()
    if not email:
        return await on_email_verify(update, context)
    if not (len(code) == 6 and code.isdigit()):
        text = _result_card(False, "کد باید ۶ رقم باشد.")
        kb = build_verify_keyboard(cooldown=0)
        await _send_new(update, context, text, kb)
        return WAITING_CODE

    res = verify_email_code(update.effective_user.id, email, code)
    if res.get("ok"):
        context.user_data.pop("pending_email", None)
        _set_ui_active(context, False)
        text = _result_card(True, "ایمیل با موفقیت تایید شد.", email=email)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 برگشت به منو", callback_data=CB_MENU_ROOT)]])
        await _send_new(update, context, text, kb)
        return ConversationHandler.END

    err = res.get("error")
    if err == "invalid_code":
        attempts_left = int(res.get("attempts_left") or 0)
        text = _result_card(False, f"کد اشتباه است. تلاش باقی‌مانده: {attempts_left}")
        kb = build_verify_keyboard(cooldown=0)
        await _send_new(update, context, text, kb)
        return WAITING_CODE
    if err == "too_many_attempts":
        context.user_data.pop("pending_email", None)
        _set_ui_active(context, False)
        text = _result_card(False, "تعداد تلاش‌ها بیش از حد مجاز است. دوباره درخواست دهید.")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ تایید ایمیل", callback_data=CB_EMAIL_VERIFY)]])
        await _send_new(update, context, text, kb)
        return ConversationHandler.END
    if err == "expired":
        context.user_data.pop("pending_email", None)
        text = _result_card(False, "کد منقضی شده، دوباره درخواست دهید.")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 ارسال مجدد", callback_data=CB_EMAIL_RESEND)]])
        await _send_new(update, context, text, kb)
        return WAITING_CODE
    if err == "already_verified":
        _set_ui_active(context, False)
        text = _result_card(True, "ایمیل قبلاً تایید شده است.", email=email)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 برگشت به منو", callback_data=CB_MENU_ROOT)]])
        await _send_new(update, context, text, kb)
        return ConversationHandler.END

    text = _result_card(False, "تایید ایمیل با خطا مواجه شد. لطفاً دوباره تلاش کنید.")
    kb = build_verify_keyboard(cooldown=0)
    await _send_new(update, context, text, kb)
    return WAITING_CODE


def build_email_verification_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(on_email_verify, pattern=f"^{CB_EMAIL_VERIFY}$"),
            CallbackQueryHandler(on_email_change, pattern=f"^{CB_EMAIL_CHANGE}$"),
            CallbackQueryHandler(on_email_resend, pattern=f"^{CB_EMAIL_RESEND}$"),
        ],
        states={
            WAITING_EMAIL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_email),
                CallbackQueryHandler(on_email_back, pattern=f"^{CB_EMAIL_BACK}$"),
            ],
            WAITING_CODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_code),
                CallbackQueryHandler(on_email_resend, pattern=f"^{CB_EMAIL_RESEND}$"),
                CallbackQueryHandler(on_email_change, pattern=f"^{CB_EMAIL_CHANGE}$"),
                CallbackQueryHandler(on_email_back, pattern=f"^{CB_EMAIL_BACK}$"),
                CallbackQueryHandler(on_email_cooldown, pattern=f"^{CB_EMAIL_COOLDOWN}$"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(on_email_back, pattern=f"^{CB_EMAIL_BACK}$"),
        ],
        name="email_ui_conversation",
        persistent=False,
        block=True,
    )
