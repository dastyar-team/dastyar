from __future__ import annotations

import logging
import random
import time
from contextlib import suppress
from typing import Optional

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

LOGGER = logging.getLogger("doi_bot.iranpaper")

HOME_URL = "https://iranpaper.ir/"
LOGIN_URL = "https://iranpaper.ir/login"

# اگر جایی خواستی از هدر لاگین استفاده کنی، این‌ها به درد می‌خورند
LOGIN_BUTTON_XPATHS = [
    "//header//button[contains(.,'ورود')]",
    "//button[contains(@aria-label,'sign in')]",
    "//button[contains(@class,'sign__in')]",
]

# فیلد نام کاربری (موبایل/ایمیل) – بر اساس HTML واقعی سایت
LOGIN_INPUT_EMAIL = (
    "#input-294, "
    "input[name='name'], "
    "input[name='email'], input[type='email'], "
    "input[placeholder*='ایمیل'], input[placeholder*='موبایل'], "
    "input[placeholder*='نام\u200cکاربری'], input[placeholder*='کاربری']"
)

# فیلد رمز عبور – بر اساس HTML واقعی سایت
LOGIN_INPUT_PASSWORD = (
    "#input-298, "
    "input[name='password'], input[type='password'], "
    "input[placeholder*='رمز'], input[placeholder*='گذرواژه']"
)


# =====================
# مرحله ۱: هندل چَلِنج / کوکی
# =====================

def _wait_challenge(driver: WebDriver, total_ms: int = 20000) -> None:
    """
    اگر Cloudflare / Turnstile «Checking your browser…» باشد کمی صبر می‌کند
    تا برطرف شود و فرم لاگین ظاهر شود.
    """
    end_time = time.time() + total_ms / 1000.0
    while time.time() < end_time:
        try:
            html = (driver.page_source or "").lower()
        except Exception:
            html = ""

        # اگر هنوز در صفحهٔ چک مرورگر هستیم
        if any(key in html for key in ("checking your browser", "turnstile", "cf-chl", "cloudflare")):
            time.sleep(1.0)
            continue

        # اگر فرم لاگین را دیدیم، برگرد
        try:
            if driver.find_elements(By.CSS_SELECTOR, LOGIN_INPUT_EMAIL):
                return
        except Exception:
            pass
        time.sleep(0.5)


def _dismiss_overlays(driver: WebDriver) -> None:
    """
    بستن بنرهای کوکی/مودال مزاحم.
    در Selenium :has-text نداریم، پس برای دکمه‌های متنی از XPath استفاده می‌کنیم.
    """
    with suppress(Exception):
        driver.switch_to.default_content()

    # دکمه‌های با متن «قبول»، «باشه»، «موافقم»
    text_buttons = ["قبول", "باشه", "موافقم"]
    for txt in text_buttons:
        try:
            elems = driver.find_elements(
                By.XPATH,
                f"//button[contains(normalize-space(.), '{txt}')]"
            )
        except Exception:
            elems = []
        for elem in elems:
            try:
                if elem.is_displayed() and elem.is_enabled():
                    elem.click()
                    time.sleep(0.3)
                    break
            except Exception:
                continue

    # بستن بر اساس id / class / aria-label
    selectors = [
        "#cookie-accept",
        ".cookie-accept",
        'button[aria-label="close"]',
    ]
    for sel in selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, sel)
        except Exception:
            continue
        for elem in elements:
            try:
                if elem.is_displayed() and elem.is_enabled():
                    elem.click()
                    time.sleep(0.3)
                    break
            except Exception:
                continue


# =====================
# مرحله ۲: لاگین
# =====================

def _wait_login_form(driver: WebDriver, timeout: int = 25) -> bool:
    """
    صبر می‌کند تا هر دو فیلد نام کاربری و رمز عبور روی صفحه قابل‌مشاهده شوند.
    """
    try:
        WebDriverWait(driver, timeout).until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, LOGIN_INPUT_EMAIL))
        )
        WebDriverWait(driver, timeout).until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, LOGIN_INPUT_PASSWORD))
        )
        return True
    except TimeoutException:
        LOGGER.warning("iranpaper_login_inputs_timeout")
        return False


