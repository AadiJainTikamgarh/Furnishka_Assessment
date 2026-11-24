import os
import sqlite3
import logging
from typing import Iterable, Set

logger = logging.getLogger(__name__)


class LeadDB:
    """
    SQLite persistence layer for tracking seen lead IDs to ensure idempotency.
    """

    def __init__(self, db_path: str = "data/seen_leads.db"):
        self.db_path = db_path
        dirname = os.path.dirname(db_path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)

        self.conn = self._init_connection()
        self._init_schema()

    def _init_connection(self) -> sqlite3.Connection:
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            # Test connection integrity
            conn.execute("PRAGMA schema_version")
            return conn
        except sqlite3.DatabaseError as e:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
            logger.warning(
                f"Existing database at '{self.db_path}' is invalid or corrupted ({e}). Recreating a clean database."
            )
            if os.path.exists(self.db_path):
                try:
                    os.remove(self.db_path)
                except Exception as rem_err:
                    logger.error(f"Failed to remove corrupted db file: {rem_err}")
            return sqlite3.connect(self.db_path)

    def _init_schema(self) -> None:
        with self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS seen_leads (
                    lead_id TEXT PRIMARY KEY,
                    first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_seen_leads_id ON seen_leads(lead_id)"
            )

    def is_seen(self, lead_id: str) -> bool:
        """Check if a single lead ID has already been seen."""
        if not lead_id:
            return False
        cur = self.conn.execute(
            "SELECT 1 FROM seen_leads WHERE lead_id = ? LIMIT 1", (str(lead_id),)
        )
        return cur.fetchone() is not None

    def mark_seen(self, lead_id: str) -> None:
        """Record a single lead ID as seen."""
        if not lead_id:
            return
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO seen_leads (lead_id) VALUES (?)",
                (str(lead_id),),
            )

    def mark_seen_many(self, lead_ids: Iterable[str]) -> None:
        """Record multiple lead IDs in a single batch transaction."""
        valid_ids = [(str(lid),) for lid in lead_ids if lid]
        if not valid_ids:
            return
        with self.conn:
            self.conn.executemany(
                "INSERT OR IGNORE INTO seen_leads (lead_id) VALUES (?)",
                valid_ids,
            )

    def get_all_seen_ids(self) -> Set[str]:
        """Return a set of all seen lead IDs."""
        cur = self.conn.execute("SELECT lead_id FROM seen_leads")
        return {row[0] for row in cur.fetchall()}

    def get_total_count(self) -> int:
        """Return total count of recorded leads."""
        cur = self.conn.execute("SELECT COUNT(*) FROM seen_leads")
        row = cur.fetchone()
        return row[0] if row else 0

    def close(self) -> None:
        """Close SQLite database connection."""
        if self.conn:
            self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
