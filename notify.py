import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

from logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------
# Environment variables
# ---------------------------------------------------------
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
# Where alerts are delivered; falls back to the sending account.
ALERT_EMAIL = os.getenv("ALERT_EMAIL") or EMAIL_USER


# ---------------------------------------------------------
# Build the plain-text body listing each failed step
# ---------------------------------------------------------
def _build_body(failures: list[tuple[str, BaseException, str]]) -> str:
    lines = [
        f"The supercell-data pipeline reported {len(failures)} failed step(s).",
        "See the attached log file for the full run.",
        "",
    ]
    for name, exc, tb in failures:
        lines.append(f"=== Step: {name} ===")
        lines.append(f"Error: {type(exc).__name__}: {exc}")
        lines.append("")
        lines.append(tb.rstrip())
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------
# Attach the run's log file to the message
# ---------------------------------------------------------
def _attach_log(msg: EmailMessage, log_file: str) -> None:
    try:
        # Flush handlers so the on-disk log reflects everything logged so far.
        for handler in logging.getLogger().handlers:
            handler.flush()
        with open(log_file, "rb") as f:
            data = f.read()
        msg.add_attachment(
            data,
            maintype="text",
            subtype="plain",
            filename=os.path.basename(log_file),
        )
    except Exception as e:
        logger.warning(f"Could not attach log file {log_file}: {e}")


# ---------------------------------------------------------
# Send a failure alert email with the log file attached
# ---------------------------------------------------------
def send_failure_email(
    failures: list[tuple[str, BaseException, str]], log_file: str
) -> None:
    if not failures:
        return

    if not (EMAIL_USER and EMAIL_PASS and ALERT_EMAIL):
        logger.error(
            "Cannot send alert email: missing EMAIL_USER, EMAIL_PASS or ALERT_EMAIL"
        )
        return

    failed_steps = ", ".join(name for name, _, _ in failures)
    msg = EmailMessage()
    msg["Subject"] = f"[supercell-data] Pipeline failure: {failed_steps}"
    msg["From"] = EMAIL_USER
    msg["To"] = ALERT_EMAIL
    msg.set_content(_build_body(failures))

    _attach_log(msg, log_file)

    try:
        logger.info(f"Sending failure alert to {ALERT_EMAIL} via {SMTP_SERVER}...")
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context) as server:
            server.login(EMAIL_USER, EMAIL_PASS)
            server.send_message(msg)
        logger.info("Alert email sent")
    except Exception as e:
        logger.error(f"Failed to send alert email: {e}")