def _fill_login_form(driver: WebDriver, username: str, password: str) -> bool:
    """
    فیلدهای لاگین را پر و روی دکمه «ورود» کلیک می‌کند.
    """
    with suppress(Exception):
        driver.switch_to.default_content()

    try:
        email_input = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, LOGIN_INPUT_EMAIL))
        )
        password_input = WebDriverWait(driver, 20).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, LOGIN_INPUT_PASSWORD))
        )
    except TimeoutException:
        LOGGER.warning("iranpaper_login_inputs_not_clickable")
        return False

    try:
        email_input.clear()
        email_input.send_keys(username)
        password_input.clear()
        password_input.send_keys(password)
        LOGGER.debug("iranpaper_login_form_filled")
    except Exception as exc:
        LOGGER.warning("iranpaper_login_fill_error | err=%s", exc)
        return False

    # تلاش برای پیدا کردن دکمه «ورود»
    # ۱) دکمه‌هایی که type="submit" دارند
    submit_selectors = [
        "#login-form button.primary",
        "form#login-form button[type='button'].primary",
        "form#login-form button[type='submit']",
        "button[type='submit']",
    ]
    for sel in submit_selectors:
        try:
            btn = driver.find_element(By.CSS_SELECTOR, sel)
            if btn.is_displayed() and btn.is_enabled():
                btn.click()
                LOGGER.debug("iranpaper_login_submit | selector=%s", sel)
                return True
        except Exception:
            continue

    # ۲) Fallback: جستجو بین همهٔ button ها که متن «ورود» یا «login» داشته باشند
    try:
        for btn in driver.find_elements(By.TAG_NAME, "button"):
            text = (btn.text or "").strip()
            if any(word in text for word in ("ورود", "login", "Login", "SIGN IN", "Sign in", "sign in")):
                if btn.is_displayed() and btn.is_enabled():
                    btn.click()
                    LOGGER.debug("iranpaper_login_submit_fallback")
                    return True
    except Exception:
        pass

    LOGGER.warning("iranpaper_login_submit_not_found")
    return False


def _is_logged_in(driver: WebDriver) -> bool:
    """
    تأیید لاگین: دنبال لینک/دکمه خروج یا وجود متن‌های مربوط به صفحه کاربری/دسترسی مستقیم می‌گردد.
    """
    try:
        if driver.find_elements(
            By.XPATH,
            "//a[contains(@href,'logout')] | //button[contains(.,'خروج')]"
        ):
            return True
    except Exception:
        pass

    try:
        html = (driver.page_source or "").lower()
    except Exception:
        html = ""

    # متن‌هایی که معمولاً بعد از لاگین دیده می‌شوند
    if "دسترسی مستقیم" in html or "خروج" in html or "پنل کاربری" in html:
        return True

    return False


def login_to_iranpaper(driver: WebDriver, username: str, password: str) -> bool:
    """
    لاگین به IranPaper.
    به‌جای باز کردن مودال از صفحه اصلی، مستقیماً می‌رود به /login که پایدارتر است.
    """
    LOGGER.info("iranpaper_visit_login_page")
    try:
        driver.get(LOGIN_URL)
    except Exception as exc:
        LOGGER.warning("iranpaper_login_page_navigation_failed | err=%s", exc)
        return False

    # منتظر Cloudflare / Turnstile
    _wait_challenge(driver, total_ms=30000)

    # اوورلی‌ها و بنرهای کوکی را ببند
    _dismiss_overlays(driver)

    # منتظر ظاهر شدن فرم لاگین
    if not _wait_login_form(driver, timeout=30):
        LOGGER.warning("iranpaper_login_form_timeout")
        return False

    # پر کردن فرم
    if not _fill_login_form(driver, username, password):
        LOGGER.warning("iranpaper_login_fill_failed")
        return False

    # منتظر تأیید لاگین
    try:
        WebDriverWait(driver, 40).until(lambda d: _is_logged_in(d))
    except TimeoutException:
        if not _is_logged_in(driver):
            LOGGER.warning("iranpaper_login_not_confirmed | url=%s", driver.current_url)
            return False

    LOGGER.info("iranpaper_login_success")
    return True


# =====================
# مرحله ۳: دسترسی مستقیم و ScienceDirect
# =====================

