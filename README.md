# Furnishka Meta Lead Fetcher

A robust, idempotent CLI application designed to fetch Lead Ads data from Meta's Graph API, normalize raw responses into a clean structured schema, and persist ingested records using SQLite to eliminate duplicates across executions.

This tool supports both **Live API Access (Option A)** and an **Offline Mode (Option B)** with bundled sample data.

---

## Features

- **Dual-Mode Operation**:
  - **Live Mode**: Interacts with Meta's Graph API (`/v16.0/{form_id}/leads`) with full cursor pagination (`paging.next`).
  - **Offline Mode**: Automatically falls back to local sample data (`data/meta_leads_sample.json`) when live credentials are not set, or when explicitly requested via `--offline`.
- **Idempotency & Deduplication**:
  - Tracks processed lead IDs in a local SQLite database (`data/seen_leads.db`).
  - Re-running the tool processes only new, unrecorded leads.
- **Data Normalization & Cleaning**:
  - Flattens nested `field_data` structures into a standard schema: `lead_id`, `created_time`, `full_name`, `email`, `phone`.
  - Intelligently handles field aliases (e.g., `full_name` vs `first_name` + `last_name`, `phone_number` vs `contact_number`).
  - Validates contact details, automatically skipping and logging records missing both email and phone.
- **Resilience & Transient Failure Recovery**:
  - Built-in exponential backoff retry mechanism handling HTTP 429 rate limits (respecting `Retry-After` headers), HTTP 5xx server errors, and network timeouts.
- **Export Formats**:
  - Supports `--output json` (default) and `--output csv`.
- **Automated Test Suite**:
  - 14 automated unit tests covering normalization, persistence, idempotency, date filtering, and retry backoff.

---

## Project Structure

```
assesment/
├── .env.sample             # Environment variable template
├── .gitignore               # Excludes secrets, caches, and DB files
├── requirements.txt         # Project dependencies
├── README.md                # Project documentation & run guide
├── data/
│   ├── meta_leads_sample.json  # Offline test leads
│   └── seen_leads.db           # SQLite database tracking seen leads
├── src/
│   ├── __init__.py
│   ├── fetcher.py           # Core CLI fetcher, normalization & export logic
│   └── utils/
│       ├── __init__.py
│       └── db.py            # SQLite persistence layer
└── tests/
    ├── __init__.py
    ├── test_db.py           # Unit tests for SQLite persistence & idempotency
    └── test_fetcher.py      # Unit tests for normalization, parsing & retry
```

---

## Prerequisites

- **Python 3.8+** (Tested on Python 3.14)
- **Git**

---

## Installation & Setup

1. **Navigate to the project directory**:
   ```bash
   cd assesment
   ```

2. **Create and activate a virtual environment**:
   - On Windows (PowerShell):
     ```powershell
     python -m venv .venv
     .\.venv\Scripts\Activate.ps1
     ```
   - On Linux / macOS:
     ```bash
     python3 -m venv .venv
     source .venv/bin/activate
     ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

---

## Configuration

Copy `.env.sample` to `.env`:
```bash
cp .env.sample .env
```

Edit `.env` based on your desired execution mode:

### Option A: Live API Mode
Obtain credentials from your Meta Developer App and Facebook Business Settings:
```dotenv
META_APP_ID=your_meta_app_id
META_APP_SECRET=your_meta_app_secret
META_ACCESS_TOKEN=your_system_user_or_page_access_token
LEAD_FORM_ID=your_lead_form_id
OFFLINE_MODE=false
```

### Option B: Offline Mode (Default)
If you don't have active Meta Graph API credentials, keep the placeholders or set `OFFLINE_MODE=true`:
```dotenv
META_ACCESS_TOKEN=your_access_token_here
LEAD_FORM_ID=your_lead_form_id_here
OFFLINE_MODE=true
```

---

## Running the CLI Tool

### 1. Default Run (Offline Mode)
Outputs new leads into `new_leads.json`:
```bash
python -m src.fetcher
```

### 2. Output as CSV
Exports formatted CSV into `new_leads.csv`:
```bash
python -m src.fetcher --output csv
```

### 3. Filter Leads Since a Specific Date
```bash
python -m src.fetcher --since 2025-07-24T12:00:00+0000 --output json
```

### 4. Custom File Paths
```bash
python -m src.fetcher --offline --file data/meta_leads_sample.json --out-file custom_leads.json --db-path data/custom_db.db
```

### 5. CLI Options Reference
```
options:
  -h, --help           Show help message and exit
  --since SINCE        ISO 8601 timestamp to fetch leads since (e.g. 2025-07-01T00:00:00+0000)
  --output {json,csv}  Output file format (default: json)
  --out-file OUT_FILE  Custom output destination path
  --db-path DB_PATH    Path to SQLite seen leads database (default: data/seen_leads.db)
  --offline            Force offline mode using sample data
  --file FILE          Sample JSON file path for offline mode
  --verbose            Enable verbose debug logging
