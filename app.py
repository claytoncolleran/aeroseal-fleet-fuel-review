"""
Fuel Review — Fleet Manager Approval Dashboard
Aeroseal-branded web interface for reviewing fuel transactions and anomalies.
Supports monthly review cycles with upload, process, notify, and archive.
"""

from flask import (Flask, render_template, request, jsonify, redirect,
                   url_for, session, flash, send_file)
from werkzeug.utils import secure_filename
from functools import wraps
import json
import os
import random
import sys
import time
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
SUB_ACCOUNTS_FILE = os.path.join(PERSIST_DIR, "sub_accounts.json")
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
    empty_report = {"transactions": [], "temporary_cards": [], "equipment_cards": [],
                     "declined_transactions": [], "group_summary": {}, "summary": {},
                     "mpg_summary_by_vehicle": {}}
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


def load_sub_accounts():
    """Return the sorted list of known sub-account names."""
    if USE_DB:
        return database.db_list_sub_accounts()
    if os.path.exists(SUB_ACCOUNTS_FILE):
        with open(SUB_ACCOUNTS_FILE) as f:
            return sorted(json.load(f))
    return []


def add_sub_accounts(names):
    """Idempotently register sub-account names. Returns newly added ones."""
    names = [n for n in names if n]
    if USE_DB:
        return database.db_add_sub_accounts(names)
    existing = set(load_sub_accounts())
    new = sorted(set(names) - existing)
    if new:
        with open(SUB_ACCOUNTS_FILE, "w") as f:
            json.dump(sorted(existing | set(names)), f, indent=2)
    return new


