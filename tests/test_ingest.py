"""Tests for the ingestion pipeline — the system's accuracy boundary.

Covers: CSV header remapping, type/enum validation, quarantine of bad rows
(never silent drops), referential integrity, duplicate detection, provenance
metadata, and synthetic round-trip fidelity.
"""

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ingest import ingest, validate_event, validate_truck

FIXTURES = Path(__file__).parent / "fixtures" / "sample_export"


def _ingest_fixture(tmp_path):
    out = tmp_path / "live.db"
    report = ingest("csv", out, export_dir=FIXTURES)
    return out, report


class TestCsvIngestion:
    def test_aliased_headers_are_remapped(self, tmp_path):
        out, report = _ingest_fixture(tmp_path)
        con = sqlite3.connect(out)
        # trucks.csv used vehicle_id/odometer/model_year aliases
        row = con.execute("SELECT truck_id, mileage, year FROM trucks"
                          " WHERE truck_id='T-001'").fetchone()
        assert row is not None and row[1] > 0 and row[2] >= 2016

    def test_bad_rows_quarantined_not_loaded(self, tmp_path):
        out, report = _ingest_fixture(tmp_path)
        con = sqlite3.connect(out)
        assert con.execute("SELECT COUNT(*) FROM trucks WHERE truck_id='T-BAD1'").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM maintenance_log WHERE truck_id='T-999'").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM maintenance_log WHERE cost < 0").fetchone()[0] == 0
        assert report["counts"]["trucks"]["rejected"] == 2
        assert report["counts"]["maintenance_log"]["rejected"] == 3

    def test_quarantine_file_records_reasons(self, tmp_path):
        out, _ = _ingest_fixture(tmp_path)
        q = json.loads(out.with_suffix(".quarantine.json").read_text())
        reasons = " | ".join(e for item in q for e in item["errors"])
        assert "year: out of range" in reasons
        assert "unknown truck" in reasons
        assert "cost: negative" in reasons
        assert "unparseable date" in reasons

    def test_provenance_recorded(self, tmp_path):
        out, _ = _ingest_fixture(tmp_path)
        con = sqlite3.connect(out)
        src, counts, warnings = con.execute(
            "SELECT source, counts_json, warnings_json FROM sync_metadata").fetchone()
        assert src.startswith("csv:")
        assert json.loads(counts)["trucks"]["accepted"] == 12
        assert any("quarantined" in w for w in json.loads(warnings))


class TestSyntheticRoundTrip:
    def test_synthetic_ingest_matches_generator_output(self, tmp_path):
        """The full pipeline over the synthetic connector must reproduce the
        exact table contents of the committed fleet.db — proof that ingestion
        is lossless for clean data."""
        out = tmp_path / "rt.db"
        report = ingest("synthetic", out)
        assert all(c["rejected"] == 0 for c in report["counts"].values())
        a, b = sqlite3.connect(out), sqlite3.connect(Path(__file__).parent.parent / "fleet.db")
        for table in ("trucks", "fault_codes", "parts", "maintenance_log"):
            rows_a = a.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            rows_b = b.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            assert rows_a == rows_b, f"{table} differs after ingestion round-trip"


class TestValidators:
    def test_truck_validator_rejects_garbage_year(self):
        row, errs = validate_truck({"truck_id": "T-1", "year": "banana"})
        assert row is None and any("year" in e for e in errs)

    def test_event_validator_enforces_known_trucks(self):
        row, errs = validate_event(
            {"truck_id": "T-404", "event_date": "2026-01-01", "event_type": "repair"},
            known_trucks={"T-001"})
        assert row is None and any("unknown truck" in e for e in errs)

    def test_duplicate_truck_ids_rejected(self, tmp_path):
        out, report = _ingest_fixture(tmp_path)
        con = sqlite3.connect(out)
        ids = [r[0] for r in con.execute("SELECT truck_id FROM trucks")]
        assert len(ids) == len(set(ids))
