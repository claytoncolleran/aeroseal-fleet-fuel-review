"""
Fuel Review — Fleet Manager Approval Dashboard
Aeroseal-branded web interface for reviewing fuel transactions and anomalies.
Includes authentication, role-based access, and user management.
"""

from flask import (Flask, render_template, request, jsonify, redirect,
                   url_for, session, flash)
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import json
import os
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")

# ─── Paths ───────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
REPORT_FILE = os.path.join(DATA_DIR, "anomaly_report.json")

# Persistent storage: use /var/data on Render, local data/ in development
PERSIST_DIR = "/var/data" if os.path.isdir("/var/data") else DATA_DIR
DECISIONS_FILE = os.path.join(PERSIST_DIR, "review_decisions.json")
USERS_FILE = os.path.join(PERSIST_DIR, "users.json")


# ─── User Management ────────────────────────────────────────────────────────
def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE) as f:
            return json.load(f)
    return {}


def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)


def init_default_admin():
    """Create default admin account if no users exist."""
    users = load_users()
    if not users:
        default_pw = os.environ.get("ADMIN_DEFAULT_PASSWORD", "changeme")
        users["admin"] = {
            "password_hash": generate_password_hash(default_pw, method="pbkdf2:sha256"),
            "role": "admin",
            "display_name": "Administrator",
            "fleet_group": None,
            "created_at": datetime.now().isoformat(),
        }
        save_users(users)
        print(f"  Default admin account created (username: admin)")


# ─── Auth Decorators ─────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("login"))
        if session.get("role") != "admin":
            flash("Admin access required.", "error")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated


def get_current_user():
    """Return current user info dict or None."""
    if "username" not in session:
        return None
    return {
        "username": session["username"],
        "role": session.get("role"),
        "display_name": session.get("display_name"),
        "fleet_group": session.get("fleet_group"),
    }


# ─── Context Processor ──────────────────────────────────────────────────────
@app.context_processor
def inject_user():
    return {"current_user": get_current_user()}


# ─── Data Loading ────────────────────────────────────────────────────────────
def load_report():
    with open(REPORT_FILE) as f:
        return json.load(f)


def load_decisions():
    if os.path.exists(DECISIONS_FILE):
        with open(DECISIONS_FILE) as f:
            return json.load(f)
    return {}


def save_decisions(decisions):
    with open(DECISIONS_FILE, "w") as f:
        json.dump(decisions, f, indent=2)


# ─── Auth Routes ─────────────────────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    if "username" in session:
        return redirect(url_for("index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")

        users = load_users()
        user = users.get(username)

        if user and check_password_hash(user["password_hash"], password):
            session["username"] = username
            session["role"] = user["role"]
            session["display_name"] = user.get("display_name", username)
            session["fleet_group"] = user.get("fleet_group")
            session.permanent = True
            return redirect(url_for("index"))
        else:
            flash("Invalid username or password.", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ─── Dashboard ───────────────────────────────────────────────────────────────
@app.route("/")
@login_required
def index():
    """Landing page — shows groups based on user role."""
    report = load_report()
    user = get_current_user()

    if user["role"] == "admin":
        groups = sorted(report["group_summary"].keys())
    else:
        # Managers see only their assigned group
        assigned = user.get("fleet_group")
        groups = [assigned] if assigned and assigned in report["group_summary"] else []

    return render_template("index.html", groups=groups, summary=report["summary"],
                           group_summary=report["group_summary"])


# ─── Fleet Manager Group View ────────────────────────────────────────────────
@app.route("/group/<group_name>")
@login_required
def group_view(group_name):
    """Fleet manager review page for a specific group."""
    user = get_current_user()

    # Managers can only see their assigned group
    if user["role"] != "admin" and user.get("fleet_group") != group_name:
        flash("You don't have access to this fleet group.", "error")
        return redirect(url_for("index"))

    report = load_report()
    decisions = load_decisions()

    txns = [t for t in report["transactions"] if t["fleet_group"] == group_name]
    temp_cards = [t for t in report["temporary_cards"] if t["fleet_group"] == group_name]
    declined = [t for t in report["declined_transactions"] if t["fleet_group"] == group_name]

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
                           summary=report["summary"],
                           group_summary=report["group_summary"].get(group_name, {}),
                           decisions=group_decisions)


# ─── API: Decisions ──────────────────────────────────────────────────────────
@app.route("/api/submit-decision", methods=["POST"])
@login_required
def submit_decision():
    """Save an approve/deny decision for a flagged transaction."""
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
        "reviewer": session.get("display_name", session.get("username", "")),
        "timestamp": datetime.now().isoformat(),
    }
    save_decisions(decisions)
    return jsonify({"status": "ok"})


