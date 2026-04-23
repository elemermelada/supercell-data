import os
import requests
import jwt
import browser_cookie3
from jwt import ExpiredSignatureError, InvalidTokenError
from datetime import datetime, timezone, timedelta
from logger import get_logger

logger = get_logger(__name__)

BEGIN_URL = "https://support.supercell.com/api/gdpr/begin"
SUBMIT_URL = "https://support.supercell.com/api/gdpr/submit"


# ---------------------------------------------------------
# JWT decode + expiration check
# ---------------------------------------------------------
def on_token_expiring_soon(decoded_jwt, expires_at):
    logger.warning("JWT expires in less than 3 days")
    logger.warning(f"Expiration time: {expires_at}")


def decode_jwt_and_check_expiry(token: str):
    try:
        decoded = jwt.decode(token, options={"verify_signature": False})
        exp = decoded.get("exp")

        if not exp:
            logger.warning("JWT has no exp claim")
            return decoded

        expires_at = datetime.fromtimestamp(exp, tz=timezone.utc)
        now = datetime.now(timezone.utc)
        remaining = expires_at - now

        logger.info(f"JWT expires at: {expires_at}")
        logger.info(f"JWT time remaining: {remaining}")

        if remaining < timedelta(days=3):
            on_token_expiring_soon(decoded, expires_at)

        return decoded

    except ExpiredSignatureError:
        logger.warning("JWT token is expired")
    except InvalidTokenError:
        logger.warning("JWT token is invalid")

    return None


# ---------------------------------------------------------
# Load cookies from Chrome
# ---------------------------------------------------------
def load_browser_cookies(session):
    logger.info("Loading cookies from Chrome...")

    try:
        chrome_cookies = browser_cookie3.chrome(domain_name="supercell.com")
    except Exception as e:
        raise RuntimeError(f"Failed to load browser cookies: {e}")

    count = 0
    for c in chrome_cookies:
        session.cookies.set(c.name, c.value, domain=c.domain, path=c.path)
        count += 1

    logger.info(f"Loaded {count} cookies from Chrome")


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
    user_jwt = os.getenv("SUPERCELL_ACCOUNT_INFO_TOKEN")
    if not user_jwt:
        raise EnvironmentError("SUPERCELL_ACCOUNT_INFO_TOKEN not set in .env")

    logger.info("Decoding JWT...")
    decode_jwt_and_check_expiry(user_jwt)

    session = requests.Session()

    load_browser_cookies(session)

    session.cookies.set(
        "account-user-info-token",
        user_jwt,
        domain="support.supercell.com",
        path="/"
    )

    csrf_token = fetch_csrf(session, game="hay-day", action="request")
    submit_request(session, csrf_token, game="hay-day", action="request")


if __name__ == "__main__":
    from logger import setup_console_logging
    setup_console_logging()
    request()
