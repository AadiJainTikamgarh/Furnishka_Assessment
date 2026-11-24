import os
import sqlite3
import pytest
from src.utils.db import LeadDB


@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "test_leads.db"
    db = LeadDB(str(db_file))
    yield db
    db.close()


def test_db_initialization(temp_db):
    assert temp_db.get_total_count() == 0
    assert temp_db.get_all_seen_ids() == set()


def test_mark_and_check_single_lead(temp_db):
    assert not temp_db.is_seen("lead_123")
    temp_db.mark_seen("lead_123")
    assert temp_db.is_seen("lead_123")
    assert temp_db.get_total_count() == 1


def test_idempotent_inserts(temp_db):
    temp_db.mark_seen("lead_abc")
    temp_db.mark_seen("lead_abc")
    temp_db.mark_seen("lead_abc")
    assert temp_db.get_total_count() == 1


def test_mark_seen_many(temp_db):
    lead_ids = ["lead_1", "lead_2", "lead_3", "lead_1"]
    temp_db.mark_seen_many(lead_ids)
    assert temp_db.get_total_count() == 3
    assert temp_db.is_seen("lead_1")
    assert temp_db.is_seen("lead_2")
    assert temp_db.is_seen("lead_3")
    assert not temp_db.is_seen("lead_4")


def test_empty_or_none_handling(temp_db):
    temp_db.mark_seen(None)
    temp_db.mark_seen("")
    assert temp_db.get_total_count() == 0
    assert not temp_db.is_seen(None)
    assert not temp_db.is_seen("")


def test_corrupt_database_recovery(tmp_path):
    corrupt_file = tmp_path / "corrupt.db"
    # Write garbage into file
    with open(corrupt_file, "wb") as f:
        f.write(b"not a valid sqlite file")

    # LeadDB should safely detect corruption and recreate clean database
    db = LeadDB(str(corrupt_file))
    assert db.get_total_count() == 0
    db.mark_seen("recovered_lead")
    assert db.is_seen("recovered_lead")
    db.close()
