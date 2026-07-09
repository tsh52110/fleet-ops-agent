"""Synthetic demo connector — wraps the seeded generator.

This is the demo/eval data source: it builds the deterministic synthetic
database in a temp location and serves its rows through the same connector
contract as real sources, so the whole ingestion pipeline (validation,
provenance, quarantine) is exercised identically in demo and production.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from typing import Iterable


def _rows(con: sqlite3.Connection, table: str) -> Iterable[dict]:
    con.row_factory = sqlite3.Row
    for row in con.execute(f"SELECT * FROM {table}"):  # table names are internal constants
        yield dict(row)


class SyntheticConnector:
    source_name = "synthetic"

    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            from src.generate_data import build
            db_path = Path(tempfile.mkdtemp()) / "synthetic_source.db"
            build(db_path)
        self._con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

    def fetch_trucks(self):
        return _rows(self._con, "trucks")

    def fetch_fault_codes(self):
        return _rows(self._con, "fault_codes")

    def fetch_parts(self):
        return _rows(self._con, "parts")

    def fetch_maintenance_log(self):
        return _rows(self._con, "maintenance_log")
