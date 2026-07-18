import email
import imaplib
import json
import os
import re
from datetime import UTC, datetime
from email.header import decode_header
from email.utils import parsedate_to_datetime
from time import sleep

import requests

from config import DOWNLOAD_DIR, settings
from logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------
# Persisted state: last retrieved email date
# ---------------------------------------------------------
STATE_FILE = settings.state_file
DEFAULT_SINCE = datetime(2024, 1, 1, tzinfo=UTC)

# A file existing in DOWNLOAD_DIR (shared via config) is what dedup is based on.

# Seconds to wait on the file download request before giving up.
HTTP_TIMEOUT = 30

# ---------------------------------------------------------
# Retry behavior when no new emails have arrived yet
# ---------------------------------------------------------
# The export email arrives shortly after the request, so retrieve() polls for
# it: up to RETRIEVE_MAX_ATTEMPTS tries, waiting RETRIEVE_RETRY_INTERVAL
# seconds between them.
RETRIEVE_RETRY_INTERVAL = settings.retrieve_retry_interval
RETRIEVE_MAX_ATTEMPTS = settings.retrieve_max_attempts


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
        # Atomic replace so a crash mid-write can't corrupt the state file.
        tmp_path = f"{STATE_FILE}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump({"last_email_date": dt.isoformat()}, f)
        os.replace(tmp_path, STATE_FILE)
        logger.info(f"Saved last email date: {dt.isoformat()}")
    except Exception as e:
        # Don't raise: a state-write failure must not fail a successful
        # download; the skip path repairs the state file on the next run.
        logger.error(f"Could not write state file: {e}")


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
    imap_server, email_user, email_pass = settings.require_imap()

    logger.info("Connecting to IMAP server...")
    mail = imaplib.IMAP4_SSL(imap_server)
    mail.login(email_user, email_pass)
    logger.info("Logged in successfully")

    return mail


# ---------------------------------------------------------
# Search for matching emails
# ---------------------------------------------------------
def search_emails(mail, sender: str, since_date: str):
    imap_date = convert_date(since_date)

    logger.info(f"Searching for emails FROM '{sender}' SINCE {imap_date}")

    mail.select("INBOX")
    # Escape backslashes and quotes so a special character in the sender can't
    # break out of the IMAP quoted string and corrupt the search query.
    escaped_sender = sender.replace("\\", "\\\\").replace('"', '\\"')
    status, data = mail.search(None, f'(FROM "{escaped_sender}" SINCE {imap_date})')

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

    The matched tail is used verbatim as the on-disk filename, so this regex
    is also what sanitizes it.
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
def download_file(url: str) -> tuple[str, str | None]:
    """Download ``url`` into DOWNLOAD_DIR.

    Returns a ``(status, path)`` tuple:
      - ``("downloaded", path)`` — the export was newly fetched and saved.
      - ``("skipped", path)`` — the export file or an expired stub is already
        on disk; no HTTP request is made.
      - ``("failed", None)`` — transient error, or a 403 (permanently expired
        link) that wrote a stub so the dead link is never re-requested.
    """
    filename = url.split("/")[-1]
    filepath = os.path.join(DOWNLOAD_DIR, filename)
    # Non-.html extension keeps process() from treating the stub as an export.
    stub_path = os.path.splitext(filepath)[0] + ".expired"

    if os.path.exists(filepath):
        logger.debug(f"Already downloaded {filename}, skipping")
        return "skipped", filepath

    if os.path.exists(stub_path):
        logger.debug(f"Link for {filename} previously expired, skipping")
        return "skipped", stub_path

    logger.info(f"Downloading data from: {url}")

    try:
        r = requests.get(url, timeout=HTTP_TIMEOUT)
    except Exception as e:
        logger.warning(f"Request failed: {e}")
        return "failed", None

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    if r.status_code == 403:
        logger.warning("Link expired (HTTP 403). Writing stub so it is not retried.")
        with open(stub_path, "w", encoding="utf-8") as f:
            f.write("<!-- EXPIRED: link returned HTTP 403 -->\n")
        return "failed", None

    if r.status_code != 200:
        logger.warning(f"Unexpected status code {r.status_code}. Skipping.")
        return "failed", None

    # Atomic replace: a truncated .html would be treated as complete by dedup
    # forever.
    tmp_path = f"{filepath}.tmp"
    with open(tmp_path, "wb") as f:
        f.write(r.content)
    os.replace(tmp_path, filepath)

    logger.info(f"Saved file to: {filepath}")
    return "downloaded", filepath


