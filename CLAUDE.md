# Aeroseal Fleet Fuel Review

## Project Overview
Fuel analytics and fleet manager review workflow for Aeroseal's fleet. Ingests monthly Corpay fuel card exports, cross-references against Fleetio vehicle/fleet group data, flags anomalies, and routes reviews through a multi-tier workflow: Fleet Manager → Fleet Administrator → Accounting.

**Repo:** https://github.com/claytoncolleran/aeroseal-fleet-fuel-review
**Live site:** https://aeroseal-fleet-fuel-review.onrender.com
**Working directory:** `~/dev/projects/Aeroseal/Fuel-Review/`
**Hosting:** Render.com (Starter plan, persistent disk at `/var/data`)

## Architecture (WAT Framework)
- **Tools:** `tools/` — Python scripts for deterministic execution
- **Data:** `data/` — MPG baselines, reference Corpay file (monthly data lives on persistent disk)
- **Database:** `db.py` — PostgreSQL via psycopg2 (connection pool, schema init, CRUD for users/reviews/transactions/decisions)
- **Web App:** `app.py` — Flask server with auth, review management, email notifications
- **Templates:** `templates/` — Jinja2 HTML with Aeroseal branding
- **Workflows:** `workflows/` — Markdown SOPs

## Key Files
| File | Purpose |
|---|---|
| `app.py` | Flask app — auth, dashboard, manager views, admin panel, review management, report generation, email notifications |
| `db.py` | PostgreSQL database layer — connection pooling, schema creation, CRUD for users, reviews, transactions, flags, decisions, submissions, approvals. Falls back to JSON files if `DATABASE_URL` is not set. |
| `tools/anomaly_detection.py` | Core engine — loads Corpay data, matches to Fleetio, runs 6 anomaly flags. Accepts custom input/output paths for monthly reviews. |
| `data/mpg_baselines.json` | MPG baselines for 101 fleet vehicles (EPA + manufacturer estimates) |
| `data/Corpay_Transactions.xlsx` | Reference copy of initial Corpay export (monthly uploads go to persistent disk) |
| `Procfile` | Render start command: `gunicorn app:app` |
| `render.yaml` | Render service config — Python runtime, env vars, persistent disk, database |

## Authentication
- **Email-based login** — users log in with email + password
- **Two roles:** `admin` (full access + user management) and `manager` (sees only assigned fleet group)
- **Invite flow:** admins invite users via email (no temp passwords). User receives a link to `/setup-account/<token>` where they set their own password. Tokens expire after 48 hours.
- **Password reset:** admins click "Reset Password" which sends a new setup link via email, invalidating the old password
- **Password hashing:** pbkdf2:sha256 via werkzeug
- **Session-based** — Flask session cookies
- **User data** stored in PostgreSQL `users` table (or `users.json` on persistent disk as fallback)
- **Default admin** auto-created on first run from `ADMIN_DEFAULT_EMAIL` + `ADMIN_DEFAULT_PASSWORD` env vars
- **User management:** admins invite/edit/delete users at `/admin/users`

## Monthly Review Workflow
1. **Admin** goes to `/admin/reviews` → uploads Corpay spreadsheet, sets period, label, and deadline
2. **Processing** runs anomaly detection in-app, creates `reviews/<period>/` directory with report + decisions
3. **Admin** clicks "Send Notifications" → emails all fleet managers via Resend with a link to their review
4. **Fleet managers** log in, review flagged transactions, acknowledge or comment on each flag, submit
5. **Admin** monitors progress, sends reminders to managers who haven't submitted
6. **All managers submit** → admin is automatically notified via email
7. **Admin** generates consolidated accounting report at `/admin/report`, acknowledges + signs
8. **Review marked complete** — archived and accessible as read-only history

### Review Data Structure (persistent disk)
```
/var/data/                      (Render) or data/ (local dev)
  users.json                    Email-keyed user accounts (JSON fallback only)
  reviews/
    2026-03/
      corpay_upload.xlsx         Original upload
      anomaly_report.json        Generated report
      review_decisions.json      Manager decisions (JSON fallback only)
      meta.json                  Period, label, deadline, status, timestamps
```

### Database Schema (PostgreSQL on Render)
Tables: `users`, `reviews`, `transactions`, `flags`, `vehicle_mpg`, `decisions`, `group_submissions`, `admin_approvals`, `flag_settings`. All tables auto-created on startup via `db.init_db()`. Cascading deletes via foreign keys ensure clean review deletion.

