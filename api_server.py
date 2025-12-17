from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
import time
from typing import Any, Dict, Optional, Tuple

from aiohttp import web

from downloadmain import (
    CFG,
    PARSE_HTML,
    db_get_quota_status,
    db_get_user,
    db_get_user_by_email,
    normalize_doi,
    process_dois_batch_oa_only,
    verify_email_code,
    fetch_openalex,
    _find_oa_pdf_from_openalex_raw,
)


@dataclass
class RateLimit:
    window_s: int = 60
    max_hits: int = 30


_RL = RateLimit(
    window_s=int(getattr(CFG, "API_RATE_WINDOW_S", 60) if hasattr(CFG, "API_RATE_WINDOW_S") else 60),
    max_hits=int(getattr(CFG, "API_RATE_MAX_HITS", 30) if hasattr(CFG, "API_RATE_MAX_HITS") else 30),
)
_HITS: Dict[str, list[float]] = {}


def _cors_headers() -> Dict[str, str]:
    # برای استفاده از اکستنشن کروم، Origin می‌تواند chrome-extension://... باشد.
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }


@web.middleware
async def cors_middleware(request: web.Request, handler):
    if request.method == "OPTIONS":
        return web.Response(status=200, headers=_cors_headers())

    # نرخ‌محدود سازی ساده برای جلوگیری از brute-force روی API (به‌خصوص وقتی ریموت است)
    ip = request.remote or "unknown"
    now = time.time()
    arr = _HITS.get(ip, [])
    cutoff = now - _RL.window_s
    arr = [t for t in arr if t >= cutoff]
    arr.append(now)
    _HITS[ip] = arr
    if len(arr) > _RL.max_hits:
        return web.json_response({"ok": False, "error": "rate_limited"}, status=429, headers=_cors_headers())

    resp = await handler(request)
    for k, v in _cors_headers().items():
        resp.headers.setdefault(k, v)
    return resp


def _auth_user(email: str, code: str) -> Tuple[Optional[int], Optional[Dict[str, Any]]]:
    user_id = verify_email_code(email, code)
    if not user_id:
        return None, None
    user = db_get_user(int(user_id))
    if not user:
        return None, None
    return int(user_id), user


async def _doi_info(session, doi_raw: str) -> Dict[str, Any]:
    doi = normalize_doi(doi_raw)
    title = None
    year = None
    is_oa = False
    oa_pdf_url = None

    if not doi:
        return {"ok": False, "error": "invalid_doi"}

    try:
        title, year, _journal, _abstract, _concepts, _src, oa_raw = await fetch_openalex(session, doi)
        if isinstance(oa_raw, dict):
            try:
                oa = oa_raw.get("open_access") or {}
                is_oa = bool(oa.get("is_oa"))
            except Exception:
                is_oa = False
            with suppress(Exception):
                oa_pdf_url = _find_oa_pdf_from_openalex_raw(oa_raw)
    except Exception:
        pass

    if is_oa:
        color = "green"
    elif isinstance(year, int) and year < 2022:
        color = "yellow"
    else:
        color = "red"

    return {
        "ok": True,
        "doi": doi,
        "title": title,
        "year": year,
        "is_oa": is_oa,
        "oa_pdf_url_present": bool(oa_pdf_url),
        "color": color,
    }


