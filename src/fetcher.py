#!/usr/bin/env python3
import os
import sys
import json
import time
import random
import logging
import argparse
from datetime import datetime
from typing import List, Dict, Any, Optional

import requests
from dotenv import load_dotenv

# Support running both as `python -m src.fetcher` and `python src/fetcher.py`
try:
    from src.utils.db import LeadDB
except ModuleNotFoundError:
    try:
        from utils.db import LeadDB
    except ModuleNotFoundError:
        from .utils.db import LeadDB

logger = logging.getLogger("meta_lead_fetcher")


def parse_iso_datetime(dt_str: str) -> Optional[datetime]:
    """Parse ISO 8601 string into a datetime object for comparison."""
    if not dt_str:
        return None
    try:
        # Normalize timezone offsets like +0000 to +00:00 for fromisoformat
        cleaned = dt_str.strip()
        if len(cleaned) >= 5 and (cleaned[-5] == "+" or cleaned[-5] == "-") and ":" not in cleaned[-5:]:
            cleaned = cleaned[:-2] + ":" + cleaned[-2:]
        return datetime.fromisoformat(cleaned)
    except Exception:
        return None


def fetch_leads_offline(file_path: str, since: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Load raw leads from a local JSON file (Option B: Offline Mode).
    """
    logger.info(f"Running in OFFLINE mode. Reading leads from '{file_path}'")
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Offline leads file not found: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    raw_leads = data.get("data", []) if isinstance(data, dict) else data

    if since:
        since_dt = parse_iso_datetime(since)
        if since_dt:
            filtered = []
            for item in raw_leads:
                created_dt = parse_iso_datetime(item.get("created_time"))
                if created_dt and created_dt >= since_dt:
                    filtered.append(item)
            logger.info(f"Filtered {len(raw_leads)} down to {len(filtered)} leads after --since {since}")
            return filtered

    return raw_leads


def fetch_with_retry(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    max_retries: int = 4,
    backoff_factor: float = 1.0,
    timeout: int = 30,
) -> requests.Response:
    """
    Execute a GET request with exponential backoff on transient failures (429, 5xx, timeouts).
    """
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            
            # Handle rate limiting (429) or transient server errors (5xx)
            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    sleep_time = float(retry_after)
                else:
                    # Exponential backoff with jitter
                    sleep_time = backoff_factor * (2 ** (attempt - 1)) + random.uniform(0.1, 0.5)

                if attempt == max_retries:
                    resp.raise_for_status()

                logger.warning(
                    f"HTTP {resp.status_code} on attempt {attempt}/{max_retries}. Retrying in {sleep_time:.2f}s..."
                )
                time.sleep(sleep_time)
                continue

            resp.raise_for_status()
            return resp

        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as net_err:
            if attempt == max_retries:
                logger.error(f"Network error on final attempt {attempt}/{max_retries}: {net_err}")
                raise
            sleep_time = backoff_factor * (2 ** (attempt - 1)) + random.uniform(0.1, 0.5)
            logger.warning(
                f"Network exception '{net_err}' on attempt {attempt}/{max_retries}. Retrying in {sleep_time:.2f}s..."
            )
            time.sleep(sleep_time)

    raise RuntimeError("Unexpected failure in retry loop")


def fetch_leads_live(
    access_token: str,
    form_id: str,
    since: Optional[str] = None,
    api_version: str = "v16.0",
) -> List[Dict[str, Any]]:
    """
    Fetch all leads from Meta Graph API using pagination until exhaustively completed.
    """
    url = f"https://graph.facebook.com/{api_version}/{form_id}/leads"
    params: Optional[Dict[str, Any]] = {
        "access_token": access_token,
        "fields": "id,created_time,field_data",
        "limit": 100,
    }
    if since:
        params["since"] = since

    all_leads = []
    page = 1

    while url:
        logger.info(f"Fetching page {page} from Meta Graph API...")
        resp = fetch_with_retry(url, params=params)
        data = resp.json()

        leads_batch = data.get("data", [])
        all_leads.extend(leads_batch)
        logger.info(f"Retrieved {len(leads_batch)} leads on page {page}.")

        # Pagination: the `next` link already encapsulates token and query params
        paging = data.get("paging", {})
        url = paging.get("next")
        params = None
        page += 1

    return all_leads


def normalize_lead(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize and flatten raw Meta lead JSON into a standardized schema:
    - lead_id: string
    - created_time: string (ISO 8601)
    - full_name: string
    - email: string or None
    - phone: string or None
    Includes any additional custom fields submitted in the form.
    """
    lead: Dict[str, Any] = {
        "lead_id": raw.get("id"),
        "created_time": raw.get("created_time"),
        "full_name": None,
        "email": None,
        "phone": None,
    }

    first_name = None
    last_name = None

    for field in raw.get("field_data", []):
        field_name = field.get("name", "").strip().lower()
        values = field.get("values") or []
        val = values[0].strip() if values and values[0] is not None else None

        if not field_name:
            continue

        # Standard field mappings
        if field_name in ("full_name", "name", "your_name"):
            lead["full_name"] = val
        elif field_name in ("first_name",):
            first_name = val
        elif field_name in ("last_name",):
            last_name = val
        elif field_name in ("email", "email_address", "work_email"):
            lead["email"] = val
        elif field_name in ("phone", "phone_number", "contact_number", "mobile", "phone_no"):
            lead["phone"] = val
        else:
            # Preserve additional custom fields
            lead[field.get("name")] = val

    # Combine first_name and last_name if full_name was not provided
    if not lead["full_name"] and (first_name or last_name):
        lead["full_name"] = " ".join(filter(None, [first_name, last_name]))

    return lead


def validate_lead_contact(lead: Dict[str, Any]) -> bool:
    """
    Validates whether the normalized lead has at least one valid contact method (email or phone).
    """
    has_email = bool(lead.get("email") and str(lead["email"]).strip())
    has_phone = bool(lead.get("phone") and str(lead["phone"]).strip())
    return has_email or has_phone


def save_output(leads: List[Dict[str, Any]], format_type: str, file_path: Optional[str] = None) -> str:
    """
    Save normalized leads into JSON or CSV file.
    """
    target_file = file_path or f"new_leads.{format_type}"
    
    if format_type == "json":
        with open(target_file, "w", encoding="utf-8") as f:
            json.dump(leads, f, indent=2, ensure_ascii=False)
    elif format_type == "csv":
        import csv
        if leads:
            # Gather all distinct keys across all leads
            fieldnames = []
            for item in leads:
                for k in item.keys():
                    if k not in fieldnames:
                        fieldnames.append(k)
            with open(target_file, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(leads)
        else:
            with open(target_file, "w", newline="", encoding="utf-8") as f:
                f.write("lead_id,created_time,full_name,email,phone\n")

    return target_file


def is_live_configured(access_token: Optional[str], form_id: Optional[str]) -> bool:
    """Check if valid live Meta credentials are provided."""
    dummy_markers = ["your_access_token_here", "your_lead_form_id_here", "your_app_id_here", "", None]
    return access_token not in dummy_markers and form_id not in dummy_markers


def main():
    load_dotenv()
    
    parser = argparse.ArgumentParser(
        description="Furnishka Meta Lead Fetcher - Fetch, normalize, and deduplicate Meta leads."
    )
    parser.add_argument("--since", help="ISO timestamp to fetch leads since (e.g. 2025-07-01T00:00:00+0000)")
    parser.add_argument("--output", choices=["json", "csv"], default="json", help="Output file format (json or csv)")
    parser.add_argument("--out-file", help="Custom output file destination")
    parser.add_argument("--db-path", default="data/seen_leads.db", help="Path to SQLite seen leads database")
    parser.add_argument("--offline", action="store_true", help="Force offline mode using sample data")
    parser.add_argument(
        "--file", default="data/meta_leads_sample.json", help="Sample JSON file path for offline mode"
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose debug logging")
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    access_token = os.getenv("META_ACCESS_TOKEN")
    form_id = os.getenv("LEAD_FORM_ID")
    env_offline = os.getenv("OFFLINE_MODE", "false").lower() in ("true", "1", "yes")

    # Determine whether to run in Live API or Offline mode
    use_offline = args.offline or env_offline or not is_live_configured(access_token, form_id)

    db = LeadDB(args.db_path)

    try:
        if use_offline:
            if not args.offline and not env_offline:
                logger.info("Live credentials not configured; defaulting to OFFLINE mode (Option B).")
            raw_leads = fetch_leads_offline(args.file, since=args.since)
        else:
            logger.info("Live Meta credentials detected; initiating LIVE API fetch (Option A).")
            raw_leads = fetch_leads_live(access_token, form_id, since=args.since)

        new_leads = []
        skipped_count = 0
        duplicate_count = 0

        for raw in raw_leads:
            lead = normalize_lead(raw)
            lead_id = lead.get("lead_id")

            # Validate contact info (require at least email or phone)
            if not validate_lead_contact(lead):
                logger.warning(f"Skipping lead {lead_id}: missing contact info (email & phone both empty)")
                skipped_count += 1
                continue

            # Idempotency check
            if db.is_seen(lead_id):
                logger.debug(f"Lead {lead_id} has already been seen. Skipping duplicate.")
                duplicate_count += 1
                continue

            new_leads.append(lead)
            db.mark_seen(lead_id)

        # Output results
        saved_file = save_output(new_leads, args.output, args.out_file)

        print("\n" + "=" * 55)
        print("          FURNISHKA LEAD FETCHER SUMMARY")
        print("=" * 55)
        print(f" Mode:                 {'OFFLINE (Option B)' if use_offline else 'LIVE (Option A)'}")
        print(f" Total Leads Retrieved: {len(raw_leads)}")
        print(f" New Leads Ingested:    {len(new_leads)}")
        print(f" Duplicates Skipped:    {duplicate_count}")
        print(f" Invalid/Skipped Leads: {skipped_count}")
        print(f" Output Destination:    {saved_file}")
        print(f" Database Total Stored: {db.get_total_count()}")
        print("=" * 55 + "\n")

    finally:
        db.close()


if __name__ == "__main__":
    main()
