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
import secrets
import db as database

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

USE_DB = database.use_db()


# ═══════════════════════════════════════════════════════════════════════════════
# REVIEW MANAGEMENT — Monthly review lifecycle
# ═══════════════════════════════════════════════════════════════════════════════

def get_review_dir(period):
    """Get directory for a specific review period (e.g., '2026-03')."""
    return os.path.join(REVIEWS_DIR, period)


def load_review_meta(period):
    """Load review metadata for a period. Checks database first, then JSON file."""
    if USE_DB:
        meta = database.db_get_review(period)
        if meta:
            return meta

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
    if USE_DB:
        return database.db_list_reviews()
    reviews = []
    if os.path.isdir(REVIEWS_DIR):
        for name in sorted(os.listdir(REVIEWS_DIR), reverse=True):
            meta = load_review_meta(name)
            if meta:
                reviews.append(meta)
    return reviews


def get_active_review():
    """Get the most recent review that is in 'in_review' status, or the latest."""
    if USE_DB:
        return database.db_get_active_review()
    reviews = list_reviews()
    for r in reviews:
        if r.get("status") == "in_review":
            return r
    return reviews[0] if reviews else None


def load_report(period=None):
    """Load anomaly report for a period (defaults to active review).
    Tries database first when USE_DB is True, falls back to JSON file."""
    empty_report = {"transactions": [], "temporary_cards": [], "declined_transactions": [],
                     "group_summary": {}, "summary": {}, "mpg_summary_by_vehicle": {}}
    if not period:
        active = get_active_review()
        if not active:
            return empty_report
        period = active["period"]

    # Try database first
    if USE_DB:
        try:
            report = database.db_build_report(period)
            if report:
                return report
        except Exception as e:
            print(f"  [load_report] db_build_report failed for {period}: {e}", flush=True)

    # Fall back to JSON file on disk
    report_path = os.path.join(get_review_dir(period), "anomaly_report.json")
    if os.path.exists(report_path):
        with open(report_path) as f:
            return json.load(f)
    return empty_report


def load_decisions(period=None):
    """Load review decisions for a period."""
    if USE_DB:
        if not period:
            active = get_active_review()
            if not active:
                return {}
            period = active["period"]
        review_id = database.db_get_review_id(period)
        if review_id:
            return database.db_get_decisions(review_id)
        return {}

    # JSON fallback
    if not period:
        active = get_active_review()
        if not active:
            return {}
        period = active["period"]

    decisions_path = os.path.join(get_review_dir(period), "review_decisions.json")
    if os.path.exists(decisions_path):
        with open(decisions_path) as f:
            return json.load(f)
    return {}


def save_decisions(decisions, period=None):
    """Save review_decisions.json for a period (JSON fallback only)."""
    if USE_DB:
        return  # DB writes happen directly in route handlers

    if not period:
        active = get_active_review()
        if active:
            period = active["period"]
        else:
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
    if USE_DB:
        return database.db_get_users()
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE) as f:
            return json.load(f)
    return {}


def save_users(users):
    if USE_DB:
        return  # DB writes happen directly in route handlers
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)


