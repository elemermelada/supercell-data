# Supercell-Data

Script to automate GDPR data collection from Supercell accounts (Hay Day).

*Mostly vibe-coded, but it does the job.*

## What it does

The pipeline runs in four stages:

1. **request** — Authenticates via Chrome cookies + JWT and submits a GDPR data export request to Supercell
2. **retrieve** — Connects to IMAP, finds the export email, and downloads the HTML data file to `downloads/`
3. **process** — Parses the HTML export and extracts Hay Day metrics into a JSON file alongside each HTML
4. **update** — Reads all JSON files from `downloads/` and appends new rows to a Google Sheet (run separately)

## Prerequisites

- Python 3.9+
- Chrome browser logged in to `support.supercell.com` (cookies are read automatically by `request.py`)
- A Google service account JSON file (`service_account.json`) in the project root, with edit access to your target sheet

## Install

```bash
git clone https://github.com/elemermelada/supercell-data
cd supercell-data
```

Optionally create and activate a virtual environment, then install dependencies:

```bash
pip install -r requirements.txt
```

## Configuration

Copy `.env.example` to `.env` and fill in the values. Environment variables must be exported before running — there is no automatic `.env` loading:

```bash
export $(grep -v '^#' .env | xargs)
```

| Variable | Required for | Description |
|---|---|---|
| `SUPERCELL_ACCOUNT_INFO_TOKEN` | request | JWT from your Supercell account session. Find it in Chrome DevTools → Application → Cookies → `support.supercell.com` → `account-user-info-token` |
| `IMAP_SERVER` | retrieve | IMAP hostname (e.g. `imap.gmail.com`) |
| `EMAIL_USER` | retrieve | Email address that receives the GDPR export |
| `EMAIL_PASS` | retrieve | Email password or app password |
| `SENDER_FILTER` | retrieve | Sender address to filter on (e.g. `no-reply@mydata.supercell.com`) |
| `SPREADSHEET_ID` | update | Google Sheets ID from the sheet URL |
| `SHEET_NAME` | update | Worksheet tab name (e.g. `HayDayData`) |

Place `service_account.json` in the project root.

## Usage

### Full pipeline: request → retrieve → process

```bash
python main.py
```

This submits the GDPR export request, waits for the email, downloads the HTML, and parses it to JSON. Logs for the run are written to `logs/run_YYYYMMDD_HHMMSS.log`.

> **Note:** Supercell sends the export email asynchronously. If `retrieve` finds no new emails on the first run, wait a few minutes and run `python retrieve.py` again.

### Upload to Google Sheets

```bash
python update.py
```

Reads all `.json` files from `downloads/` and appends new rows to the configured sheet. Already-uploaded UUIDs are skipped automatically. This step is intentionally separate from `main.py`.

### Run individual stages

```bash
python request.py   # submit GDPR export request
python retrieve.py  # download exported HTML from email
python process.py   # parse HTML → JSON
```

Each script logs to the console when run directly.

## Notes

- The JWT (`SUPERCELL_ACCOUNT_INFO_TOKEN`) expires periodically. You'll see a warning in the logs when it's within 3 days of expiry — grab a fresh one from Chrome at that point.
- `retrieve.py` searches for emails since `2024-01-01` (hardcoded). Edit `since_date` in `retrieve()` if you need a different range.
- The `downloads/` directory is gitignored. Processed JSON files live there alongside their source HTML.
