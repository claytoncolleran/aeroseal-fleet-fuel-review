"""
Fuel Review — Fleet Manager Approval Dashboard
Aeroseal-branded web interface for reviewing fuel transactions and anomalies.
"""

from flask import Flask, render_template, request, jsonify, make_response
import json
import os
from datetime import datetime

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
REPORT_FILE = os.path.join(DATA_DIR, "anomaly_report.json")
DECISIONS_FILE = os.path.join(DATA_DIR, "review_decisions.json")


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


@app.route("/")
def index():
    """Landing page — select fleet manager view."""
    report = load_report()
    groups = sorted(report["group_summary"].keys())
    return render_template("index.html", groups=groups, summary=report["summary"],
                           group_summary=report["group_summary"])


@app.route("/group/<group_name>")
def group_view(group_name):
    """Fleet manager review page for a specific group."""
    report = load_report()
    decisions = load_decisions()

    # Filter transactions for this group
    txns = [t for t in report["transactions"] if t["fleet_group"] == group_name]
    temp_cards = [t for t in report["temporary_cards"] if t["fleet_group"] == group_name]
    declined = [t for t in report["declined_transactions"] if t["fleet_group"] == group_name]

    # Group transactions by vehicle
    vehicles = {}
    for t in txns:
        vname = t["vehicle_name"]
        if vname not in vehicles:
            vehicles[vname] = {"transactions": [], "flag_count": 0}
        vehicles[vname]["transactions"].append(t)
        vehicles[vname]["flag_count"] += t["flag_count"]

    # MPG summary for this group's vehicles
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


@app.route("/api/submit-decision", methods=["POST"])
def submit_decision():
    """Save an approve/deny decision for a flagged transaction."""
    data = request.json
    group_name = data.get("group_name")
    txn_key = data.get("txn_key")
    action = data.get("action")  # "approve" or "deny"
    reason = data.get("reason", "")

    decisions = load_decisions()
    if group_name not in decisions:
        decisions[group_name] = {}

    decisions[group_name][txn_key] = {
        "action": action,
        "reason": reason,
        "timestamp": datetime.now().isoformat(),
    }
    save_decisions(decisions)
    return jsonify({"status": "ok"})


@app.route("/api/submit-group", methods=["POST"])
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
        "submitted_at": datetime.now().isoformat(),
        "status": "submitted",
    }
    save_decisions(decisions)
    return jsonify({"status": "ok"})


@app.route("/admin")
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

        # Count flagged items and decisions made
        g_txns = [t for t in report["transactions"] if t["fleet_group"] == g]
        flagged = [t for t in g_txns if t["flag_count"] > 0]
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


@app.route("/admin/group/<group_name>")
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

    # Separate flagged with decisions
    flagged_txns = []
    for t in txns:
        if t["flag_count"] > 0:
            txn_key = f"{t['vehicle_name']}_{t['transaction_date']}_{t['transaction_time']}"
            decision = g_decisions.get(txn_key, {})
            flagged_txns.append({**t, "decision": decision, "txn_key": txn_key})

    # Count approvals and denials
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


@app.route("/admin/report")
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

        # Get flagged items with decisions
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
def admin_approve():
    """Fleet admin final approval."""
    data = request.json
    admin_name = data.get("admin_name", "")

    decisions = load_decisions()
    decisions["_admin_approval"] = {
        "admin_name": admin_name,
        "approved_at": datetime.now().isoformat(),
        "status": "approved",
    }
    save_decisions(decisions)
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(debug=True, port=5001)