# ---------------------------------------------------------
# Process a single email
# ---------------------------------------------------------
def process_email(mail, email_id) -> tuple[datetime | None, str]:
    """Return ``(email_date, status)`` where status is one of ``"downloaded"``,
    ``"skipped"`` or ``"failed"`` (see download_file)."""
    fetch_status, msg_data = mail.fetch(email_id, "(RFC822)")
    if fetch_status != "OK":
        logger.warning(f"Failed to fetch email ID {email_id}")
        return None, "failed"

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
        return email_date, "failed"

    download_status, saved_path = download_file(url)
    if download_status == "downloaded":
        append_email_date_to_html(saved_path, email_date)
    return email_date, download_status


# ---------------------------------------------------------
# Single retrieval attempt
# ---------------------------------------------------------
def _retrieve_once(sender: str, last_date: datetime) -> int:
    """Run one connect → search → process → write-state → cleanup pass.

    ``last_date`` is the resumed state (read once by the caller) and narrows
    the search window. Returns the number of newly downloaded emails. Opens (and
    closes) a fresh IMAP connection so each attempt is independent. Transient
    IMAP/network errors propagate to the caller and are not retried here.
    """
    since_date = last_date.strftime("%Y-%m-%d")

    mail = connect_imap()

    newest_date = last_date
    downloaded = 0
    skipped = 0
    try:
        email_ids = search_emails(mail, sender=sender, since_date=since_date)

        for eid in email_ids:
            email_date, status = process_email(mail, eid)

            # A skip advances the date but does NOT end the poll: a stale
            # same-day email must not satisfy a freshly requested export
            # (IMAP SINCE is day-granular).
            if status == "downloaded":
                downloaded += 1
            elif status == "skipped":
                skipped += 1
            else:
                continue

            if email_date and email_date > newest_date:
                newest_date = email_date

        # Write state even on skips: it repairs a failed state write from a
        # previous run.
        if downloaded + skipped > 0:
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

    return downloaded


# ---------------------------------------------------------
# Main entry point
# ---------------------------------------------------------
def retrieve():
    sender_filter = settings.require_sender_filter()

    # Read state once so the search window is fixed for the whole poll.
    last_date = read_last_email_date()
    since_date = last_date.strftime("%Y-%m-%d")

    # The export email may take a moment to land, so poll for it.
    for attempt in range(1, RETRIEVE_MAX_ATTEMPTS + 1):
        try:
            downloaded = _retrieve_once(sender_filter, last_date)
        except PermissionError:
            # Permanent local failure (unwritable downloads/ or state file) —
            # fail fast instead of retrying it as a network error.
            raise
        except (imaplib.IMAP4.error, OSError) as e:
            # OSError covers ConnectionError, TimeoutError and socket.timeout,
            # which flaky networks surface through imaplib.
            logger.warning(
                f"Transient IMAP/network error on attempt "
                f"{attempt}/{RETRIEVE_MAX_ATTEMPTS}: {e}"
            )
            downloaded = 0
        else:
            if downloaded > 0:
                logger.info(f"Processed {downloaded} new email(s). Done.")
                return

        if attempt < RETRIEVE_MAX_ATTEMPTS:
            logger.info(
                f"No new emails yet, retrying in {RETRIEVE_RETRY_INTERVAL}s "
                f"(attempt {attempt}/{RETRIEVE_MAX_ATTEMPTS})"
            )
            sleep(RETRIEVE_RETRY_INTERVAL)

    raise RuntimeError(
        f"No new emails processed (searched FROM '{sender_filter}' "
        f"SINCE {since_date})"
    )


if __name__ == "__main__":
    from logger import setup_console_logging

    setup_console_logging()
    retrieve()
