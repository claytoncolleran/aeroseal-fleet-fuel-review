"""
Database layer for Aeroseal Fuel Review.
PostgreSQL via psycopg2. Falls back to JSON files if DATABASE_URL is not set.
"""

import os
import json
from datetime import datetime
from contextlib import contextmanager

DATABASE_URL = os.environ.get("DATABASE_URL")

# Fix Render's postgres:// URL (psycopg2 requires postgresql://)
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# ─── Connection Pool ─────────────────────────────────────────────────────────
_pool = None

def _get_pool():
    global _pool
    if _pool is None:
        from psycopg2 import pool
        _pool = pool.SimpleConnectionPool(1, 5, DATABASE_URL)
    return _pool


@contextmanager
def get_db():
    """Get a database connection from the pool. Auto-commits on success, rolls back on error."""
    conn = _get_pool().getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _get_pool().putconn(conn)


def use_db():
    """Check if we should use the database (DATABASE_URL is set)."""
    return DATABASE_URL is not None


# ─── Schema Creation ─────────────────────────────────────────────────────────
def init_db():
    """Create all tables if they don't exist."""
    if not use_db():
        return

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                email VARCHAR(255) UNIQUE NOT NULL,
                password_hash VARCHAR(255),
                display_name VARCHAR(255),
                role VARCHAR(50) NOT NULL DEFAULT 'manager',
                fleet_group VARCHAR(255),
                invite_token VARCHAR(64),
                invite_expires TIMESTAMP,
                created_at TIMESTAMP DEFAULT NOW(),
                created_by VARCHAR(255),
                updated_at TIMESTAMP,
                updated_by VARCHAR(255)
            );
            ALTER TABLE users ADD COLUMN IF NOT EXISTS invite_token VARCHAR(64);
            ALTER TABLE users ADD COLUMN IF NOT EXISTS invite_expires TIMESTAMP;
            -- Drop old column if it exists (replaced by invite flow)
            ALTER TABLE users DROP COLUMN IF EXISTS must_change_password;

            CREATE TABLE IF NOT EXISTS reviews (
                id SERIAL PRIMARY KEY,
                period VARCHAR(7) NOT NULL UNIQUE,
                label VARCHAR(255),
                status VARCHAR(50) DEFAULT 'draft',
                deadline DATE,
                created_by VARCHAR(255),
                created_at TIMESTAMP DEFAULT NOW(),
                processed_at TIMESTAMP,
                notifications_sent_at TIMESTAMP,
                completed_at TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id SERIAL PRIMARY KEY,
                review_id INTEGER REFERENCES reviews(id) ON DELETE CASCADE,
                transaction_date DATE,
                transaction_time TIME,
                vehicle_name VARCHAR(255),
                fleet_group VARCHAR(255),
                driver VARCHAR(255),
                vendor VARCHAR(500),
                location VARCHAR(500),
                state VARCHAR(10),
                status VARCHAR(50),
                gallons DECIMAL(10,2),
                gross_price DECIMAL(10,2),
                net_price DECIMAL(10,2),
                gross_ppg DECIMAL(10,4),
                product VARCHAR(255),
                odometer DECIMAL(12,1),
                card_no VARCHAR(50),
                sub_account VARCHAR(100),
                card_type VARCHAR(20),
                flag_count INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS flags (
                id SERIAL PRIMARY KEY,
                transaction_id INTEGER REFERENCES transactions(id) ON DELETE CASCADE,
                flag_number INTEGER NOT NULL,
                flag_name VARCHAR(100),
                reason TEXT
            );

            CREATE TABLE IF NOT EXISTS vehicle_mpg (
                id SERIAL PRIMARY KEY,
                review_id INTEGER REFERENCES reviews(id) ON DELETE CASCADE,
                vehicle_name VARCHAR(255),
                period_mpg DECIMAL(6,1),
                baseline_mpg DECIMAL(6,1),
                total_miles INTEGER,
                total_gallons DECIMAL(10,1),
                fill_count INTEGER,
                pct_diff DECIMAL(6,1),
                flagged BOOLEAN DEFAULT FALSE,
                reason TEXT,
                needs_review BOOLEAN DEFAULT FALSE
            );

            CREATE TABLE IF NOT EXISTS decisions (
                id SERIAL PRIMARY KEY,
                review_id INTEGER REFERENCES reviews(id) ON DELETE CASCADE,
                transaction_id INTEGER REFERENCES transactions(id) ON DELETE CASCADE,
                fleet_group VARCHAR(255),
                action VARCHAR(20),
                reason TEXT,
                reviewer VARCHAR(255),
                reviewed_at TIMESTAMP DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS group_submissions (
                id SERIAL PRIMARY KEY,
                review_id INTEGER REFERENCES reviews(id) ON DELETE CASCADE,
                fleet_group VARCHAR(255),
                manager_name VARCHAR(255),
                submitted_by VARCHAR(255),
                submitted_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(review_id, fleet_group)
            );

            CREATE TABLE IF NOT EXISTS admin_approvals (
                id SERIAL PRIMARY KEY,
                review_id INTEGER REFERENCES reviews(id) ON DELETE CASCADE UNIQUE,
                admin_name VARCHAR(255),
                approved_by VARCHAR(255),
                approved_at TIMESTAMP DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS flag_settings (
                id SERIAL PRIMARY KEY,
                fleet_group VARCHAR(255),
                flag_number INTEGER NOT NULL,
                enabled BOOLEAN DEFAULT TRUE,
                config JSONB DEFAULT '{}',
                updated_by VARCHAR(255),
                updated_at TIMESTAMP DEFAULT NOW()
            );
            CREATE UNIQUE INDEX IF NOT EXISTS uq_flag_settings_group_flag
                ON flag_settings (COALESCE(fleet_group, ''), flag_number);
        """)
    print("  Database tables initialized.")


# ═══════════════════════════════════════════════════════════════════════════════
# USER CRUD
# ═══════════════════════════════════════════════════════════════════════════════

def db_get_users():
    """Return all users as dict keyed by email."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT email, password_hash, display_name, role, fleet_group, invite_token, invite_expires, created_at, created_by, updated_at, updated_by FROM users")
        users = {}
        for row in cur.fetchall():
            has_password = row[1] is not None and row[1] != ''
            users[row[0]] = {
                "password_hash": row[1],
                "display_name": row[2],
                "role": row[3],
                "fleet_group": row[4],
                "invite_token": row[5],
                "invite_expires": row[6].isoformat() if row[6] else None,
                "status": "active" if has_password else "pending",
                "created_at": row[7].isoformat() if row[7] else None,
                "created_by": row[8],
                "updated_at": row[9].isoformat() if row[9] else None,
                "updated_by": row[10],
            }
        return users


def db_get_user(email):
    """Return a single user dict or None."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT password_hash, display_name, role, fleet_group FROM users WHERE email = %s", (email,))
        row = cur.fetchone()
        if row:
            return {"password_hash": row[0], "display_name": row[1], "role": row[2], "fleet_group": row[3]}
        return None


def db_create_user(email, password_hash, display_name, role, fleet_group, created_by=None):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO users (email, password_hash, display_name, role, fleet_group, created_by) VALUES (%s, %s, %s, %s, %s, %s)",
            (email, password_hash, display_name, role, fleet_group, created_by)
        )


def db_create_invited_user(email, display_name, role, fleet_group, invite_token, created_by=None):
    """Create a user with no password and an invite token."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO users (email, password_hash, display_name, role, fleet_group, invite_token, invite_expires, created_by)
               VALUES (%s, NULL, %s, %s, %s, %s, NOW() + INTERVAL '48 hours', %s)""",
            (email, display_name, role, fleet_group, invite_token, created_by)
        )


def db_get_user_by_token(token):
    """Look up a user by invite token. Returns user dict or None."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT email, display_name, role, fleet_group, invite_expires FROM users WHERE invite_token = %s", (token,))
        row = cur.fetchone()
        if row:
            return {
                "email": row[0], "display_name": row[1], "role": row[2],
                "fleet_group": row[3], "invite_expires": row[4],
            }
        return None


def db_set_password_and_clear_token(email, password_hash):
    """Set password and clear the invite token."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET password_hash = %s, invite_token = NULL, invite_expires = NULL, updated_at = NOW() WHERE email = %s",
            (password_hash, email)
        )


def db_set_invite_token(email, token):
    """Set or refresh an invite/reset token (48hr expiry)."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET invite_token = %s, invite_expires = NOW() + INTERVAL '48 hours', password_hash = NULL, updated_at = NOW() WHERE email = %s",
            (token, email)
        )


