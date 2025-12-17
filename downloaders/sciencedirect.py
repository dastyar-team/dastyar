from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from contextlib import suppress
from typing import Any, Callable, Dict, List, Optional, Tuple

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from downloaders.iranpaper import ensure_sciencedirect_session

try:
    from groq import AsyncGroq
    _HAS_GROQ = True
except Exception:  # pragma: no cover - optional dependency
    AsyncGroq = None  # type: ignore
    _HAS_GROQ = False

LOGGER = logging.getLogger("doi_bot.sciencedirect")
SCIDIR_LIMIT_PER_HOUR = 6

# وضعیت درایور برای هر اکانت (slot: 1..3)
_DRIVERS: Dict[int, webdriver.Chrome] = {}
_HANDLES: Dict[int, str] = {}
_REFRESH_AT: Dict[int, float] = {}
_PROXY: Dict[int, str] = {}


# =========================
# ابزارهای داخلی
# =========================
def _driver_alive(driver: Optional[webdriver.Chrome]) -> bool:
    if not driver:
        return False
    try:
        driver.execute_script("return document.readyState")
        return True
    except Exception:
        return False


def _destroy_driver(slot: int) -> None:
    drv = _DRIVERS.pop(slot, None)
    if drv:
        with suppress(Exception):
            drv.quit()
    _HANDLES.pop(slot, None)
    _REFRESH_AT.pop(slot, None)
    _PROXY.pop(slot, None)


def _schedule_refresh(slot: int) -> None:
    _REFRESH_AT[slot] = time.time() + random.uniform(3 * 3600, 4 * 3600)


def _human_pause(min_s: float, max_s: float) -> None:
    time.sleep(random.uniform(min_s, max_s))


def _looks_404(driver: webdriver.Chrome) -> bool:
    try:
        title = (driver.title or "").lower()
        if "404" in title or "page not found" in title or "خطا" in title:
            return True
        html = driver.page_source.lower()
        return (("404" in html and "not found" in html) or "page not found" in html)
    except Exception:
        return False


def _scidir_find_pdf_button(driver: webdriver.Chrome) -> Optional[WebElement]:
    selectors = [
        "a[data-testid='pdf-download-button']",
        "a[data-aa-name='download pdf']",
        "a[href*='pdf']",
        "button[data-testid='pdf-download-button']",
    ]
    for sel in selectors:
        try:
            return WebDriverWait(driver, 40).until(EC.element_to_be_clickable((By.CSS_SELECTOR, sel)))
        except Exception:
            continue
    return None


# =========================
# AI helpers (Groq)
# =========================
async def ai_check_sciencedirect_journal(cfg, journal: Optional[str]) -> Tuple[bool, float, str]:
    if not journal:
        return False, 0.0, "no_journal"
    if not _HAS_GROQ or not cfg.GROQ_API_KEY:
        return False, 0.0, "groq_unavailable"

    client = AsyncGroq(api_key=cfg.GROQ_API_KEY)
    system = (
        "You are an experienced librarian who knows Elsevier platforms. "
        "Answer in STRICT JSON: {\"is_sciencedirect\":true/false,\"confidence\":0..1,\"reason\":\"\"}."
    )
    user = (
        "Determine if the given journal is distributed on ScienceDirect (Elsevier). "
        "Reply true only if it is primarily hosted or published on ScienceDirect.\n"
        f"Journal: {journal}"
    )
    try:
        resp = await client.chat.completions.create(
            model=cfg.GROQ_MODEL,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content if getattr(resp, "choices", None) else None
    except Exception as e:
        LOGGER.warning("groq_scidir_journal_failed | journal=%s err=%s", journal, e)
        return False, 0.0, "groq_error"

    if not content:
        return False, 0.0, "no_content"
    try:
        data = json.loads(content)
    except Exception:
        return False, 0.0, "json_parse_error"
    flag = bool(data.get("is_sciencedirect"))
    conf = float(data.get("confidence") or 0.0)
    reason = str(data.get("reason") or "")
    return flag, conf, reason


async def ai_validate_sciencedirect_match(
    cfg,
    target_title: Optional[str],
    target_abstract: Optional[str],
    candidate_title: str,
    candidate_snippet: Optional[str],
) -> Tuple[bool, float, str]:
    if not target_title:
        return False, 0.0, "no_target_title"
    if not _HAS_GROQ or not cfg.GROQ_API_KEY:
        return False, 0.0, "groq_unavailable"

    snippet = candidate_snippet or ""
    abstract = target_abstract or ""
    system = (
        "You compare two research papers. Return STRICT JSON: "
        "{\"match\":true/false,\"confidence\":0..1,\"reason\":\"\"}. "
        "Answer true only if they are the same work."
    )
    user = (
        f"Target title: {target_title}\n"
        f"Target abstract: {abstract}\n"
        f"Candidate title: {candidate_title}\n"
        f"Candidate snippet: {snippet}\n"
        "Check if they are at least 95% the same paper."
    )
    client = AsyncGroq(api_key=cfg.GROQ_API_KEY)
    try:
        resp = await client.chat.completions.create(
            model=cfg.GROQ_MODEL,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0,
            response_format={"type": "json_object"},
        )
    except Exception as e:
        LOGGER.warning("groq_scidir_match_failed | title=%s err=%s", target_title, e)
        return False, 0.0, "groq_error"
    content = resp.choices[0].message.content if getattr(resp, "choices", None) else None
    if not content:
        return False, 0.0, "no_content"
    try:
        data = json.loads(content)
    except Exception:
        return False, 0.0, "json_parse_error"
    flag = bool(data.get("match"))
    conf = float(data.get("confidence") or 0.0)
    reason = str(data.get("reason") or "")
    return flag, conf, reason


# =========================
# Rate limiting per account
# =========================
def _load_usage(db_get_setting: Callable[[str], Optional[str]]) -> Dict[str, List[float]]:
    raw = db_get_setting("SCIDIR_USAGE_V2") or "{}"
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    out: Dict[str, List[float]] = {}
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, list):
                out[str(k)] = [float(x) for x in v if isinstance(x, (int, float, str))]
    return out


