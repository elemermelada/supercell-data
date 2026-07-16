import json
import os
import re

from bs4 import BeautifulSoup

from logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------
# Extract Hay Day data from a single HTML file
# ---------------------------------------------------------
def extract_hay_day_data(html_path):
    with open(html_path, encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "lxml")

    data = {}

    # ---------------------------------------------------------
    # Extract EMAIL_DATE from appended HTML comment
    # ---------------------------------------------------------
    email_date_comment = soup.find(string=re.compile(r"EMAIL_DATE"))
    if email_date_comment:
        m = re.search(r"EMAIL_DATE:\s*(.+)", email_date_comment)
        if m:
            data["email_date"] = m.group(1)

    # ---------------------------------------------------------
    # Find the Hay Day section
    # ---------------------------------------------------------
    hayday_header = soup.find("h2", string="Hay Day")
    if not hayday_header:
        raise ValueError(f"Unexpected HTML format in {html_path}: no Hay Day section")

    # Collect <p> tags until next <h2>
    hayday_data = []
    for tag in hayday_header.find_all_next():
        if tag.name == "h2" and tag.text != "Hay Day":
            break
        if tag.name == "p":
            hayday_data.append(tag.get_text(" ", strip=True))

    # ---------------------------------------------------------
    # Parse Hay Day data
    # ---------------------------------------------------------
    for line in hayday_data:
        # Name + age
        m = re.search(r"Your name is (.+?) and age is (\d+)", line)
        if m:
            data["name"] = m.group(1)
            data["age"] = int(m.group(2))

        # Farm creation
        m = re.search(r"Your farm was created on (.+?) in (.+?) \((.+?)\)", line)
        if m:
            data["farm_created"] = m.group(1)
            data["farm_country"] = m.group(2)
            data["farm_ip"] = m.group(3)

        # Ban/lock
        m = re.search(r"Your account is (.+?) and (.+?)\.", line)
        if m:
            data["banned"] = m.group(1)
            data["locked"] = m.group(2)

        # Total sessions
        m = re.search(r"You have played (\d+) sessions", line)
        if m:
            data["total_sessions"] = int(m.group(1))

        # Neighborhood
        m = re.search(
            r"You are a member of a neighborhood called (.+?)\. Your rank is \"(.+?)\"",
            line,
        )
        if m:
            data["neighborhood"] = m.group(1)
            data["rank"] = m.group(2)

        # Gems
        m = re.search(r"You have (\d+) gems", line)
        if m:
            data["gems"] = int(m.group(1))

        # Reputation + XP
        m = re.search(r"reputation level (\d+) and (\d+) experience points", line)
        if m:
            data["reputation_level"] = int(m.group(1))
            data["experience_points"] = int(m.group(2))

        # Level
        m = re.search(r"experience level is (\d+)", line)
        if m:
            data["level"] = int(m.group(1))

        # Coins + vouchers
        m = re.search(
            r"Resources: (\d+) coins and vouchers: "
            r"(\d+) Blue, (\d+) Green, (\d+) Purple and (\d+) Gold",
            line,
        )
        if m:
            data["coins"] = int(m.group(1))
            data["vouchers"] = {
                "blue": int(m.group(2)),
                "green": int(m.group(3)),
                "purple": int(m.group(4)),
                "gold": int(m.group(5)),
            }

        # Valley resources
        m = re.search(
            r"Your Valley resources: (\d+) fuel, (\d+) chickens, "
            r"(\d+) sanctuary animals, (\d+) sun points and vouchers: "
            r"(\d+) Blue, (\d+) Green, (\d+) Red",
            line,
        )
        if m:
            data["valley"] = {
                "fuel": int(m.group(1)),
                "chickens": int(m.group(2)),
                "sanctuary_animals": int(m.group(3)),
                "sun_points": int(m.group(4)),
                "vouchers": {
                    "blue": int(m.group(5)),
                    "green": int(m.group(6)),
                    "red": int(m.group(7)),
                },
            }

        # GameCenter
        m = re.search(r"GameCenter: (.+)", line)
        if m:
            data["gamecenter"] = m.group(1)

    # ---------------------------------------------------------
    # Surface partial format drift: log any expected field whose regex
    # matched nothing, so a layout change is visible in the logs before it
    # silently produces incomplete rows (or trips the hard check below).
    # ---------------------------------------------------------
    expected_fields = (
        "email_date",
        "name",
        "age",
        "farm_created",
        "farm_country",
        "farm_ip",
        "banned",
        "locked",
        "total_sessions",
        "rank",
        "gems",
        "reputation_level",
        "experience_points",
        "level",
        "coins",
        "vouchers",
        "valley",
    )
    # Fields that are legitimately absent for accounts that never used them.
    # Their absence isn't format drift, so keep it at info level to avoid noise.
    optional_fields = (
        "neighborhood",
        "gamecenter",
    )
    unmatched = [f for f in expected_fields if f not in data]
    if unmatched:
        logger.warning(
            f"{html_path}: no match for fields {unmatched} "
            "(possible export format drift)"
        )
    unmatched_optional = [f for f in optional_fields if f not in data]
    if unmatched_optional:
        logger.info(f"{html_path}: no match for optional fields {unmatched_optional}")

    # ---------------------------------------------------------
    # Sanity-check the parse: core fields must be present, otherwise
    # the export layout has changed and downstream data would be garbage.
    # ---------------------------------------------------------
    core_fields = ("name", "level", "gems")
    missing = [f for f in core_fields if f not in data]
    if missing:
        raise ValueError(
            f"Unexpected HTML format in {html_path}: missing core fields {missing}"
        )

    # ---------------------------------------------------------
    # Save JSON next to HTML
    # ---------------------------------------------------------
    json_path = os.path.splitext(html_path)[0] + ".json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

    logger.info(f"Saved Hay Day JSON to {json_path}")
    return data


# ---------------------------------------------------------
# Process all HTML files missing JSON
# ---------------------------------------------------------
def process(directory="downloads"):
    if not os.path.isdir(directory):
        logger.warning(f"Directory '{directory}' does not exist.")
        return

    files = os.listdir(directory)
    html_files = [f for f in files if f.endswith(".html")]

    if not html_files:
        logger.info("No HTML files found.")
        return

    errors = []
    for filename in html_files:
        html_path = os.path.join(directory, filename)
        json_path = os.path.splitext(html_path)[0] + ".json"

        if os.path.exists(json_path):
            logger.info(f"JSON already exists for {filename}, skipping.")
            continue

        logger.info(f"Processing {filename}...")
        try:
            extract_hay_day_data(html_path)
        except Exception as e:
            logger.error(f"Failed to process {filename}: {e}")
            errors.append(filename)

    if errors:
        raise ValueError(
            f"Failed to parse {len(errors)} HTML file(s) with unexpected format: "
            f"{errors}"
        )


# ---------------------------------------------------------
# Main entry point
# ---------------------------------------------------------
if __name__ == "__main__":
    from logger import setup_console_logging

    setup_console_logging()
    process()
