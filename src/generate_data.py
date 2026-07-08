"""Generate the synthetic FleetOps SQLite database (fleet.db).

Fully reproducible: fixed RNG seed and a fixed ANCHOR_DATE, so re-running
produces a byte-identical dataset. All data is synthetic — no real fleet,
VIN, or vendor data. Public repos/datasets informed the *schema shape* only.

Usage:
    python src/generate_data.py [--db fleet.db]

Deliberately seeded patterns (used by the golden eval dataset):
  * ~15% of active trucks are overdue for service (by date and/or mileage).
  * Five "problem trucks" with recurring aftertreatment fault SPN-3226-FMI-4.
  * Two parts stocked below their minimum level (DPF filter, NOx sensor).
  * A winter cluster of battery/starting faults (Dec 2025 - Feb 2026).
"""

from __future__ import annotations

import argparse
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

SEED = 42
ANCHOR_DATE = date(2026, 7, 8)  # fixed "today" for reproducibility
N_TRUCKS = 200
TARGET_LOG_ROWS = 2000

MAKES_MODELS = [
    ("Freightliner", "Cascadia"),
    ("Freightliner", "M2 106"),
    ("Western Star", "49X"),
    ("Volvo", "VNL 860"),
    ("Kenworth", "T680"),
    ("Peterbilt", "579"),
    ("International", "LT625"),
    ("Mack", "Anthem"),
]
DEPOTS = ["Portland OR", "Detroit MI", "Dallas TX", "Atlanta GA", "Reno NV"]
TECHNICIANS = ["M. Alvarez", "J. Chen", "R. Okafor", "S. Patel", "D. Kowalski", "L. Nguyen"]

# (code, description, severity, system, action, part_key)
FAULT_CODES = [
    ("SPN-3226-FMI-4", "Aftertreatment NOx sensor - voltage below normal", "high", "aftertreatment", "Replace outlet NOx sensor; run forced DPF regen", "nox_sensor"),
    ("SPN-3719-FMI-0", "DPF soot load above critical threshold", "critical", "aftertreatment", "Immediate parked regen; replace DPF filter if regen fails", "dpf_filter"),
    ("SPN-100-FMI-1", "Engine oil pressure - low, most severe", "critical", "engine", "Stop vehicle; check oil level and pressure sensor; inspect oil pump", "oil_pump"),
    ("SPN-111-FMI-17", "Coolant level below normal", "medium", "engine", "Top off coolant; pressure-test for leaks", "coolant_reservoir"),
    ("SPN-627-FMI-4", "Power supply voltage low - battery/charging fault", "high", "electrical", "Load-test batteries; inspect alternator and cables", "battery"),
    ("SPN-639-FMI-9", "J1939 CAN bus - abnormal update rate", "medium", "electrical", "Inspect CAN harness and connectors; check terminating resistors", "can_harness"),
    ("SPN-791-FMI-5", "Trailer ABS lamp circuit - open circuit", "low", "brakes", "Inspect ABS lamp circuit wiring and connector", "abs_module"),
    ("SPN-4094-FMI-31", "DEF quality/dosing fault - derate imminent", "high", "aftertreatment", "Verify DEF quality; replace DEF doser if faulty", "def_doser"),
    ("SPN-3251-FMI-0", "DPF differential pressure too high", "high", "aftertreatment", "Inspect DPF for face-plugging; clean or replace filter", "dpf_filter"),
    ("SPN-171-FMI-3", "Ambient air temp sensor - voltage above normal", "low", "engine", "Replace ambient air temperature sensor", "air_temp_sensor"),
    ("SPN-597-FMI-2", "Brake switch data erratic", "medium", "brakes", "Adjust/replace brake pedal switch", "brake_switch"),
    ("SPN-1231-FMI-9", "Turbocharger speed - abnormal update rate", "high", "engine", "Inspect turbo speed sensor wiring; check turbocharger", "turbo_sensor"),
]

# part_key -> (name, number, unit_cost, stock, min_stock, supplier, lead_days)
PARTS = {
    "nox_sensor":        ("NOx Sensor (Outlet)",          "NX-4410-B", 412.50, 3,  6, "DieselTech Supply", 7),
    "dpf_filter":        ("DPF Filter Assembly",           "DPF-9920",  1890.00, 1, 4, "ClearFlow Emissions", 14),
    "oil_pump":          ("Engine Oil Pump",               "OP-3350",   645.00, 5,  2, "HeavyParts Direct", 10),
    "coolant_reservoir": ("Coolant Reservoir Tank",        "CR-1180",   157.25, 9,  3, "HeavyParts Direct", 5),
    "battery":           ("Group 31 AGM Battery",          "BAT-31AGM", 289.99, 14, 8, "VoltMax Distribution", 3),
    "can_harness":       ("J1939 CAN Harness Kit",         "CH-0093",   118.40, 11, 4, "DieselTech Supply", 6),
    "abs_module":        ("Trailer ABS Control Module",    "ABS-7742",  534.10, 6,  2, "BrakeSafe Inc", 9),
    "def_doser":         ("DEF Dosing Unit",               "DD-2810",   722.00, 4,  3, "ClearFlow Emissions", 12),
    "air_temp_sensor":   ("Ambient Air Temp Sensor",       "AT-0521",   38.75,  22, 5, "DieselTech Supply", 4),
    "brake_switch":      ("Brake Pedal Switch",            "BS-1104",   45.20,  17, 5, "BrakeSafe Inc", 4),
    "turbo_sensor":      ("Turbo Speed Sensor",            "TS-6600",   204.90, 7,  3, "HeavyParts Direct", 8),
    "oil_filter":        ("Oil Filter (PM Kit)",           "OF-2001",   28.50,  60, 20, "HeavyParts Direct", 2),
    "air_filter":        ("Air Filter Element",            "AF-3010",   64.00,  35, 12, "HeavyParts Direct", 3),
}