def init_default_admin():
    """Create default admin account if no users exist."""
    default_email = os.environ.get("ADMIN_DEFAULT_EMAIL", "admin@aeroseal.com")
    default_pw = os.environ.get("ADMIN_DEFAULT_PASSWORD", "changeme")
    pw_hash = generate_password_hash(default_pw, method="pbkdf2:sha256")

    if USE_DB:
        if database.db_user_count() == 0:
            database.db_create_user(default_email, pw_hash, "Administrator", "admin", None)
            print(f"  Default admin account created in DB (email: {default_email})")
    else:
        users = load_users()
        if users and not any("@" in k for k in users.keys()):
            print("  Migrating users.json from username to email format (old accounts cleared)")
            users = {}
        if not users:
            users[default_email] = {
                "password_hash": pw_hash,
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

        if USE_DB:
            user = database.db_get_user(email)
        else:
            users = load_users()
            user = users.get(email)

        if user and user.get("password_hash") and check_password_hash(user["password_hash"], password):
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


@app.route("/setup-account/<token>", methods=["GET", "POST"])
def setup_account(token):
    """Set password via invite or reset link."""
    if not USE_DB:
        flash("Account setup requires database.", "error")
        return redirect(url_for("login"))

    user = database.db_get_user_by_token(token)
    if not user:
        flash("This link is invalid or has already been used.", "error")
        return redirect(url_for("login"))

    if user["invite_expires"] and user["invite_expires"] < datetime.now():
        flash("This link has expired. Please ask your administrator to send a new invite.", "error")
        return redirect(url_for("login"))

    if request.method == "POST":
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not new_password or len(new_password) < 6:
            flash("Password must be at least 6 characters.", "error")
            return render_template("setup_account.html", user=user, token=token)

        if new_password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template("setup_account.html", user=user, token=token)

        pw_hash = generate_password_hash(new_password, method="pbkdf2:sha256")
        database.db_set_password_and_clear_token(user["email"], pw_hash)

        # Log them in
        session["email"] = user["email"]
        session["role"] = user["role"]
        session["display_name"] = user.get("display_name", user["email"])
        session["fleet_group"] = user.get("fleet_group")
        session.permanent = True
        return redirect(url_for("index"))

    return render_template("setup_account.html", user=user, token=token)


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

    all_group_summary = report.get("group_summary", {})

    if user["role"] == "admin":
        groups = sorted(all_group_summary.keys())
        summary = report.get("summary", {})
    else:
        # Managers: filter stats to their group only
        assigned = user.get("fleet_group")
        groups = [assigned] if assigned and assigned in all_group_summary else []
        if groups:
            gs = all_group_summary.get(assigned, {})
            summary = {
                "total_vehicle_transactions_analyzed": gs.get("total_txns", 0),
                "total_flagged_transactions": gs.get("flagged_txns", 0),
                "total_flags": gs.get("flagged_txns", 0),
                "total_fill_events": gs.get("total_txns", 0),
                "total_spend": gs.get("total_spend", 0),
            }
        else:
            summary = {}

    return render_template("index.html", groups=groups,
                           summary=summary,
                           group_summary=all_group_summary,
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
    reviewer = session.get("display_name", session.get("email", ""))

    if USE_DB:
        active = get_active_review()
        if active:
            review_id = database.db_get_review_id(active["period"])
            if review_id:
                database.db_save_decision(review_id, txn_key, group_name, action, reason, reviewer)
    else:
        decisions = load_decisions()
        if group_name not in decisions:
            decisions[group_name] = {}
        decisions[group_name][txn_key] = {
            "action": action, "reason": reason,
            "reviewer": reviewer, "timestamp": datetime.now().isoformat(),
        }
        save_decisions(decisions)

    return jsonify({"status": "ok"})


@app.route("/api/submit-group", methods=["POST"])
@login_required
def submit_group():
    data = request.json
    group_name = data.get("group_name")
    manager_name = data.get("manager_name", "")
    submitted_by = session.get("email", "")

    active = get_active_review()

    if USE_DB:
        if active:
            review_id = database.db_get_review_id(active["period"])
            if review_id:
                database.db_save_group_submission(review_id, group_name, manager_name, submitted_by)
    else:
        decisions = load_decisions()
        if group_name not in decisions:
            decisions[group_name] = {}
        decisions[group_name]["_submission"] = {
            "manager_name": manager_name, "submitted_by": submitted_by,
            "submitted_at": datetime.now().isoformat(), "status": "submitted",
        }
        save_decisions(decisions)

    # Check if all groups have submitted — if so, notify admins
    if active:
        try:
            report = load_report()
            all_decisions = load_decisions()
            all_groups = set(report.get("group_summary", {}).keys())
            submitted_groups = {g for g in all_groups
                                if all_decisions.get(g, {}).get("_submission")}
            if all_groups and submitted_groups == all_groups:
                _notify_admins_all_complete(active, request.host_url.rstrip("/"))
        except Exception:
            pass  # Don't fail the submission if notification fails

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
    approved_by = session.get("email", "")
    active = get_active_review()

    if USE_DB and active:
        review_id = database.db_get_review_id(active["period"])
        if review_id:
            database.db_save_admin_approval(review_id, admin_name, approved_by)
            database.db_update_review(active["period"], status="complete", completed_at=datetime.now())
    else:
        decisions = load_decisions()
        decisions["_admin_approval"] = {
            "admin_name": admin_name, "approved_by": approved_by,
            "approved_at": datetime.now().isoformat(), "status": "approved",
        }
        save_decisions(decisions)
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
    if USE_DB:
        database.db_complete_other_reviews(period)
    else:
        for r in list_reviews():
            if r.get("status") == "in_review" and r["period"] != period:
                r["status"] = "complete"
                r["completed_at"] = datetime.now().isoformat()
                save_review_meta(r["period"], r)

    # Load flag settings
    flag_settings = None
    if USE_DB:
        flag_settings = {
            "global": database.db_get_global_settings(),
            "groups": database.db_get_all_group_overrides(),
        }

    # Run anomaly detection
    output_path = os.path.join(review_dir, "anomaly_report.json")
    try:
        sys.path.insert(0, TOOLS_DIR)
        from anomaly_detection import run as run_anomaly
        report = run_anomaly(
            corpay_file=upload_path,
            baselines_file=BASELINES_FILE,
            output_file=output_path,
            flag_settings=flag_settings,
        )
    except Exception as e:
        return jsonify({"status": "error", "message": f"Processing failed: {str(e)}"}), 500

    created_by = session.get("email", "")

    if USE_DB:
        # Create review in database
        review_id = database.db_create_review(period, label or f"{period} Fuel Review",
                                               deadline, created_by)
        # Write transactions and flags to database
        if report:
            all_txns = report.get("transactions", [])
            temp_cards = report.get("temporary_cards", [])
            declined = report.get("declined_transactions", [])

            # Tag card types for DB storage
            for t in all_txns:
                t["card_type"] = "vehicle"
            for t in temp_cards:
                t["card_type"] = "temporary"
                t["flag_count"] = 0
                t["flags"] = []
            for t in declined:
                t["card_type"] = "declined"
                t["flag_count"] = 0
                t["flags"] = []

            database.db_insert_transactions(review_id, all_txns + temp_cards + declined)

            # Write vehicle MPG data
            mpg_data = report.get("mpg_summary_by_vehicle", {})
            if mpg_data:
                database.db_insert_vehicle_mpg(review_id, mpg_data)
    else:
        # JSON fallback
        decisions_path = os.path.join(review_dir, "review_decisions.json")
        with open(decisions_path, "w") as f:
            json.dump({}, f)

        meta = {
            "period": period,
            "label": label or f"{period} Fuel Review",
            "status": "in_review",
            "deadline": deadline or None,
            "created_by": created_by,
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

    sendgrid_key = os.environ.get("RESEND_API_KEY")
    if not sendgrid_key:
        return jsonify({"status": "error",
                        "message": "RESEND_API_KEY not configured. Set it in Render environment variables."}), 500

    from_email = os.environ.get("FROM_EMAIL", "notifications@aeroseal.com")
    app_url = request.host_url.rstrip("/")

    sent = 0
    errors = []
    for email, user in managers.items():
        group = user.get("fleet_group", "")
        review_url = f"{app_url}/group/{group}"
        try:
            _send_email(
                api_key=sendgrid_key,
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
    if USE_DB:
        database.db_update_review(period, notifications_sent_at=datetime.now())
    else:
        meta["notifications_sent_at"] = datetime.now().isoformat()
        save_review_meta(period, meta)

    if errors:
        return jsonify({"status": "partial", "sent": sent, "errors": errors})
    return jsonify({"status": "ok", "sent": sent})


@app.route("/api/reviews/<period>", methods=["DELETE"])
@admin_required
def delete_review(period):
    """Delete a review and all its associated data."""
    if USE_DB:
        review_id = database.db_get_review_id(period)
        if not review_id:
            return jsonify({"status": "error", "message": "Review not found."}), 404
        database.db_delete_review(review_id)
    else:
        review_dir = get_review_dir(period)
        if os.path.isdir(review_dir):
            import shutil
            shutil.rmtree(review_dir)

    return jsonify({"status": "ok"})


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

    sendgrid_key = os.environ.get("RESEND_API_KEY")
    if not sendgrid_key:
        return jsonify({"status": "error", "message": "RESEND_API_KEY not configured."}), 500

    from_email = os.environ.get("FROM_EMAIL", "notifications@aeroseal.com")
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
                api_key=sendgrid_key,
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

def _send_email(api_key, from_email, to_email, subject, html):
    """Send an email via Resend HTTP API (no SDK dependency)."""
    import urllib.request
    payload = json.dumps({
        "from": f"Aeroseal Fleet Review <{from_email}>",
        "to": [to_email],
        "subject": subject,
        "html": html,
    }).encode()
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "AerosealFleetReview/1.0",
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


def _build_invite_html(name, setup_url):
    return f"""
    <div style="font-family: 'Figtree', Arial, sans-serif; max-width: 520px; margin: 0 auto; padding: 32px;">
      <div style="text-align: center; margin-bottom: 24px;">
        <div style="font-size: 22px; font-weight: 700; color: #005A90;">Aeroseal Fuel Review</div>
      </div>
      <p>Hi {name},</p>
      <p>You've been invited to the Aeroseal Fleet Fuel Review system. Click the button below to set your password and get started.</p>
      <div style="text-align: center; margin: 28px 0;">
        <a href="{setup_url}" style="background: #008CD1; color: #fff; padding: 14px 32px; border-radius: 6px; text-decoration: none; font-weight: 600; font-size: 15px;">Set Up Your Account</a>
      </div>
      <p style="color: #5A6B7B; font-size: 13px;">This link expires in 48 hours. If it expires, ask your administrator to resend the invite.</p>
    </div>
    """


def _notify_admins_all_complete(meta, app_url=None):
    """Send email to all admin users that all fleet managers have submitted."""
    resend_key = os.environ.get("RESEND_API_KEY")
    if not resend_key:
        return

    from_email = os.environ.get("FROM_EMAIL", "notifications@aeroseal.com")
    if not app_url:
        app_url = os.environ.get("APP_URL", "https://aeroseal-fleet-fuel-review.onrender.com")
    report_url = f"{app_url}/admin/report"
    label = meta.get("label", meta.get("period", ""))

    users = load_users()
    admins = {email: u for email, u in users.items() if u.get("role") == "admin"}

    for email, user in admins.items():
        try:
            _send_email(
                api_key=resend_key,
                from_email=from_email,
                to_email=email,
                subject=f"All Reviews Complete: {label}",
                html=f"""
                <div style="font-family: 'Figtree', Arial, sans-serif; max-width: 520px; margin: 0 auto; padding: 32px;">
                  <div style="text-align: center; margin-bottom: 24px;">
                    <div style="font-size: 22px; font-weight: 700; color: #005A90;">Aeroseal Fuel Review</div>
                  </div>
                  <p>Hi {user.get('display_name', email)},</p>
                  <p>All fleet managers have completed their reviews for <strong>{label}</strong>.</p>
                  <p>The consolidated accounting report is ready to generate.</p>
                  <div style="text-align: center; margin: 28px 0;">
                    <a href="{report_url}" style="background: #008CD1; color: #fff; padding: 14px 32px; border-radius: 6px; text-decoration: none; font-weight: 600; font-size: 15px;">Generate Report</a>
                  </div>
                </div>
                """,
            )
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN: FLAG SETTINGS
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/admin/settings")
@admin_required
def admin_settings():
    """Flag settings — global defaults + per-group overrides."""
    report = load_report()
    fleet_groups = sorted(report.get("group_summary", {}).keys())

    if USE_DB:
        global_settings = database.db_get_global_settings()
        group_overrides = database.db_get_all_group_overrides()
    else:
        global_settings = dict(database.FLAG_DEFAULTS)
        group_overrides = {}

    active = get_active_review()
    return render_template("admin_settings.html",
                           fleet_groups=fleet_groups,
                           global_settings=global_settings,
                           group_overrides=group_overrides,
                           flag_defaults=database.FLAG_DEFAULTS,
                           active_review=active)


@app.route("/api/settings/global", methods=["POST"])
@admin_required
def save_global_settings():
    """Save global flag defaults."""
    if not USE_DB:
        return jsonify({"status": "error", "message": "Settings require database."}), 500

    data = request.json
    updated_by = session.get("email", "")
    for flag_str, cfg in data.items():
        flag_num = int(flag_str)
        enabled = cfg.get("enabled", True)
        config = {k: v for k, v in cfg.items() if k != "enabled"}
        database.db_save_flag_setting(None, flag_num, enabled, config, updated_by)

    return jsonify({"status": "ok"})


@app.route("/api/settings/group/<group_name>", methods=["POST"])
@admin_required
def save_group_overrides(group_name):
    """Save per-group flag overrides."""
    if not USE_DB:
        return jsonify({"status": "error", "message": "Settings require database."}), 500

    data = request.json
    updated_by = session.get("email", "")
    for flag_str, cfg in data.items():
        flag_num = int(flag_str)
        if cfg is None:
            # Remove override
            database.db_delete_group_override(group_name, flag_num)
        else:
            enabled = cfg.get("enabled", True)
            config = {k: v for k, v in cfg.items() if k != "enabled"}
            database.db_save_flag_setting(group_name, flag_num, enabled, config, updated_by)

    return jsonify({"status": "ok"})


@app.route("/api/settings/group/<group_name>/<int:flag_number>", methods=["DELETE"])
@admin_required
def delete_group_override(group_name, flag_number):
    """Remove a per-group override."""
    if not USE_DB:
        return jsonify({"status": "error", "message": "Settings require database."}), 500
    database.db_delete_group_override(group_name, flag_number)
    return jsonify({"status": "ok"})


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


@app.route("/api/users/invite", methods=["POST"])
@admin_required
def invite_user():
    """Create user with no password and send invite email."""
    data = request.json
    email = data.get("email", "").strip().lower()
    role = data.get("role", "manager")
    display_name = data.get("display_name", "")
    fleet_group = data.get("fleet_group") or None

    if not email:
        return jsonify({"status": "error", "message": "Email is required."}), 400

    if USE_DB:
        if database.db_user_exists(email):
            return jsonify({"status": "error", "message": "Email already exists."}), 400

        token = secrets.token_urlsafe(32)
        database.db_create_invited_user(
            email, display_name or email, role,
            fleet_group if role == "manager" else None,
            token, session.get("email", "")
        )
    else:
        return jsonify({"status": "error", "message": "Invite requires database."}), 500

    # Send invite email
    resend_key = os.environ.get("RESEND_API_KEY")
    if resend_key:
        from_email = os.environ.get("FROM_EMAIL", "notifications@aeroseal.com")
        app_url = request.host_url.rstrip("/")
        setup_url = f"{app_url}/setup-account/{token}"
        try:
            _send_email(
                api_key=resend_key,
                from_email=from_email,
                to_email=email,
                subject="You're Invited to Aeroseal Fuel Review",
                html=_build_invite_html(display_name or email, setup_url),
            )
        except Exception as e:
            return jsonify({"status": "ok", "warning": f"User created but email failed: {str(e)}"})

    return jsonify({"status": "ok"})


@app.route("/api/users/<path:username>/resend-invite", methods=["POST"])
@admin_required
def resend_invite(username):
    """Resend invite or send password reset link."""
    if not USE_DB:
        return jsonify({"status": "error", "message": "Requires database."}), 500

    if not database.db_user_exists(username):
        return jsonify({"status": "error", "message": "User not found."}), 404

    token = secrets.token_urlsafe(32)
    database.db_set_invite_token(username, token)

    resend_key = os.environ.get("RESEND_API_KEY")
    if not resend_key:
        return jsonify({"status": "error", "message": "RESEND_API_KEY not configured."}), 500

    from_email = os.environ.get("FROM_EMAIL", "notifications@aeroseal.com")
    app_url = request.host_url.rstrip("/")
    setup_url = f"{app_url}/setup-account/{token}"

    user = database.db_get_user(username)
    display_name = user.get("display_name", username) if user else username

    try:
        _send_email(
            api_key=resend_key,
            from_email=from_email,
            to_email=username,
            subject="Set Your Password - Aeroseal Fuel Review",
            html=_build_invite_html(display_name, setup_url),
        )
    except Exception as e:
        return jsonify({"status": "error", "message": f"Email failed: {str(e)}"}), 500

    return jsonify({"status": "ok"})


@app.route("/api/users/<path:username>", methods=["PUT"])
@admin_required
def update_user(username):
    data = request.json

    if USE_DB:
        if not database.db_user_exists(username):
            return jsonify({"status": "error", "message": "User not found."}), 404
        kwargs = {"updated_by": session.get("email", "")}
        if data.get("display_name"):
            kwargs["display_name"] = data["display_name"]
        if data.get("role"):
            kwargs["role"] = data["role"]
        if data.get("fleet_group") is not None:
            kwargs["fleet_group"] = data["fleet_group"]
        database.db_update_user(username, **kwargs)
    else:
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
        user["updated_at"] = datetime.now().isoformat()
        user["updated_by"] = session.get("email", "")
        save_users(users)

    return jsonify({"status": "ok"})


@app.route("/api/users/<path:username>", methods=["DELETE"])
@admin_required
def delete_user(username):
    if username == session.get("email"):
        return jsonify({"status": "error", "message": "Cannot delete your own account."}), 400

    if USE_DB:
        if not database.db_user_exists(username):
            return jsonify({"status": "error", "message": "User not found."}), 404
        database.db_delete_user(username)
    else:
        users = load_users()
        if username not in users:
            return jsonify({"status": "error", "message": "User not found."}), 404
        del users[username]
        save_users(users)

    return jsonify({"status": "ok"})


# ═══════════════════════════════════════════════════════════════════════════════
# STARTUP
# ═══════════════════════════════════════════════════════════════════════════════

if USE_DB:
    database.init_db()
    print("  Database mode: PostgreSQL")
else:
    print("  Database mode: JSON files (no DATABASE_URL)")
init_default_admin()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=True)
