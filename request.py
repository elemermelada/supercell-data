from collections.abc import Callable
from http.cookiejar import CookieJar

import browser_cookie3
import requests

from config import BROWSER_CHOICES, settings
from logger import get_logger

logger = get_logger(__name__)

BEGIN_URL = "https://support.supercell.com/api/gdpr/begin"
SUBMIT_URL = "https://support.supercell.com/api/gdpr/submit"

# Supercell ID single sign-on. The support API only accepts a short-lived
# (1 hour) `account-user-info-token` cookie, which the support site's JS mints
# on every page load by exchanging the long-lived Supercell ID cookies
# (`scsso_*`, ~1 year) for an authorization code and posting it to the support
# backend. The browser cookie store therefore almost never holds a valid token
# by the time the scheduled run starts, so request() performs the same exchange
# itself. Client id and scope are the support site's own, taken from its page
# config (`ssoClientId`) and bundle.
SSO_AUTHORIZE_URL = "https://accounts.supercell.com/oauth/sso/authorize"
SSO_LOGIN_URL = "https://support.supercell.com/api/oauth/sso/login"
SSO_CLIENT_ID = "65109BC2-72CD-423B-B151-5F47E812C3BE"
SSO_SCOPE = (
    "social.profile identity.email "
    "identity.connections_raw identity.connections_game_data"
)
SUPPORT_ORIGIN = "https://support.supercell.com"

# Seconds to wait on any single HTTP call before giving up, so a hung
# connection can't stall the whole scheduled run indefinitely.
HTTP_TIMEOUT = 30

USER_AGENT = settings.user_agent

# Browser type -> cookie-loading callable. Keys must stay in sync with
# config.BROWSER_CHOICES: config validates browser_type against that set, so a
# choice allowed there but missing here would pass validation and then KeyError
# at runtime. tests/test_request.py guards the two against drifting.
FETCHERS: dict[str, Callable[..., CookieJar]] = {
    "firefox": browser_cookie3.firefox,
    "chrome": browser_cookie3.chrome,
}

# Guard: FETCHERS and config.BROWSER_CHOICES must cover exactly the same set. A
# choice allowed by config but missing here would pass validation and then
# KeyError at runtime (for only that browser). Fail loudly at import instead.
if set(FETCHERS) != set(BROWSER_CHOICES):
    raise RuntimeError(
        "FETCHERS and config.BROWSER_CHOICES are out of sync — "
        f"only in FETCHERS: {sorted(set(FETCHERS) - set(BROWSER_CHOICES))}, "
        f"only in BROWSER_CHOICES: {sorted(set(BROWSER_CHOICES) - set(FETCHERS))}"
    )


# ---------------------------------------------------------
# Fetch cookies from the specified browser
# ---------------------------------------------------------
def browser_cookie_fetcher() -> Callable[..., CookieJar]:
    # config.py validated browser_type against BROWSER_CHOICES at import, so it
    # is guaranteed to be one of the keys below.
    browser_type = settings.browser_type
    logger.info(f"Using browser type: {browser_type}")

    return FETCHERS[browser_type]


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
# Log in to the support site via Supercell ID SSO
# ---------------------------------------------------------
def sso_login(session: requests.Session):
    """Exchange the Supercell ID cookies for a fresh support-site session.

    Raises RuntimeError with an actionable message when the Supercell ID login
    itself has expired, since only a manual login in the browser can fix that.
    """
    relogin_hint = (
        f"log in to Supercell ID at {SUPPORT_ORIGIN} in {settings.browser_type}"
    )
    logger.info("Authorizing with Supercell ID...")

    r = session.post(
        SSO_AUTHORIZE_URL,
        data={"client_id": SSO_CLIENT_ID, "scope": SSO_SCOPE},
        headers={
            "Origin": SUPPORT_ORIGIN,
            "Referer": f"{SUPPORT_ORIGIN}/",
            "User-Agent": USER_AGENT,
        },
        timeout=HTTP_TIMEOUT,
    )
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code == 401:
        raise RuntimeError(f"Supercell ID session expired or missing; {relogin_hint}")
    if not r.ok or not body.get("ok"):
        raise RuntimeError(
            f"Supercell ID authorize failed: HTTP {r.status_code} — "
            f"{body.get('error') or r.text[:200]}"
        )

    data = body.get("data") or {}
    if data.get("refreshNeeded"):
        raise RuntimeError(f"Supercell ID session needs a refresh; {relogin_hint}")
    code = data.get("authorizationCode")
    if not code:
        raise RuntimeError("Supercell ID authorize returned no authorization code")
    logger.info(f"Supercell ID session expires: {data.get('sessionExpiration')}")

    r = session.post(
        SSO_LOGIN_URL,
        json={"authorizationCode": code},
        headers={"User-Agent": USER_AGENT},
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    logger.info("Logged in to support site")


# ---------------------------------------------------------
# Fetch CSRF cookie
# ---------------------------------------------------------
def fetch_csrf(session: requests.Session, game: str, action: str):
    params = {"game": game, "action": action}
    logger.info("Fetching CSRF token...")

    r = session.get(BEGIN_URL, params=params, timeout=HTTP_TIMEOUT)
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
        "User-Agent": USER_AGENT,
    }

    payload = {"game": game, "action": action}

    logger.info("Submitting GDPR request...")
    r = session.post(SUBMIT_URL, json=payload, headers=headers, timeout=HTTP_TIMEOUT)
    logger.info(f"Response status: {r.status_code}")
    logger.debug(f"Response body: {r.text}")

    return r


# ---------------------------------------------------------
# Main entry point
# ---------------------------------------------------------
def request():
    session = requests.Session()

    load_browser_cookies(session)
    sso_login(session)

    csrf_token = fetch_csrf(session, game="hay-day", action="request")
    response = submit_request(session, csrf_token, game="hay-day", action="request")

    if response.status_code != 200:
        raise RuntimeError(
            f"GDPR request failed: HTTP {response.status_code} — "
            f"{response.text[:200]}"
        )


if __name__ == "__main__":
    from logger import setup_console_logging

    setup_console_logging()
    request()