@app.route("/api/submit-group", methods=["POST"])
@login_required
def submit_group():
    """Submit all decisions for a fleet group."""
    data = request.json
    group_name = data.get("group_name")
    manager_name = data.get("manager_name", "")

    decisions = load_decisions()
    if group_name not in decisions:
        decisions[group_name] = {}

    decisions[group_name]["_submission"] = {
        "manager_name": manager_name,
        "submitted_by": session.get("username", ""),
        "submitted_at": datetime.now().isoformat(),
        "status": "submitted",
    }
    save_decisions(decisions)
    return jsonify({"status": "ok"})


# ─── Admin: Overview ─────────────────────────────────────────────────────────
@app.route("/admin")
@admin_required
def admin_view():
    """Fleet admin overview of all group submissions."""
    report = load_report()
    decisions = load_decisions()
    groups = sorted(report["group_summary"].keys())

    group_statuses = {}
    for g in groups:
        g_decisions = decisions.get(g, {})
        submission = g_decisions.get("_submission")
        g_summary = report["group_summary"].get(g, {})

        g_txns = [t for t in report["transactions"] if t["fleet_group"] == g]
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
                           summary=report["summary"],
                           decisions=decisions)


# ─── Admin: Group Detail ─────────────────────────────────────────────────────
@app.route("/admin/group/<group_name>")
@admin_required
def admin_group_detail(group_name):
    """Admin drill-down into a specific group's submission."""
    report = load_report()
    decisions = load_decisions()

    txns = [t for t in report["transactions"] if t["fleet_group"] == group_name]
    temp_cards = [t for t in report["temporary_cards"] if t["fleet_group"] == group_name]
    declined = [t for t in report["declined_transactions"] if t["fleet_group"] == group_name]
    g_decisions = decisions.get(group_name, {})
    submission = g_decisions.get("_submission")
    g_summary = report["group_summary"].get(group_name, {})
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


# ─── Admin: Report ───────────────────────────────────────────────────────────
@app.route("/admin/report")
@admin_required
def generate_report():
    """Generate consolidated accounting report as downloadable HTML (print to PDF)."""
    report = load_report()
    decisions = load_decisions()
    groups = sorted(report["group_summary"].keys())

    group_data = []
    grand_total = 0
    total_flagged = 0
    total_approved = 0
    total_denied = 0

    for g in groups:
        g_summary = report["group_summary"].get(g, {})
        g_decisions = decisions.get(g, {})
        submission = g_decisions.get("_submission")
        spend = g_summary.get("total_spend", 0)
        grand_total += spend

        g_txns = [t for t in report["transactions"] if t["fleet_group"] == g]
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
                           summary=report["summary"],
                           mpg_summary=mpg_summary,
                           flagged_vehicles=flagged_vehicles,
                           generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"))


@app.route("/api/admin-approve", methods=["POST"])
@admin_required
def admin_approve():
    """Fleet admin final approval."""
    data = request.json
    admin_name = data.get("admin_name", "")

    decisions = load_decisions()
    decisions["_admin_approval"] = {
        "admin_name": admin_name,
        "approved_by": session.get("username", ""),
        "approved_at": datetime.now().isoformat(),
        "status": "approved",
    }
    save_decisions(decisions)
    return jsonify({"status": "ok"})


# ─── Admin: User Management ─────────────────────────────────────────────────
@app.route("/admin/users")
@admin_required
def admin_users():
    """User management page."""
    users = load_users()
    report = load_report()
    fleet_groups = sorted(report["group_summary"].keys())
    return render_template("admin_users.html", users=users, fleet_groups=fleet_groups)


@app.route("/api/users", methods=["POST"])
@admin_required
def create_user():
    """Create a new user."""
    data = request.json
    username = data.get("username", "").strip().lower()
    password = data.get("password", "")
    role = data.get("role", "manager")
    display_name = data.get("display_name", "")
    fleet_group = data.get("fleet_group") or None

    if not username or not password:
        return jsonify({"status": "error", "message": "Username and password required."}), 400

    users = load_users()
    if username in users:
        return jsonify({"status": "error", "message": "Username already exists."}), 400

    users[username] = {
        "password_hash": generate_password_hash(password, method="pbkdf2:sha256"),
        "role": role,
        "display_name": display_name or username,
        "fleet_group": fleet_group if role == "manager" else None,
        "created_at": datetime.now().isoformat(),
        "created_by": session.get("username", ""),
    }
    save_users(users)
    return jsonify({"status": "ok"})


@app.route("/api/users/<username>", methods=["PUT"])
@admin_required
def update_user(username):
    """Update an existing user."""
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
    user["updated_by"] = session.get("username", "")
    save_users(users)
    return jsonify({"status": "ok"})


@app.route("/api/users/<username>", methods=["DELETE"])
@admin_required
def delete_user(username):
    """Delete a user."""
    if username == session.get("username"):
        return jsonify({"status": "error", "message": "Cannot delete your own account."}), 400

    users = load_users()
    if username not in users:
        return jsonify({"status": "error", "message": "User not found."}), 404

    del users[username]
    save_users(users)
    return jsonify({"status": "ok"})


# ─── Startup ─────────────────────────────────────────────────────────────────
init_default_admin()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=True)
