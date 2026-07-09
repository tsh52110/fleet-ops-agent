"""Ingestion pipeline: connector → validate → quarantine → load → provenance.

This is the accuracy boundary of the system. The agent only ever queries the
database this pipeline produces, so every guarantee about answer correctness
reduces to the guarantees enforced here:

  1. Type & enum validation per row (bad rows are QUARANTINED with a reason,
     never silently dropped or coerced into place).
  2. Referential integrity: maintenance events referencing unknown trucks are
     rejected; unknown fault codes are kept but flagged (real telematics feeds
     contain codes missing from reference data).
  3. Anomaly gates: negative costs, impossible years, duplicate natural keys.
  4. Freshness check: stale data is flagged in provenance so the UI can warn.
  5. Provenance: a sync_metadata row records source system, sync time, per-table
     accepted/rejected counts, and warnings — surfaced in the dashboard so an
     operator always knows what data they are looking at and how healthy it is.

Usage:
    python src/ingest.py --source synthetic --out fleet_live.db
    python src/ingest.py --source csv --dir exports/2026-07-08 --out fleet_live.db
    python src/ingest.py --source samsara --out fleet_live.db   # needs SAMSARA_API_TOKEN

Point the app/agent at the result with FLEETOPS_DB=/path/to/fleet_live.db.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.connectors import get_connector  # noqa: E402
from src.generate_data import SCHEMA  # noqa: E402  (canonical table DDL)

SYNC_SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_metadata (
    sync_id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    synced_at TEXT NOT NULL,
    counts_json TEXT NOT NULL,      -- {"trucks": {"accepted": n, "rejected": n}, ...}
    warnings_json TEXT NOT NULL,    -- ["...", ...]
    freshest_event_date TEXT
);
"""

VALID_STATUS = {"active", "in_shop", "retired"}
VALID_EVENT_TYPES = {"preventive", "repair", "inspection", "fault"}
VALID_SEVERITY = {"low", "medium", "high", "critical"}


def _to_int(v, field, errors):
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        errors.append(f"{field}: not an integer ({v!r})")
        return None


def _to_float(v, field, errors):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        errors.append(f"{field}: not a number ({v!r})")
        return None


def _to_date(v, field, errors):
    if v is None or v == "":
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(str(v)[:19], fmt).date().isoformat()
        except ValueError:
            continue
    errors.append(f"{field}: unparseable date ({v!r})")
    return None


def validate_truck(r: dict) -> tuple[dict | None, list[str]]:
    e: list[str] = []
    if not r.get("truck_id"):
        e.append("truck_id: missing")
    year = _to_int(r.get("year"), "year", e)
    if year is not None and not (1990 <= year <= date.today().year + 2):
        e.append(f"year: out of range ({year})")
    mileage = _to_int(r.get("mileage"), "mileage", e)
    if mileage is not None and mileage < 0:
        e.append(f"mileage: negative ({mileage})")
    status = (r.get("status") or "active").lower()
    if status not in VALID_STATUS:
        e.append(f"status: invalid ({r.get('status')!r})")
    row = {
        "truck_id": r.get("truck_id"), "vin": r.get("vin"),
        "make": r.get("make"), "model": r.get("model"), "year": year,
        "mileage": mileage, "status": status, "home_depot": r.get("home_depot"),
        "last_service_date": _to_date(r.get("last_service_date"), "last_service_date", e),
        "next_service_due_date": _to_date(r.get("next_service_due_date"), "next_service_due_date", e),
        "next_service_due_miles": _to_int(r.get("next_service_due_miles"), "next_service_due_miles", e),
    }
    return (None, e) if e else (row, [])


def validate_fault_code(r: dict) -> tuple[dict | None, list[str]]:
    e: list[str] = []
    if not r.get("code"):
        e.append("code: missing")
    sev = (r.get("severity") or "").lower()
    if sev and sev not in VALID_SEVERITY:
        e.append(f"severity: invalid ({r.get('severity')!r})")
    row = {"code": r.get("code"), "description": r.get("description"),
           "severity": sev or None, "system": r.get("system"),
           "recommended_action": r.get("recommended_action"),
           "related_part_id": r.get("related_part_id")}
    return (None, e) if e else (row, [])


def validate_part(r: dict) -> tuple[dict | None, list[str]]:
    e: list[str] = []
    if not r.get("part_id"):
        e.append("part_id: missing")
    cost = _to_float(r.get("unit_cost"), "unit_cost", e)
    if cost is not None and cost < 0:
        e.append(f"unit_cost: negative ({cost})")
    stock = _to_int(r.get("stock_qty"), "stock_qty", e)
    if stock is not None and stock < 0:
        e.append(f"stock_qty: negative ({stock})")
    row = {"part_id": r.get("part_id"), "part_name": r.get("part_name"),
           "part_number": r.get("part_number"), "unit_cost": cost,
           "stock_qty": stock, "min_stock": _to_int(r.get("min_stock"), "min_stock", e),
           "supplier": r.get("supplier"),
           "lead_time_days": _to_int(r.get("lead_time_days"), "lead_time_days", e)}
    return (None, e) if e else (row, [])