def init_default_admin():
    """Create default admin account if no users exist. No password is set;
    admin signs in with their email via an emailed 6-digit code."""
    default_email = os.environ.get("ADMIN_DEFAULT_EMAIL", "admin@aeroseal.com")

    if USE_DB:
        if database.db_user_count() == 0:
            database.db_create_user(default_email, None, "Administrator", "admin", None)
            print(f"  Default admin account created in DB (email: {default_email})")
    else:
        users = load_users()
        if users and not any("@" in k for k in users.keys()):
            print("  Migrating users.json from username to email format (old accounts cleared)")
            users = {}
        if not users:
            users[default_email] = {
                "password_hash": None,
                "role": "admin",
                "display_name": "Administrator",
                "fleet_group": None,
                "sub_accounts": [],
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
            return redirect(url_for("login", next=request.full_path if request.query_string else request.path))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "email" not in session:
            return redirect(url_for("login", next=request.full_path if request.query_string else request.path))
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
# AUTH ROUTES — email + 6-digit code
# ═══════════════════════════════════════════════════════════════════════════════

# In-memory rate limiting: {(ip, path): [(timestamp, ...)]}
_login_attempts = {}
_LOGIN_WINDOW_SECONDS = 15 * 60
_LOGIN_MAX_ATTEMPTS = 10


def _login_rate_limited(scope):
    """True if the calling IP has exceeded the attempt limit for this scope."""
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
    key = (ip, scope)
    now = time.time()
    events = [t for t in _login_attempts.get(key, []) if now - t < _LOGIN_WINDOW_SECONDS]
    if len(events) >= _LOGIN_MAX_ATTEMPTS:
        _login_attempts[key] = events
        return True
    events.append(now)
    _login_attempts[key] = events
    if len(_login_attempts) > 5000:
        for k, v in list(_login_attempts.items()):
            if not v or now - v[-1] > _LOGIN_WINDOW_SECONDS:
                _login_attempts.pop(k, None)
    return False


def _generate_login_code():
    return f"{random.SystemRandom().randint(0, 999999):06d}"


@app.route("/login", methods=["GET", "POST"])
def login():
    if "email" in session:
        return redirect(url_for("index"))

    callback_url = request.values.get("next") or request.args.get("next") or ""

    if request.method == "POST":
        if _login_rate_limited("login"):
            flash("Too many attempts. Wait 15 minutes and try again.", "error")
            return render_template("login.html", callback_url=callback_url)

        email = request.form.get("email", "").strip().lower()
        if not email:
            flash("Enter your email address.", "error")
            return render_template("login.html", callback_url=callback_url)

        if USE_DB:
            user = database.db_get_user(email)
        else:
            users = load_users()
            user = users.get(email)

        # We send a code only to addresses that already have an account, but
        # we keep the response identical to avoid leaking which emails exist.
        if user and USE_DB:
            code = _generate_login_code()
            database.db_set_login_code(email, code, ttl_minutes=15)
            resend_key = os.environ.get("RESEND_API_KEY")
            if resend_key:
                from_email = os.environ.get("FROM_EMAIL", "notifications@aeroseal.com")
                try:
                    _send_email(
                        api_key=resend_key,
                        from_email=from_email,
                        to_email=email,
                        subject=f"Your Aeroseal Fuel Review sign-in code: {code}",
                        html=_build_code_email_html(code),
                    )
                except Exception as e:
                    print(f"  [login] code email failed for {email}: {e}", flush=True)

        session["pending_email"] = email
        session["pending_next"] = callback_url
        return redirect(url_for("login_verify"))

    return render_template("login.html", callback_url=callback_url)


@app.route("/login/verify", methods=["GET", "POST"])
def login_verify():
    if "email" in session:
        return redirect(url_for("index"))

    pending_email = session.get("pending_email", "")
    if not pending_email:
        return redirect(url_for("login"))

    if request.method == "POST":
        if _login_rate_limited("verify"):
            flash("Too many attempts. Wait 15 minutes and try again.", "error")
            return render_template("login_verify.html", email=pending_email)

        code = request.form.get("code", "").strip()
        if not code or len(code) != 6 or not code.isdigit():
            flash("Enter the 6-digit code from your email.", "error")
            return render_template("login_verify.html", email=pending_email)

        if not USE_DB:
            flash("Code sign-in requires the database.", "error")
            return render_template("login_verify.html", email=pending_email)

        if not database.db_verify_login_code(pending_email, code):
            flash("That code didn't work. Double-check the code, or request a new one.", "error")
            return render_template("login_verify.html", email=pending_email)

        database.db_clear_login_code(pending_email)
        user = database.db_get_user(pending_email)
        if not user:
            flash("Account not found. Ask your administrator to invite you.", "error")
            session.pop("pending_email", None)
            return redirect(url_for("login"))

        session.pop("pending_email", None)
        next_url = session.pop("pending_next", "") or url_for("index")
        session["email"] = pending_email
        session["role"] = user["role"]
        session["display_name"] = user.get("display_name", pending_email)
        session["fleet_group"] = user.get("fleet_group")
        session.permanent = True
        return redirect(next_url)

    return render_template("login_verify.html", email=pending_email)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/setup-account/<token>")
def setup_account(token):
    """Deprecated: password setup has been replaced with emailed 6-digit codes.
    Any legacy invite link just redirects to the sign-in page."""
    flash("Sign-in has changed. Enter your email below and we'll send you a 6-digit code.", "info")
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
    equipment_cards = [t for t in report.get("equipment_cards", []) if t["fleet_group"] == group_name]
    flagged_equipment = [t for t in equipment_cards if t.get("flag_count", 0) > 0]
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
                           flagged_equipment=flagged_equipment,
                           equipment_total_count=len(equipment_cards),
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
    equipment_cards = [t for t in report.get("equipment_cards", []) if t["fleet_group"] == group_name]
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

    flagged_equipment = []
    for t in equipment_cards:
        if t.get("flag_count", 0) > 0:
            txn_key = f"EQUIP_{t.get('card_no') or t.get('cardholder')}_{t['transaction_date']}_{t['transaction_time']}"
            decision = g_decisions.get(txn_key, {})
            flagged_equipment.append({**t, "decision": decision, "txn_key": txn_key})

    approvals = sum(1 for f in flagged_txns if f["decision"].get("action") == "approve")
    denials = sum(1 for f in flagged_txns if f["decision"].get("action") == "deny")
    equipment_approvals = sum(1 for f in flagged_equipment if f["decision"].get("action") == "approve")
    equipment_denials = sum(1 for f in flagged_equipment if f["decision"].get("action") == "deny")

    return render_template("admin_group.html",
                           group_name=group_name,
                           flagged_txns=flagged_txns,
                           temp_cards=temp_cards,
                           flagged_equipment=flagged_equipment,
                           equipment_total_count=len(equipment_cards),
                           declined=declined,
                           submission=submission,
                           g_summary=g_summary,
                           mpg_summary=mpg_summary,
                           approvals=approvals,
                           denials=denials,
                           equipment_approvals=equipment_approvals,
                           equipment_denials=equipment_denials,
                           total_txns=len(txns))


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN: REPORT
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/admin/report")
@app.route("/admin/report/<period>")
@admin_required
def generate_report(period=None):
    report = load_report(period)
    decisions = load_decisions(period)
    groups = sorted(report.get("group_summary", {}).keys())
    active = get_active_review()

    # Which review is this report for, and is it a read-only archived view?
    viewing = load_review_meta(period) if period else active
    admin_approval = decisions.get("_admin_approval")
    # Read-only when explicitly viewing a period that is not the active review
    # (archived reports must not expose the Sign control, which always posts
    # against the active review).
    report_readonly = bool(period) and (not active or period != active.get("period"))

    temp_by_group = {}
    temp_count_by_group = {}
    for t in report.get("temporary_cards", []):
        g = t.get("fleet_group")
        temp_by_group[g] = temp_by_group.get(g, 0) + (t.get("net_price") or 0)
        temp_count_by_group[g] = temp_count_by_group.get(g, 0) + 1

    equipment_by_group = {}
    equipment_count_by_group = {}
    equipment_flag_count_global = 0
    for t in report.get("equipment_cards", []):
        g = t.get("fleet_group")
        equipment_by_group[g] = equipment_by_group.get(g, 0) + (t.get("net_price") or 0)
        equipment_count_by_group[g] = equipment_count_by_group.get(g, 0) + 1
        if t.get("flag_count", 0) > 0:
            equipment_flag_count_global += 1

    # Include fleet groups that only have equipment/temp spend but no vehicle txns
    all_groups = set(groups) | set(temp_by_group.keys()) | set(equipment_by_group.keys())
    all_groups.discard(None)
    groups = sorted(all_groups)

    group_data = []
    vehicle_grand_total = 0
    temp_grand_total = 0
    equipment_grand_total = 0
    total_flagged = 0
    total_approved = 0
    total_denied = 0
    total_equip_flagged = 0
    total_equip_approved = 0
    total_equip_denied = 0

    for g in groups:
        g_summary = report.get("group_summary", {}).get(g, {})
        g_decisions = decisions.get(g, {})
        submission = g_decisions.get("_submission")
        spend = g_summary.get("total_spend", 0)
        temp_spend = temp_by_group.get(g, 0)
        equipment_spend = equipment_by_group.get(g, 0)
        vehicle_grand_total += spend
        temp_grand_total += temp_spend
        equipment_grand_total += equipment_spend

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

        g_equipment = [t for t in report.get("equipment_cards", []) if t["fleet_group"] == g]
        equipment_flagged = []
        for t in g_equipment:
            if t.get("flag_count", 0) > 0:
                txn_key = f"EQUIP_{t.get('card_no') or t.get('cardholder')}_{t['transaction_date']}_{t['transaction_time']}"
                decision = g_decisions.get(txn_key, {})
                equipment_flagged.append({**t, "decision": decision})

        equip_approved = sum(1 for f in equipment_flagged if f["decision"].get("action") == "approve")
        equip_denied = sum(1 for f in equipment_flagged if f["decision"].get("action") == "deny")
        total_equip_flagged += len(equipment_flagged)
        total_equip_approved += equip_approved
        total_equip_denied += equip_denied

        group_data.append({
            "name": g,
            "summary": g_summary,
            "submission": submission,
            "flagged": flagged,
            "denied_items": [f for f in flagged if f["decision"].get("action") == "deny"],
            "approved": approved,
            "denied": denied,
            "equipment_flagged": equipment_flagged,
            "equipment_denied_items": [f for f in equipment_flagged if f["decision"].get("action") == "deny"],
            "equipment_approved": equip_approved,
            "equipment_denied": equip_denied,
            "equipment_flag_count": len(equipment_flagged),
            "spend": spend,
            "temp_spend": temp_spend,
            "equipment_spend": equipment_spend,
            "combined_spend": spend + temp_spend + equipment_spend,
        })

    mpg_summary = report.get("mpg_summary_by_vehicle", {})
    flagged_vehicles = {k: v for k, v in mpg_summary.items() if v.get("flagged")}

    equipment_total_count = len(report.get("equipment_cards", []))
    if equipment_total_count == 0:
        equipment_review_status = "No equipment transactions this period"
        equipment_tone = "ok"
    elif equipment_flag_count_global == 0:
        equipment_review_status = "No flags raised (all fills below threshold, no data-quality issues)"
        equipment_tone = "ok"
    else:
        equipment_review_status = (f"Flag-reviewed by fleet manager "
                                    f"({equipment_flag_count_global} of {equipment_total_count} flagged)")
        equipment_tone = "ok"

    spend_categories = [
        {
            "label": "Vehicle Cards",
            "txn_count": report.get("summary", {}).get("total_vehicle_transactions_analyzed", 0),
            "spend": vehicle_grand_total,
            "review_status": "Reviewed by fleet manager (flag review + acknowledgment)",
            "status_tone": "ok",
        },
        {
            "label": "Temporary Cards",
            "txn_count": len(report.get("temporary_cards", [])),
            "spend": temp_grand_total,
            "review_status": "Reviewed by fleet manager (every transaction acknowledged)",
            "status_tone": "ok",
        },
        {
            "label": "Equipment / Unit Cards",
            "txn_count": equipment_total_count,
            "spend": equipment_grand_total,
            "review_status": equipment_review_status,
            "status_tone": equipment_tone,
        },
    ]

    return render_template("report.html",
                           group_data=group_data,
                           grand_total=vehicle_grand_total + temp_grand_total + equipment_grand_total,
                           vehicle_grand_total=vehicle_grand_total,
                           temp_grand_total=temp_grand_total,
                           equipment_grand_total=equipment_grand_total,
                           spend_categories=spend_categories,
                           total_flagged=total_flagged,
                           total_approved=total_approved,
                           total_denied=total_denied,
                           total_equip_flagged=total_equip_flagged,
                           total_equip_approved=total_equip_approved,
                           total_equip_denied=total_equip_denied,
                           summary=report.get("summary", {}),
                           mpg_summary=mpg_summary,
                           flagged_vehicles=flagged_vehicles,
                           active_review=active,
                           viewing=viewing,
                           viewing_period=period,
                           report_readonly=report_readonly,
                           admin_approval=admin_approval,
                           generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"))


@app.route("/admin/backfill-equipment/<period>", methods=["GET", "POST"])
@admin_required
def backfill_equipment(period):
    """One-off backfill: read the saved Corpay upload for a review period,
    extract UNIT/EQUIPMENT card rows (whose dollar data was previously dropped),
    and insert them into the transactions table with card_type='equipment'.

    Idempotent: re-running will skip if equipment rows already exist for the
    period. GET returns a preview; POST executes."""
    if not USE_DB:
        return jsonify({"status": "error",
                         "message": "Backfill requires DATABASE_URL (production only)."}), 400

    review = database.db_get_review(period)
    if not review:
        return jsonify({"status": "error", "message": f"No review found for {period}"}), 404
    review_id = review["id"]

    upload_path = os.path.join(get_review_dir(period), "corpay_upload.xlsx")
    if not os.path.exists(upload_path):
        return jsonify({"status": "error",
                         "message": f"Corpay upload not found at {upload_path}"}), 404

    sys.path.insert(0, TOOLS_DIR)
    from anomaly_detection import (load_corpay, safe_float,
                                     infer_group_from_subaccount,
                                     _resolve_flag_config)

    # Load flag settings so backfill applies the same configured thresholds
    flag_settings = {
        "global": database.db_get_global_settings(),
        "groups": database.db_get_all_group_overrides(),
    }

    rows = load_corpay(corpay_file=upload_path)
    equipment_records = []
    for row in rows:
        first_name = row.get("Cardholder First Name")
        status = row.get("Status", "")
        if first_name not in ("UNIT", "EQUIPMENT"):
            continue
        if status == "DECLINED":
            continue

        group = infer_group_from_subaccount(row.get("Sub Account"))
        net = safe_float(row.get("Net Price")) or 0
        gal = safe_float(row.get("Unit/Gallons")) or 0
        ppg = safe_float(row.get("Gross PPU/PPG")) or 0
        odo = safe_float(row.get("Odometer"))

        flags_for_txn = []

        # Flag 7: Large equipment fill
        f7_cfg = _resolve_flag_config(group, 7, flag_settings)
        if f7_cfg.get("enabled", True):
            threshold_dollars = f7_cfg.get("threshold_dollars", 50)
            if net > threshold_dollars:
                flags_for_txn.append({
                    "flag": 7,
                    "flag_name": "Large Equipment Fill",
                    "reason": (f"Equipment card transaction of ${net:,.2f} exceeds "
                               f"${threshold_dollars} threshold. Verify this fill "
                               f"was not for a vehicle."),
                })

        # Flag 8: Corpay 1.0-gallon default (no odometer)
        f8_cfg = _resolve_flag_config(group, 8, flag_settings)
        if f8_cfg.get("enabled", True):
            if gal == 1.0 and ppg == 0 and not odo:
                flags_for_txn.append({
                    "flag": 8,
                    "flag_name": "Corpay Default (No Odometer)",
                    "reason": (f"Corpay defaulted to 1.0 gallon at $0.00/gal "
                               f"because no odometer was entered at the pump. "
                               f"Actual amount charged: ${net:,.2f}."),
                })

        equipment_records.append({
            "transaction_date": row.get("Transaction Date - Date"),
            "transaction_time": row.get("Transaction Date - Time"),
            "vehicle_name": None,
            "fleet_group": group,
            "cardholder": row.get("Cardholder Full Name"),
            "driver": row.get("Spender") or row.get("Cardholder Full Name"),
            "vendor": row.get("Vendor") or row.get("Description"),
            "location": row.get("Address"),
            "state": row.get("State"),
            "status": row.get("Status"),
            "gallons": gal,
            "gross_price": safe_float(row.get("Gross Price")),
            "net_price": net,
            "gross_ppg": ppg,
            "product": row.get("Product Description"),
            "odometer": odo,
            "card_no": row.get("Card No."),
            "sub_account": row.get("Sub Account"),
            "card_type": "equipment",
            "flag_count": len(flags_for_txn),
            "flags": flags_for_txn,
        })

    total_spend = sum((r.get("net_price") or 0) for r in equipment_records)
    spend_by_group = {}
    for r in equipment_records:
        g = r["fleet_group"]
        spend_by_group[g] = spend_by_group.get(g, 0) + (r.get("net_price") or 0)

    # Check for existing equipment rows (idempotency check)
    with database.get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM transactions WHERE review_id = %s AND card_type = %s",
            (review_id, "equipment")
        )
        existing_count = cur.fetchone()[0]

    preview = {
        "period": period,
        "review_id": review_id,
        "upload_path": upload_path,
        "equipment_rows_found": len(equipment_records),
        "total_equipment_spend": round(total_spend, 2),
        "spend_by_group": {k: round(v, 2) for k, v in sorted(spend_by_group.items())},
        "existing_equipment_rows_in_db": existing_count,
    }

    if request.method == "GET":
        preview["status"] = "preview"
        preview["next_step"] = f"POST to this same URL to insert {len(equipment_records)} rows"
        return jsonify(preview), 200

    if existing_count > 0:
        preview["status"] = "skipped"
        preview["message"] = (f"{existing_count} equipment rows already exist for {period}. "
                               "No changes made. Delete existing rows first if you want to re-insert.")
        return jsonify(preview), 200

    if not equipment_records:
        preview["status"] = "noop"
        preview["message"] = "No equipment rows found in the Corpay file."
        return jsonify(preview), 200

    database.db_insert_transactions(review_id, equipment_records, replace=False)

    flagged_count = sum(1 for r in equipment_records if r["flag_count"] > 0)
    preview["status"] = "inserted"
    preview["flagged_equipment_rows"] = flagged_count
    preview["message"] = (f"Inserted {len(equipment_records)} equipment rows totaling "
                           f"${total_spend:,.2f}. {flagged_count} flagged for manager review.")
    return jsonify(preview), 200


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
    # Hide the active review from the archive list while it's still in progress.
    # Once marked complete it will reappear in the archive naturally.
    if active and active.get("status") == "in_review":
        reviews = [r for r in reviews if r.get("period") != active.get("period")]
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

    # ── Sub-account detection (forward-only; no stored data touched) ──
    # Equipment/temporary cards group by their raw Sub Account string. Register
    # any Sub Account values seen here, and warn the admin about brand-new ones
    # and any that have no manager mapped yet. Never blocks the upload.
    sub_account_warnings = []
    if report:
        seen = set()
        for t in report.get("equipment_cards", []) + report.get("temporary_cards", []):
            sa = (t.get("sub_account") or "").strip()
            if sa:
                seen.add(sa)
        if seen:
            newly_added = add_sub_accounts(sorted(seen))
            mapped = set()
            for u in load_users().values():
                if u.get("role") == "manager":
                    mapped.update(u.get("sub_accounts") or [])
            unmapped = sorted(seen - mapped)
            if newly_added:
                sub_account_warnings.append(
                    "New sub-account(s) detected and added to the mapping list: "
                    + ", ".join(newly_added)
                    + ". Assign a manager to each in User Management."
                )
            if unmapped:
                sub_account_warnings.append(
                    "Sub-account(s) with no manager mapped: "
                    + ", ".join(unmapped)
                    + ". Their equipment/temp spend will show as Unassigned "
                      "until mapped in User Management."
                )

    if USE_DB:
        # Create review in database
        review_id = database.db_create_review(period, label or f"{period} Fuel Review",
                                               deadline, created_by)
        # Write transactions and flags to database
        if report:
            all_txns = report.get("transactions", [])
            temp_cards = report.get("temporary_cards", [])
            equipment_cards = report.get("equipment_cards", [])
            declined = report.get("declined_transactions", [])

            # Tag card types for DB storage
            for t in all_txns:
                t["card_type"] = "vehicle"
            for t in temp_cards:
                t["card_type"] = "temporary"
                t["flag_count"] = 0
                t["flags"] = []
            for t in equipment_cards:
                t["card_type"] = "equipment"
                t["vehicle_name"] = None
                # flag_count and flags already populated by anomaly_detection.py
                t.setdefault("flag_count", 0)
                t.setdefault("flags", [])
            for t in declined:
                t["card_type"] = "declined"
                t["flag_count"] = 0
                t["flags"] = []

            database.db_insert_transactions(review_id, all_txns + temp_cards + equipment_cards + declined)

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

    return jsonify({"status": "ok", "period": period,
                    "warnings": sub_account_warnings})


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
      <p style="color: #5A6B7B; font-size: 13px;">Sign in with your Aeroseal email to access your review. We'll send you a 6-digit code.</p>
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


def _build_invite_html(name, sign_in_url):
    return f"""
    <div style="font-family: 'Figtree', Arial, sans-serif; max-width: 520px; margin: 0 auto; padding: 32px;">
      <div style="text-align: center; margin-bottom: 24px;">
        <div style="font-size: 22px; font-weight: 700; color: #005A90;">Aeroseal Fuel Review</div>
      </div>
      <p>Hi {name},</p>
      <p>You've been added to the Aeroseal Fleet Fuel Review system. When you're ready to sign in, visit the link below and enter your email. We'll send you a 6-digit code each time you sign in.</p>
      <div style="text-align: center; margin: 28px 0;">
        <a href="{sign_in_url}" style="background: #008CD1; color: #fff; padding: 14px 32px; border-radius: 6px; text-decoration: none; font-weight: 600; font-size: 15px;">Open Fuel Review</a>
      </div>
      <p style="color: #5A6B7B; font-size: 13px;">No password to remember. Codes expire 15 minutes after they're sent.</p>
    </div>
    """


def _build_code_email_html(code):
    return f"""
    <div style="font-family: 'Figtree', Arial, sans-serif; max-width: 520px; margin: 0 auto; padding: 32px;">
      <div style="text-align: center; margin-bottom: 24px;">
        <div style="font-size: 22px; font-weight: 700; color: #005A90;">Aeroseal Fuel Review</div>
      </div>
      <p>Use this 6-digit code to sign in:</p>
      <div style="text-align: center; margin: 28px 0;">
        <div style="display: inline-block; background: #F2F4F7; border: 1px solid #E4E7EB; border-radius: 12px; padding: 20px 32px; font-family: 'JetBrains Mono', Menlo, monospace; font-size: 32px; font-weight: 700; letter-spacing: 0.35em; color: #005A90;">{code}</div>
      </div>
      <p style="color: #5A6B7B; font-size: 13px; text-align: center;">This code expires in 15 minutes. If you didn't try to sign in, you can ignore this email.</p>
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
    """Create a user and send a welcome email. No password is set; the user
    signs in with their email via an emailed 6-digit code."""
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
        database.db_create_user(
            email, None, display_name or email, role,
            fleet_group if role == "manager" else None,
            session.get("email", "")
        )
    else:
        return jsonify({"status": "error", "message": "Invite requires database."}), 500

    resend_key = os.environ.get("RESEND_API_KEY")
    if resend_key:
        from_email = os.environ.get("FROM_EMAIL", "notifications@aeroseal.com")
        sign_in_url = request.host_url.rstrip("/") + "/login"
        try:
            _send_email(
                api_key=resend_key,
                from_email=from_email,
                to_email=email,
                subject="You're Invited to Aeroseal Fuel Review",
                html=_build_invite_html(display_name or email, sign_in_url),
            )
        except Exception as e:
            return jsonify({"status": "ok", "warning": f"User created but email failed: {str(e)}"})

    return jsonify({"status": "ok"})


@app.route("/api/users/<path:username>/resend-invite", methods=["POST"])
@admin_required
def resend_invite(username):
    """Resend the welcome email so the user has the sign-in link again."""
    if not USE_DB:
        return jsonify({"status": "error", "message": "Requires database."}), 500

    if not database.db_user_exists(username):
        return jsonify({"status": "error", "message": "User not found."}), 404

    resend_key = os.environ.get("RESEND_API_KEY")
    if not resend_key:
        return jsonify({"status": "error", "message": "RESEND_API_KEY not configured."}), 500

    from_email = os.environ.get("FROM_EMAIL", "notifications@aeroseal.com")
    sign_in_url = request.host_url.rstrip("/") + "/login"

    user = database.db_get_user(username)
    display_name = user.get("display_name", username) if user else username

    try:
        _send_email(
            api_key=resend_key,
            from_email=from_email,
            to_email=username,
            subject="Welcome to Aeroseal Fuel Review",
            html=_build_invite_html(display_name, sign_in_url),
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
# ADMIN: REVIEW NOTES (standalone briefing docs)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/admin/review-notes/2026-03")
@admin_required
def admin_review_notes_2026_03():
    """Serve the Fleet Administrator briefing document for the March 2026 review."""
    return send_file(
        os.path.join(BASE_DIR, "admin_review_notes_2026-03.html"),
        mimetype="text/html",
    )


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
