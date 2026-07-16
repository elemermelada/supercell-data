# Supercell-Data

Script to automate GDPR data collection from Supercell accounts (Hay Day).

_Mostly vibe-coded, but it does the job._

## What it does

`main.py` runs the full pipeline end to end in four stages, and is designed to be run **daily as a scheduled task**:

1. **request** — Loads your browser cookies and submits a GDPR data export request to Supercell
2. **retrieve** — Connects to IMAP, finds new export emails, and downloads the HTML data file to `downloads/`
3. **process** — Parses each HTML export and extracts Hay Day metrics into a JSON file alongside the HTML
4. **update** — Reads all JSON files from `downloads/` and appends new rows to a Google Sheet

Supercell only accepts one GDPR request per day, so running the pipeline once a day keeps a rolling history of account snapshots in the sheet.

## Prerequisites

- Python 3.11
- A browser (Firefox by default, or Chrome) logged in to `support.supercell.com`. Cookies are read automatically by `request.py` — no manual token copying required.
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

### Git hooks (optional)

A pre-commit hook runs `black`, `ruff`, and `mypy` (the same checks as CI) before each commit. Enable it once per clone:

```bash
pip install black ruff mypy
git config core.hooksPath .githooks
```

Bypass for a single commit with `git commit --no-verify`.

## Configuration

Copy `.env.example` to `.env` and fill in the values. `main.py` loads `.env` automatically via `python-dotenv`, so no manual exporting is needed:

```bash
cp .env.example .env
```

| Variable         | Required for | Description                                                                           |
| ---------------- | ------------ | ------------------------------------------------------------------------------------- |
| `BROWSER_TYPE`   | request      | Browser to read cookies from: `firefox` (default) or `chrome`                         |
| `IMAP_SERVER`    | retrieve     | IMAP hostname (e.g. `imap.gmail.com`)                                                  |
| `EMAIL_USER`     | retrieve     | Email address that receives the GDPR export                                           |
| `EMAIL_PASS`     | retrieve     | Email password or app password                                                        |
| `SENDER_FILTER`  | retrieve     | Sender address to filter on (e.g. `no-reply@mydata.supercell.com`)                    |
| `STATE_FILE`     | retrieve     | Optional. Path to the state file tracking the last retrieved email (default `state.json`) |
| `SPREADSHEET_ID` | update       | Google Sheets ID from the sheet URL                                                   |
| `SHEET_NAME`     | update       | Worksheet tab name (e.g. `HayDayData`)                                                 |

Place `service_account.json` in the project root.

## Usage

### Full pipeline (daily task)

```bash
python main.py
```

This submits the GDPR export request, waits briefly, downloads any new export emails, parses them to JSON, and uploads new rows to Google Sheets. Logs for the run are written to `logs/run_YYYYMMDD_HHMMSS.log`.

> **Note:** Supercell sends the export email almost instantly after the request, so `main.py` pauses a few seconds between `request` and `retrieve` to let it land in the inbox — the same run then downloads _today's_ export. `retrieve` records the newest email it has seen in `state.json` and resumes from there on the next run, so no email is downloaded twice.

Schedule it with cron / Task Scheduler / a systemd timer to run once a day.

### Run individual stages

Each script can be run directly and logs to the console:

```bash
python request.py   # submit GDPR export request
python retrieve.py  # download exported HTML from new emails
python process.py   # parse HTML → JSON
python update.py    # append new JSON rows to Google Sheets
```

## Notes

- **State tracking:** `retrieve.py` remembers the date of the newest email it processed in `state.json`. On the first run (no state file) it searches from `2024-01-01`. Delete `state.json` to re-scan from that default.
- **Already-uploaded rows are skipped:** `update.py` keys rows by the export UUID (the HTML filename), so re-running it never duplicates data in the sheet.
- The `downloads/`, `logs/`, `state.json`, `.env`, and `service_account.json` paths are all gitignored. Processed JSON files live in `downloads/` alongside their source HTML.
