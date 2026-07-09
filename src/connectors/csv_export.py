"""CSV export connector — the universal real-world on-ramp.

Every fleet-management / telematics platform (Samsara, Geotab, Motive,
Fleetio, Dossier…) can export CSVs, and many fleets already move data this
way on a nightly schedule. This connector reads a directory of four CSVs
(trucks.csv, fault_codes.csv, parts.csv, maintenance_log.csv) whose headers
are mapped to the canonical schema via COLUMN_ALIASES — so a Fleetio export
with `vehicle_id` or a Samsara export with `name` maps onto `truck_id`
without editing the file.

This connector is fully tested (tests/test_ingest.py) against the fixture
export in tests/fixtures/sample_export/.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

# canonical column -> accepted source-header aliases (lowercased)
COLUMN_ALIASES: dict[str, dict[str, list[str]]] = {
    "trucks": {
        "truck_id": ["truck_id", "vehicle_id", "asset_id", "unit_number", "name"],
        "vin": ["vin", "vehicle_vin"],
        "make": ["make", "vehicle_make"],
        "model": ["model", "vehicle_model"],
        "year": ["year", "model_year"],
        "mileage": ["mileage", "odometer", "odometer_miles", "obd_odometer_miles"],
        "status": ["status", "vehicle_status"],
        "home_depot": ["home_depot", "depot", "home_terminal", "group"],
        "last_service_date": ["last_service_date", "last_service"],
        "next_service_due_date": ["next_service_due_date", "next_service_date", "service_due_date"],
        "next_service_due_miles": ["next_service_due_miles", "service_due_miles"],
    },
    "fault_codes": {
        "code": ["code", "fault_code", "spn_fmi", "dtc"],
        "description": ["description", "fault_description"],
        "severity": ["severity"],
        "system": ["system", "subsystem"],
        "recommended_action": ["recommended_action", "action"],
        "related_part_id": ["related_part_id", "part_id"],
    },
    "parts": {
        "part_id": ["part_id", "id"],
        "part_name": ["part_name", "name", "description"],
        "part_number": ["part_number", "sku", "oem_number"],
        "unit_cost": ["unit_cost", "cost", "price"],
        "stock_qty": ["stock_qty", "quantity", "on_hand", "qty_on_hand"],
        "min_stock": ["min_stock", "reorder_point", "minimum"],
        "supplier": ["supplier", "vendor"],
        "lead_time_days": ["lead_time_days", "lead_time"],
    },
    "maintenance_log": {
        "truck_id": ["truck_id", "vehicle_id", "asset_id", "unit_number"],
        "event_date": ["event_date", "date", "completed_at", "service_date"],
        "event_type": ["event_type", "type", "work_order_type"],
        "fault_code": ["fault_code", "code", "dtc"],
        "description": ["description", "notes", "work_performed"],
        "part_id": ["part_id"],
        "labor_hours": ["labor_hours", "hours"],
        "cost": ["cost", "total_cost", "amount"],
        "technician": ["technician", "tech", "assigned_to"],
        "odometer": ["odometer", "odometer_miles", "meter"],
    },
}


def _remap(row: dict, aliases: dict[str, list[str]]) -> dict:
    lowered = {k.strip().lower(): (v if v != "" else None) for k, v in row.items()}
    out = {}
    for canonical, names in aliases.items():
        out[canonical] = next((lowered[n] for n in names if n in lowered), None)
    return out


class CsvExportConnector:
    source_name = "csv"

    def __init__(self, export_dir: str | Path):
        self.export_dir = Path(export_dir)
        missing = [f for f in ("trucks.csv", "fault_codes.csv", "parts.csv",
                               "maintenance_log.csv")
                   if not (self.export_dir / f).exists()]
        if missing:
            raise FileNotFoundError(
                f"CSV export dir {self.export_dir} is missing: {', '.join(missing)}")
        self.source_name = f"csv:{self.export_dir.name}"

    def _read(self, table: str) -> Iterable[dict]:
        with open(self.export_dir / f"{table}.csv", newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                yield _remap(row, COLUMN_ALIASES[table])

    def fetch_trucks(self):
        return self._read("trucks")

    def fetch_fault_codes(self):
        return self._read("fault_codes")

    def fetch_parts(self):
        return self._read("parts")

    def fetch_maintenance_log(self):
        return self._read("maintenance_log")