def db_update_user(email, **kwargs):
    with get_db() as conn:
        cur = conn.cursor()
        sets = []
        vals = []
        for key in ("display_name", "role", "fleet_group", "password_hash", "updated_by"):
            if key in kwargs and kwargs[key] is not None:
                sets.append(f"{key} = %s")
                vals.append(kwargs[key])
        if sets:
            sets.append("updated_at = NOW()")
            vals.append(email)
            cur.execute(f"UPDATE users SET {', '.join(sets)} WHERE email = %s", vals)


def db_delete_user(email):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM users WHERE email = %s", (email,))


def db_user_exists(email):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM users WHERE email = %s", (email,))
        return cur.fetchone() is not None


def db_user_count():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM users")
        return cur.fetchone()[0]


# ═══════════════════════════════════════════════════════════════════════════════
# REVIEW CRUD
# ═══════════════════════════════════════════════════════════════════════════════

def _row_to_review(row):
    return {
        "id": row[0],
        "period": row[1],
        "label": row[2],
        "status": row[3],
        "deadline": str(row[4]) if row[4] else None,
        "created_by": row[5],
        "created_at": row[6].isoformat() if row[6] else None,
        "processed_at": row[7].isoformat() if row[7] else None,
        "notifications_sent_at": row[8].isoformat() if row[8] else None,
        "completed_at": row[9].isoformat() if row[9] else None,
    }


