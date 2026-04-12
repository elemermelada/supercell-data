import os
import requests
import jwt
import browser_cookie3
from jwt import ExpiredSignatureError, InvalidTokenError
from datetime import datetime, timezone, timedelta

BEGIN_URL = "https://support.supercell.com/api/gdpr/begin"
SUBMIT_URL = "https://support.supercell.com/api/gdpr/submit"


# ---------------------------------------------------------
# JWT decode + expiration check
# ---------------------------------------------------------
def on_token_expiring_soon(decoded_jwt, expires_at):
    print("[ALERT] JWT expires in less than 3 days")
    print(f"[ALERT] Expiration time: {expires_at}")


def decode_jwt_and_check_expiry(token: str):
    try:
        decoded = jwt.decode(token, options={"verify_signature": False})
        exp = decoded.get("exp")

        if not exp:
            print("[JWT] No exp claim found")
            return decoded

        expires_at = datetime.fromtimestamp(exp, tz=timezone.utc)
        now = datetime.now(timezone.utc)
        remaining = expires_at - now

        print(f"[JWT] Token expires at: {expires_at}")
        print(f"[JWT] Time remaining: {remaining}")

        if remaining < timedelta(days=3):
            on_token_expiring_soon(decoded, expires_at)

        return decoded

    except ExpiredSignatureError:
        print("[JWT] Token is expired")
    except InvalidTokenError:
        print("[JWT] Invalid token")

    return None


# ---------------------------------------------------------
# Load cookies from Chrome
# ---------------------------------------------------------
def load_browser_cookies(session):
    """
    Loads cookies from Chrome for *.supercell.com.
    This gives us the exact cookies the browser uses.
    """
    print("[INFO] Loading cookies from Chrome...")

    try:
        chrome_cookies = browser_cookie3.chrome(domain_name="supercell.com")
    except Exception as e:
        raise RuntimeError(f"Failed to load browser cookies: {e}")

    count = 0
    for c in chrome_cookies:
        session.cookies.set(c.name, c.value, domain=c.domain, path=c.path)
        count += 1

    print(f"[INFO] Loaded {count} cookies from Chrome")


# ---------------------------------------------------------
# Fetch CSRF cookie
# ---------------------------------------------------------
def fetch_csrf(session: requests.Session, game: str, action: str):
    params = {"game": game, "action": action}
    print("[INFO] Fetching CSRF token...")

    r = session.get(BEGIN_URL, params=params)
    r.raise_for_status()

    csrf_cookie = session.cookies.get("csrf_")
    if not csrf_cookie:
        raise RuntimeError("CSRF cookie not found in response")

    print(f"[INFO] CSRF cookie obtained: {csrf_cookie}")
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

    print("[INFO] Submitting GDPR request...")
    r = session.post(SUBMIT_URL, json=payload, headers=headers)
    print(f"[INFO] Response status: {r.status_code}")
    print("[INFO] Response body:", r.text)

    return r


# ---------------------------------------------------------
# Main entry point
# ---------------------------------------------------------
def request():
    # Load JWT from environment
    user_jwt = os.getenv("SUPERCELL_ACCOUNT_INFO_TOKEN")
    if not user_jwt:
        raise EnvironmentError("SUPERCELL_ACCOUNT_INFO_TOKEN not set in .env")

    print("\n=== Decoding JWT ===")
    decode_jwt_and_check_expiry(user_jwt)

    session = requests.Session()

    # Step 1: Load cookies from Chrome
    load_browser_cookies(session)

    # Step 2: Ensure JWT cookie is present
    session.cookies.set(
        "account-user-info-token",
        user_jwt,
        domain="support.supercell.com",
        path="/"
    )

    # Step 3: Fetch CSRF
    csrf_token = fetch_csrf(session, game="hay-day", action="request")

    # Step 4: Submit GDPR request
    submit_request(session, csrf_token, game="hay-day", action="request")


if __name__ == "__main__":
    request()