## Email Notifications
- **Provider:** Resend (resend.com) — HTTP API, no SDK dependency
- **Sending domain:** `aeroseal.com` (verified in Resend)
- **From address:** `notifications@aeroseal.com`
- **Note:** Sending via Resend from aeroseal.com — ensure DNS records (SPF, DKIM) are configured in Resend so emails pass authentication
- **Notification types:** user invite/password reset, initial review ready, reminders for pending managers, admin notification when all managers have submitted

## Credentials & Environment Variables
**Local (.env, gitignored):**
- `FLEETIO_API_KEY`, `FLEETIO_ACCOUNT_TOKEN`

**Render environment variables:**
| Key | Purpose |
|---|---|
| `FLEETIO_API_KEY` | Fleetio API auth |
| `FLEETIO_ACCOUNT_TOKEN` | Fleetio account identifier |
| `SECRET_KEY` | Flask session encryption |
| `ADMIN_DEFAULT_EMAIL` | Default admin account email (e.g., `clayton.colleran@aeroseal.com`) |
| `ADMIN_DEFAULT_PASSWORD` | Default admin password (change after first login) |
| `RESEND_API_KEY` | Resend email API key (starts with `re_`) |
| `FROM_EMAIL` | Sending address (e.g., `notifications@aeroseal.com`) |
| `DATABASE_URL` | PostgreSQL connection string (auto-set by Render from linked database) |

## Anomaly Flags
All flag thresholds are configurable via `/admin/settings`. Global defaults + per-group overrides stored in `flag_settings` table. Settings must be configured before uploading a spreadsheet (applied at processing time, not retroactively).

Flags F1-F6 apply to vehicle card transactions. Flags E1 and E2 (stored as flag_number 7 and 8 in the database) apply to equipment / unit card transactions.

| Flag | Card Type | Level | Default Logic | Configurable |
|---|---|---|---|---|
| F1 — Fuel Efficiency | Vehicle | **Vehicle-level** | Period MPG >20% below baseline | Threshold %, enable/disable |
| F2 — Cost Per Gallon | Vehicle | Transaction | >15% above monthly median for fuel type | Threshold %, enable/disable |
| F3 — Odometer Issue | Vehicle | Transaction | Missing or decreasing odometer readings | Enable/disable |
| F4 — Small Fill | Vehicle | Transaction | Fill < 25% of vehicle's average. Skips Corpay 1.0-gal defaults. | Threshold %, enable/disable |
| F5 — High Frequency | Vehicle | Transaction | Driver with >3 fills in any 24-hour window | Fill count, time window, enable/disable |
| F6 — Wrong Fuel Type | Vehicle | Transaction | Fuel mismatch vs Fleetio record. Gas vehicles: regular unleaded only. | Allowed fuel types per group, enable/disable |
| E1 — Large Equipment Fill | Equipment | Transaction | Net price > $50. Catches vehicle-sized fuel purchases on equipment cards (legit equipment fills typically 2-6 gallons / under $25). | $ threshold, enable/disable |
| E2 — Corpay Default (No Odometer) | Equipment | Transaction | Gallons == 1.0 AND PPG == 0 AND no odometer. Corpay's default when no odometer entered at pump; surfaces the pattern for review instead of silently dropping. | Enable/disable |

## Manager Actions — Acknowledge / Comment
Fleet managers review flagged transactions and take one of two actions:
- **Acknowledge** — manager has reviewed and accepts the flag (no further action needed)
- **Comment** — manager provides a written explanation or context for the flagged transaction

These replaced the earlier "Approve/Deny" terminology. The underlying data model still uses `action: "approve"` / `action: "deny"` values for backward compatibility with existing decisions.

## MPG Calculation — Important Design Decision
Per-fill MPG is **not used** because drivers don't always fill to full or drive to empty. The gallons pumped at fill N ≠ gallons consumed between fills. Instead, we use vehicle-level period MPG over the entire reporting period, which self-corrects for partial fills.

**Corpay 1.0-gallon defaults:** When no odometer is entered at the pump, Corpay defaults gallons to 1.0 with variable pricing. These are excluded from MPG calculations and Flag 4 analysis.

## Data Flow
1. Admin configures flag settings at `/admin/settings` (global defaults + per-group overrides)
2. Admin uploads `Corpay_Transactions.xlsx` via `/admin/reviews`
3. `anomaly_detection.py` runs with flag settings + custom paths → generates `anomaly_report.json`
4. Fleetio API provides vehicles + fleet groups (pulled live during processing)
5. Corpay header row auto-detected (handles raw exports with metadata rows before column headers)
6. Matching: Corpay `Cardholder Last Name` suffix (zero-padded) → Fleetio vehicle name suffix
7. Card type split: `VEHICLE` (flag-reviewed F1-F6) | `UNIT`/`EQUIPMENT` (flag-reviewed E1-E2) | `TEMPORARY` (every txn acknowledged)
8. Report data stored in PostgreSQL (`transactions`, `flags`, `vehicle_mpg` tables) and reconstructed via `db_build_report()`
9. Flask app reads report + decisions from the database (falls back to JSON files if no DB)

