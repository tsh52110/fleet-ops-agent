"""Data connectors — where fleet data comes from.

In production, fleet data is not generated; it is synced from source systems:

  * Telematics / ELD (Samsara, Geotab, Motive, OEM systems like Detroit
    Connect): vehicle list, odometer, engine hours, and live J1939 SPN/FMI
    fault events.
  * Maintenance / CMMS (Fleetio, Dossier, shop systems): work orders, service
    schedules, labor hours, costs.
  * Parts / inventory (ERP or dealer network systems): stock levels, costs,
    suppliers, lead times.

Every connector implements the same small contract (`FleetDataConnector`)
and returns plain dict rows in the canonical schema (docs/SCHEMA.md). The
ingestion pipeline (src/ingest.py) — not the connector — owns validation,
quarantine, and loading, so data-quality rules are enforced identically for
every source. The agent NEVER talks to a vendor API directly: it only ever
queries the local, validated, read-only replica produced by ingestion.

Connectors:
  synthetic  — seeded demo generator (default; keeps evals reproducible)
  csv        — universal import from FMS/telematics CSV exports (tested)
  samsara    — REST API scaffold (requires SAMSARA_API_TOKEN; see module)
"""

from __future__ import annotations

from typing import Iterable, Protocol


class FleetDataConnector(Protocol):
    """Contract every data source implements. Rows use the canonical schema
    column names (see docs/SCHEMA.md); values may be strings — the ingestion
    pipeline coerces and validates types."""

    source_name: str

    def fetch_trucks(self) -> Iterable[dict]: ...
    def fetch_fault_codes(self) -> Iterable[dict]: ...
    def fetch_parts(self) -> Iterable[dict]: ...
    def fetch_maintenance_log(self) -> Iterable[dict]: ...


def get_connector(source: str, **kwargs) -> "FleetDataConnector":
    if source == "synthetic":
        from .synthetic import SyntheticConnector
        return SyntheticConnector(**kwargs)
    if source == "csv":
        from .csv_export import CsvExportConnector
        return CsvExportConnector(**kwargs)
    if source == "samsara":
        from .samsara import SamsaraConnector
        return SamsaraConnector(**kwargs)
    raise ValueError(f"Unknown source {source!r}. Available: synthetic, csv, samsara")
