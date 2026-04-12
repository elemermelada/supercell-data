import os
import requests
import jwt
from jwt import ExpiredSignatureError, InvalidTokenError
from datetime import datetime, timezone, timedelta

BEGIN_URL = "https://support.supercell.com/api/gdpr/begin"
SUBMIT_URL = "https://support.supercell.com/api/gdpr/submit"

# ---------------------------------------------------------
# PLACEHOLDER: You implement this function however you want
# ---------------------------------------------------------
def on_token_expiring_soon(decoded_jwt, expires_at):
    """
    This function is called when the JWT expires in < 3 days.
    Implement your own logic here.
    """
    print("[ALERT] JWT expires in less than 3 days")
    print(f"[ALERT] Expiration time: {expires_at}")
    # Your custom logic goes here
    pass


def decode_jwt_and_check_expiry(token: str):
    """
    Decodes a JWT without verifying the signature.
    Checks if expiration is within 3 days.
    """
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


def fetch_csrf(session: requests.Session, game: str, action: str):
    """
    Calls the /begin endpoint to obtain the csrf_ cookie.
    """
    params = {"game": game, "action": action}
    print("[INFO] Fetching CSRF token...")

    r = session.get(BEGIN_URL, params=params)
    r.raise_for_status()

    csrf_cookie = session.cookies.get("csrf_")
    if not csrf_cookie:
        raise RuntimeError("CSRF cookie not found in response")

    print(f"[INFO] CSRF cookie obtained: {csrf_cookie}")
    return csrf_cookie


def submit_request(session: requests.Session, csrf_token: str, game: str, action: str):
    """
    Sends the GDPR request using the CSRF token.
    """
    headers = {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrf_token,
        "Origin": "https://support.supercell.com",
        "Referer": "https://support.supercell.com/hay-day/en/articles/gdpr.html",
    }

    payload = {"game": game, "action": action}

    print("[INFO] Submitting GDPR request...")
    r = session.post(SUBMIT_URL, json=payload, headers=headers)
    print(f"[INFO] Response status: {r.status_code}")
    print("[INFO] Response body:", r.text)

    return r


def request():
    # Read token from environment variable
    user_jwt = os.getenv("SUPERCELL_ACCOUNT_INFO_TOKEN")

    if not user_jwt:
        raise EnvironmentError(
            "Environment variable SUPERCELL_ACCOUNT_INFO_TOKEN is not set."
        )

    print("\n=== Decoding JWT ===")
    decode_jwt_and_check_expiry(user_jwt)

    session = requests.Session()
    session.cookies.set("account-user-info-token", user_jwt, domain="support.supercell.com")

    csrf_token = fetch_csrf(session, game="hay-day", action="request")
    submit_request(session, csrf_token, game="hay-day", action="request")


if __name__ == "__main__":
    request()