PM_DESCRIPTIONS = [
    "PM-A service: oil & filter change, chassis lube, fluid top-off",
    "PM-B service: PM-A plus air/fuel filters, valve adjustment check",
    "DOT annual inspection completed",
    "Brake inspection and adjustment",
    "Tire rotation and tread depth check",
]

SCHEMA = """
CREATE TABLE trucks (
    truck_id TEXT PRIMARY KEY,           -- e.g. 'T-042'
    vin TEXT NOT NULL UNIQUE,            -- synthetic VIN
    make TEXT NOT NULL,
    model TEXT NOT NULL,
    year INTEGER NOT NULL,
    mileage INTEGER NOT NULL,            -- current odometer, miles
    status TEXT NOT NULL,                -- active | in_shop | retired
    home_depot TEXT NOT NULL,
    last_service_date TEXT NOT NULL,     -- ISO date
    next_service_due_date TEXT NOT NULL, -- ISO date; past => overdue
    next_service_due_miles INTEGER NOT NULL -- odometer threshold; below current => overdue
);

CREATE TABLE fault_codes (
    code TEXT PRIMARY KEY,               -- SAE J1939 style 'SPN-xxxx-FMI-x'
    description TEXT NOT NULL,
    severity TEXT NOT NULL,              -- low | medium | high | critical
    system TEXT NOT NULL,                -- engine | aftertreatment | electrical | brakes
    recommended_action TEXT NOT NULL,
    related_part_id TEXT REFERENCES parts(part_id)
);

CREATE TABLE parts (
    part_id TEXT PRIMARY KEY,            -- e.g. 'P-004'
    part_name TEXT NOT NULL,
    part_number TEXT NOT NULL UNIQUE,
    unit_cost REAL NOT NULL,
    stock_qty INTEGER NOT NULL,
    min_stock INTEGER NOT NULL,          -- reorder threshold; stock below => low stock
    supplier TEXT NOT NULL,
    lead_time_days INTEGER NOT NULL
);

CREATE TABLE maintenance_log (
    log_id INTEGER PRIMARY KEY,
    truck_id TEXT NOT NULL REFERENCES trucks(truck_id),
    event_date TEXT NOT NULL,            -- ISO date
    event_type TEXT NOT NULL,            -- preventive | repair | inspection | fault
    fault_code TEXT REFERENCES fault_codes(code),
    description TEXT NOT NULL,
    part_id TEXT REFERENCES parts(part_id),
    labor_hours REAL NOT NULL,
    cost REAL NOT NULL,                  -- parts + labor, USD
    technician TEXT NOT NULL,
    odometer INTEGER NOT NULL
);
"""


def make_vin(rng: random.Random) -> str:
    chars = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"  # no I/O/Q, like real VINs
    return "1FS" + "".join(rng.choice(chars) for _ in range(14))