def _save_usage(db_set_setting: Callable[[str, str], None], data: Dict[str, List[float]]) -> None:
    try:
        payload = json.dumps(data, ensure_ascii=False)
    except Exception:
        payload = "{}"
    db_set_setting("SCIDIR_USAGE_V2", payload)


def _register_download(
    slot: int,
    db_get_setting: Callable[[str], Optional[str]],
    db_set_setting: Callable[[str, str], None],
) -> None:
    usage = _load_usage(db_get_setting)
    arr = usage.get(str(slot), [])
    arr.append(time.time())
    cutoff = time.time() - 3600
    usage[str(slot)] = [t for t in arr if t >= cutoff]
    _save_usage(db_set_setting, usage)


def _can_download_now(
    slot: int,
    db_get_setting: Callable[[str], Optional[str]],
    db_set_setting: Callable[[str, str], None],
    limit: int = SCIDIR_LIMIT_PER_HOUR,
) -> Tuple[bool, Optional[float]]:
    usage = _load_usage(db_get_setting)
    arr = usage.get(str(slot), [])
    cutoff = time.time() - 3600
    arr = [t for t in arr if t >= cutoff]
    usage[str(slot)] = arr
    _save_usage(db_set_setting, usage)
    if len(arr) < limit:
        return True, None
    oldest = min(arr) if arr else time.time()
    wait = max(0.0, 3600 - (time.time() - oldest))
    return False, wait


# =========================
# Driver و پروکسی
# =========================
def get_scidir_driver(
    account: Dict[str, Any],
    *,
    build_chrome_driver: Callable[..., webdriver.Chrome],
    ensure_v2ray_running: Callable[[str, str], Optional[str]],
    solve_recaptcha: Callable[[webdriver.Chrome, str], bool],
) -> Optional[webdriver.Chrome]:
    slot = int(account.get("slot") or 0)
    email = account.get("email") or ""
    password = account.get("password") or ""
    vpn_data = account.get("vpn_data") or ""

    if not slot or not email or not password:
        LOGGER.warning("scidir_account_missing_fields | slot=%s", slot)
        return None

    now = time.time()
    refresh_at = _REFRESH_AT.get(slot, 0)
    if refresh_at and now >= refresh_at:
        LOGGER.info("scidir_refresh_due | slot=%s refresh_at=%.0f now=%.0f", slot, refresh_at, now)
        _destroy_driver(slot)

    driver = _DRIVERS.get(slot)
    proxy_url = _PROXY.get(slot)
    if not _driver_alive(driver) or not proxy_url:
        _destroy_driver(slot)
        proxy_url = ensure_v2ray_running("iran", vpn_data)
        if not proxy_url:
            LOGGER.warning("scidir_proxy_missing | slot=%s", slot)
            return None
        driver = build_chrome_driver(proxy_url=proxy_url)
        with suppress(Exception):
            driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
                "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            })
        _DRIVERS[slot] = driver
        _PROXY[slot] = proxy_url
        _HANDLES.pop(slot, None)
        LOGGER.info("scidir_driver_initialized | slot=%s proxy=%s", slot, proxy_url)

    # لاگین IranPaper و باز کردن دسترسی مستقیم
    if not _HANDLES.get(slot) or _HANDLES[slot] not in (driver.window_handles if driver else []):
        handle = ensure_sciencedirect_session(driver, email, password)
        if handle:
            _HANDLES[slot] = handle
            with suppress(Exception):
                driver.switch_to.window(handle)
            _schedule_refresh(slot)
            LOGGER.info("scidir_proxy_session_ready | slot=%s refresh_at=%.0f", slot, _REFRESH_AT.get(slot, 0))
        else:
            LOGGER.warning("scidir_proxy_session_failed | slot=%s", slot)
            return None
    else:
        with suppress(Exception):
            driver.switch_to.window(_HANDLES[slot])

    return driver