def _click_direct_access_tile(driver: WebDriver) -> bool:
    """
    روی کارت «دسترسی مستقیم» کلیک می‌کند.
    اول بر اساس متن جستجو می‌کند و اگر پیدا نشد، از CSS ضبط‌شده در JSON استفاده می‌کند.
    """
    with suppress(Exception):
        driver.switch_to.default_content()

    # ۱) تلاش براساس متن «دسترسی مستقیم»
    try:
        tile = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((
                By.XPATH,
                # یک div با کلاس item و c-pointer که داخلش متن «دسترسی مستقیم» دیده می‌شود
                "//div[contains(@class,'item') and contains(@class,'c-pointer')]"
                "[.//text()[contains(normalize-space(),'دسترسی مستقیم')]]"
            ))
        )
        driver.execute_script(
            "arguments[0].scrollIntoView({behavior:'smooth', block:'center'});",
            tile
        )
        time.sleep(random.uniform(0.4, 0.8))
        tile.click()
        LOGGER.debug("iranpaper_directaccess_tile_clicked | via text xpath")
        return True
    except TimeoutException:
        LOGGER.warning("iranpaper_directaccess_tile_text_not_found, trying CSS fallback...")
    except Exception as exc:
        LOGGER.warning("iranpaper_directaccess_tile_text_error | err=%s", exc)

    # ۲) Fallback: همان CSS که افزونه ضبط کرده بود (روی svg کارت کلیک می‌کنیم)
    css_sel = (
        "div.w-100.mt-9.mb-0.mb-0 > "
        "div.d-flex.row.w-80.mx-auto.justify-center.align-center.mb-1 > "
        "div.col.d-flex.flex-column.rounded-lg.justify-center.align-center.px-0.mx-0.item.c-pointer.white--text > "
        "div.mt-4.svg-size.text-center.rounded-lg.pa-1.d-flex.align-center.justify-center.v-card.v-card--link."
        "v-sheet.theme--light.elevation-0.white.item-unselected > svg"
    )
    try:
        tile_svg = WebDriverWait(driver, 15).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, css_sel))
        )
        driver.execute_script(
            "arguments[0].scrollIntoView({behavior:'smooth', block:'center'});",
            tile_svg
        )
        time.sleep(random.uniform(0.4, 0.8))
        tile_svg.click()
        LOGGER.debug("iranpaper_directaccess_tile_clicked | via CSS recorded selector")
        return True
    except TimeoutException:
        LOGGER.warning("iranpaper_directaccess_tile_css_timeout")
    except Exception as exc:
        LOGGER.warning("iranpaper_directaccess_tile_css_error | err=%s", exc)

    return False


def _find_scidir_link_button_from_table(driver: WebDriver):
    """
    در جدول «دسترسی مستقیم»:
    - ردیفی که متنش ScienceDirect است را پیدا می‌کند.
    - سپس در همان ردیف یا ردیف‌های بعدی، دکمه «لینک» مربوطه را برمی‌گرداند.
    این کار برای حالت‌هایی که ویوتیفای ردیف را روی چند <tr> می‌شکند هم کمک می‌کند.
    """
    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "table tbody tr"))
        )
    except TimeoutException:
        LOGGER.warning("iranpaper_directaccess_table_timeout")
        return None
    except Exception as exc:
        LOGGER.warning("iranpaper_directaccess_table_error | err=%s", exc)
        return None

    rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
    if not rows:
        LOGGER.warning("iranpaper_directaccess_table_empty")
        return None

    # پیدا کردن ایندکس ردیفی که ScienceDirect در آن است
    target_idx = None
    for idx, row in enumerate(rows):
        try:
            text = row.text or ""
        except Exception:
            continue
        low = text.lower()
        if (
            "sciencedirect" in low
            or "science direct" in low
            or "ساینس دایرکت" in text
            or "ساینس" in text
        ):
            target_idx = idx
            LOGGER.debug("iranpaper_scidir_row_found | index=%s text=%s",
                         idx, text.replace("\n", " ")[:80])
            break

    if target_idx is None:
        LOGGER.warning("iranpaper_scidir_row_not_found_in_table")
        return None

    # در بعضی قالب‌ها دکمه «لینک» در ردیف بعدی/بعدی‌ها می‌آید
    for j in range(target_idx, min(target_idx + 3, len(rows))):
        row = rows[j]

        # ۱) دکمه‌هایی که متن‌شان «لینک» / «Link» باشد
        try:
            btns = row.find_elements(
                By.XPATH,
                ".//button[contains(normalize-space(.),'لینک') or "
                "contains(normalize-space(.),'Link') or contains(normalize-space(.),'link')]"
            )
        except Exception:
            btns = []
        for b in btns:
            try:
                if b.is_displayed() and b.is_enabled():
                    LOGGER.debug("iranpaper_scidir_button_found_in_row | row_index=%s", j)
                    return b
            except Exception:
                continue

        # ۲) Fallback: CSS ضبط‌شده از اکستنشن
        try:
            btn = row.find_element(
                By.CSS_SELECTOR,
                "td.text-center div.d-flex.flex-row.align-center.justify-center.w-100 "
                "a.white--text.ml-1.pa-0 > button"
            )
            if btn.is_displayed() and btn.is_enabled():
                LOGGER.debug("iranpaper_scidir_button_found_via_css | row_index=%s", j)
                return btn
        except Exception:
            pass

    LOGGER.warning("iranpaper_scidir_link_button_not_found_near_row")
    return None