def db_list_reviews():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, period, label, status, deadline, created_by, created_at, processed_at, notifications_sent_at, completed_at FROM reviews ORDER BY period DESC")
        return [_row_to_review(r) for r in cur.fetchall()]


def db_get_review(period):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, period, label, status, deadline, created_by, created_at, processed_at, notifications_sent_at, completed_at FROM reviews WHERE period = %s", (period,))
        row = cur.fetchone()
        return _row_to_review(row) if row else None


def db_get_active_review():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, period, label, status, deadline, created_by, created_at, processed_at, notifications_sent_at, completed_at FROM reviews WHERE status = 'in_review' ORDER BY period DESC LIMIT 1")
        row = cur.fetchone()
        if row:
            return _row_to_review(row)
        # Fall back to latest
        cur.execute("SELECT id, period, label, status, deadline, created_by, created_at, processed_at, notifications_sent_at, completed_at FROM reviews ORDER BY period DESC LIMIT 1")
        row = cur.fetchone()
        return _row_to_review(row) if row else None


def db_get_review_id(period):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM reviews WHERE period = %s", (period,))
        row = cur.fetchone()
        return row[0] if row else None


def db_create_review(period, label, deadline, created_by):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO reviews (period, label, status, deadline, created_by, processed_at)
               VALUES (%s, %s, 'in_review', %s, %s, NOW())
               ON CONFLICT (period) DO UPDATE SET
                 label = EXCLUDED.label, status = 'in_review', deadline = EXCLUDED.deadline,
                 processed_at = NOW()
               RETURNING id""",
            (period, label, deadline or None, created_by)
        )
        return cur.fetchone()[0]


def db_update_review(period, **kwargs):
    with get_db() as conn:
        cur = conn.cursor()
        sets = []
        vals = []
        for key in ("status", "notifications_sent_at", "completed_at", "label", "deadline"):
            if key in kwargs:
                sets.append(f"{key} = %s")
                vals.append(kwargs[key])
        if sets:
            vals.append(period)
            cur.execute(f"UPDATE reviews SET {', '.join(sets)} WHERE period = %s", vals)


def db_delete_review(review_id):
    """Delete a review and all associated data (cascades via FK constraints)."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM reviews WHERE id = %s", (review_id,))