## Running Locally
```bash
cd ~/dev/projects/Aeroseal/Fuel-Review
python3 app.py                       # start Flask on port 5001 (login: admin@aeroseal.com / changeme)
# Or run anomaly detection standalone:
python3 tools/anomaly_detection.py
```

## Deployment
- Render auto-deploys on `git push` to `main`
- Persistent disk at `/var/data` survives deploys
- PostgreSQL database linked via `DATABASE_URL` env var
- App auto-detects environment: `DATABASE_URL` set → PostgreSQL; `/var/data` exists → Render disk; otherwise → local `data/`

## Sub-Account → Fleet Group Mapping
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

## Development Status
All core phases complete. System is deployed and operational. First live review (2026-03) completed manager approval phase 2026-04-10 (9/9 submitted).

- Phases 1-5: COMPLETE
- Authentication + user management: COMPLETE
- Email invite flow (no temp passwords): COMPLETE
- PostgreSQL database layer with JSON fallback: COMPLETE
- Render deployment with persistent disk + database: COMPLETE
- Monthly review workflow (upload/process/notify/archive): COMPLETE
- Email notifications via Resend (aeroseal.com domain): COMPLETE
- Acknowledge/Comment terminology (replaced Approve/Deny): COMPLETE
- Auto-notify admins when all managers submit: COMPLETE
- Delete review functionality: COMPLETE
- Configurable flag settings (global defaults + per-group overrides): COMPLETE
- Corpay header row auto-detection: COMPLETE
- Report data loaded from PostgreSQL (db_build_report): COMPLETE
- Aeroseal brand mark favicon + header logo: COMPLETE
- Zero-flag manager submission fix (managers with no flags can sign and submit): COMPLETE
- Equipment / unit card capture (preserved through anomaly_detection, DB, report): COMPLETE
- Equipment card flag review (E1 Large Fill >$50, E2 Corpay Default, with acknowledge/comment UI for managers): COMPLETE
- Consolidated report restructured as Total Fleet Spend with Spend Breakdown by Category table (vehicle + temporary + equipment, review status per category): COMPLETE
- Idempotent admin backfill route for equipment data on prior review periods (`/admin/backfill-equipment/<period>`): COMPLETE

## Roadmap / Open Items

### Fleet Administrator Review Notes (briefing doc) - productionize
**Status:** Prototype exists as a static one-off at `/admin/review-notes/2026-03` (added 2026-04-10 commit `423d3b5`). It is a hard-coded HTML file listing flagged transactions that warrant follow-up with fleet managers before the Fleet Administrator signs off on the consolidated accounting report.

**What it should become:**
1. **Dynamic route** - replace the static `2026-03` file with a data-driven route like `/admin/review-notes/<period>` that queries the `transactions`, `flags`, `decisions`, and `group_submissions` tables and renders the briefing from live data for any review period.
2. **Auto-generate at workflow milestone** - when the existing "all managers submitted" hook fires (currently sends an email to admins), extend that hook to also:
   - Generate the review notes document for the period
   - Include a link to it in the admin notification email
   - Attach or embed a summary in the email itself
3. **On-demand generation** - add a "Generate Review Notes" button to `/admin/report` (the consolidated accounting page) so the Fleet Administrator can pull it up at any time during the review cycle, not only after all submissions.
4. **Content logic to port from the 2026-03 prototype:**
   - Risk tiering (High / Medium / Low) per manager based on flag count and severity
   - Pattern detection: same-minute double-fills, repeated F6 on same vehicle/driver, $1-gallon defaults with high dollar amounts, implausible odometer readings, cost-per-gallon outliers vs fleet median
   - Process observations section (zero-comments pattern, delegated submissions, signature typos)
   - Explicit action items naming which managers to follow up with and what questions to raise
5. **Access fix** - the static 2026-03 page had a rendering issue where viewing it with a manager-role session appeared to scope to that manager's group. Dynamic version should enforce admin-only and never filter by session group.

**Context for the feature:** The March 2026 review produced 109 flagged transactions with zero written comments, which masked several high-concern patterns (a potential methanol misfuel on a Ram truck, repeated diesel fills on vans, premium-grade upgrades across drivers). A structured briefing doc helps the Fleet Administrator catch these before signing off and gives managers a prompt to add missing context. This is the bridge between manager approval and accounting sign-off.