def open_sciencedirect_via_iranpaper(driver: WebDriver) -> Optional[str]:
    """
    فرض: کاربر از قبل در IranPaper لاگین شده است.

    مراحل:
      1) رفتن به صفحه اصلی IranPaper
      2) کلیک روی کارت «دسترسی مستقیم»
      3) پیدا کردن ردیف ScienceDirect + دکمه «لینک»
      4) کلیک روی دکمه «لینک ۱»
      5) سوئیچ به تب جدید daccess / sciencedirect و برگرداندن handle آن
    """
    LOGGER.info("iranpaper_open_scidir")
    try:
        driver.get(HOME_URL)
    except Exception as exc:
        LOGGER.warning("iranpaper_home_navigation_failed | err=%s", exc)
        return None

    # اوورلی‌ها را ببند
    _dismiss_overlays(driver)

    # کلیک روی کارت «دسترسی مستقیم»
    if not _click_direct_access_tile(driver):
        LOGGER.warning("iranpaper_directaccess_tile_failed")
        return None

    # کمی صبر تا جدول بیاید
    time.sleep(1.0)

    # پیدا کردن دکمه لینک ScienceDirect از داخل جدول
    try:
        link_btn = WebDriverWait(driver, 25).until(
            lambda d: _find_scidir_link_button_from_table(d)
        )
    except TimeoutException:
        LOGGER.warning("iranpaper_scidir_link_button_not_found")
        return None

    if not link_btn:
        LOGGER.warning("iranpaper_scidir_link_button_is_none")
        return None

    # کلیک روی لینک و منتظر باز شدن تب جدید
    handles_before = set(driver.window_handles)
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({behavior:'smooth', block:'center'});",
            link_btn
        )
        time.sleep(random.uniform(0.4, 0.8))
        link_btn.click()
        LOGGER.debug("iranpaper_scidir_link_clicked")
    except Exception as exc:
        LOGGER.warning("iranpaper_scidir_link_click_error | err=%s", exc)
        return None

    try:
        WebDriverWait(driver, 20).until(
            lambda d: len(d.window_handles) > len(handles_before)
        )
    except TimeoutException:
        LOGGER.warning("iranpaper_scidir_new_tab_missing")
        return None

    # پیدا کردن هندل تب جدید
    new_handle = next(
        (h for h in driver.window_handles if h not in handles_before),
        driver.current_window_handle
    )
    driver.switch_to.window(new_handle)

    # صبر تا daccess / ScienceDirect آماده شود
    try:
        WebDriverWait(driver, 45).until(
            lambda d: "daccess" in (d.current_url or "").lower()
        )
    except TimeoutException:
        LOGGER.warning(
            "iranpaper_scidir_proxy_timeout | url=%s", driver.current_url
        )
        return None

    LOGGER.info("iranpaper_scidir_proxy_ready | url=%s", driver.current_url)
    return new_handle


# =====================
# مرحله ۴: تابع کمکی اصلی برای بیرون
# =====================

def ensure_sciencedirect_session(driver: WebDriver, username: str, password: str) -> Optional[str]:
    """
    از بیرون فقط این را صدا بزن:

      handle = ensure_sciencedirect_session(driver, user, pwd)

    اگر همه‌چیز خوب پیش برود، handle تب ScienceDirect را برمی‌گرداند.
    """
    if not login_to_iranpaper(driver, username, password):
        return None
    return open_sciencedirect_via_iranpaper(driver)
