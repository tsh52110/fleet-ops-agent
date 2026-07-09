# Where the data comes from in production

The demo runs on a seeded synthetic database so every answer is verifiable
against known ground truth. In a real deployment nothing is generated — data
is **synced from the systems a fleet already runs**, through the ingestion
pipeline in [`src/ingest.py`](../src/ingest.py), into a local read-only
replica that is the *only* thing the agent can query.

## Source systems → canonical tables

| Canonical table | Real-world source | Example systems | Sync cadence |
|---|---|---|---|
| `trucks` (vehicle master, odometer) | **Telematics / ELD platform** — vehicle list, VIN, live odometer, engine hours | Samsara, Geotab, Motive, Verizon Connect; OEM telematics (Detroit Connect for Freightliner/Western Star, Volvo Connect, Mack GuardDog) | hourly–daily |
| `maintenance_log` (`fault` events) | **Telematics J1939 feed** — live SPN/FMI diagnostic trouble codes per vehicle | same as above (e.g. Samsara `GET /fleet/vehicles/stats?types=faultCodes` — see developers.samsara.com) | near-real-time |
| `maintenance_log` (work orders: preventive/repair/inspection, labor, cost) | **Maintenance system / CMMS** — completed work orders with labor hours and cost | Fleetio, Dossier, Whip Around, TMT Fleet Maintenance, in-house shop system | daily |
| `trucks.next_service_due_*` | **CMMS service schedules** (PM programs by date/mileage) | same CMMS | daily |
| `fault_codes` (reference data) | **J1939 SPN/FMI standard + OEM fault-code manuals** — descriptions, severity, recommended actions | SAE J1939-73 tables; OEM action plans (e.g. Detroit Diesel / Cummins fault guides) | static, updated per OEM release |
| `parts` (stock, cost, lead time) | **Parts inventory / ERP** | NetSuite/SAP, dealer network systems (e.g. Excelerator for DTNA parts), shop inventory module of the CMMS | daily |

Three practical integration paths, in order of how fleets actually adopt them:

1. **CSV exports (universal, implemented & tested).** Every platform above can
   schedule CSV exports. `CsvExportConnector` maps vendor column names onto the
   canonical schema via an alias table — a Fleetio `vehicle_id` or a Samsara
   `odometer` lands in the right column without editing files.
   `python src/ingest.py --source csv --dir exports/2026-07-08 --out fleet_live.db`
2. **Vendor REST APIs (scaffolded).** `SamsaraConnector` shows the shape:
   token-authenticated pulls of the vehicle list + J1939 fault feed. It is a
   clearly-labeled **untested scaffold** (running it requires a customer API
   token); endpoints reference the public docs at developers.samsara.com.
   A Geotab/Fleetio/Detroit Connect connector implements the same 4-method
   contract.
3. **Webhooks/streaming** (later): telematics platforms can push fault events;
   the pipeline stays identical — events land in a staging queue and go
   through the same validation.

## The accuracy boundary

**The agent never talks to a vendor API.** It queries only the validated local
replica, read-only. That is deliberate:

```
Telematics ─┐
CMMS ───────┼─► connector ─► INGESTION GATES ─► fleet_live.db ─► agent (read-only)
ERP/parts ──┘                (src/ingest.py)      + sync_metadata      │
                                                                       ▼
                                                    dashboard shows source, sync
                                                    time, and quality warnings
```

Every ingestion run enforces, per row:

- **Type & enum validation** — unparseable dates, non-numeric costs, invalid
  statuses are **quarantined to `<db>.quarantine.json` with a reason**, never
  silently dropped or coerced.
- **Referential integrity** — events referencing unknown trucks are rejected;
  fault codes missing from reference data are kept but flagged as a warning.
- **Anomaly gates** — negative costs/mileage, impossible model years,
  duplicate primary keys.
- **Freshness** — if the newest event is older than 7 days the sync is flagged
  stale.
- **Provenance** — a `sync_metadata` row records source system, sync time,
  accepted/rejected counts per table, and all warnings. The dashboard sidebar
  and the agent's system prompt both read it, so the operator and the model
  always know which data they are reasoning over and how healthy it is.

`--strict` makes any quarantined row fail the sync (exit 1) for pipelines where
partial data is worse than no data.

## What changes for evals with real data

The golden dataset's reference answers are ground truth for the *synthetic*
DB. Against a live replica the same methodology applies but the answers are
**regenerated from the replica itself**: each golden question is paired with a
ground-truth SQL query (not a frozen answer), executed at eval time against
the same snapshot the agent sees. Tool-selection scoring and the write-refusal
guardrail are data-independent and run unchanged. Snapshot the replica per
eval run so agent, judge, and ground truth all see identical data.

## Trust chain, end to end

1. Source system exports → checksummed, dated files / paginated API pulls.
2. Ingestion validates or quarantines every row; nothing enters silently.
3. Replica is read-only to the agent (SQLite `mode=ro` + authorizer + statement
   validation — three layers, tested in CI).
4. Every agent answer cites its tool calls; the dashboard shows the exact SQL
   and rows behind each answer.
5. Provenance panel shows source, sync age, and quality warnings next to every
   conversation.
6. Evals score answers against ground-truth SQL over the same snapshot.