def build(db_path: Path) -> None:
    rng = random.Random(SEED)
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)

    # --- parts ---
    part_ids: dict[str, str] = {}
    for i, (key, (name, number, cost, stock, min_stock, supplier, lead)) in enumerate(
        PARTS.items(), start=1
    ):
        pid = f"P-{i:03d}"
        part_ids[key] = pid
        con.execute(
            "INSERT INTO parts VALUES (?,?,?,?,?,?,?,?)",
            (pid, name, number, cost, stock, min_stock, supplier, lead),
        )

    # --- fault codes ---
    for code, desc, sev, system, action, part_key in FAULT_CODES:
        con.execute(
            "INSERT INTO fault_codes VALUES (?,?,?,?,?,?)",
            (code, desc, sev, system, action, part_ids[part_key]),
        )

    # --- trucks ---
    trucks: list[dict] = []
    for i in range(1, N_TRUCKS + 1):
        make, model = rng.choice(MAKES_MODELS)
        year = rng.randint(2016, 2025)
        mileage = rng.randint(30_000, 620_000)
        status = rng.choices(["active", "in_shop", "retired"], weights=[86, 9, 5])[0]
        last_service = ANCHOR_DATE - timedelta(days=rng.randint(10, 170))
        overdue = status == "active" and rng.random() < 0.15
        if overdue:
            # overdue by date, by mileage, or both
            due_date = ANCHOR_DATE - timedelta(days=rng.randint(5, 60))
            due_miles = mileage - rng.randint(500, 8_000) if rng.random() < 0.5 else mileage + rng.randint(2_000, 15_000)
        else:
            due_date = ANCHOR_DATE + timedelta(days=rng.randint(7, 120))
            due_miles = mileage + rng.randint(3_000, 25_000)
        trucks.append(
            dict(
                truck_id=f"T-{i:03d}", vin=make_vin(rng), make=make, model=model,
                year=year, mileage=mileage, status=status, depot=rng.choice(DEPOTS),
                last_service=last_service, due_date=due_date, due_miles=due_miles,
            )
        )
    for t in trucks:
        con.execute(
            "INSERT INTO trucks VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (t["truck_id"], t["vin"], t["make"], t["model"], t["year"], t["mileage"],
             t["status"], t["depot"], t["last_service"].isoformat(),
             t["due_date"].isoformat(), t["due_miles"]),
        )

    # --- maintenance log ---
    fault_by_code = {f[0]: f for f in FAULT_CODES}
    log_rows: list[tuple] = []

    def add_event(truck: dict, day: date, etype: str, fault: str | None,
                  desc: str, part_key: str | None, hours: float, part_qty: int = 1) -> None:
        part_cost = PARTS[part_key][2] * part_qty if part_key else 0.0
        cost = round(part_cost + hours * 135.0, 2)  # $135/hr shop labor rate
        odo = max(1000, truck["mileage"] - rng.randint(0, 40_000))
        log_rows.append(
            (truck["truck_id"], day.isoformat(), etype, fault, desc,
             part_ids[part_key] if part_key else None, hours, cost,
             rng.choice(TECHNICIANS), odo)
        )

    # 1) Problem trucks: recurring SPN-3226 NOx faults (4-6 occurrences each)
    problem_trucks = [t for t in trucks if t["status"] == "active"][:5]
    for t in problem_trucks:
        for _ in range(rng.randint(4, 6)):
            day = ANCHOR_DATE - timedelta(days=rng.randint(5, 330))
            add_event(t, day, "fault", "SPN-3226-FMI-4",
                      "Recurring NOx sensor fault; sensor replaced, regen performed",
                      "nox_sensor", round(rng.uniform(1.5, 4.0), 1))

    # 2) Winter battery-fault cluster (Dec 2025 - Feb 2026)
    winter_days = [date(2025, 12, 1) + timedelta(days=d) for d in range(90)]
    for _ in range(70):
        t = rng.choice(trucks)
        add_event(t, rng.choice(winter_days), "fault", "SPN-627-FMI-4",
                  "Cold-start low-voltage fault; battery load-tested",
                  "battery" if rng.random() < 0.6 else None,
                  round(rng.uniform(0.5, 2.0), 1))

    # 3) General fault events across the fleet
    other_codes = [c for c in fault_by_code if c not in ("SPN-3226-FMI-4", "SPN-627-FMI-4")]
    for _ in range(520):
        t = rng.choice(trucks)
        code = rng.choice(other_codes)
        _, desc, _, _, action, part_key = fault_by_code[code]
        use_part = rng.random() < 0.55
        add_event(t, ANCHOR_DATE - timedelta(days=rng.randint(1, 540)), "fault", code,
                  f"{desc}. Action: {action}", part_key if use_part else None,
                  round(rng.uniform(0.5, 6.0), 1))

    # 4) Preventive / inspection events to reach ~TARGET_LOG_ROWS
    while len(log_rows) < TARGET_LOG_ROWS:
        t = rng.choice(trucks)
        desc = rng.choice(PM_DESCRIPTIONS)
        etype = "inspection" if "inspection" in desc.lower() else "preventive"
        part_key = "oil_filter" if "oil" in desc else ("air_filter" if "PM-B" in desc else None)
        add_event(t, ANCHOR_DATE - timedelta(days=rng.randint(1, 540)), etype, None,
                  desc, part_key, round(rng.uniform(1.0, 5.0), 1))

    con.executemany(
        "INSERT INTO maintenance_log (truck_id, event_date, event_type, fault_code,"
        " description, part_id, labor_hours, cost, technician, odometer)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        log_rows,
    )
    con.commit()

    n_trucks = con.execute("SELECT COUNT(*) FROM trucks").fetchone()[0]
    n_logs = con.execute("SELECT COUNT(*) FROM maintenance_log").fetchone()[0]
    n_overdue = con.execute(
        "SELECT COUNT(*) FROM trucks WHERE status='active' AND"
        " (next_service_due_date < ? OR next_service_due_miles < mileage)",
        (ANCHOR_DATE.isoformat(),),
    ).fetchone()[0]
    low_stock = con.execute("SELECT COUNT(*) FROM parts WHERE stock_qty < min_stock").fetchone()[0]
    con.close()
    print(f"fleet.db written: {n_trucks} trucks, {n_logs} log rows, "
          f"{n_overdue} overdue trucks, {low_stock} low-stock parts")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(Path(__file__).resolve().parent.parent / "fleet.db"))
    build(Path(ap.parse_args().db))