def db_complete_other_reviews(except_period):
    """Mark all in_review reviews as complete except the given period."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE reviews SET status = 'complete', completed_at = NOW() WHERE status = 'in_review' AND period != %s",
            (except_period,)
        )


# ═══════════════════════════════════════════════════════════════════════════════
# TRANSACTION CRUD
# ═══════════════════════════════════════════════════════════════════════════════

def db_insert_transactions(review_id, transactions):
    """Bulk insert transactions from anomaly report. Returns list of (db_id, txn_key)."""
    with get_db() as conn:
        cur = conn.cursor()
        # Clear existing transactions for this review
        cur.execute("DELETE FROM transactions WHERE review_id = %s", (review_id,))

        results = []
        for t in transactions:
            cur.execute(
                """INSERT INTO transactions
                   (review_id, transaction_date, transaction_time, vehicle_name, fleet_group,
                    driver, vendor, location, state, status, gallons, gross_price, net_price,
                    gross_ppg, product, odometer, card_no, sub_account, card_type, flag_count)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING id""",
                (review_id, t.get("transaction_date"), t.get("transaction_time"),
                 t.get("vehicle_name"), t.get("fleet_group"), t.get("driver"),
                 t.get("vendor"), t.get("location"), t.get("state"), t.get("status"),
                 t.get("gallons"), t.get("gross_price"), t.get("net_price"),
                 t.get("gross_ppg"), t.get("product"), t.get("odometer"),
                 t.get("card_no"), t.get("sub_account"), t.get("card_type", "vehicle"),
                 t.get("flag_count", 0))
            )
            txn_id = cur.fetchone()[0]
            txn_key = f"{t.get('vehicle_name')}_{t.get('transaction_date')}_{t.get('transaction_time')}"
            results.append((txn_id, txn_key))

            # Insert flags
            for flag in t.get("flags", []):
                cur.execute(
                    "INSERT INTO flags (transaction_id, flag_number, flag_name, reason) VALUES (%s,%s,%s,%s)",
                    (txn_id, flag.get("flag"), flag.get("flag_name"), flag.get("reason"))
                )

        return results


def db_insert_vehicle_mpg(review_id, mpg_data):
    """Bulk insert vehicle MPG summary."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM vehicle_mpg WHERE review_id = %s", (review_id,))
        for vname, m in mpg_data.items():
            cur.execute(
                """INSERT INTO vehicle_mpg
                   (review_id, vehicle_name, period_mpg, baseline_mpg, total_miles,
                    total_gallons, fill_count, pct_diff, flagged, reason, needs_review)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (review_id, vname, m.get("period_mpg"), m.get("baseline_mpg"),
                 m.get("total_miles"), m.get("total_gallons"), m.get("fill_count"),
                 m.get("pct_diff"), m.get("flagged", False), m.get("reason"),
                 m.get("needs_review", False))
            )


# ═══════════════════════════════════════════════════════════════════════════════
# DECISION CRUD
# ═══════════════════════════════════════════════════════════════════════════════

def db_save_decision(review_id, txn_key, fleet_group, action, reason, reviewer):
    """Save or update a decision. Uses txn_key to find the transaction."""
    with get_db() as conn:
        cur = conn.cursor()
        # Parse txn_key to find the transaction
        parts = txn_key.rsplit("_", 2)
        if len(parts) >= 3:
            vname = parts[0]
            tdate = parts[1]
            ttime = parts[2]
            cur.execute(
                "SELECT id FROM transactions WHERE review_id = %s AND vehicle_name = %s AND transaction_date = %s AND transaction_time = %s LIMIT 1",
                (review_id, vname, tdate, ttime)
            )
        else:
            cur.execute("SELECT id FROM transactions WHERE review_id = %s LIMIT 0", (review_id,))

        row = cur.fetchone()
        txn_id = row[0] if row else None

        # Upsert decision
        if txn_id:
            cur.execute("DELETE FROM decisions WHERE review_id = %s AND transaction_id = %s", (review_id, txn_id))
            cur.execute(
                "INSERT INTO decisions (review_id, transaction_id, fleet_group, action, reason, reviewer) VALUES (%s,%s,%s,%s,%s,%s)",
                (review_id, txn_id, fleet_group, action, reason, reviewer)
            )
        else:
            # Fallback: store without transaction_id (for temp/declined cards)
            cur.execute(
                "INSERT INTO decisions (review_id, fleet_group, action, reason, reviewer) VALUES (%s,%s,%s,%s,%s)",
                (review_id, fleet_group, action, reason, reviewer)
            )


def db_get_decisions(review_id):
    """Get all decisions for a review as a nested dict matching the JSON format."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT d.fleet_group, t.vehicle_name, t.transaction_date, t.transaction_time,
                      d.action, d.reason, d.reviewer, d.reviewed_at
               FROM decisions d
               LEFT JOIN transactions t ON d.transaction_id = t.id
               WHERE d.review_id = %s""",
            (review_id,)
        )
        decisions = {}
        for row in cur.fetchall():
            group = row[0] or "Unknown"
            if group not in decisions:
                decisions[group] = {}
            if row[1] and row[2] and row[3]:
                txn_key = f"{row[1]}_{row[2]}_{row[3]}"
            else:
                txn_key = f"_decision_{row[6]}_{row[7].isoformat() if row[7] else ''}"
            decisions[group][txn_key] = {
                "action": row[4],
                "reason": row[5],
                "reviewer": row[6],
                "timestamp": row[7].isoformat() if row[7] else None,
            }

        # Add group submissions
        cur.execute(
            "SELECT fleet_group, manager_name, submitted_by, submitted_at FROM group_submissions WHERE review_id = %s",
            (review_id,)
        )
        for row in cur.fetchall():
            group = row[0]
            if group not in decisions:
                decisions[group] = {}
            decisions[group]["_submission"] = {
                "manager_name": row[1],
                "submitted_by": row[2],
                "submitted_at": row[3].isoformat() if row[3] else None,
                "status": "submitted",
            }

        # Add admin approval
        cur.execute(
            "SELECT admin_name, approved_by, approved_at FROM admin_approvals WHERE review_id = %s",
            (review_id,)
        )
        row = cur.fetchone()
        if row:
            decisions["_admin_approval"] = {
                "admin_name": row[0],
                "approved_by": row[1],
                "approved_at": row[2].isoformat() if row[2] else None,
                "status": "approved",
            }

        return decisions


def db_save_group_submission(review_id, fleet_group, manager_name, submitted_by):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO group_submissions (review_id, fleet_group, manager_name, submitted_by)
               VALUES (%s,%s,%s,%s)
               ON CONFLICT (review_id, fleet_group) DO UPDATE SET
                 manager_name = EXCLUDED.manager_name, submitted_by = EXCLUDED.submitted_by,
                 submitted_at = NOW()""",
            (review_id, fleet_group, manager_name, submitted_by)
        )


