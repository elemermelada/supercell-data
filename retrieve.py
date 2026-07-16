import email
import imaplib
import json
import os
import re
from datetime import UTC, datetime
from email.header import decode_header
from email.utils import parsedate_to_datetime

import requests

from logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------
# Environment variables
# ---------------------------------------------------------
IMAP_SERVER = os.getenv("IMAP_SERVER")
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
SENDER_FILTER = os.getenv("SENDER_FILTER")

# ---------------------------------------------------------
# Persisted state: last retrieved email date
# ---------------------------------------------------------
STATE_FILE = os.getenv("STATE_FILE", "state.json")
DEFAULT_SINCE = datetime(2024, 1, 1, tzinfo=UTC)


# ---------------------------------------------------------
# Utility: Ensure a datetime is timezone-aware (assume UTC)
# ---------------------------------------------------------
def ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


# ---------------------------------------------------------
# Read last retrieved email date from state file
# ---------------------------------------------------------
def read_last_email_date() -> datetime:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
        value = state.get("last_email_date")
        if value:
            last = ensure_aware(datetime.fromisoformat(value))
            logger.info(f"Resuming from last email date: {last.isoformat()}")
            return last
    except FileNotFoundError:
        logger.info(f"No state file at {STATE_FILE}, starting from default date")
    except Exception as e:
        logger.warning(f"Could not read state file: {e}")

    return DEFAULT_SINCE


# ---------------------------------------------------------
# Write last retrieved email date to state file
# ---------------------------------------------------------
def write_last_email_date(dt: datetime) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"last_email_date": dt.isoformat()}, f)
        logger.info(f"Saved last email date: {dt.isoformat()}")
    except Exception as e:
        logger.warning(f"Could not write state file: {e}")


# ---------------------------------------------------------
# Utility: Convert date to IMAP format
# ---------------------------------------------------------
def convert_date(date_str: str) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return dt.strftime("%d-%b-%Y")


# ---------------------------------------------------------
# Connect to IMAP
# ---------------------------------------------------------
def connect_imap():
    if not all([IMAP_SERVER, EMAIL_USER, EMAIL_PASS]):
        raise OSError("Missing IMAP environment variables")

    logger.info("Connecting to IMAP server...")
    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    mail.login(EMAIL_USER, EMAIL_PASS)
    logger.info("Logged in successfully")

    return mail


# ---------------------------------------------------------
# Search for matching emails
# ---------------------------------------------------------
def search_emails(mail, sender: str, since_date: str):
    imap_date = convert_date(since_date)

    logger.info(f"Searching for emails FROM '{sender}' SINCE {imap_date}")

    mail.select("INBOX")
    status, data = mail.search(None, f'(FROM "{sender}" SINCE {imap_date})')

    if status != "OK":
        raise RuntimeError("IMAP search failed")

    email_ids = data[0].split()
    logger.info(f"Found {len(email_ids)} matching emails")

    return email_ids


# ---------------------------------------------------------
# Extract plain text body
# ---------------------------------------------------------
def extract_plaintext(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                return part.get_payload(decode=True).decode(errors="ignore")
        return ""
    else:
        return msg.get_payload(decode=True).decode(errors="ignore")


# ---------------------------------------------------------
# Extract download link from email body
# ---------------------------------------------------------
def extract_download_link(body: str):
    """
    Extracts ONLY the Supercell GDPR download link.
    Must:
      - start with https://mydata.supercell.com/data/
      - end with .html
    """
    pattern = r"https://mydata\.supercell\.com/data/[A-Za-z0-9\-_]+\.html"
    match = re.search(pattern, body)
    return match.group(0) if match else None


# ---------------------------------------------------------
# Append email date to HTML file
# ---------------------------------------------------------
def append_email_date_to_html(html_path, email_date):
    try:
        with open(html_path, "a", encoding="utf-8") as f:
            f.write(f"\n<!-- EMAIL_DATE: {email_date.isoformat()} -->\n")
        logger.info(f"Appended email date to {html_path}")
    except Exception as e:
        logger.warning(f"Failed to append email date: {e}")


# ---------------------------------------------------------
# Download the linked file
# ---------------------------------------------------------
def download_file(url: str):
    logger.info(f"Downloading data from: {url}")

    try:
        r = requests.get(url)
    except Exception as e:
        logger.warning(f"Request failed: {e}")
        return None

    if r.status_code == 403:
        logger.warning("Link expired (HTTP 403). Skipping.")
        return None

    if r.status_code != 200:
        logger.warning(f"Unexpected status code {r.status_code}. Skipping.")
        return None

    os.makedirs("downloads", exist_ok=True)

    filename = url.split("/")[-1]
    filepath = os.path.join("downloads", filename)

    with open(filepath, "wb") as f:
        f.write(r.content)

    logger.info(f"Saved file to: {filepath}")
    return filepath


# ---------------------------------------------------------
# Process a single email
# ---------------------------------------------------------
def process_email(mail, email_id) -> tuple[datetime | None, bool]:
    """Return (email_date, downloaded). downloaded is True only when the
    export HTML was successfully fetched and saved."""
    status, msg_data = mail.fetch(email_id, "(RFC822)")
    if status != "OK":
        logger.warning(f"Failed to fetch email ID {email_id}")
        return None, False

    raw_email = msg_data[0][1]
    msg = email.message_from_bytes(raw_email)

    email_date_raw = msg.get("Date")
    email_date = (
        ensure_aware(parsedate_to_datetime(email_date_raw)) if email_date_raw else None
    )

    subject, enc = decode_header(msg["Subject"])[0]
    if isinstance(subject, bytes):
        subject = subject.decode(enc or "utf-8", errors="ignore")

    logger.debug(
        f"From: {msg.get('From')} | Subject: {subject} | Date: {email_date_raw}"
    )

    body = extract_plaintext(msg)

    url = extract_download_link(body)
    if not url:
        logger.warning("No download link found in email")
        return email_date, False

    html_path = download_file(url)
    if not html_path:
        return email_date, False

    append_email_date_to_html(html_path, email_date)
    return email_date, True


# ---------------------------------------------------------
# Main entry point
# ---------------------------------------------------------
def retrieve():
    if not SENDER_FILTER:
        raise OSError("SENDER_FILTER missing")

    last_date = read_last_email_date()
    since_date = last_date.strftime("%Y-%m-%d")

    mail = connect_imap()

    newest_date = last_date
    processed = 0
    try:
        email_ids = search_emails(mail, sender=SENDER_FILTER, since_date=since_date)

        for eid in email_ids:
            email_date, downloaded = process_email(mail, eid)
            if email_date and email_date > newest_date:
                newest_date = email_date
            # Only count emails newer than the last processed run as "new".
            if downloaded and email_date and email_date > last_date:
                processed += 1

        if newest_date > last_date:
            write_last_email_date(newest_date)
    finally:
        try:
            mail.close()
        except Exception:
            pass
        try:
            mail.logout()
        except Exception:
            pass

    if processed == 0:
        raise RuntimeError(
            f"No new emails processed (searched FROM '{SENDER_FILTER}' "
            f"SINCE {since_date})"
        )

    logger.info(f"Processed {processed} new email(s). Done.")


if __name__ == "__main__":
    from logger import setup_console_logging

    setup_console_logging()
    retrieve()