# =========================
# جست‌وجو و دانلود
# =========================
def _scidir_search_pdf_url(
    cfg,
    driver: webdriver.Chrome,
    base_url: str,
    search_title: str,
    target_title: str,
    target_abstract: Optional[str],
    doi: str,
    loop: asyncio.AbstractEventLoop,
    *,
    solve_recaptcha: Callable[[webdriver.Chrome, str], bool],
) -> Optional[str]:
    LOGGER.info("scidir_driver_start | doi=%s base=%s", doi, base_url)
    driver.set_page_load_timeout(60)

    try:
        driver.get(base_url)
        with suppress(Exception):
            solve_recaptcha(driver, driver.current_url)
        _human_pause(1, 2)
        LOGGER.debug("scidir_after_load_url | url=%s", driver.current_url)
    except Exception:
        pass

    if _looks_404(driver):
        LOGGER.info("scidir_404_warmup | retry via homepage")
        with suppress(Exception):
            driver.get("https://www.sciencedirect.com/")
            solve_recaptcha(driver, driver.current_url)
        _human_pause(2, 3)
        with suppress(Exception):
            driver.get(base_url)
            solve_recaptcha(driver, driver.current_url)
        _human_pause(2, 3)

    try:
        search_input = WebDriverWait(driver, 40).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='search'], input[name='qs'], input[id*='search']"))
        )
        _human_pause(1, 2)
        search_input.clear()
        for ch in search_title:
            search_input.send_keys(ch)
            time.sleep(random.uniform(0.05, 0.18))
        _human_pause(1, 2)
        search_input.send_keys(Keys.ENTER)
        _human_pause(2, 3)
    except Exception as exc:
        LOGGER.debug("scidir_search_box_failed | err=%s", exc)

    WebDriverWait(driver, 40).until(
        EC.presence_of_all_elements_located((By.CSS_SELECTOR, "a[href*='/science/article/']"))
    )
    links = driver.find_elements(By.CSS_SELECTOR, "a[href*='/science/article/']")
    inspected = 0

    for link in links:
        try:
            text = link.text.strip()
        except Exception:
            continue
        if not text:
            continue
        try:
            parent = link.find_element(By.XPATH, "ancestor::article")
            snippet = parent.text
        except Exception:
            snippet = text

        inspected += 1
        if inspected > 5:
            break

        fut = asyncio.run_coroutine_threadsafe(
            ai_validate_sciencedirect_match(cfg, target_title, target_abstract, text, snippet),
            loop,
        )
        try:
            ok, conf, reason = fut.result(timeout=90)
        except Exception as e:
            LOGGER.warning("scidir_ai_compare_failed | doi=%s err=%s", doi, e)
            continue
        LOGGER.info(
            "scidir_ai_match | doi=%s title=%s ok=%s conf=%.2f reason=%s",
            doi,
            text[:80],
            ok,
            conf,
            reason,
        )
        if not ok or conf < 0.95:
            continue

        _human_pause(1, 2)
        try:
            link.click()
        except Exception:
            driver.execute_script("arguments[0].click();", link)
        _human_pause(2, 3)

    if len(driver.window_handles) > 1:
        driver.switch_to.window(driver.window_handles[-1])
    WebDriverWait(driver, 40).until(EC.url_contains("/science/article"))
    _human_pause(2, 3)
    with suppress(Exception):
        solve_recaptcha(driver, driver.current_url)

    pdf_el = _scidir_find_pdf_button(driver)
    if not pdf_el:
        return None

    href = pdf_el.get_attribute("href") or pdf_el.get_attribute("data-url")
    if href:
        LOGGER.info("scidir_pdf_href | doi=%s url=%s", doi, href)
        return href

    pdf_el.click()
    _human_pause(2, 3)
    if len(driver.window_handles) > 1:
        driver.switch_to.window(driver.window_handles[-1])
    WebDriverWait(driver, 40).until(EC.url_contains("pdf"))
    _human_pause(2, 3)
    with suppress(Exception):
        solve_recaptcha(driver, driver.current_url)
    return driver.current_url