def create_api_app(*, bot) -> web.Application:
    app = web.Application(middlewares=[cors_middleware])
    app["bot"] = bot
    app["session"] = None

    async def startup(app_: web.Application) -> None:
        import aiohttp

        ua = "doi-bot-api/1.0"
        headers = {"User-Agent": ua}
        app_["session"] = aiohttp.ClientSession(headers=headers)

    async def cleanup(app_: web.Application) -> None:
        sess = app_.get("session")
        if sess:
            with suppress(Exception):
                await sess.close()

    app.on_startup.append(startup)
    app.on_cleanup.append(cleanup)

    async def login(request: web.Request) -> web.Response:
        payload = await request.json()
        email = str(payload.get("email") or "").strip()
        code = str(payload.get("code") or "").strip()
        user_id, user = _auth_user(email, code)
        if not user_id or not user:
            return web.json_response({"ok": False, "error": "invalid_credentials"}, status=401)
        quota = db_get_quota_status(user_id)
        return web.json_response(
            {
                "ok": True,
                "user_id": user_id,
                "email": user.get("email"),
                "quota": quota,
                "account_active": bool(quota["remaining_free"] or quota["remaining_paid"]),
                "ai_active": bool(CFG.GROQ_API_KEY),
            }
        )

    async def me(request: web.Request) -> web.Response:
        payload = await request.json()
        email = str(payload.get("email") or "").strip()
        code = str(payload.get("code") or "").strip()
        user_id, user = _auth_user(email, code)
        if not user_id or not user:
            return web.json_response({"ok": False, "error": "invalid_credentials"}, status=401)
        quota = db_get_quota_status(user_id)
        return web.json_response(
            {
                "ok": True,
                "user_id": user_id,
                "email": user.get("email"),
                "quota": quota,
                "account_active": bool(quota["remaining_free"] or quota["remaining_paid"]),
                "ai_active": bool(CFG.GROQ_API_KEY),
            }
        )

    async def doi_info(request: web.Request) -> web.Response:
        payload = await request.json()
        doi_raw = str(payload.get("doi") or "").strip()
        sess = request.app.get("session")
        if not sess:
            return web.json_response({"ok": False, "error": "session_not_ready"}, status=503)
        info = await _doi_info(sess, doi_raw)
        status = 200 if info.get("ok") else 400
        return web.json_response(info, status=status)

    async def submit_doi(request: web.Request) -> web.Response:
        payload = await request.json()
        email = str(payload.get("email") or "").strip()
        code = str(payload.get("code") or "").strip()
        doi_raw = str(payload.get("doi") or "").strip()

        user_id, user = _auth_user(email, code)
        if not user_id or not user:
            return web.json_response({"ok": False, "error": "invalid_credentials"}, status=401)

        sess = request.app.get("session")
        if not sess:
            return web.json_response({"ok": False, "error": "session_not_ready"}, status=503)

        info = await _doi_info(sess, doi_raw)
        if not info.get("ok"):
            return web.json_response(info, status=400)

        # فقط OA: اگر OA نیست، فقط وضعیت را برگردان (بدون شروع دانلود)
        if not info.get("is_oa"):
            return web.json_response(
                {
                    "ok": False,
                    "error": "not_open_access",
                    "doi_info": info,
                    "message": "This paper does not appear to be Open Access.",
                },
                status=403,
            )

        bot_ = request.app["bot"]
        doi = str(info.get("doi") or "")

        async def _run() -> None:
            try:
                await bot_.send_message(
                    chat_id=user_id,
                    text=f"📎 DOI دریافت شد: {doi}\nدر حال بررسی Open Access و دانلود…",
                    parse_mode=PARSE_HTML if PARSE_HTML else None,
                )
            except Exception:
                pass
            await process_dois_batch_oa_only(user_id, [doi], user_id, bot_)

        asyncio.create_task(_run())
        return web.json_response({"ok": True, "queued": True, "doi_info": info})

    app.router.add_route("POST", "/api/v1/login", login)
    app.router.add_route("POST", "/api/v1/me", me)
    app.router.add_route("POST", "/api/v1/doi_info", doi_info)
    app.router.add_route("POST", "/api/v1/submit_doi", submit_doi)
    app.router.add_route("OPTIONS", "/{tail:.*}", lambda r: web.Response(status=200, headers=_cors_headers()))
    return app


async def start_api_server(*, bot) -> Optional[web.AppRunner]:
    if not CFG.API_ENABLED:
        return None
    app = create_api_app(bot=bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=CFG.API_HOST, port=CFG.API_PORT)
    await site.start()
    return runner


async def stop_api_server(runner: Optional[web.AppRunner]) -> None:
    if not runner:
        return
    with suppress(Exception):
        await runner.cleanup()