def db_build_report(period):
    """Reconstruct the full anomaly report from database tables."""
    review = db_get_review(period)
    if not review:
        return None
    review_id = review["id"]

    with get_db() as conn:
        cur = conn.cursor()

        # ── Transactions with flags ──────────────────────────────────────────
        cur.execute(
            """SELECT id, transaction_date, transaction_time, vehicle_name, fleet_group,
                      driver, vendor, location, state, status, gallons, gross_price, net_price,
                      gross_ppg, product, odometer, card_no, sub_account, card_type, flag_count
               FROM transactions WHERE review_id = %s ORDER BY id""",
            (review_id,)
        )
        txn_rows = cur.fetchall()

        # Fetch all flags for this review in one query
        cur.execute(
            """SELECT f.transaction_id, f.flag_number, f.flag_name, f.reason
               FROM flags f JOIN transactions t ON f.transaction_id = t.id
               WHERE t.review_id = %s ORDER BY f.transaction_id, f.flag_number""",
            (review_id,)
        )
        flags_by_txn = {}
        for row in cur.fetchall():
            flags_by_txn.setdefault(row[0], []).append({
                "flag": row[1], "flag_name": row[2], "reason": row[3]
            })

        transactions = []
        temporary_cards = []
        declined_transactions = []

        for row in txn_rows:
            txn_id = row[0]
            txn = {
                "transaction_date": str(row[1]) if row[1] else None,
                "transaction_time": str(row[2]) if row[2] else None,
                "vehicle_name": row[3],
                "fleet_group": row[4],
                "driver": row[5],
                "vendor": row[6],
                "location": row[7],
                "state": row[8],
                "status": row[9],
                "gallons": float(row[10]) if row[10] is not None else None,
                "gross_price": float(row[11]) if row[11] is not None else None,
                "net_price": float(row[12]) if row[12] is not None else None,
                "gross_ppg": float(row[13]) if row[13] is not None else None,
                "product": row[14],
                "odometer": float(row[15]) if row[15] is not None else None,
                "card_no": row[16],
                "sub_account": row[17],
                "flags": flags_by_txn.get(txn_id, []),
                "flag_count": row[19] or 0,
            }
            card_type = row[18] or "vehicle"
            if card_type == "temporary":
                temporary_cards.append(txn)
            elif card_type == "declined":
                declined_transactions.append(txn)
            else:
                transactions.append(txn)

        # ── Vehicle MPG summary ──────────────────────────────────────────────
        cur.execute(
            """SELECT vehicle_name, period_mpg, baseline_mpg, total_miles, total_gallons,
                      fill_count, pct_diff, flagged, reason, needs_review
               FROM vehicle_mpg WHERE review_id = %s""",
            (review_id,)
        )
        mpg_summary = {}
        for row in cur.fetchall():
            mpg_summary[row[0]] = {
                "period_mpg": float(row[1]) if row[1] is not None else None,
                "baseline_mpg": float(row[2]) if row[2] is not None else None,
                "total_miles": int(row[3]) if row[3] is not None else None,
                "total_gallons": float(row[4]) if row[4] is not None else None,
                "fill_count": int(row[5]) if row[5] is not None else None,
                "pct_diff": float(row[6]) if row[6] is not None else None,
                "flagged": row[7] or False,
                "reason": row[8],
            }

        # ── Build group_summary ──────────────────────────────────────────────
        group_summary = {}
        for txn in transactions:
            g = txn.get("fleet_group")
            if not g:
                continue
            if g not in group_summary:
                group_summary[g] = {
                    "total_txns": 0, "flagged_txns": 0,
                    "total_spend": 0.0, "flag_types": set(),
                    "vehicles_flagged_mpg": [],
                }
            gs = group_summary[g]
            gs["total_txns"] += 1
            if txn["flag_count"] > 0:
                gs["flagged_txns"] += 1
            gs["total_spend"] += txn.get("net_price") or txn.get("gross_price") or 0.0
            for flag in txn.get("flags", []):
                gs["flag_types"].add(flag.get("flag"))

        # Add MPG-flagged vehicles to their groups
        for vname, m in mpg_summary.items():
            if m.get("flagged"):
                # Find which group this vehicle belongs to
                for txn in transactions:
                    if txn["vehicle_name"] == vname:
                        g = txn.get("fleet_group")
                        if g and g in group_summary:
                            if vname not in group_summary[g]["vehicles_flagged_mpg"]:
                                group_summary[g]["vehicles_flagged_mpg"].append(vname)
                        break

        # Convert sets to sorted lists for JSON serialization
        for gs in group_summary.values():
            gs["flag_types"] = sorted(gs["flag_types"])
            gs["total_spend"] = round(gs["total_spend"], 2)

        # ── Build summary ────────────────────────────────────────────────────
        total_flags = sum(t["flag_count"] for t in transactions)
        flags_by_type = {}
        flag_names = {
            1: "flag_1_fuel_efficiency_vehicle_level",
            2: "flag_2_cost_per_gallon_outlier",
            3: "flag_3_odometer_issue",
            4: "flag_4_unusually_small_fill",
            5: "flag_5_high_frequency_fills",
            6: "flag_6_wrong_fuel_type",
        }
        for txn in transactions:
            for flag in txn.get("flags", []):
                key = flag_names.get(flag.get("flag"), f"flag_{flag.get('flag')}_unknown")
                flags_by_type[key] = flags_by_type.get(key, 0) + 1

        summary = {
            "total_vehicle_transactions_analyzed": len(transactions),
            "total_flagged_transactions": sum(1 for t in transactions if t["flag_count"] > 0),
            "total_flags": total_flags,
            "flags_by_type": flags_by_type,
            "vehicles_flagged_for_mpg": [v for v, m in mpg_summary.items() if m.get("flagged")],
            "temporary_card_transactions": len(temporary_cards),
            "declined_transactions": len(declined_transactions),
        }

        return {
            "summary": summary,
            "group_summary": group_summary,
            "transactions": transactions,
            "temporary_cards": temporary_cards,
            "declined_transactions": declined_transactions,
            "mpg_summary_by_vehicle": mpg_summary,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# FLAG SETTINGS CRUD
# ═══════════════════════════════════════════════════════════════════════════════

# Default flag configuration — used when no setting exists in the database.
FLAG_DEFAULTS = {
    1: {"enabled": True, "threshold_pct": 20},
    2: {"enabled": True, "threshold_pct": 15},
    3: {"enabled": True},
    4: {"enabled": True, "threshold_pct": 25},
    5: {"enabled": True, "fill_count": 3, "window_hours": 24},
    6: {"enabled": True, "allowed_fuel_types": ["Regular Unleaded"]},
}


def db_get_flag_settings():
    """Return all flag settings as a dict: {(fleet_group, flag_number): {enabled, config}}."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT fleet_group, flag_number, enabled, config FROM flag_settings")
        settings = {}
        for row in cur.fetchall():
            group = row[0]  # None = global default
            settings[(group, row[1])] = {"enabled": row[2], "config": row[3] or {}}
        return settings


def db_get_global_settings():
    """Return global defaults from DB, merged with hardcoded defaults."""
    settings = {}
    for flag_num, defaults in FLAG_DEFAULTS.items():
        settings[flag_num] = dict(defaults)

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT flag_number, enabled, config FROM flag_settings WHERE fleet_group IS NULL")
        for row in cur.fetchall():
            merged = dict(FLAG_DEFAULTS.get(row[0], {}))
            merged["enabled"] = row[1]
            merged.update(row[2] or {})
            settings[row[0]] = merged

    return settings


def db_get_group_overrides(fleet_group):
    """Return overrides for a specific group (not merged with globals)."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT flag_number, enabled, config FROM flag_settings WHERE fleet_group = %s", (fleet_group,))
        overrides = {}
        for row in cur.fetchall():
            overrides[row[0]] = {"enabled": row[1], "config": row[2] or {}}
        return overrides


