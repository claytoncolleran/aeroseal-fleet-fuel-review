# Aeroseal Fleet Fuel Review

## Project Overview
Fuel analytics and fleet manager approval workflow for Aeroseal's fleet. Ingests monthly Corpay fuel card exports, cross-references against Fleetio vehicle/fleet group data, flags anomalies, and routes approvals: Fleet Manager → Fleet Administrator → Accounting.

**Repo:** https://github.com/claytoncolleran/aeroseal-fleet-fuel-review
**Working directory:** `~/dev/projects/aeroseal/Fuel-Review/`

## Architecture (WAT Framework)
- **Workflows:** `workflows/` — Markdown SOPs
- **Tools:** `tools/` — Python scripts for deterministic execution
- **Data:** `data/` — persistent project data
- **Web App:** `app.py` — Flask server on port 5001
- **Templates:** `templates/` — Jinja2 HTML with Aeroseal branding

## Key Files
| File | Purpose |
|---|---|
| `tools/anomaly_detection.py` | Core engine — loads Corpay data, matches to Fleetio, runs 6 anomaly flags, outputs `anomaly_report.json` |
| `data/mpg_baselines.json` | MPG baselines for all 101 fleet vehicles (EPA + manufacturer estimates) |
| `data/Corpay_Transactions.xlsx` | Monthly Corpay fuel card export (source data — never overwrite) |
| `data/anomaly_report.json` | Generated report consumed by the web app |
| `data/review_decisions.json` | Fleet manager approve/deny decisions (gitignored) |
| `app.py` | Flask app — dashboard, manager views, admin drill-down, accounting report |
| `.env` | Fleetio API credentials (gitignored) |

## Credentials
- `.env` contains `FLEETIO_API_KEY` and `FLEETIO_ACCOUNT_TOKEN` — never commit
- Fleetio API base: `https://secure.fleetio.com/api/v1`
- Auth headers: `Authorization: Token {key}` + `Account-Token: {token}`

## Data Flow
1. `Corpay_Transactions.xlsx` → loaded by `anomaly_detection.py`
2. Fleetio API → vehicles + fleet groups pulled live
3. Matching: Corpay `Cardholder Last Name` suffix (zero-padded) → Fleetio vehicle name suffix
4. Card type split: `Cardholder First Name` = `VEHICLE` (vehicle analysis) | `UNIT`/`EQUIPMENT` (equipment, excluded) | `TEMPORARY` (separate section)
5. Anomaly detection → `anomaly_report.json`
6. Flask app reads report + decisions → serves UI

## Anomaly Flags
| Flag | Level | Logic |
|---|---|---|
| F1 — Fuel Efficiency | **Vehicle-level** | Period MPG = (Last Odo - First Odo) / Sum(Gallons from fill 2+). Flag if >20% below baseline. NOT per-transaction — partial fills make per-fill MPG unreliable. |
| F2 — Cost Per Gallon | Transaction | Flag if >15% above monthly median for that fuel type |
| F3 — Odometer Issue | Transaction | Missing or decreasing odometer readings |
| F4 — Small Fill | Transaction | Fill < 25% of vehicle's average fill size |
| F5 — High Frequency | Transaction | Driver with >2 fills in any 48-hour window |
| F6 — Wrong Fuel Type | Transaction | Cross-reference Corpay product vs Fleetio fuel type. Gas vehicles: regular unleaded only (no premium/midgrade). Diesel vehicles: diesel only. |

## MPG Calculation — Important Design Decision
Per-fill MPG is **not used** because drivers don't always fill to full or drive to empty. The gallons pumped at fill N ≠ gallons consumed between fills N-1 and N. Instead, we use vehicle-level period MPG over the entire reporting period, which self-corrects for partial fills (error bounded by tank size ~20-25 gal over thousands of miles).

Split fills (same odometer within 10 minutes) are combined into single fill events.
Bad odometer entries (decreasing or producing MPG >40) are filtered from the clean sequence.

## Approval Workflow
1. **Fleet Managers** (`/group/<name>`) — review flagged transactions, temp cards, declined txns. Approve/deny each flag with reason. Submit when all reviewed.
2. **Fleet Admin** (`/admin`) — sees all group submissions, drills into each. Generates consolidated report.
3. **Accounting Report** (`/admin/report`) — print-friendly page with spend breakdown, denied items, MPG flags, fuel pricing, signature block. Browser print → PDF.

## UI / Branding
- Aeroseal brand system: Steel Blue `#005A90`, Sapphire `#008CD1`, Sky Blue `#51BDE7`, Citrus Green `#C9DC50`, Midnight Blue `#113C5B`
- Font: Figtree (Google Fonts)
- Icons: Lucide-style thin stroke (fuel pump in header)
- Reference the `aeroseal-brand` skill for any visual changes

## Development Status
- **Phase 1:** Data loading & validation — COMPLETE
- **Phase 2:** Anomaly detection — COMPLETE
- **Phase 3:** Fleet group UI — COMPLETE
- **Phase 4:** Admin review & report generation — COMPLETE
- **Phase 5:** Final review & cleanup — NOT STARTED

## Running Locally
```bash
cd ~/dev/projects/aeroseal/Fuel-Review
python3 tools/anomaly_detection.py  # regenerate anomaly_report.json
python3 app.py                       # start Flask on port 5001
```

## Sub-Account → Fleet Group Mapping
Used for temporary and declined cards that don't have a Fleetio vehicle match:
| Sub Account | Fleet Manager |
|---|---|
| AEROSEAL CARTX | Robert Tamayo |
| AEROSEAL CO | Campbell Johnson |
| AEROSEAL OH | Jason Riley |
| AEROSEAL SUMSC | Caleb Severance |
| AEROSEAL MD | Pat Richardson |
| AEROSEAL GA | Corey Dean |
| AEROSEAL LAS | Robert Kvenvik |
| AEROSEAL AZ | Max Zimmerman |
| AEROSEAL NM | Max Zimmerman |
| AEROSEAL MAGTX | Robert Tamayo |
| AEROSEAL ODETX | Robert Tamayo |
| AEROSEAL SATX | Robert Tamayo |