```

---

## Idempotency Verification

When executing the fetcher consecutively against the same source, subsequent runs report `0` new leads:

### Run 1 (Fresh Ingestion):
```text
2026-11-23 23:11:13 [INFO] meta_lead_fetcher: Running in OFFLINE mode. Reading leads from 'data/meta_leads_sample.json'
2026-11-23 23:11:13 [WARNING] meta_lead_fetcher: Skipping lead 1002: missing contact info (email & phone both empty)

=======================================================
          FURNISHKA LEAD FETCHER SUMMARY
=======================================================
 Mode:                 OFFLINE (Option B)
 Total Leads Retrieved: 2
 New Leads Ingested:    1
 Duplicates Skipped:    0
 Invalid/Skipped Leads: 1
 Output Destination:    new_leads.json
 Database Total Stored: 1
=======================================================
```

### Run 2 (Idempotent Run - Immediate duplicate skip):
```text
2026-11-23 23:11:13 [INFO] meta_lead_fetcher: Running in OFFLINE mode. Reading leads from 'data/meta_leads_sample.json'
2026-11-23 23:11:13 [WARNING] meta_lead_fetcher: Skipping lead 1002: missing contact info (email & phone both empty)

=======================================================
          FURNISHKA LEAD FETCHER SUMMARY
=======================================================
 Mode:                 OFFLINE (Option B)
 Total Leads Retrieved: 2
 New Leads Ingested:    0
 Duplicates Skipped:    1
 Invalid/Skipped Leads: 1
 Output Destination:    new_leads.json
 Database Total Stored: 1
=======================================================
```

---

## Running Automated Tests

Run the test suite with `pytest`:
```bash
pytest -v
```

Expected output:
```text
============================= test session starts =============================
collected 14 items

tests/test_db.py::test_db_initialization PASSED                          [  7%]
tests/test_db.py::test_mark_and_check_single_lead PASSED                 [ 14%]
tests/test_db.py::test_idempotent_inserts PASSED                         [ 21%]
tests/test_db.py::test_mark_seen_many PASSED                             [ 28%]
tests/test_db.py::test_empty_or_none_handling PASSED                     [ 35%]
tests/test_db.py::test_corrupt_database_recovery PASSED                  [ 42%]
tests/test_fetcher.py::test_normalize_lead_standard PASSED               [ 50%]
tests/test_fetcher.py::test_normalize_lead_split_name_and_synonyms PASSED [ 57%]
tests/test_fetcher.py::test_validate_lead_contact PASSED                 [ 64%]
tests/test_fetcher.py::test_parse_iso_datetime PASSED                    [ 71%]
tests/test_fetcher.py::test_fetch_leads_offline PASSED                   [ 78%]
tests/test_fetcher.py::test_save_output_json_and_csv PASSED              [ 85%]
tests/test_fetcher.py::test_fetch_with_retry_server_error_recovery PASSED [ 92%]
tests/test_fetcher.py::test_fetch_with_retry_rate_limit PASSED           [100%]

============================= 14 passed in 0.43s ==============================
```

---

## Submission Preparation

To generate a clean submission ZIP file excluding `.env`, `.venv`, and temporary database files:
```powershell
Compress-Archive -Path src, tests, data\meta_leads_sample.json, requirements.txt, .env.sample, .gitignore, README.md -DestinationPath furnishka_meta_lead_fetcher_submission.zip -Force
```