async def download_via_sciencedirect(
    session,
    doi: str,
    title: Optional[str],
    abstract: Optional[str],
    journal: Optional[str],
    *,
    cfg,
    bot,
    chat_id: int,
    force: bool = False,
    accounts: List[Dict[str, Any]],
    build_chrome_driver: Callable[..., webdriver.Chrome],
    ensure_v2ray_running: Callable[[str, str], Optional[str]],
    solve_recaptcha: Callable[[webdriver.Chrome, str], bool],
    db_get_setting: Callable[[str], Optional[str]],
    db_set_setting: Callable[[str, str], None],
    download_pdf_to_tmp: Callable[..., Any],
) -> Optional[Any]:
    if not title or not journal:
        return None

    allow, conf, reason = await ai_check_sciencedirect_journal(cfg, journal)
    LOGGER.info(
        "scidir_journal_check | doi=%s journal=%s allow=%s conf=%.2f reason=%s",
        doi,
        journal,
        allow,
        conf,
        reason,
    )
    if (not allow or conf < 0.6) and not force:
        return None

    notified = False

    for acc in accounts:
        slot = int(acc.get("slot") or 0)
        if not acc.get("active") or not slot:
            continue

        ok_now, wait_s = _can_download_now(slot, db_get_setting, db_set_setting, SCIDIR_LIMIT_PER_HOUR)
        if not ok_now:
            LOGGER.info("scidir_rate_limit | slot=%s wait=%.1fs", slot, wait_s or 0)
            continue

        driver = get_scidir_driver(
            acc,
            build_chrome_driver=build_chrome_driver,
            ensure_v2ray_running=ensure_v2ray_running,
            solve_recaptcha=solve_recaptcha,
        )
        if not driver:
            LOGGER.warning("scidir_driver_unavailable | slot=%s", slot)
            continue

        if not notified:
            try:
                await bot.send_message(
                    chat_id,
                    "⏳ تلاش برای دانلود از ScienceDirect آغاز شد؛ ممکن است چند دقیقه طول بکشد.",
                )
            except Exception:
                pass
            notified = True

        loop = asyncio.get_running_loop()
        pdf_url = await asyncio.to_thread(
            _scidir_search_pdf_url,
            cfg,
            driver,
            acc.get("base_url") or acc.get("direct_url") or "https://iranpaper.ir/directaccess",
            title,
            title,
            abstract,
            doi,
            loop,
            solve_recaptcha=solve_recaptcha,
        )
        if not pdf_url:
            LOGGER.info("scidir_pdf_not_found | doi=%s slot=%s", doi, slot)
            continue

        fpath = await download_pdf_to_tmp(session, pdf_url, hint=f"{doi.replace('/', '_')}_scidir_slot{slot}")
        if fpath:
            _register_download(slot, db_get_setting, db_set_setting)
            return fpath

    return None


async def warmup_accounts(
    accounts: List[Dict[str, Any]],
    *,
    cfg,
    build_chrome_driver: Callable[..., webdriver.Chrome],
    ensure_v2ray_running: Callable[[str, str], Optional[str]],
    solve_recaptcha: Callable[[webdriver.Chrome, str], bool],
    delay_first: Tuple[int, int] = (30, 60),
    delay_between: Tuple[int, int] = (180, 600),
) -> None:
    LOGGER.info("scidir_warmup_start | total=%d | active=%d | delay_first=%s", len(accounts), len([a for a in accounts if a.get("active")]), delay_first)
    # باز کردن تب‌ها با تأخیر رندوم
    await asyncio.sleep(random.uniform(*delay_first))
    for idx, acc in enumerate(accounts):
        if not acc.get("active"):
            continue
        driver = get_scidir_driver(
            acc,
            build_chrome_driver=build_chrome_driver,
            ensure_v2ray_running=ensure_v2ray_running,
            solve_recaptcha=solve_recaptcha,
        )
        if driver:
            LOGGER.info("scidir_warmup_ok | slot=%s", acc.get("slot"))
        if idx < len(accounts) - 1:
            await asyncio.sleep(random.uniform(*delay_between))
