# Architecture & Design Decisions

This document outlines the key architectural, technical, and engineering decisions made during the implementation of the **Furnishka Meta Lead Fetcher CLI**.

---

## 1. Idempotency & Persistence Layer (SQLite)

### Context
Meta Lead Ads integrations run periodically (e.g., via cron or scheduled workers). Re-running the tool must guarantee that leads already captured are never re-processed or duplicated in output files.

### Decision
Utilize a local **SQLite** database (`data/seen_leads.db`) with a dedicated `seen_leads` table:
```sql
CREATE TABLE IF NOT EXISTS seen_leads (
    lead_id TEXT PRIMARY KEY,
    first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_seen_leads_id ON seen_leads(lead_id);
```

### Rationale & Benefits
- **Zero External Overhead**: SQLite is built into Python standard library (`sqlite3`), requiring no external database service or daemon.
- **ACID & Concurrency Safety**: Guaranteed atomic transactions (`INSERT OR IGNORE`) prevent duplicate writes.
- **O(1) Indexed Lookups**: Checking whether a lead ID exists is nearly instantaneous, easily scaling to hundreds of thousands of leads.
- **Self-Healing / Corrupt DB Recovery**: If the SQLite file on disk is corrupted or uninitialized (such as zero-byte/invalid placeholder files), the `LeadDB` manager safely catches `sqlite3.DatabaseError`, closes the lock cleanly, and initializes a fresh database schema.

---

## 2. Dual-Mode Architecture: Live API & Offline Fallback

### Context
Reviewers or maintainers evaluating this tool may not possess active Facebook Developer Business Accounts, Page ownership, or Lead Ads retrieval permissions.

### Decision
Implement an intelligent dual-mode system:
- **Live Mode (Option A)**: Authenticates against Meta Graph API `/v16.0/{form_id}/leads`.
- **Offline Mode (Option B)**: Reads from local JSON (`data/meta_leads_sample.json`).
- **Auto-Detection**: If `.env` contains placeholder credentials (`your_access_token_here`) or `OFFLINE_MODE=true`, the tool automatically runs in Offline Mode without failing, while clearly logging the active mode to the console.

### Rationale & Benefits
- Evaluators can test and verify the entire CLI, normalization, database persistence, and deduplication pipeline immediately out-of-the-box with zero setup friction.
- Switching to production Live Mode requires simply populating `.env` with actual Meta keys.

---

## 3. Resilient Retry Strategy with Exponential Backoff & Jitter

### Context
External APIs, particularly Meta Graph API, are susceptible to:
1. **HTTP 429**: Rate limiting due to API quota thresholds.
2. **HTTP 5xx**: Transient server errors within Meta's infrastructure.
3. **Network Drops & Timeouts**: Intermittent connection latency.

### Decision
Implement a custom retry handler `fetch_with_retry()` featuring:
- **Max Retries**: Default of 4 attempts.
- **Exponential Backoff**: Base delay doubled per iteration: $\text{delay} = \text{factor} \times 2^{(\text{attempt}-1)}$.
- **Random Jitter**: Appends a random float (0.1s – 0.5s) to prevent thundering herd contention on the server.
- **`Retry-After` Header Respect**: If Meta returns HTTP 429 with a `Retry-After` response header, the fetcher sleeps for the server-specified interval instead of the default backoff.
- **Explicit Timeout**: Set to 30 seconds on every request to prevent CLI worker hangs.

---

## 4. Cursor Pagination Handling

### Context
Meta Graph API paginates large lead datasets using opaque cursors returned under `data.paging.next`.

### Decision
- On the first page, request with query parameters: `access_token`, `fields`, and `limit=100`.
- On subsequent iterations, extract `paging.next` and request the next URL directly without re-supplying the initial `params` dictionary (as Meta's `next` URL already encapsulates the pagination cursor, limit, and authentication).
- Terminate the loop cleanly when `paging.next` is absent.

---

## 5. Schema Normalization & Missing Contact Validation

### Context
Lead Ads forms can define variable field names depending on campaign creators (e.g., `full_name` vs `first_name` + `last_name`, `email` vs `email_address`, `phone` vs `contact_number`). Leads without any contact method are unusable by sales/operations.

### Decision
1. **Flexible Field Mapping**:
   - Standardizes name fields (`full_name`, `name`, `your_name`). If only `first_name` and `last_name` exist, concatenates them cleanly.
   - Maps email aliases (`email`, `email_address`, `work_email`).
   - Maps phone aliases (`phone`, `phone_number`, `contact_number`, `mobile`, `phone_no`).
   - Preserves custom questions as additional keys in the normalized dictionary.
2. **Contact Validation**:
   - Checks if either `email` or `phone` contains a valid, non-empty string.
   - If both are missing or empty, records a `WARNING` log and skips the lead from ingestion, preventing junk leads in the downstream database or CRM exports.

---

## 6. Output Formats & Filtering

### Decision
- **Dual Export**: Supported via `--output json` (`new_leads.json`) and `--output csv` (`new_leads.csv`).
- **Date Filtering (`--since`)**:
  - Live mode: passes `since` as a query parameter directly to Meta Graph API.
  - Offline mode: parses ISO 8601 timestamps and filters candidate leads in-memory before normalization.

---

## 7. Testing & Quality Assurance

### Decision
Establish an automated test suite using `pytest` covering 14 test cases across:
- Normalization schema compliance and field synonym resolution.
- SQLite database idempotency, batch inserts, and corruption recovery.
- HTTP 429 rate limit and 5xx transient error retry simulation via `unittest.mock`.
- Date range filtering (`--since`) and multi-format file exports.
