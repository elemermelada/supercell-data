import os
import json
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
from dateutil import parser
from logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------
# Single source of truth: fixed column order
# ---------------------------------------------------------
COLUMN_ORDER = [
    "email_date",
    "uuid",
    "name",
    "age",
    "farm_created",
    "farm_country",
    "farm_ip",
    "banned",
    "locked",
    "total_sessions",
    "neighborhood",
    "rank",
    "gems",
    "reputation_level",
    "experience_points",
    "level",
    "coins",
    "vouchers_blue",
    "vouchers_green",
    "vouchers_purple",
    "vouchers_gold",
    "valley_fuel",
    "valley_chickens",
    "valley_sanctuary_animals",
    "valley_sun_points",
    "valley_vouchers_blue",
    "valley_vouchers_green",
    "valley_vouchers_red",
    "gamecenter",
]

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


# ---------------------------------------------------------
# Normalize ISO date → string (Sheets will parse it)
# ---------------------------------------------------------
def normalize_date(dt_str):
    if not dt_str:
        return ""
    try:
        dt_str = dt_str.strip().replace("-->", "").strip()
        dt = parser.parse(dt_str)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception as e:
        logger.warning(f"Could not parse date '{dt_str}': {e}")
        return dt_str


# ---------------------------------------------------------
# Flatten JSON structure
# ---------------------------------------------------------
def flatten(data, uuid):
    return {
        "email_date": normalize_date(data.get("email_date")),
        "uuid": uuid,
        "name": data.get("name"),
        "age": data.get("age"),
        "farm_created": data.get("farm_created"),
        "farm_country": data.get("farm_country"),
        "farm_ip": data.get("farm_ip"),
        "banned": data.get("banned"),
        "locked": data.get("locked"),
        "total_sessions": data.get("total_sessions"),
        "neighborhood": data.get("neighborhood"),
        "rank": data.get("rank"),
        "gems": data.get("gems"),
        "reputation_level": data.get("reputation_level"),
        "experience_points": data.get("experience_points"),
        "level": data.get("level"),
        "coins": data.get("coins"),
        "vouchers_blue": data.get("vouchers", {}).get("blue"),
        "vouchers_green": data.get("vouchers", {}).get("green"),
        "vouchers_purple": data.get("vouchers", {}).get("purple"),
        "vouchers_gold": data.get("vouchers", {}).get("gold"),
        "valley_fuel": data.get("valley", {}).get("fuel"),
        "valley_chickens": data.get("valley", {}).get("chickens"),
        "valley_sanctuary_animals": data.get("valley", {}).get("sanctuary_animals"),
        "valley_sun_points": data.get("valley", {}).get("sun_points"),
        "valley_vouchers_blue": data.get("valley", {}).get("vouchers", {}).get("blue"),
        "valley_vouchers_green": data.get("valley", {}).get("vouchers", {}).get("green"),
        "valley_vouchers_red": data.get("valley", {}).get("vouchers", {}).get("red"),
        "gamecenter": data.get("gamecenter"),
    }


# ---------------------------------------------------------
# Ensure header row exists and is up to date
# ---------------------------------------------------------
def ensure_header_row(sheet, required_fields):
    header = COLUMN_ORDER.copy()

    for field in required_fields:
        if field not in header:
            header.append(field)

    try:
        existing_header = sheet.row_values(1)
    except Exception as e:
        logger.warning(f"Could not read header row: {e}")
        existing_header = []

    if not existing_header:
        sheet.insert_row(header, 1)
        logger.info("Created header row")
        return header

    if existing_header != header:
        sheet.update("1:1", [header])
        logger.info("Updated header row to canonical order")

    return header


# ---------------------------------------------------------
# Optional: format the date column nicely
# ---------------------------------------------------------
def format_date_column(sheet, header):
    if "email_date" not in header:
        return

    col = header.index("email_date") + 1
    col_letter = chr(ord('A') + col - 1)

    try:
        sheet.format(
            f"{col_letter}2:{col_letter}",
            {
                "numberFormat": {
                    "type": "DATE_TIME",
                    "pattern": "yyyy-mm-dd hh:mm:ss"
                }
            }
        )
        logger.info("Applied date formatting to email_date column")
    except Exception as e:
        logger.warning(f"Could not format date column: {e}")


# ---------------------------------------------------------
# Insert missing rows
# ---------------------------------------------------------
def update(directory="downloads"):
    spreadsheet_id = os.getenv("SPREADSHEET_ID")
    sheet_name = os.getenv("SHEET_NAME")

    if not spreadsheet_id or not sheet_name:
        raise EnvironmentError("Missing SPREADSHEET_ID or SHEET_NAME environment variables")

    creds = Credentials.from_service_account_file("service_account.json", scopes=SCOPES)
    gc = gspread.authorize(creds)
    sheet = gc.open_by_key(spreadsheet_id).worksheet(sheet_name)

    json_files = [f for f in os.listdir(directory) if f.endswith(".json")]

    if not json_files:
        logger.info("No JSON files found.")
        return

    all_fields = set()
    json_data = {}

    for file in json_files:
        uuid = file.replace(".json", "")
        with open(os.path.join(directory, file), "r", encoding="utf-8") as f:
            data = json.load(f)
        flat = flatten(data, uuid)
        json_data[uuid] = flat
        all_fields.update(flat.keys())

    header = ensure_header_row(sheet, list(all_fields))

    existing_uuids = sheet.col_values(header.index("uuid") + 1)

    for uuid, flat in json_data.items():
        if uuid in existing_uuids:
            logger.info(f"UUID {uuid} already exists, skipping.")
            continue

        row = [flat.get(col, "") for col in header]
        sheet.append_row(row)
        logger.info(f"Added row for {uuid}")

    format_date_column(sheet, header)


# ---------------------------------------------------------
# Main entry point
# ---------------------------------------------------------
if __name__ == "__main__":
    from logger import setup_console_logging
    setup_console_logging()
    update()