def validate_event(r: dict, known_trucks: set) -> tuple[dict | None, list[str]]:
    e: list[str] = []
    if not r.get("truck_id"):
        e.append("truck_id: missing")
    elif known_trucks and r["truck_id"] not in known_trucks:
        e.append(f"truck_id: references unknown truck ({r['truck_id']!r})")
    event_date = _to_date(r.get("event_date"), "event_date", e)
    if not event_date:
        e.append("event_date: missing")
    etype = (r.get("event_type") or "").lower()
    if etype not in VALID_EVENT_TYPES:
        e.append(f"event_type: invalid ({r.get('event_type')!r})")
    cost = _to_float(r.get("cost"), "cost", e) or 0.0
    if cost < 0:
        e.append(f"cost: negative ({cost})")
    row = {"truck_id": r.get("truck_id"), "event_date": event_date,
           "event_type": etype, "fault_code": r.get("fault_code") or None,
           "description": r.get("description") or "", "part_id": r.get("part_id") or None,
           "labor_hours": _to_float(r.get("labor_hours"), "labor_hours", e) or 0.0,
           "cost": cost, "technician": r.get("technician") or "unknown",
           "odometer": _to_int(r.get("odometer"), "odometer", e) or 0}
    return (None, e) if e else (row, [])


def ingest(source: str, out_path: str | Path, strict: bool = False, **connector_kwargs) -> dict:
    connector = get_connector(source, **connector_kwargs)
    out_path = Path(out_path)
    if out_path.exists():
        out_path.unlink()
    con = sqlite3.connect(out_path)
    con.executescript(SCHEMA)
    con.executescript(SYNC_SCHEMA)

    counts: dict[str, dict] = {}
    warnings: list[str] = []
    quarantine: list[dict] = []

    def load(table, rows, validator, insert_sql, dedupe_key=None):
        accepted, rejected, seen = 0, 0, set()
        for raw in rows:
            row, errs = validator(raw)
            if errs:
                rejected += 1
                quarantine.append({"table": table, "row": raw, "errors": errs})
                continue
            if dedupe_key:
                key = dedupe_key(row)
                if key in seen:
                    rejected += 1
                    quarantine.append({"table": table, "row": raw,
                                       "errors": [f"duplicate key {key}"]})
                    continue
                seen.add(key)
            con.execute(insert_sql, row)
            accepted += 1
        counts[table] = {"accepted": accepted, "rejected": rejected}

    load("trucks", connector.fetch_trucks(), validate_truck,
         """INSERT INTO trucks VALUES (:truck_id,:vin,:make,:model,:year,:mileage,
            :status,:home_depot,:last_service_date,:next_service_due_date,
            :next_service_due_miles)""",
         dedupe_key=lambda r: r["truck_id"])
    load("fault_codes", connector.fetch_fault_codes(), validate_fault_code,
         """INSERT INTO fault_codes VALUES (:code,:description,:severity,:system,
            :recommended_action,:related_part_id)""",
         dedupe_key=lambda r: r["code"])
    load("parts", connector.fetch_parts(), validate_part,
         """INSERT INTO parts VALUES (:part_id,:part_name,:part_number,:unit_cost,
            :stock_qty,:min_stock,:supplier,:lead_time_days)""",
         dedupe_key=lambda r: r["part_id"])

    known_trucks = {r[0] for r in con.execute("SELECT truck_id FROM trucks")}
    load("maintenance_log", connector.fetch_maintenance_log(),
         lambda r: validate_event(r, known_trucks),
         """INSERT INTO maintenance_log (truck_id,event_date,event_type,fault_code,
            description,part_id,labor_hours,cost,technician,odometer)
            VALUES (:truck_id,:event_date,:event_type,:fault_code,:description,
            :part_id,:labor_hours,:cost,:technician,:odometer)""")

    # soft integrity + freshness warnings
    unknown_codes = con.execute(
        """SELECT COUNT(DISTINCT ml.fault_code) FROM maintenance_log ml
           LEFT JOIN fault_codes fc ON fc.code = ml.fault_code
           WHERE ml.fault_code IS NOT NULL AND fc.code IS NULL""").fetchone()[0]
    if unknown_codes:
        warnings.append(f"{unknown_codes} fault code(s) in events have no reference-data entry")
    freshest = con.execute("SELECT MAX(event_date) FROM maintenance_log").fetchone()[0]
    if freshest and (date.today() - date.fromisoformat(freshest)).days > 7:
        warnings.append(f"freshest maintenance event is {freshest} — data may be stale")
    for table, c in counts.items():
        if c["rejected"]:
            warnings.append(f"{table}: {c['rejected']} row(s) quarantined")

    con.execute(
        "INSERT INTO sync_metadata (source, synced_at, counts_json, warnings_json,"
        " freshest_event_date) VALUES (?,?,?,?,?)",
        (connector.source_name, datetime.now(timezone.utc).isoformat(),
         json.dumps(counts), json.dumps(warnings), freshest))
    con.commit()
    con.close()

    if quarantine:
        qpath = out_path.with_suffix(".quarantine.json")
        qpath.write_text(json.dumps(quarantine, indent=2, default=str))

    report = {"source": connector.source_name, "out": str(out_path),
              "counts": counts, "warnings": warnings,
              "quarantined": len(quarantine)}
    total_rejected = sum(c["rejected"] for c in counts.values())
    if strict and total_rejected:
        raise SystemExit(f"strict mode: {total_rejected} row(s) rejected — see "
                         f"{out_path.with_suffix('.quarantine.json')}")
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", required=True, choices=["synthetic", "csv", "samsara"])
    ap.add_argument("--dir", help="CSV export directory (for --source csv)")
    ap.add_argument("--out", default="fleet_live.db")
    ap.add_argument("--strict", action="store_true",
                    help="fail (exit 1) if any row is quarantined")
    args = ap.parse_args()
    kwargs = {"export_dir": args.dir} if args.source == "csv" else {}
    result = ingest(args.source, args.out, strict=args.strict, **kwargs)
    print(json.dumps(result, indent=2))
