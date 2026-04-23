import os
import re
import imaplib
import email
import requests
from email.header import decode_header
from email.utils import parsedate_to_datetime
from datetime import datetime

# ---------------------------------------------------------
# Environment variables
# ---------------------------------------------------------
IMAP_SERVER = os.getenv("IMAP_SERVER")
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
SENDER_FILTER = os.getenv("SENDER_FILTER")


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
        raise EnvironmentError("Missing IMAP environment variables")

    print("[INFO] Connecting to IMAP server...")
    mail = imaplib.IMAP4_SSL(IMAP_SERVER)
    mail.login(EMAIL_USER, EMAIL_PASS)
    print("[INFO] Logged in successfully")

    return mail


# ---------------------------------------------------------
# Search for matching emails
# ---------------------------------------------------------
def search_emails(mail, sender: str, since_date: str):
    imap_date = convert_date(since_date)

    print(f"[INFO] Searching for emails FROM '{sender}' SINCE {imap_date}")

    mail.select("INBOX")
    status, data = mail.search(
        None,
        f'(FROM "{sender}" SINCE {imap_date})'
    )

    if status != "OK":
        raise RuntimeError("IMAP search failed")

    email_ids = data[0].split()
    print(f"[INFO] Found {len(email_ids)} matching emails")

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
        print(f"[INFO] Appended email date to {html_path}")
    except Exception as e:
        print(f"[WARN] Failed to append email date: {e}")


# ---------------------------------------------------------
# Download the linked file
# ---------------------------------------------------------
def download_file(url: str):
    print(f"[INFO] Downloading data from: {url}")

    try:
        r = requests.get(url)
    except Exception as e:
        print(f"[WARN] Request failed: {e}")
        return None

    # Ignore expired links
    if r.status_code == 403:
        print("[WARN] Link expired (HTTP 403). Skipping.")
        return None

    # Handle other errors normally
    if r.status_code != 200:
        print(f"[WARN] Unexpected status code {r.status_code}. Skipping.")
        return None

    os.makedirs("downloads", exist_ok=True)

    filename = url.split("/")[-1]
    filepath = os.path.join("downloads", filename)

    with open(filepath, "wb") as f:
        f.write(r.content)

    print(f"[INFO] Saved file to: {filepath}")
    return filepath


# ---------------------------------------------------------
# Process a single email
# ---------------------------------------------------------
def process_email(mail, email_id):
    status, msg_data = mail.fetch(email_id, "(RFC822)")
    if status != "OK":
        print(f"[WARN] Failed to fetch email ID {email_id}")
        return

    raw_email = msg_data[0][1]
    msg = email.message_from_bytes(raw_email)

    # Parse email date
    email_date_raw = msg.get("Date")
    email_date = parsedate_to_datetime(email_date_raw)

    # Decode subject
    subject, enc = decode_header(msg["Subject"])[0]
    if isinstance(subject, bytes):
        subject = subject.decode(enc or "utf-8", errors="ignore")

    print("\n--- EMAIL ---")
    print("From:", msg.get("From"))
    print("Subject:", subject)
    print("Date:", email_date_raw)

    body = extract_plaintext(msg)
    print("Body:\n", body)

    # Extract link
    url = extract_download_link(body)
    if not url:
        print("[WARN] No download link found in email")
        return

    # Download file
    html_path = download_file(url)
    if not html_path:
        return

    # Append email date to the HTML file
    append_email_date_to_html(html_path, email_date)


# ---------------------------------------------------------
# Main entry point
# ---------------------------------------------------------
def retrieve():
    if not SENDER_FILTER:
        raise EnvironmentError("SENDER_FILTER missing")

    # Hardcoded date
    since_date = "2024-01-01"

    mail = connect_imap()

    email_ids = search_emails(
        mail,
        sender=SENDER_FILTER,
        since_date=since_date
    )

    for eid in email_ids:
        process_email(mail, eid)

    mail.close()
    mail.logout()
    print("\n[INFO] Done.")


if __name__ == "__main__":
    retrieve()
