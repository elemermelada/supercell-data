import os
from collections.abc import Callable
from http.cookiejar import CookieJar

import browser_cookie3
import requests

from logger import get_logger

logger = get_logger(__name__)

BEGIN_URL = "https://support.supercell.com/api/gdpr/begin"
SUBMIT_URL = "https://support.supercell.com/api/gdpr/submit"


# ---------------------------------------------------------
# Fetch cookies from the specified browser
# ---------------------------------------------------------
def browser_cookie_fetcher() -> Callable[..., CookieJar]:
    browser_type = os.getenv("BROWSER_TYPE", "firefox").lower()
    logger.info(f"Using browser type: {browser_type}")

    if browser_type == "firefox":
        return browser_cookie3.firefox
    elif browser_type == "chrome":
        return browser_cookie3.chrome
    else:
        raise ValueError(f"Unsupported browser type: {browser_type}")


# ---------------------------------------------------------
# Load cookies from Browser
# ---------------------------------------------------------
def load_browser_cookies(session):
    logger.info("Loading cookies from Browser...")

    try:
        cookies = browser_cookie_fetcher()(domain_name="supercell.com")
    except Exception as e:
        raise RuntimeError(f"Failed to load browser cookies: {e}") from e

    count = 0
    for c in cookies:
        session.cookies.set(c.name, c.value, domain=c.domain, path=c.path)
        count += 1

    logger.info(f"Loaded {count} cookies from Browser")


# ---------------------------------------------------------
# Fetch CSRF cookie
# ---------------------------------------------------------
def fetch_csrf(session: requests.Session, game: str, action: str):
    params = {"game": game, "action": action}
    logger.info("Fetching CSRF token...")

    r = session.get(BEGIN_URL, params=params)
    r.raise_for_status()

    csrf_cookie = session.cookies.get("csrf_")
    if not csrf_cookie:
        raise RuntimeError("CSRF cookie not found in response")

    logger.info(f"CSRF cookie obtained: {csrf_cookie}")
    return csrf_cookie


# ---------------------------------------------------------
# Submit GDPR request
# ---------------------------------------------------------
def submit_request(session: requests.Session, csrf_token: str, game: str, action: str):
    headers = {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrf_token,
        "Origin": "https://support.supercell.com",
        "Referer": f"https://support.supercell.com/{game}/en/articles/gdpr.html",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/146.0.0.0 Safari/537.36"
        ),
    }

    payload = {"game": game, "action": action}

    logger.info("Submitting GDPR request...")
    r = session.post(SUBMIT_URL, json=payload, headers=headers)
    logger.info(f"Response status: {r.status_code}")
    logger.debug(f"Response body: {r.text}")

    return r


# ---------------------------------------------------------
# Main entry point
# ---------------------------------------------------------
def request():
    session = requests.Session()

    load_browser_cookies(session)

    csrf_token = fetch_csrf(session, game="hay-day", action="request")
    submit_request(session, csrf_token, game="hay-day", action="request")


if __name__ == "__main__":
    from logger import setup_console_logging

    setup_console_logging()
    request()
