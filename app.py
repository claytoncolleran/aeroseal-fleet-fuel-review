"""
Fuel Review — Fleet Manager Approval Dashboard
Aeroseal-branded web interface for reviewing fuel transactions and anomalies.
Supports monthly review cycles with upload, process, notify, and archive.
"""

from flask import (Flask, render_template, request, jsonify, redirect,
                   url_for, session, flash)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from functools import wraps
import json
import os
import sys
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")

# ─── Paths ───────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
TOOLS_DIR = os.path.join(BASE_DIR, "tools")
BASELINES_FILE = os.path.join(DATA_DIR, "mpg_baselines.json")

# Persistent storage: /var/data on Render, local data/ in development
PERSIST_DIR = "/var/data" if os.path.isdir("/var/data") else DATA_DIR
USERS_FILE = os.path.join(PERSIST_DIR, "users.json")
REVIEWS_DIR = os.path.join(PERSIST_DIR, "reviews")
os.makedirs(REVIEWS_DIR, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════════════
# REVIEW MANAGEMENT — Monthly review lifecycle
# ═══════════════════════════════════════════════════════════════════════════════

def get_review_dir(period):
    """Get directory for a specific review period (e.g., '2026-03')."""
    return os.path.join(REVIEWS_DIR, period)


def load_review_meta(period):
    """Load meta.json for a review period."""
    meta_path = os.path.join(get_review_dir(period), "meta.json")
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            return json.load(f)
    return None


def save_review_meta(period, meta):
    """Save meta.json for a review period."""
    review_dir = get_review_dir(period)
    os.makedirs(review_dir, exist_ok=True)
    with open(os.path.join(review_dir, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)


def list_reviews():
    """List all review periods, most recent first."""
    reviews = []
    if os.path.isdir(REVIEWS_DIR):
        for name in sorted(os.listdir(REVIEWS_DIR), reverse=True):
            meta = load_review_meta(name)
            if meta:
                reviews.append(meta)
    return reviews


def get_active_review():
    """Get the most recent review that is in 'in_review' status, or the latest."""
    reviews = list_reviews()
    for r in reviews:
        if r.get("status") == "in_review":
            return r
    return reviews[0] if reviews else None


def load_report(period=None):
    """Load anomaly_report.json for a period (defaults to active review)."""
    if not period:
        active = get_active_review()
        if not active:
            # Fall back to legacy data/ directory
            legacy = os.path.join(DATA_DIR, "anomaly_report.json")
            if os.path.exists(legacy):
                with open(legacy) as f:
                    return json.load(f)
            return {"transactions": [], "temporary_cards": [], "declined_transactions": [],
                    "group_summary": {}, "summary": {}, "mpg_summary_by_vehicle": {}}
        period = active["period"]

    report_path = os.path.join(get_review_dir(period), "anomaly_report.json")
    if os.path.exists(report_path):
        with open(report_path) as f:
            return json.load(f)
    return {"transactions": [], "temporary_cards": [], "declined_transactions": [],
            "group_summary": {}, "summary": {}, "mpg_summary_by_vehicle": {}}


def load_decisions(period=None):
    """Load review_decisions.json for a period."""
    if not period:
        active = get_active_review()
        if not active:
            # Fall back to legacy
            legacy = os.path.join(PERSIST_DIR, "review_decisions.json")
            if os.path.exists(legacy):
                with open(legacy) as f:
                    return json.load(f)
            return {}
        period = active["period"]

    decisions_path = os.path.join(get_review_dir(period), "review_decisions.json")
    if os.path.exists(decisions_path):
        with open(decisions_path) as f:
            return json.load(f)
    return {}


def save_decisions(decisions, period=None):
    """Save review_decisions.json for a period."""
    if not period:
        active = get_active_review()
        if active:
            period = active["period"]
        else:
            # Fall back to legacy
            with open(os.path.join(PERSIST_DIR, "review_decisions.json"), "w") as f:
                json.dump(decisions, f, indent=2)
            return

    review_dir = get_review_dir(period)
    os.makedirs(review_dir, exist_ok=True)
    with open(os.path.join(review_dir, "review_decisions.json"), "w") as f:
        json.dump(decisions, f, indent=2)


# ═══════════════════════════════════════════════════════════════════════════════
# USER MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE) as f:
            return json.load(f)
    return {}


def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)


def init_default_admin():
    """Create default admin account if no users exist. Wipes old username-keyed users."""
    users = load_users()
    if users and not any("@" in k for k in users.keys()):
        print("  Migrating users.json from username to email format (old accounts cleared)")
        users = {}
    if not users:
        default_email = os.environ.get("ADMIN_DEFAULT_EMAIL", "admin@aeroseal.com")
        default_pw = os.environ.get("ADMIN_DEFAULT_PASSWORD", "changeme")
        users[default_email] = {
            "password_hash": generate_password_hash(default_pw, method="pbkdf2:sha256"),
            "role": "admin",
            "display_name": "Administrator",
            "fleet_group": None,
            "created_at": datetime.now().isoformat(),
        }
        save_users(users)
        print(f"  Default admin account created (email: {default_email})")


# ═══════════════════════════════════════════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════════════════════════════════════════

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "email" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "email" not in session:
            return redirect(url_for("login"))
        if session.get("role") != "admin":
            flash("Admin access required.", "error")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated


def get_current_user():
    if "email" not in session:
        return None
    return {
        "email": session["email"],
        "role": session.get("role"),
        "display_name": session.get("display_name"),
        "fleet_group": session.get("fleet_group"),
    }


@app.context_processor
def inject_globals():
    active = get_active_review()
    return {
        "current_user": get_current_user(),
        "active_review": active,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# AUTH ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/login", methods=["GET", "POST"])
def login():
    if "email" in session:
        return redirect(url_for("index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        users = load_users()
        user = users.get(email)

        if user and check_password_hash(user["password_hash"], password):
            session["email"] = email
            session["role"] = user["role"]
            session["display_name"] = user.get("display_name", email)
            session["fleet_group"] = user.get("fleet_group")
            session.permanent = True
            return redirect(url_for("index"))
        else:
            flash("Invalid email or password.", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ═══════════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/")
@login_required
def index():
    """Landing page — shows active review groups based on user role."""
    active = get_active_review()
    report = load_report()
    user = get_current_user()

    if user["role"] == "admin":
        groups = sorted(report.get("group_summary", {}).keys())
    else:
        assigned = user.get("fleet_group")
        groups = [assigned] if assigned and assigned in report.get("group_summary", {}) else []

    return render_template("index.html", groups=groups,
                           summary=report.get("summary", {}),
                           group_summary=report.get("group_summary", {}),
                           active_review=active)


# ═══════════════════════════════════════════════════════════════════════════════
# FLEET MANAGER GROUP VIEW
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/group/<group_name>")
@login_required
def group_view(group_name):
    user = get_current_user()
    if user["role"] != "admin" and user.get("fleet_group") != group_name:
        flash("You don't have access to this fleet group.", "error")
        return redirect(url_for("index"))

    active = get_active_review()
    report = load_report()
    decisions = load_decisions()

    txns = [t for t in report.get("transactions", []) if t["fleet_group"] == group_name]
    temp_cards = [t for t in report.get("temporary_cards", []) if t["fleet_group"] == group_name]
    declined = [t for t in report.get("declined_transactions", []) if t["fleet_group"] == group_name]

    vehicles = {}
    for t in txns:
        vname = t["vehicle_name"]
        if vname not in vehicles:
            vehicles[vname] = {"transactions": [], "flag_count": 0}
        vehicles[vname]["transactions"].append(t)
        vehicles[vname]["flag_count"] += t["flag_count"]

    mpg_summary = {k: v for k, v in report.get("mpg_summary_by_vehicle", {}).items()
                   if k in vehicles}

    group_decisions = decisions.get(group_name, {})

    return render_template("group.html",
                           group_name=group_name,
                           vehicles=vehicles,
                           temp_cards=temp_cards,
                           declined=declined,
                           mpg_summary=mpg_summary,
                           summary=report.get("summary", {}),
                           group_summary=report.get("group_summary", {}).get(group_name, {}),
                           decisions=group_decisions,
                           active_review=active)


# ═══════════════════════════════════════════════════════════════════════════════
# API: DECISIONS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/submit-decision", methods=["POST"])
@login_required
def submit_decision():
    data = request.json
    group_name = data.get("group_name")
    txn_key = data.get("txn_key")
    action = data.get("action")
    reason = data.get("reason", "")

    decisions = load_decisions()
    if group_name not in decisions:
        decisions[group_name] = {}

    decisions[group_name][txn_key] = {
        "action": action,
        "reason": reason,
        "reviewer": session.get("display_name", session.get("email", "")),
        "timestamp": datetime.now().isoformat(),
    }
    save_decisions(decisions)
    return jsonify({"status": "ok"})


@app.route("/api/submit-group", methods=["POST"])
@login_required
def submit_group():
    data = request.json
    group_name = data.get("group_name")
    manager_name = data.get("manager_name", "")

    decisions = load_decisions()
    if group_name not in decisions:
        decisions[group_name] = {}

    decisions[group_name]["_submission"] = {
        "manager_name": manager_name,
        "submitted_by": session.get("email", ""),
        "submitted_at": datetime.now().isoformat(),
        "status": "submitted",
    }
    save_decisions(decisions)
    return jsonify({"status": "ok"})


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN: OVERVIEW
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/admin")
@admin_required
def admin_view():
    report = load_report()
    decisions = load_decisions()
    groups = sorted(report.get("group_summary", {}).keys())

    group_statuses = {}
    for g in groups:
        g_decisions = decisions.get(g, {})
        submission = g_decisions.get("_submission")
        g_summary = report.get("group_summary", {}).get(g, {})
        decided = sum(1 for k, v in g_decisions.items()
                      if k != "_submission" and isinstance(v, dict) and "action" in v)

        group_statuses[g] = {
            "total_txns": g_summary.get("total_txns", 0),
            "flagged_txns": g_summary.get("flagged_txns", 0),
            "total_spend": g_summary.get("total_spend", 0),
            "decisions_made": decided,
            "submission": submission,
        }

    return render_template("admin.html",
                           groups=groups,
                           group_statuses=group_statuses,
                           summary=report.get("summary", {}),
                           decisions=decisions)


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN: GROUP DETAIL
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/admin/group/<group_name>")
@admin_required
def admin_group_detail(group_name):
    report = load_report()
    decisions = load_decisions()

    txns = [t for t in report.get("transactions", []) if t["fleet_group"] == group_name]
    temp_cards = [t for t in report.get("temporary_cards", []) if t["fleet_group"] == group_name]
    declined = [t for t in report.get("declined_transactions", []) if t["fleet_group"] == group_name]
    g_decisions = decisions.get(group_name, {})
    submission = g_decisions.get("_submission")
    g_summary = report.get("group_summary", {}).get(group_name, {})
    mpg_summary = report.get("mpg_summary_by_vehicle", {})

    flagged_txns = []
    for t in txns:
        if t["flag_count"] > 0:
            txn_key = f"{t['vehicle_name']}_{t['transaction_date']}_{t['transaction_time']}"
            decision = g_decisions.get(txn_key, {})
            flagged_txns.append({**t, "decision": decision, "txn_key": txn_key})

    approvals = sum(1 for f in flagged_txns if f["decision"].get("action") == "approve")
    denials = sum(1 for f in flagged_txns if f["decision"].get("action") == "deny")

    return render_template("admin_group.html",
                           group_name=group_name,
                           flagged_txns=flagged_txns,
                           temp_cards=temp_cards,
                           declined=declined,
                           submission=submission,
                           g_summary=g_summary,
                           mpg_summary=mpg_summary,
                           approvals=approvals,
                           denials=denials,
                           total_txns=len(txns))


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN: REPORT
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/admin/report")
@admin_required
def generate_report():
    report = load_report()
    decisions = load_decisions()
    groups = sorted(report.get("group_summary", {}).keys())
    active = get_active_review()

    group_data = []
    grand_total = 0
    total_flagged = 0
    total_approved = 0
    total_denied = 0

    for g in groups:
        g_summary = report.get("group_summary", {}).get(g, {})
        g_decisions = decisions.get(g, {})
        submission = g_decisions.get("_submission")
        spend = g_summary.get("total_spend", 0)
        grand_total += spend

        g_txns = [t for t in report.get("transactions", []) if t["fleet_group"] == g]
        flagged = []
        for t in g_txns:
            if t["flag_count"] > 0:
                txn_key = f"{t['vehicle_name']}_{t['transaction_date']}_{t['transaction_time']}"
                decision = g_decisions.get(txn_key, {})
                flagged.append({**t, "decision": decision})

        approved = sum(1 for f in flagged if f["decision"].get("action") == "approve")
        denied = sum(1 for f in flagged if f["decision"].get("action") == "deny")
        total_flagged += len(flagged)
        total_approved += approved
        total_denied += denied

        group_data.append({
            "name": g,
            "summary": g_summary,
            "submission": submission,
            "flagged": flagged,
            "denied_items": [f for f in flagged if f["decision"].get("action") == "deny"],
            "approved": approved,
            "denied": denied,
            "spend": spend,
        })

    mpg_summary = report.get("mpg_summary_by_vehicle", {})
    flagged_vehicles = {k: v for k, v in mpg_summary.items() if v.get("flagged")}

    return render_template("report.html",
                           group_data=group_data,
                           grand_total=grand_total,
                           total_flagged=total_flagged,
                           total_approved=total_approved,
                           total_denied=total_denied,
                           summary=report.get("summary", {}),
                           mpg_summary=mpg_summary,
                           flagged_vehicles=flagged_vehicles,
                           active_review=active,
                           generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"))


@app.route("/api/admin-approve", methods=["POST"])
@admin_required
def admin_approve():
    data = request.json
    admin_name = data.get("admin_name", "")

    decisions = load_decisions()
    decisions["_admin_approval"] = {
        "admin_name": admin_name,
        "approved_by": session.get("email", ""),
        "approved_at": datetime.now().isoformat(),
        "status": "approved",
    }
    save_decisions(decisions)

    # Mark review as complete
    active = get_active_review()
    if active:
        active["status"] = "complete"
        active["completed_at"] = datetime.now().isoformat()
        save_review_meta(active["period"], active)

    return jsonify({"status": "ok"})


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN: REVIEW MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/admin/reviews")
@admin_required
def admin_reviews():
    """Review management hub — upload, process, notify, archive."""
    reviews = list_reviews()
    active = get_active_review()
    report = load_report() if active else {}
    decisions = load_decisions() if active else {}

    # Calculate progress for active review
    progress = None
    if active and report.get("group_summary"):
        groups = report.get("group_summary", {})
        total_groups = len(groups)
        submitted_groups = sum(1 for g in groups
                               if decisions.get(g, {}).get("_submission"))
        progress = {
            "total": total_groups,
            "submitted": submitted_groups,
            "pct": round(submitted_groups / total_groups * 100) if total_groups else 0,
        }

    return render_template("admin_reviews.html",
                           reviews=reviews,
                           active=active,
                           progress=progress)


@app.route("/api/reviews/create", methods=["POST"])
@admin_required
def create_review():
    """Upload spreadsheet, run anomaly detection, create a new monthly review."""
    if "file" not in request.files:
        return jsonify({"status": "error", "message": "No file uploaded."}), 400

    file = request.files["file"]
    if not file.filename.endswith((".xlsx", ".xls")):
        return jsonify({"status": "error", "message": "File must be an Excel spreadsheet (.xlsx)."}), 400

    period = request.form.get("period", "").strip()  # e.g. "2026-03"
    label = request.form.get("label", "").strip()
    deadline = request.form.get("deadline", "").strip()

    if not period:
        return jsonify({"status": "error", "message": "Period is required."}), 400

    # Create review directory
    review_dir = get_review_dir(period)
    os.makedirs(review_dir, exist_ok=True)

    # Save uploaded file
    upload_path = os.path.join(review_dir, "corpay_upload.xlsx")
    file.save(upload_path)

    # Mark any existing in_review as complete
    for r in list_reviews():
        if r.get("status") == "in_review" and r["period"] != period:
            r["status"] = "complete"
            r["completed_at"] = datetime.now().isoformat()
            save_review_meta(r["period"], r)

    # Run anomaly detection
    output_path = os.path.join(review_dir, "anomaly_report.json")
    try:
        sys.path.insert(0, TOOLS_DIR)
        from anomaly_detection import run as run_anomaly
        run_anomaly(
            corpay_file=upload_path,
            baselines_file=BASELINES_FILE,
            output_file=output_path,
        )
    except Exception as e:
        return jsonify({"status": "error", "message": f"Processing failed: {str(e)}"}), 500

    # Initialize empty decisions
    decisions_path = os.path.join(review_dir, "review_decisions.json")
    with open(decisions_path, "w") as f:
        json.dump({}, f)

    # Save meta
    meta = {
        "period": period,
        "label": label or f"{period} Fuel Review",
        "status": "in_review",
        "deadline": deadline or None,
        "created_by": session.get("email", ""),
        "created_at": datetime.now().isoformat(),
        "processed_at": datetime.now().isoformat(),
        "notifications_sent_at": None,
        "completed_at": None,
    }
    save_review_meta(period, meta)

    return jsonify({"status": "ok", "period": period})


@app.route("/api/reviews/<period>/notify", methods=["POST"])
@admin_required
def send_notifications(period):
    """Send email notifications to fleet managers."""
    meta = load_review_meta(period)
    if not meta:
        return jsonify({"status": "error", "message": "Review not found."}), 404

    users = load_users()
    managers = {email: u for email, u in users.items() if u.get("role") == "manager"}

    sendgrid_key = os.environ.get("SENDGRID_API_KEY")
    if not sendgrid_key:
        return jsonify({"status": "error",
                        "message": "SENDGRID_API_KEY not configured. Set it in Render environment variables."}), 500

    from_email = os.environ.get("FROM_EMAIL", "fleet@aeroseal.com")
    app_url = request.host_url.rstrip("/")

    sent = 0
    errors = []
    for email, user in managers.items():
        group = user.get("fleet_group", "")
        review_url = f"{app_url}/group/{group}"
        try:
            _send_email(
                sendgrid_key=sendgrid_key,
                from_email=from_email,
                to_email=email,
                subject=f"Fuel Review Ready: {meta.get('label', period)}",
                html=_build_notification_html(
                    user["display_name"], meta, review_url
                ),
            )
            sent += 1
        except Exception as e:
            errors.append(f"{email}: {str(e)}")

    # Update meta
    meta["notifications_sent_at"] = datetime.now().isoformat()
    save_review_meta(period, meta)

    if errors:
        return jsonify({"status": "partial", "sent": sent, "errors": errors})
    return jsonify({"status": "ok", "sent": sent})


@app.route("/api/reviews/<period>/remind", methods=["POST"])
@admin_required
def send_reminders(period):
    """Send reminder emails to managers who haven't submitted."""
    meta = load_review_meta(period)
    if not meta:
        return jsonify({"status": "error", "message": "Review not found."}), 404

    report = load_report(period)
    decisions = load_decisions(period)
    users = load_users()

    # Find groups that haven't submitted
    pending_groups = set()
    for g in report.get("group_summary", {}).keys():
        if not decisions.get(g, {}).get("_submission"):
            pending_groups.add(g)

    sendgrid_key = os.environ.get("SENDGRID_API_KEY")
    if not sendgrid_key:
        return jsonify({"status": "error", "message": "SENDGRID_API_KEY not configured."}), 500

    from_email = os.environ.get("FROM_EMAIL", "fleet@aeroseal.com")
    app_url = request.host_url.rstrip("/")

    sent = 0
    for email, user in users.items():
        if user.get("role") != "manager":
            continue
        if user.get("fleet_group") not in pending_groups:
            continue
        group = user.get("fleet_group", "")
        review_url = f"{app_url}/group/{group}"
        try:
            _send_email(
                sendgrid_key=sendgrid_key,
                from_email=from_email,
                to_email=email,
                subject=f"Reminder: Fuel Review Due — {meta.get('label', period)}",
                html=_build_reminder_html(user["display_name"], meta, review_url),
            )
            sent += 1
        except Exception:
            pass

    return jsonify({"status": "ok", "sent": sent, "pending_groups": list(pending_groups)})


# ═══════════════════════════════════════════════════════════════════════════════
# EMAIL HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _send_email(sendgrid_key, from_email, to_email, subject, html):
    """Send an email via SendGrid HTTP API (no SDK dependency)."""
    import urllib.request
    payload = json.dumps({
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {"email": from_email, "name": "Aeroseal Fleet Review"},
        "subject": subject,
        "content": [{"type": "text/html", "value": html}],
    }).encode()
    req = urllib.request.Request(
        "https://api.sendgrid.com/v3/mail/send",
        data=payload,
        headers={
            "Authorization": f"Bearer {sendgrid_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    urllib.request.urlopen(req)


def _build_notification_html(name, meta, review_url):
    deadline_str = f"<p>Please complete your review by <strong>{meta.get('deadline', 'N/A')}</strong>.</p>" if meta.get("deadline") else ""
    return f"""
    <div style="font-family: 'Figtree', Arial, sans-serif; max-width: 520px; margin: 0 auto; padding: 32px;">
      <div style="text-align: center; margin-bottom: 24px;">
        <div style="font-size: 22px; font-weight: 700; color: #005A90;">Aeroseal Fuel Review</div>
      </div>
      <p>Hi {name},</p>
      <p>Your fuel review for <strong>{meta.get('label', meta['period'])}</strong> is ready for your review.</p>
      {deadline_str}
      <div style="text-align: center; margin: 28px 0;">
        <a href="{review_url}" style="background: #008CD1; color: #fff; padding: 14px 32px; border-radius: 6px; text-decoration: none; font-weight: 600; font-size: 15px;">Review Now</a>
      </div>
      <p style="color: #5A6B7B; font-size: 13px;">Log in with your email and password to access your review.</p>
    </div>
    """


def _build_reminder_html(name, meta, review_url):
    deadline_str = f" by <strong>{meta.get('deadline', 'N/A')}</strong>" if meta.get("deadline") else ""
    return f"""
    <div style="font-family: 'Figtree', Arial, sans-serif; max-width: 520px; margin: 0 auto; padding: 32px;">
      <div style="text-align: center; margin-bottom: 24px;">
        <div style="font-size: 22px; font-weight: 700; color: #005A90;">Aeroseal Fuel Review</div>
      </div>
      <p>Hi {name},</p>
      <p>This is a reminder that your fuel review for <strong>{meta.get('label', meta['period'])}</strong> is still pending. Please complete your review{deadline_str}.</p>
      <div style="text-align: center; margin: 28px 0;">
        <a href="{review_url}" style="background: #E65100; color: #fff; padding: 14px 32px; border-radius: 6px; text-decoration: none; font-weight: 600; font-size: 15px;">Complete Review</a>
      </div>
    </div>
    """


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN: USER MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/admin/users")
@admin_required
def admin_users():
    users = load_users()
    report = load_report()
    fleet_groups = sorted(report.get("group_summary", {}).keys())
    return render_template("admin_users.html", users=users, fleet_groups=fleet_groups)


@app.route("/api/users", methods=["POST"])
@admin_required
def create_user():
    data = request.json
    email = data.get("username", "").strip().lower()  # "username" key from JS form
    password = data.get("password", "")
    role = data.get("role", "manager")
    display_name = data.get("display_name", "")
    fleet_group = data.get("fleet_group") or None

    if not email or not password:
        return jsonify({"status": "error", "message": "Email and password required."}), 400

    users = load_users()
    if email in users:
        return jsonify({"status": "error", "message": "Email already exists."}), 400

    users[email] = {
        "password_hash": generate_password_hash(password, method="pbkdf2:sha256"),
        "role": role,
        "display_name": display_name or email,
        "fleet_group": fleet_group if role == "manager" else None,
        "created_at": datetime.now().isoformat(),
        "created_by": session.get("email", ""),
    }
    save_users(users)
    return jsonify({"status": "ok"})


@app.route("/api/users/<path:username>", methods=["PUT"])
@admin_required
def update_user(username):
    data = request.json
    users = load_users()

    if username not in users:
        return jsonify({"status": "error", "message": "User not found."}), 404

    user = users[username]
    if data.get("display_name"):
        user["display_name"] = data["display_name"]
    if data.get("role"):
        user["role"] = data["role"]
    if data.get("fleet_group") is not None:
        user["fleet_group"] = data["fleet_group"] if user["role"] == "manager" else None
    if data.get("password"):
        user["password_hash"] = generate_password_hash(data["password"], method="pbkdf2:sha256")

    user["updated_at"] = datetime.now().isoformat()
    user["updated_by"] = session.get("email", "")
    save_users(users)
    return jsonify({"status": "ok"})


@app.route("/api/users/<path:username>", methods=["DELETE"])
@admin_required
def delete_user(username):
    if username == session.get("email"):
        return jsonify({"status": "error", "message": "Cannot delete your own account."}), 400

    users = load_users()
    if username not in users:
        return jsonify({"status": "error", "message": "User not found."}), 404

    del users[username]
    save_users(users)
    return jsonify({"status": "ok"})


# ═══════════════════════════════════════════════════════════════════════════════
# STARTUP
# ═══════════════════════════════════════════════════════════════════════════════

init_default_admin()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=True)
