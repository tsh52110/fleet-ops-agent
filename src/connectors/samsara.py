"""Samsara telematics connector — API SCAFFOLD (not yet run against a live org).

⚠️  STATUS: untested scaffold. The endpoints below come from Samsara's public
API reference (https://developers.samsara.com — see "Stats feed",
GET /fleet/vehicles/stats/feed) but this module has never been executed
against a live Samsara org because that requires a customer API token.
Verify field paths against the docs before production use. It exists to show
the real integration shape; the tested real-world path today is the CSV
connector.

What Samsara provides (telematics/ELD):
  * vehicle list + VIN + odometer (GET /fleet/vehicles, stats types
    obdOdometerMeters / gpsOdometerMeters)
  * live J1939 fault codes (stats type faultCodes → spn/fmi per vehicle)

What it does NOT provide: work-order costs, labor, parts inventory — those
come from a CMMS/ERP connector. This connector therefore only fills the
`trucks` table and fault *events*; fault-code reference data, parts, and
maintenance history must come from other sources (see docs/PRODUCTION_DATA.md).
"""

from __future__ import annotations

import os
from datetime import date

API_BASE = "https://api.samsara.com"
METERS_PER_MILE = 1609.344


class SamsaraConnector:
    source_name = "samsara"

    def __init__(self, api_token: str | None = None):
        self.token = api_token or os.environ.get("SAMSARA_API_TOKEN")
        if not self.token:
            raise RuntimeError(
                "SamsaraConnector needs SAMSARA_API_TOKEN. This connector is an "
                "untested scaffold — see module docstring; use the CSV connector "
                "for a tested real-data path.")
        import httpx  # optional dep, imported lazily
        self._client = httpx.Client(
            base_url=API_BASE,
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=30,
        )

    def _paginate(self, path: str, params: dict) -> list[dict]:
        items, cursor = [], None
        while True:
            page_params = dict(params, **({"after": cursor} if cursor else {}))
            resp = self._client.get(path, params=page_params)
            resp.raise_for_status()
            body = resp.json()
            items.extend(body.get("data", []))
            pagination = body.get("pagination", {})
            if not pagination.get("hasNextPage"):
                return items
            cursor = pagination.get("endCursor")

    def fetch_trucks(self):
        vehicles = self._paginate("/fleet/vehicles", {"limit": 512})
        stats = {s["id"]: s for s in self._paginate(
            "/fleet/vehicles/stats", {"types": "obdOdometerMeters"})}
        for v in vehicles:
            odo_m = (stats.get(v["id"], {}).get("obdOdometerMeters") or {}).get("value")
            yield {
                "truck_id": v.get("name") or v["id"],
                "vin": v.get("vin"),
                "make": v.get("make"),
                "model": v.get("model"),
                "year": v.get("year"),
                "mileage": round(odo_m / METERS_PER_MILE) if odo_m else None,
                "status": "active",  # Samsara has no shop status; comes from CMMS
                "home_depot": (v.get("tags") or [{}])[0].get("name"),
                # service schedule lives in the CMMS, not telematics:
                "last_service_date": None,
                "next_service_due_date": None,
                "next_service_due_miles": None,
            }

    def fetch_maintenance_log(self):
        """Yields fault EVENTS only (type='fault') from the vehicle stats feed;
        work orders/costs come from the CMMS connector."""
        for s in self._paginate("/fleet/vehicles/stats", {"types": "faultCodes"}):
            for f in ((s.get("faultCodes") or {}).get("j1939") or {}).get("diagnosticTroubleCodes", []):
                yield {
                    "truck_id": s.get("name") or s["id"],
                    "event_date": date.today().isoformat(),
                    "event_type": "fault",
                    "fault_code": f"SPN-{f.get('spnId')}-FMI-{f.get('fmiId')}",
                    "description": f.get("spnDescription") or "J1939 fault",
                    "part_id": None, "labor_hours": 0.0, "cost": 0.0,
                    "technician": "telematics", "odometer": 0,
                }

    def fetch_fault_codes(self):
        return []  # reference data: load from J1939 standard / OEM manual export

    def fetch_parts(self):
        return []  # inventory: load from ERP/dealer system connector
