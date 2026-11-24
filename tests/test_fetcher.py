import os
import json
import pytest
from unittest.mock import patch, MagicMock
import requests

from src.fetcher import (
    normalize_lead,
    validate_lead_contact,
    parse_iso_datetime,
    fetch_leads_offline,
    save_output,
    fetch_with_retry,
)


def test_normalize_lead_standard():
    raw = {
        "id": "1001",
        "created_time": "2025-07-24T12:34:56+0000",
        "field_data": [
            {"name": "full_name", "values": ["Alice Singh"]},
            {"name": "email", "values": ["alice@example.com"]},
            {"name": "phone", "values": ["+918123456789"]},
        ],
    }
    normalized = normalize_lead(raw)
    assert normalized["lead_id"] == "1001"
    assert normalized["created_time"] == "2025-07-24T12:34:56+0000"
    assert normalized["full_name"] == "Alice Singh"
    assert normalized["email"] == "alice@example.com"
    assert normalized["phone"] == "+918123456789"


def test_normalize_lead_split_name_and_synonyms():
    raw = {
        "id": "1002",
        "created_time": "2025-07-24T13:00:00+0000",
        "field_data": [
            {"name": "first_name", "values": ["Bob"]},
            {"name": "last_name", "values": ["Sharma"]},
            {"name": "email_address", "values": ["bob@furnishka.com"]},
            {"name": "contact_number", "values": ["9876543210"]},
            {"name": "city", "values": ["Bengaluru"]},
        ],
    }
    normalized = normalize_lead(raw)
    assert normalized["lead_id"] == "1002"
    assert normalized["full_name"] == "Bob Sharma"
    assert normalized["email"] == "bob@furnishka.com"
    assert normalized["phone"] == "9876543210"
    assert normalized["city"] == "Bengaluru"


def test_validate_lead_contact():
    assert validate_lead_contact({"email": "test@example.com", "phone": None}) is True
    assert validate_lead_contact({"email": None, "phone": "+919999999999"}) is True
    assert validate_lead_contact({"email": "test@example.com", "phone": "+919999999999"}) is True
    assert validate_lead_contact({"email": None, "phone": None}) is False
    assert validate_lead_contact({"email": "", "phone": "   "}) is False


def test_parse_iso_datetime():
    dt = parse_iso_datetime("2025-07-24T12:34:56+0000")
    assert dt is not None
    assert dt.year == 2025
    assert dt.month == 7
    assert dt.day == 24


def test_fetch_leads_offline(tmp_path):
    sample_file = tmp_path / "sample.json"
    data = {
        "data": [
            {
                "id": "1",
                "created_time": "2025-07-20T10:00:00+0000",
                "field_data": [{"name": "full_name", "values": ["Old Lead"]}],
            },
            {
                "id": "2",
                "created_time": "2025-07-25T10:00:00+0000",
                "field_data": [{"name": "full_name", "values": ["New Lead"]}],
            },
        ]
    }
    with open(sample_file, "w") as f:
        json.dump(data, f)

    # Without since
    leads = fetch_leads_offline(str(sample_file))
    assert len(leads) == 2

    # With since
    filtered = fetch_leads_offline(str(sample_file), since="2025-07-22T00:00:00+0000")
    assert len(filtered) == 1
    assert filtered[0]["id"] == "2"


def test_save_output_json_and_csv(tmp_path):
    leads = [
        {
            "lead_id": "101",
            "created_time": "2025-07-24T12:00:00+0000",
            "full_name": "Test User",
            "email": "test@example.com",
            "phone": "123456",
        }
    ]
    json_path = str(tmp_path / "out.json")
    csv_path = str(tmp_path / "out.csv")

    save_output(leads, "json", json_path)
    assert os.path.exists(json_path)
    with open(json_path, "r") as f:
        loaded = json.load(f)
        assert len(loaded) == 1
        assert loaded[0]["lead_id"] == "101"

    save_output(leads, "csv", csv_path)
    assert os.path.exists(csv_path)
    with open(csv_path, "r") as f:
        content = f.read()
        assert "lead_id,created_time,full_name,email,phone" in content
        assert "Test User" in content


@patch("requests.get")
@patch("time.sleep")
def test_fetch_with_retry_server_error_recovery(mock_sleep, mock_get):
    err_resp = MagicMock()
    err_resp.status_code = 500
    err_resp.headers = {}

    ok_resp = MagicMock()
    ok_resp.status_code = 200
    ok_resp.json.return_value = {"data": []}

    # First call 500, second call 200
    mock_get.side_effect = [err_resp, ok_resp]

    result = fetch_with_retry("https://api.test/leads", max_retries=3, backoff_factor=0.01)
    assert result.status_code == 200
    assert mock_get.call_count == 2
    assert mock_sleep.call_count == 1


@patch("requests.get")
@patch("time.sleep")
def test_fetch_with_retry_rate_limit(mock_sleep, mock_get):
    rate_resp = MagicMock()
    rate_resp.status_code = 429
    rate_resp.headers = {"Retry-After": "2"}

    ok_resp = MagicMock()
    ok_resp.status_code = 200

    mock_get.side_effect = [rate_resp, ok_resp]

    result = fetch_with_retry("https://api.test/leads", max_retries=2)
    assert result.status_code == 200
    mock_sleep.assert_called_with(2.0)
