import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

from config import settings
from logger import get_logger

logger = get_logger(__name__)


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

    # Read config at call time (not import time) so the credentials reflect the
    # current settings — validated centrally in config.py. ALERT_EMAIL falls
    # back to the sending account.
    email_user = settings.email_user
    email_pass = settings.email_pass
    alert_email = settings.alert_email
    smtp_server = settings.smtp_server
    smtp_port = settings.smtp_port

    if not (email_user and email_pass and alert_email):
        logger.error(
            "Cannot send alert email: missing EMAIL_USER, EMAIL_PASS or ALERT_EMAIL"
        )
        return

    failed_steps = ", ".join(name for name, _, _ in failures)
    msg = EmailMessage()
    msg["Subject"] = f"[supercell-data] Pipeline failure: {failed_steps}"
    msg["From"] = email_user
    msg["To"] = alert_email
    msg.set_content(_build_body(failures))

    _attach_log(msg, log_file)

    try:
        logger.info(f"Sending failure alert to {alert_email} via {smtp_server}...")
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(smtp_server, smtp_port, context=context) as server:
            server.login(email_user, email_pass)
            server.send_message(msg)
        logger.info("Alert email sent")
    except Exception as e:
        logger.error(f"Failed to send alert email: {e}")
