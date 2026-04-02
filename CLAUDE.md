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
Tables: `users`, `reviews`, `transactions`, `flags`, `vehicle_mpg`, `decisions`, `group_submissions`, `admin_approvals`. All tables auto-created on startup via `db.init_db()`. Cascading deletes via foreign keys ensure clean review deletion.

## Email Notifications
- **Provider:** Resend (resend.com) — HTTP API, no SDK dependency
- **Sending domain:** `aeroseal.com` (verified in Resend)
- **From address:** `notifications@aeroseal.com`
- **Note:** Sending via Resend from aeroseal.com — ensure DNS records (SPF, DKIM) are configured in Resend so emails pass authentication
- **Notification types:** initial review ready, reminders for pending managers, admin notification when all managers have submitted

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
| Flag | Level | Logic |
|---|---|---|
| F1 — Fuel Efficiency | **Vehicle-level** | Period MPG = (Last Odo - First Odo) / Sum(Gallons from fill 2+). Flag if >20% below baseline. NOT per-transaction. |
| F2 — Cost Per Gallon | Transaction | Flag if >15% above monthly median for that fuel type |
| F3 — Odometer Issue | Transaction | Missing or decreasing odometer readings |
| F4 — Small Fill | Transaction | Fill < 25% of vehicle's average. Skips Corpay 1.0-gal defaults (no odometer entered). |
| F5 — High Frequency | Transaction | Driver with >3 fills in any 24-hour window |
| F6 — Wrong Fuel Type | Transaction | Fuel mismatch vs Fleetio record. Gas vehicles: regular unleaded only. |

## Manager Actions — Acknowledge / Comment
Fleet managers review flagged transactions and take one of two actions:
- **Acknowledge** — manager has reviewed and accepts the flag (no further action needed)
- **Comment** — manager provides a written explanation or context for the flagged transaction

These replaced the earlier "Approve/Deny" terminology. The underlying data model still uses `action: "approve"` / `action: "deny"` values for backward compatibility with existing decisions.

## MPG Calculation — Important Design Decision
Per-fill MPG is **not used** because drivers don't always fill to full or drive to empty. The gallons pumped at fill N ≠ gallons consumed between fills. Instead, we use vehicle-level period MPG over the entire reporting period, which self-corrects for partial fills.

**Corpay 1.0-gallon defaults:** When no odometer is entered at the pump, Corpay defaults gallons to 1.0 with variable pricing. These are excluded from MPG calculations and Flag 4 analysis.

## Data Flow
1. Admin uploads `Corpay_Transactions.xlsx` via `/admin/reviews`
2. `anomaly_detection.py` runs with custom paths → generates `anomaly_report.json`
3. Fleetio API provides vehicles + fleet groups (pulled live during processing)
4. Matching: Corpay `Cardholder Last Name` suffix (zero-padded) → Fleetio vehicle name suffix
5. Card type split: `VEHICLE` (analysis) | `UNIT`/`EQUIPMENT` (excluded) | `TEMPORARY` (separate section)
6. Flask app reads report + decisions from the active review's directory (or database)

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
All core phases complete. System is deployed and operational.
- Phases 1-5: COMPLETE
- Authentication + user management: COMPLETE
- PostgreSQL database layer with JSON fallback: COMPLETE
- Render deployment with persistent disk + database: COMPLETE
- Monthly review workflow (upload/process/notify/archive): COMPLETE
- Email notifications via Resend: COMPLETE
- Acknowledge/Comment terminology (replaced Approve/Deny): COMPLETE
- Auto-notify admins when all managers submit: COMPLETE
- Delete review functionality: COMPLETE
- Sending domain: aeroseal.com via Resend (previously aeroseal.app)