def db_get_all_group_overrides():
    """Return all per-group overrides as {group_name: {flag_num: {enabled, config}}}."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT fleet_group, flag_number, enabled, config FROM flag_settings WHERE fleet_group IS NOT NULL")
        result = {}
        for row in cur.fetchall():
            group = row[0]
            if group not in result:
                result[group] = {}
            result[group][row[1]] = {"enabled": row[2], "config": row[3] or {}}
        return result


def db_save_flag_setting(fleet_group, flag_number, enabled, config, updated_by):
    """Upsert a flag setting. fleet_group=None for global defaults."""
    with get_db() as conn:
        cur = conn.cursor()
        # Delete existing, then insert (works with COALESCE-based unique index)
        if fleet_group is None:
            cur.execute("DELETE FROM flag_settings WHERE fleet_group IS NULL AND flag_number = %s", (flag_number,))
        else:
            cur.execute("DELETE FROM flag_settings WHERE fleet_group = %s AND flag_number = %s", (fleet_group, flag_number))
        cur.execute(
            """INSERT INTO flag_settings (fleet_group, flag_number, enabled, config, updated_by, updated_at)
               VALUES (%s, %s, %s, %s::jsonb, %s, NOW())""",
            (fleet_group, flag_number, enabled, json.dumps(config), updated_by)
        )


def db_delete_group_override(fleet_group, flag_number):
    """Remove a per-group override (reverts to global default)."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM flag_settings WHERE fleet_group = %s AND flag_number = %s",
                    (fleet_group, flag_number))


def db_resolve_flag_config(fleet_group=None):
    """Resolve the effective flag config for a group: global defaults + group overrides.
    Returns {flag_number: {enabled, ...config params}}."""
    globals_ = db_get_global_settings()

    if not fleet_group:
        return globals_

    overrides = db_get_group_overrides(fleet_group)
    resolved = {}
    for flag_num in FLAG_DEFAULTS:
        base = dict(globals_.get(flag_num, FLAG_DEFAULTS[flag_num]))
        if flag_num in overrides:
            override = overrides[flag_num]
            base["enabled"] = override["enabled"]
            base.update(override.get("config", {}))
        resolved[flag_num] = base

    return resolved


def db_save_admin_approval(review_id, admin_name, approved_by):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO admin_approvals (review_id, admin_name, approved_by)
               VALUES (%s,%s,%s)
               ON CONFLICT (review_id) DO UPDATE SET
                 admin_name = EXCLUDED.admin_name, approved_by = EXCLUDED.approved_by,
                 approved_at = NOW()""",
            (review_id, admin_name, approved_by)
        )
