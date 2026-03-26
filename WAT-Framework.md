# Agent Instructions

You're working inside the **WAT framework** (Workflows, Agents, Tools). This architecture separates concerns so that probabilistic AI handles reasoning while deterministic code handles execution. That separation is what makes this system reliable.

## The WAT Architecture

**Layer 1: Workflows (The Instructions)**
- Markdown SOPs stored in `workflows/`
- Each workflow defines the objective, required inputs, which tools to use, expected outputs, and how to handle edge cases
- Written in plain language, the same way you'd brief someone on your team

**Layer 2: Agents (The Decision-Maker)**
- This is your role. You're responsible for intelligent coordination.
- Read the relevant workflow, run tools in the correct sequence, handle failures gracefully, and ask clarifying questions when needed
- You connect intent to execution without trying to do everything yourself

**Layer 3: Tools (The Execution)**
- Python scripts in `tools/` that do the actual work
- API calls, data transformations, file operations, database queries
- Credentials and API keys are stored in `.env`
- These scripts are consistent, testable, and fast

**Why this matters:** When AI tries to handle every step directly, accuracy drops fast. If each step is 90% accurate, you're down to 59% success after just five steps. By offloading execution to deterministic scripts, you stay focused on orchestration and decision-making where you excel.

## How to Operate

**1. Look for existing tools first**
Before building anything new, check `tools/` based on what your workflow requires. Only create new scripts when nothing exists for that task.

**2. Learn and adapt when things fail**
When you hit an error:
- Read the full error message and trace
- Fix the script and retest (if it uses paid API calls or credits, check with me before running again)
- Document what you learned in the workflow (rate limits, timing quirks, unexpected behavior)

**3. Keep workflows current**
Workflows should evolve as you learn. When you find better methods, discover constraints, or encounter recurring issues, update the workflow. That said, don't create or overwrite workflows without asking unless I explicitly tell you to.

## The Self-Improvement Loop

Every failure is a chance to make the system stronger:
1. Identify what broke
2. Fix the tool
3. Verify the fix works
4. Update the workflow with the new approach
5. Move on with a more robust system

## File Structure

**What goes where:**
- **Deliverables**: Final outputs go to cloud services (Google Sheets, Slides, etc.) where I can access them directly
- **Intermediates**: Temporary processing files that can be regenerated

**Directory layout:**
```
.tmp/           # Temporary files (scraped data, intermediate exports). Regenerated as needed.
tools/          # Python scripts for deterministic execution
workflows/      # Markdown SOPs defining what to do and how
data/           # Persistent project data (trackers, rosters, processed records)
.env            # API keys and environment variables (NEVER store secrets anywhere else)
credentials.json, token.json  # Google OAuth (gitignored)
```

**Core principle:** Local files are just for processing. Anything I need to see or use lives in cloud services. Everything in `.tmp/` is disposable.

## Bottom Line

You sit between what I want (workflows) and what actually gets done (tools). Your job is to read instructions, make smart decisions, call the right tools, recover from errors, and keep improving the system as you go.

Stay pragmatic. Stay reliable. Keep learning.

## Project: Fuel Review

This project manages fuel usage review and analysis for Aeroseal's fleet. Ingests monthly Corpay fuel card exports, cross-references against Fleetio vehicle and fleet group data, flags anomalies for review, and routes approvals: Fleet Manager → Fleet Administrator → Accounting.

### Tools (in `tools/`)
- `anomaly_detection.py` — Core engine. Loads Corpay data, pulls Fleetio vehicles via API, matches transactions, runs 6 anomaly flags (vehicle-level MPG, cost/gallon outliers, odometer validation, small fills, high-frequency fills, wrong fuel type), outputs `anomaly_report.json`.

### Data (in `data/`)
- `Corpay_Transactions.xlsx` — Monthly Corpay fuel card export (source data — never overwrite)
- `anomaly_report.json` — Generated output from anomaly detection engine, consumed by the web app
- `mpg_baselines.json` — EPA + manufacturer MPG baselines for 101 fleet vehicles

### Web App
- `app.py` — Flask server (port 5001). Dashboard, fleet manager review views, admin drill-down, consolidated accounting report (print-to-PDF). Aeroseal-branded UI.
- `templates/` — Jinja2 HTML templates (base, index, group, admin, admin_group, report)

### Workflows (in `workflows/`)
- `_template.md` — Standard workflow template for creating new SOPs

### Credentials
- `.env` — Fleetio API key and account token (gitignored, never commit)
- Fleetio API: `https://secure.fleetio.com/api/v1` with `Authorization: Token {key}` + `Account-Token: {token}`
