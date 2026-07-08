"""Unit tests for src/tools.py — including the read-only SQL guardrail.

The write-refusal tests are the safety contract for run_sql: no non-SELECT
statement may ever execute, and refusal must happen before touching the DB.
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.tools import DEFAULT_DB, generate_report, lookup_part, run_sql

WRITE_ATTEMPTS = [
    "DELETE FROM trucks",
    "DROP TABLE maintenance_log",
    "UPDATE trucks SET mileage = 0",
    "INSERT INTO parts VALUES ('P-999','x','x',1,1,1,'x',1)",
    "SELECT 1; DELETE FROM trucks",                      # stacked statement
    "select * from trucks; drop table trucks",           # stacked, lowercase
    "WITH x AS (SELECT 1) DELETE FROM trucks",           # CTE-wrapped write
    "/* sneaky */ DELETE FROM trucks -- just cleanup",   # comment obfuscation
    "PRAGMA writable_schema = 1",
    "ATTACH DATABASE '/tmp/evil.db' AS evil",
    "CREATE TABLE pwned (id INTEGER)",
    "REPLACE INTO parts VALUES ('P-001','x','x',1,1,1,'x',1)",
    "VACUUM",
]


def _table_counts() -> dict:
    con = sqlite3.connect(f"file:{DEFAULT_DB}?mode=ro", uri=True)
    counts = {
        t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("trucks", "maintenance_log", "fault_codes", "parts")
    }
    con.close()
    return counts


class TestRunSqlGuardrail:
    def test_every_write_attempt_is_refused(self):
        before = _table_counts()
        for stmt in WRITE_ATTEMPTS:
            result = run_sql(stmt)
            assert "error" in result, f"NOT refused: {stmt!r}"
            assert "rows" not in result
        assert _table_counts() == before, "database was mutated by a refused query"

    def test_empty_and_garbage_queries_refused(self):
        assert "error" in run_sql("")
        assert "error" in run_sql("-- only a comment")
        assert "error" in run_sql("EXPLAIN QUERY PLAN SELECT 1")

    def test_select_works(self):
        result = run_sql("SELECT truck_id, status FROM trucks LIMIT 3")
        assert result["row_count"] == 3
        assert result["columns"] == ["truck_id", "status"]

    def test_cte_select_works(self):
        result = run_sql(
            "WITH overdue AS (SELECT truck_id FROM trucks WHERE status='active' "
            "AND next_service_due_date < '2026-07-08') SELECT COUNT(*) AS n FROM overdue"
        )
        assert "error" not in result
        assert result["rows"][0][0] > 0

    def test_row_cap(self):
        result = run_sql("SELECT * FROM maintenance_log")
        assert result["row_count"] == 50
        assert result["truncated"] is True

    def test_sql_error_is_reported_not_raised(self):
        result = run_sql("SELECT nope FROM nothing")
        assert "error" in result


class TestLookupPart:
    def test_known_code_with_low_stock_part(self):
        result = lookup_part("SPN-3226-FMI-4")
        assert result["severity"] == "high"
        assert result["part"]["part_name"] == "NOx Sensor (Outlet)"
        assert result["part"]["stock_status"] == "low_stock"

    def test_case_insensitive(self):
        assert "error" not in lookup_part("spn-627-fmi-4")

    def test_unknown_code(self):
        assert "error" in lookup_part("SPN-0000-FMI-9")


class TestGenerateReport:
    def test_fleet_report(self, tmp_path):
        result = generate_report(days=30, reports_dir=tmp_path)
        assert "error" not in result
        assert Path(result["report_path"]).exists()
        assert result["stats"]["events"] > 0

    def test_single_truck_report(self, tmp_path):
        result = generate_report(truck_id="T-001", days=365, reports_dir=tmp_path)
        assert result["stats"]["scope"] == "T-001"

    def test_unknown_truck(self, tmp_path):
        assert "error" in generate_report(truck_id="T-999", reports_dir=tmp_path)
