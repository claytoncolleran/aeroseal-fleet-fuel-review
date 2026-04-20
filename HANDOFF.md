# Handoff: Aeroseal Fleet Fuel Review

Produced by running the [passing-the-torch](https://github.com/claytoncolleran/passing-the-torch) skill on this project. This is the transfer-specific layer that complements [CLAUDE.md](CLAUDE.md). CLAUDE.md explains how the system works; this doc explains how to transfer ownership, operate it as someone who did not build it, and where the context lives that is not captured in code.

## Handoff Summary

| | |
|---|---|
| **Outgoing owner** | Clayton Colleran, Director of Operations, Aeroseal RNC East |
| **Incoming primary owner** | Matthew Douglas, Fleet Administrator |
| **Incoming operational stakeholder** | Caleb Severance, Operations Leader, RNC East (inherits review-workflow participation as a fleet manager of the AEROSEAL SUMSC group) |
| **Handoff scope** | Full transfer of code repo, Render service, PostgreSQL database, custom domain, Resend sending configuration, all environment variables, admin account, and operational responsibility for the monthly review cycle |
| **Status at handoff** | All core phases complete. First live review (2026-03) completed manager approval phase 2026-04-10 (9 of 9 submitted). Equipment / unit card capture and flag review (E1, E2) shipped 2026-04-20 and are ready for the April 2026 cycle. System is deployed and operational. One open roadmap item (Fleet Administrator review-notes productionization) remains unshipped. |
| **Ideal handoff target** | Aeroseal-controlled GitHub organization and Aeroseal-controlled Render account. Falling back to Matthew's personal accounts is acceptable; falling back to collaborator-only status on Clayton's personal accounts is the fragile minimum. |

## 1. What this project is and why it exists

An anomaly-detection and review-routing workflow for Aeroseal's fleet fuel spend. Replaces a manual, spreadsheet-based monthly review with a structured tool that:

- Ingests the monthly Corpay fuel card export
- Runs configurable anomaly flags against vehicle cards (F1-F6) and equipment / unit cards (E1-E2)
- Routes flagged transactions to the correct fleet manager for acknowledgement or comment
- Consolidates manager decisions into a sign-off report for the Fleet Administrator
- Produces the final accounting handoff

It exists because fuel spend at fleet scale is easy to let slide. Sporadic card misuse, mis-entered odometer readings, wrong-fuel-type fills, and double-fills at the pump all look unremarkable in a raw Corpay export. The tool makes them visible, makes review mandatory per manager, and produces a clean paper trail for accounting.

The broader context is at [Passing the Torch, Fleet Management project page](https://passing-the-torch.onrender.com/projects/fleet-management).

## 2. Operational cadence

The review cycle runs once per month. The mechanics are in [CLAUDE.md, Monthly Review Workflow section](CLAUDE.md#monthly-review-workflow). The ownership map:

| Step | Owner | Frequency |
|---|---|---|
| Corpay export download | Fleet Administrator (Matthew Douglas) | Monthly, after Corpay closes the prior month |
| Upload + process in `/admin/reviews` | Fleet Administrator | Monthly, same day as export |
| Send notifications to fleet managers | Fleet Administrator | Same session as upload |
| Manager review + acknowledge/comment | Each fleet manager (12 groups today) | Within the deadline set on the review |
| Reminders to pending managers | Fleet Administrator | As needed until all submit |
| Consolidated report + admin sign-off | Fleet Administrator | After all managers submit |
| Accounting handoff | Fleet Administrator → accounting team | Same session as sign-off |

Expected duration: one to two weeks from upload to accounting handoff, depending on how fast fleet managers respond. March 2026 cycle produced 109 flagged transactions across all groups.

**Handoff note:** Clayton currently holds the Fleet Administrator admin account. That account transfers to Matthew. Caleb becomes a `manager` role user for the AEROSEAL SUMSC group (fleet manager for his crews).

## 3. Key operational workflows

Not covered in CLAUDE.md. These are the things a new owner actually needs to do, not how the code works.

### How to start a new monthly review
1. Download the Corpay export for the period (spreadsheet from Corpay fleet portal)
2. Go to `https://fuel-review.aeroseal.app/admin/reviews`
3. Upload the file, set `period` (e.g., `2026-04`), `label` (e.g., `April 2026`), and a `deadline` date
4. Wait for processing to complete (runs anomaly detection in-app)
5. Click "Send Notifications" to email all fleet managers via Resend
6. Manage from the dashboard from there

### How to add or remove a fleet manager
1. Go to `/admin/users`
2. Click "Invite User", enter email, select role (`manager`), select fleet group
3. User receives an email with a `/setup-account/<token>` link valid for 48 hours
4. To remove: delete the user from the same page; decisions they made on prior reviews are preserved

### How to reset a manager's password
`/admin/users` → find user → "Reset Password" → email sent with fresh setup link, old password invalidated

### How to generate the Fleet Administrator review-notes briefing (current state)
Today it is a static HTML file at `/admin/review-notes/2026-03` (hard-coded for the March 2026 cycle). Not dynamic yet. See the roadmap section below for the plan to productionize it.

### How to debug a failed email notification
1. Check Render logs for `send_*` function errors
2. Verify `RESEND_API_KEY` is set in Render env vars (starts with `re_`)
3. Verify `FROM_EMAIL` is `notifications@aeroseal.com` and that SPF/DKIM records for `aeroseal.com` are still valid in Resend
4. Check Resend dashboard for bounce/block events on the recipient address

### How to roll back a bad deploy
Render dashboard → Deploys tab → click a prior successful deploy → "Rollback to this deploy". The persistent disk at `/var/data` survives, so no data is lost on rollback.

### How to run the anomaly detection standalone
```bash
cd ~/dev/projects/Aeroseal/Fuel-Review
python3 tools/anomaly_detection.py
```
Useful for testing flag logic changes against a past Corpay file without going through the web workflow.

## 4. Communication map

Who to talk to when something specific happens.

| Situation | Primary contact | Secondary / escalation |
|---|---|---|
| Fleet manager can't log in | Matthew Douglas (password reset via admin panel) | Clayton (if admin panel itself is broken) |
| Corpay export format changed and anomaly detection broke | Matthew → Caleb → Clayton | Corpay support for the export itself |
| Fleetio API errors in processing | Matthew → Clayton | Fleetio support via support@fleetio.com |
| Email delivery failures (Resend) | Matthew → Clayton | Resend support via Resend dashboard |
| Render deployment failing | Matthew → Clayton | Render support |
| Custom domain `fuel-review.aeroseal.app` DNS issue | Matthew → IT / whoever owns `aeroseal.app` DNS | n/a |
| Discrepancy between manager decisions and accounting report | Fleet Administrator reviews audit trail; decisions are preserved in the `decisions` table | n/a |
| New fleet group added (sub-account → manager mapping) | Update the table in CLAUDE.md section "Sub-Account → Fleet Group Mapping" + invite the manager user | n/a |

Fleet managers and their groups (as of 2026-03 review) are in [CLAUDE.md, Sub-Account → Fleet Group Mapping](CLAUDE.md#sub-account--fleet-group-mapping).

## 5. Design decisions and principles that shaped the build

Captures the *why* behind choices that are not obvious from reading the code.

### Per-fill MPG is deliberately not used
Drivers do not always fill to full or drive to empty. Gallons pumped at fill N ≠ gallons consumed between fills. Per-fill MPG produces noisy outliers that are not actually anomalies. Vehicle-level period MPG over the reporting period self-corrects for partial fills. Anyone who comes along and says "we should flag low per-fill MPG" has not hit this wall yet; preserve the current design.

### Acknowledge / Comment replaced Approve / Deny
The earlier terminology was misleading. Managers were not "denying" transactions; they were providing context. The data model still uses `action: "approve"` / `action: "deny"` values for backward compatibility, but the UI and docs use Acknowledge / Comment. Do not rename the data model values without a migration.

### JSON fallback for user and decision data
`db.py` falls back to JSON files on persistent disk if `DATABASE_URL` is not set. This exists for local-dev ergonomics and for disaster recovery. It is not a supported production mode. Production runs on PostgreSQL.

### Corpay 1.0-gallon defaults are excluded from vehicle analysis, surfaced on equipment
When no odometer is entered at the pump, Corpay defaults gallons to 1.0 with variable pricing. On vehicle cards these are excluded from MPG calculations and Flag 4 (small fill) analysis because they pollute baselines. On equipment / unit cards the same pattern is surfaced as Flag E2 instead of dropped, because equipment spend isn't averaged into baselines and the pattern is worth flagging for manager review. If fuel patterns change and this logic breaks, revisit `tools/anomaly_detection.py`.

### Equipment cards are flag-reviewed, not force-reviewed (unlike temporary cards)
Temporary cards force the manager to acknowledge every single transaction, because the goal is to drive temporary card usage to zero by giving each driver a permanent card. Equipment cards have a different goal: they are a legitimate long-term tool, but the concern is that they get misused to fuel vehicles. Flag-based review (E1 on fills > $50, E2 on Corpay 1.0-gal defaults) catches the misuse patterns without burying managers in acknowledgements on every small generator fill. Do not change equipment cards to force-review every transaction without revisiting this rationale.

### Flag thresholds are configurable per group
Different fleet groups have different vehicle mixes, fuel types, and use patterns. Global defaults exist, but per-group overrides are supported via `/admin/settings`. Settings apply at processing time, not retroactively. Decide on settings *before* running the review; changing them after upload does not reprocess.

### Resend over SES or SendGrid
Chose Resend for simplicity: HTTP API, no SDK dependency, good deliverability for transactional email. Sending domain is `aeroseal.com` with SPF/DKIM verified in Resend.

## 6. Transition logistics, the actual handoff

### Ownership matrix (target state)

| Resource | Current owner | Target owner | Transfer method | Status |
|---|---|---|---|---|
| GitHub repo `aeroseal-fleet-fuel-review` | Clayton (personal) | Aeroseal GitHub org (preferred) or Matthew (acceptable) | GitHub transfer ownership | Not started |
| Render service `aeroseal-fleet-fuel-review` | Clayton's Render account | Aeroseal Render team account (preferred) or Matthew's | Render team transfer or new service in target account with env var migration | Not started |
| PostgreSQL database on Render | Linked to above | Same as Render service | Travels with the service | Not started |
| Custom domain `fuel-review.aeroseal.app` | `aeroseal.app` DNS | Stays with `aeroseal.app`; only the CNAME target changes if the Render service moves | DNS update at cutover | Not started |
| Resend sending config | `aeroseal.com` verified by Clayton | Re-verify under Aeroseal-controlled Resend account, or transfer API key | Re-verify domain + swap `RESEND_API_KEY` env var | Not started |
| Fleetio API access | Clayton's API key | Matthew's or Aeroseal service account | Generate new key in Fleetio, swap env vars, revoke old | Not started |
| Corpay fleet portal account | Clayton | Matthew | Already in Matthew's hands as Fleet Administrator | Complete |
| Admin account in this app | `clayton.colleran@aeroseal.com` | `matthew.douglas@aeroseal.com` | Create Matthew as admin; change default admin env vars; eventually delete Clayton's user | Not started |

### Environment variables to transfer

When the Render service moves (or when env vars rotate), copy these from the existing service to the target:

- `FLEETIO_API_KEY` (rotate at transfer)
- `FLEETIO_ACCOUNT_TOKEN`
- `SECRET_KEY` (Flask session key; regenerate for safety)
- `ADMIN_DEFAULT_EMAIL` (change to Matthew's email)
- `ADMIN_DEFAULT_PASSWORD` (change to a fresh value, communicate out-of-band)
- `RESEND_API_KEY` (rotate at transfer)
- `FROM_EMAIL`
- `DATABASE_URL` (auto-set by Render when linking the DB)

### Data transfer

If the Render service moves accounts rather than staying in place, the PostgreSQL database and `/var/data` persistent disk must come with it. Render supports database backups (pg_dump) and restore into a new database. The persistent disk contents (monthly review uploads, JSON fallback files) should be rsynced via a one-time transfer.

If the Render service stays in place and only the Render account ownership changes, data travels automatically.

### First 30 days for Matthew (or whoever becomes the owner)

Concrete checklist, in order:

1. **Get an admin account.** Clayton creates Matthew as admin via `/admin/users`. Matthew sets his own password via the email link.
2. **Run a dry review.** Download the current month's Corpay export, upload as a test with a clearly-labeled period (e.g., `2026-04-test`), process it, and review the output. Delete the test review afterward.
3. **Make one small code change.** Edit the footer copyright year or similar trivial change, push, watch it auto-deploy to Render. Builds confidence with the deploy pipeline.
4. **Trigger a password reset on a test manager account.** Verify the email lands, the link works, the reset invalidates the prior password.
5. **Read CLAUDE.md end-to-end.** Especially the Anomaly Flags section, because that logic is the heart of the review.
6. **Run the first real monthly review alone.** Clayton available on call. Do not let Clayton drive; just answer questions.
7. **Extend the anomaly detection.** Small feature, e.g., add a new flag or tune an existing threshold. Forces you to touch `tools/anomaly_detection.py`, understand it, and push a change.
8. **Rotate one credential.** Pick one of `FLEETIO_API_KEY`, `RESEND_API_KEY`, or `SECRET_KEY`. Update in Render env vars. Verify the app still works. Confirms operational control.

## 7. Open items, the roadmap

### Fleet Administrator review-notes briefing doc, productionize
**Priority:** medium
**Owner at handoff:** unclaimed; natural fit for Matthew + Caleb + a Claude-assisted session

Detailed spec in [CLAUDE.md, Roadmap / Open Items](CLAUDE.md#roadmap--open-items). Summary:

The static prototype at `/admin/review-notes/2026-03` (added 2026-04-10, commit `423d3b5`) is a hard-coded HTML file listing flagged transactions that warrant follow-up with fleet managers before the Fleet Administrator signs off.

What it should become:
1. Dynamic route `/admin/review-notes/<period>` querying live DB
2. Auto-generated when the "all managers submitted" hook fires
3. On-demand generation button on `/admin/report`
4. Port the risk-tiering, pattern detection, and process-observation logic from the 2026-03 prototype
5. Fix the manager-role session rendering issue (should be admin-only)

**Context for the feature:** March 2026 produced 109 flagged transactions with zero written comments, which masked several high-concern patterns (potential methanol misfuel on a Ram truck, repeated diesel fills on vans, premium-grade upgrades across drivers). The briefing doc helps the Fleet Administrator catch these before signing off.

### Minor open items
- **Maintenance service records smart report** (separate tool, not this repo). Spec lives in this repo's CLAUDE.md as a design reference. Implementation has not started. Would follow the same architecture (Fleetio ingest → anomaly detection → manager review → admin sign-off). Lower priority than productionizing the review-notes doc.

## 8. Context that only lives in Clayton's head

The stuff that is not in the code and not in CLAUDE.md but that matters when things go sideways.

- **The March 2026 review had a methanol misfuel concern** on a Ram truck. It was flagged by F6 (wrong fuel type) but the written comment from the manager was ambiguous. This is a good test case for the review-notes briefing doc: the pattern was real but the raw flag data alone did not escalate it. If the review-notes prototype is pulled up for `2026-03`, the write-up is already there.
- **Jason Riley and Mike Lanter have delegated submissions** on occasion. The admin panel shows which user submitted a group's decisions. If a submission comes from an unexpected user, follow up with the fleet manager to confirm the delegation was authorized.
- **Pat Richardson's AEROSEAL MD group overlaps with Caleb's SUMSC group** in terms of which crews they cover. If a vehicle shows up under the wrong sub-account in Corpay, that is a Fleetio configuration issue, not a bug in this tool. Fix it in Fleetio.
- **The `decisions` table backward-compatibility note**: do not migrate `action: "approve"` to `action: "acknowledge"` without a data migration script. Multiple places in the codebase assume the old values.
- **Render's free tier spins down.** This is on the Starter plan (paid) specifically so it does not spin down between uses. If cost becomes a concern and someone downgrades to free, first requests every morning will be slow and may time out before fleet managers can log in.
- **The Corpay export format has changed at least once during development.** Header row auto-detection exists because of this. If Corpay changes it again, `tools/anomaly_detection.py` `detect_header_row()` is where the fix goes.
- **The Render persistent disk at `/var/data` may not actually be mounted.** As of 2026-04-20, attempting to read a saved Corpay upload from `/var/data/reviews/<period>/corpay_upload.xlsx` returned path-not-found and the app fell back to the ephemeral local `data/` directory. PostgreSQL is the authoritative store for transactions and decisions, so review data itself is safe, but the original uploaded xlsx files (used by the backfill route and anomaly-detection re-run) may be lost on redeploy. Worth confirming in the Render dashboard that the `fuel-review-data` disk is provisioned and mounted. If it isn't, the `render.yaml` config exists but needs to be applied.
- **Equipment flag defaults (E1 $50 threshold, E2 enabled) are hardcoded fallbacks in `FLAG_DEFAULTS`.** They apply automatically at upload time, but do not appear in the `flag_settings` table until someone visits `/admin/settings` and clicks Save Defaults. The behavior is identical either way; just a note if you're looking at the DB directly.

## 9. People

| Name | Role | Relevance |
|---|---|---|
| Clayton Colleran | Outgoing Director of Operations | Built this tool; context-holder |
| Matthew Douglas | Fleet Administrator | Primary operational owner going forward |
| Caleb Severance | Operations Leader, RNC East | Incoming RNC East Ops Leader; fleet manager for AEROSEAL SUMSC group within this tool |
| Pat Richardson | Ops Manager, RNC East | Fleet manager for AEROSEAL MD group |
| Robert Tamayo | Fleet manager | AEROSEAL CARTX, MAGTX, ODETX, SATX |
| Campbell Johnson | Fleet manager | AEROSEAL CO |
| Jason Riley | Fleet manager | AEROSEAL OH |
| Corey Dean | Fleet manager | AEROSEAL GA |
| Robert Kvenvik | Fleet manager | AEROSEAL LAS |
| Max Zimmerman | Fleet manager | AEROSEAL AZ, NM |
| Accounting team | Recipient of final report | Monthly sign-off hand-off |

## 10. Running this skill again

This document was produced by running the `passing-the-torch` skill from https://github.com/claytoncolleran/passing-the-torch on this project. If at any point the ownership of this tool transfers again (Matthew moves on, Aeroseal reorgs fleet management, the tool gets absorbed into a bigger system), the skill can be re-run to produce an updated handoff.

The skill was designed for role-based handoffs (outgoing employee to successor). Applied to a project, the seven sessions adapt to these sections. The output lives alongside the project in `HANDOFF.md` rather than in a dedicated handoff repo.

## Approval

| Who | Role | Date | Sign |
|---|---|---|---|
| Clayton Colleran | Outgoing owner | | |
| Matthew Douglas | Incoming primary owner | | |
| Caleb Severance | Incoming operational stakeholder | | |
