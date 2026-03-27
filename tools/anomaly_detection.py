"""
Fuel Review — Anomaly Detection Engine (Phase 2)
Runs 6 flag checks against Corpay transactions matched to Fleetio vehicles.
Uses fill-to-fill odometer deltas for MPG calculation.
Outputs a structured JSON report for use in the approval workflow UI.
"""

import json
import os
from datetime import datetime, timedelta
from collections import defaultdict

# ─── Default Paths (used when run from command line) ─────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DEFAULT_CORPAY_FILE = os.path.join(DATA_DIR, "Corpay_Transactions.xlsx")
DEFAULT_BASELINES_FILE = os.path.join(DATA_DIR, "mpg_baselines.json")
DEFAULT_OUTPUT_FILE = os.path.join(DATA_DIR, "anomaly_report.json")


# ─── Load Corpay Data ────────────────────────────────────────────────────────
def load_corpay(corpay_file=None):
    import openpyxl
    wb = openpyxl.load_workbook(corpay_file or DEFAULT_CORPAY_FILE, read_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    headers = rows[0]
    data = []
    for r in rows[1:]:
        row = dict(zip(headers, r))
        data.append(row)
    wb.close()
    return data


# ─── Load Fleetio Vehicles ───────────────────────────────────────────────────
def load_fleetio_vehicles():
    from urllib.request import Request, urlopen
    api_key = os.environ.get("FLEETIO_API_KEY", "")
    account_token = os.environ.get("FLEETIO_ACCOUNT_TOKEN", "")

    if not api_key:
        env_path = os.path.join(BASE_DIR, ".env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    if line.startswith("FLEETIO_API_KEY="):
                        api_key = line.strip().split("=", 1)[1]
                    elif line.startswith("FLEETIO_ACCOUNT_TOKEN="):
                        account_token = line.strip().split("=", 1)[1]

    base = "https://secure.fleetio.com/api/v1"
    all_vehicles = []
    cursor = ""
    while True:
        url = f"{base}/vehicles?per_page=100"
        if cursor:
            url += f"&start_cursor={cursor}"
        req = Request(url)
        req.add_header("Authorization", f"Token {api_key}")
        req.add_header("Account-Token", account_token)
        req.add_header("Content-Type", "application/json")
        with urlopen(req) as resp:
            data = json.loads(resp.read())
        records = data if isinstance(data, list) else data.get("records", data.get("data", []))
        if not records:
            break
        all_vehicles.extend(records)
        if isinstance(data, dict) and data.get("next_cursor"):
            cursor = data["next_cursor"]
        else:
            break
    return all_vehicles


# ─── Match Corpay → Fleetio ──────────────────────────────────────────────────
def match_transactions(corpay_rows, fleetio_vehicles):
    """Returns (vehicle_txns, equipment_txns, temp_txns, declined_txns, unmatched)."""
    fleetio_by_suffix = {}
    for v in fleetio_vehicles:
        name = v.get("name", "")
        parts = name.split("-")
        if parts:
            suffix = parts[-1].zfill(4)
            fleetio_by_suffix[suffix] = v

    vehicle_txns = []
    equipment_txns = []
    temp_txns = []
    declined_txns = []
    unmatched = []

    for row in corpay_rows:
        first_name = row.get("Cardholder First Name")
        status = row.get("Status", "")

        if first_name is None:
            continue

        if first_name in ("UNIT", "EQUIPMENT"):
            if status == "DECLINED":
                declined_txns.append({"row": row, "card_type": "equipment", "vehicle": None})
            else:
                equipment_txns.append(row)
            continue

        if first_name != "VEHICLE":
            continue

        last_name = row.get("Cardholder Last Name", "") or ""

        if status == "DECLINED":
            parts = last_name.strip().split()
            suffix = parts[-1].zfill(4) if parts else ""
            vehicle = fleetio_by_suffix.get(suffix)
            declined_txns.append({"row": row, "card_type": "vehicle", "vehicle": vehicle})
            continue

        if "TEMPORARY" in last_name or "TEMP" in last_name:
            temp_txns.append(row)
            continue

        parts = last_name.strip().split()
        suffix = parts[-1].zfill(4) if parts else ""
        vehicle = fleetio_by_suffix.get(suffix)

        if vehicle:
            vehicle_txns.append({"row": row, "vehicle": vehicle})
        else:
            unmatched.append(row)

    return vehicle_txns, equipment_txns, temp_txns, declined_txns, unmatched


# ─── Helpers ─────────────────────────────────────────────────────────────────
def safe_float(val, default=None):
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def parse_datetime(date_str, time_str):
    if not date_str:
        return None
    try:
        if time_str:
            return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
        return datetime.strptime(date_str, "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def categorize_fuel(product_desc):
    desc = (product_desc or "").upper()
    if "DIESEL" in desc:
        return "Diesel"
    if "PREMIUM" in desc:
        return "Premium"
    if "PLUS" in desc or "MIDGRADE" in desc or "89" in desc:
        return "Midgrade"
    if "UNLEADED" in desc or "REGULAR" in desc or "86" in desc or "87" in desc:
        return "Regular Unleaded"
    if "E85" in desc:
        return "E85"
    return "Unknown"


def infer_group_from_subaccount(sub_account):
    mapping = {
        "AEROSEAL CARTX": "Robert Tamayo",
        "AEROSEAL CO": "Campbell Johnson",
        "AEROSEAL OH": "Jason Riley",
        "AEROSEAL SUMSC": "Caleb Severance",
        "AEROSEAL MD": "Pat Richardson",
        "AEROSEAL GA": "Corey Dean",
        "AEROSEAL LAS": "Robert Kvenvik",
        "AEROSEAL AZ": "Max Zimmerman",
        "AEROSEAL NM": "Max Zimmerman",
        "AEROSEAL MAGTX": "Robert Tamayo",
        "AEROSEAL ODETX": "Robert Tamayo",
        "AEROSEAL SATX": "Robert Tamayo",
    }
    return mapping.get(sub_account, "Unassigned")


# ─── Build Fill Events (combine split fills) ────────────────────────────────
def build_fill_events(vehicle_txns):
    """
    Groups transactions by vehicle, sorts chronologically, and combines
    split fills (same vehicle, same odometer, within 10 minutes) into
    single fill events. Returns dict: vehicle_name -> [fill_events].
    Each fill event has: odometer, total_gallons, datetime, transactions[].
    """
    # Group by vehicle
    by_vehicle = defaultdict(list)
    for item in vehicle_txns:
        vname = item["vehicle"].get("name", "")
        row = item["row"]
        dt = parse_datetime(row.get("Transaction Date - Date"), row.get("Transaction Date - Time"))
        odo = safe_float(row.get("Odometer"))
        gallons = safe_float(row.get("Unit/Gallons"))
        by_vehicle[vname].append({
            "dt": dt,
            "odometer": odo,
            "gallons": gallons or 0,
            "item": item,
        })

    fill_events = {}
    for vname, fills in by_vehicle.items():
        fills.sort(key=lambda x: x["dt"] or datetime.min)

        events = []
        i = 0
        while i < len(fills):
            current = fills[i]
            # Start a new fill event
            event = {
                "odometer": current["odometer"],
                "total_gallons": current["gallons"],
                "dt": current["dt"],
                "transactions": [current["item"]],
            }

            # Look ahead for split fills: same odometer, within 10 minutes
            j = i + 1
            while j < len(fills):
                nxt = fills[j]
                same_odo = (current["odometer"] is not None
                            and nxt["odometer"] is not None
                            and current["odometer"] == nxt["odometer"])
                within_window = (current["dt"] is not None
                                 and nxt["dt"] is not None
                                 and (nxt["dt"] - current["dt"]).total_seconds() <= 600)
                if same_odo and within_window:
                    event["total_gallons"] += nxt["gallons"]
                    event["transactions"].append(nxt["item"])
                    j += 1
                else:
                    break

            events.append(event)
            i = j

        fill_events[vname] = events

    return fill_events


# ─── FLAG 1: Vehicle-Level Period MPG ────────────────────────────────────────
def compute_vehicle_period_mpg(fill_events, baselines):
    """
    For each vehicle, calculate period MPG using total miles and total gallons:
      Period MPG = (Max Odometer - Min Odometer) / Sum(All Gallons)

    This is robust regardless of partial fills because over the full period,
    total gallons purchased ≈ total gallons consumed (bounded by tank size error).

    Returns dict: vehicle_name -> {period_mpg, total_miles, total_gallons,
                                    baseline_mpg, pct_diff, flagged, ...}
    """
    results = {}

    for vname, events in fill_events.items():
        baseline_data = baselines.get("vehicles", {}).get(vname)
        baseline_mpg = baseline_data.get("mpg_combined") if baseline_data else None

        # Build a clean chronological odometer sequence:
        # Walk through fills in order, keep only readings that increase from previous.
        # This filters out bad entries (typos, wrong numbers) automatically.
        clean_sequence = []  # list of (odo, gallons)
        total_gallons = 0
        fill_count = 0

        for event in events:
            odo = event["odometer"]
            gallons = event["total_gallons"]
            # Skip Corpay default (1.0 gal with no odometer) from totals
            is_default = (gallons == 1.0 and odo is None)
            if gallons and gallons > 0 and not is_default:
                total_gallons += gallons
                fill_count += 1
            if odo is not None and odo > 0:
                if not clean_sequence or odo > clean_sequence[-1][0]:
                    clean_sequence.append((odo, gallons or 0))
                # else: skip — odometer didn't increase (bad entry)

        if len(clean_sequence) < 2 or total_gallons <= 0:
            results[vname] = {
                "period_mpg": None,
                "total_miles": None,
                "total_gallons": round(total_gallons, 1),
                "fill_count": fill_count,
                "baseline_mpg": baseline_mpg,
                "pct_diff": None,
                "flagged": False,
                "reason": None,
            }
            continue

        # Use first and last of the clean sequence
        first_odo = clean_sequence[0][0]
        last_odo = clean_sequence[-1][0]
        total_miles = last_odo - first_odo

        # Sum gallons from the 2nd fill onward in the clean sequence,
        # because the first fill's gallons went into a tank with unknown starting level.
        # This gives us: miles driven from fill 1 to fill N, fueled by gallons at fills 2..N.
        clean_gallons = sum(g for _, g in clean_sequence[1:])

        # Fall back to total gallons if clean_gallons is zero or too small
        if clean_gallons <= 0:
            clean_gallons = total_gallons

        if total_miles <= 0:
            results[vname] = {
                "period_mpg": None,
                "total_miles": 0,
                "total_gallons": round(total_gallons, 1),
                "fill_count": fill_count,
                "baseline_mpg": baseline_mpg,
                "pct_diff": None,
                "flagged": False,
                "reason": None,
            }
            continue

        period_mpg = total_miles / clean_gallons

        # Determine if flagged (> 20% below baseline)
        flagged = False
        pct_diff = None
        reason = None
        if baseline_mpg and baseline_mpg > 0:
            pct_diff = round(((period_mpg - baseline_mpg) / baseline_mpg) * 100, 1)
            if period_mpg < baseline_mpg * 0.80:
                flagged = True
                reason = (f"This vehicle averaged {period_mpg:.1f} MPG over {total_miles:,.0f} miles "
                          f"and {clean_gallons:.1f} gallons this period, which is {abs(pct_diff):.0f}% "
                          f"below the expected baseline of {baseline_mpg} MPG.")

        # Sanity check: if period MPG is still unreasonable (> 40 for these vehicles),
        # there may be a large odometer gap from a bad entry we couldn't filter.
        # Mark as insufficient data rather than reporting a bogus number.
        if period_mpg > 40:
            results[vname] = {
                "period_mpg": None,
                "total_miles": round(total_miles),
                "total_gallons": round(clean_gallons, 1),
                "fill_count": fill_count,
                "baseline_mpg": baseline_mpg,
                "pct_diff": None,
                "flagged": False,
                "reason": "Period MPG unreasonably high — likely contains odometer entry errors. Requires manual review.",
                "needs_review": True,
            }
            continue

        results[vname] = {
            "period_mpg": round(period_mpg, 1),
            "total_miles": round(total_miles),
            "total_gallons": round(clean_gallons, 1),
            "fill_count": fill_count,
            "first_odo": first_odo,
            "last_odo": last_odo,
            "baseline_mpg": baseline_mpg,
            "pct_diff": pct_diff,
            "flagged": flagged,
            "reason": reason,
        }

    return results


# ─── FLAG 3: Odometer Issues (per-transaction) ──────────────────────────────
def check_odometer_issues(fill_events):
    """
    Check for missing or decreasing odometer readings per transaction.
    Returns dict: id(txn_item) -> flag dict (or None).
    """
    txn_odo_flags = {}

    for vname, events in fill_events.items():
        prev_odo = None
        for event in events:
            odo = event["odometer"]
            flag = None

            if odo is None:
                flag = {
                    "flag": 3,
                    "flag_name": "Missing Odometer",
                    "reason": "No odometer reading was entered at the time of this fill.",
                }
            elif prev_odo is not None and odo < prev_odo:
                flag = {
                    "flag": 3,
                    "flag_name": "Invalid Odometer Entry",
                    "reason": (f"Odometer reading of {odo:,.0f} is lower than the previous "
                               f"reading of {prev_odo:,.0f}. This entry appears incorrect."),
                    "current_odo": odo,
                    "previous_odo": prev_odo,
                }

            # Assign flag to all transactions in this fill event
            for txn_item in event["transactions"]:
                if flag:
                    txn_odo_flags[id(txn_item)] = flag

            # Update prev_odo only with valid (non-decreasing) readings
            if odo is not None and (prev_odo is None or odo >= prev_odo):
                prev_odo = odo

    return txn_odo_flags


# ─── FLAG 2: Cost Per Gallon Outlier ─────────────────────────────────────────
def check_flag2(txn, fuel_type_medians):
    gross_ppg = safe_float(txn.get("Gross PPU/PPG"))
    product = (txn.get("Product Description") or "").upper()

    if not gross_ppg or gross_ppg <= 0:
        return None

    fuel_cat = categorize_fuel(product)
    median = fuel_type_medians.get(fuel_cat)
    if not median or median <= 0:
        return None

    threshold = median * 1.15
    if gross_ppg > threshold:
        pct_above = ((gross_ppg - median) / median) * 100
        return {
            "flag": 2,
            "flag_name": "Cost Per Gallon Outlier",
            "reason": (f"Cost per gallon was ${gross_ppg:.3f}, significantly above the "
                       f"monthly median of ${median:.3f} for {fuel_cat}."),
            "actual_ppg": round(gross_ppg, 3),
            "median_ppg": round(median, 3),
            "fuel_category": fuel_cat,
            "pct_above": round(pct_above, 1),
        }
    return None


# ─── FLAG 4: Unusually Small Fill ────────────────────────────────────────────
def check_flag4(gallons, vehicle_avg_fill, vname):
    if not gallons or gallons <= 0:
        return None

    avg = vehicle_avg_fill.get(vname)
    if not avg or avg <= 0:
        return None

    threshold = avg * 0.25
    if gallons < threshold:
        return {
            "flag": 4,
            "flag_name": "Unusually Small Fill",
            "reason": (f"This fill of {gallons:.1f} gallons is unusually small compared to "
                       f"this vehicle's average fill of {avg:.1f} gallons. Could indicate "
                       f"personal vehicle use."),
            "gallons": round(gallons, 1),
            "avg_gallons": round(avg, 1),
        }
    return None


# ─── FLAG 5: High Frequency Fills ───────────────────────────────────────────
def check_flag5_bulk(vehicle_txns):
    """Flag drivers with more than 3 fills within any 24-hour window."""
    driver_txns = defaultdict(list)
    for item in vehicle_txns:
        row = item["row"]
        driver = row.get("Spender") or row.get("Cardholder Full Name") or "Unknown"
        dt = parse_datetime(row.get("Transaction Date - Date"), row.get("Transaction Date - Time"))
        if not dt:
            continue
        driver_txns[driver].append({"dt": dt, "item": item})

    flags = {}
    for driver, txns in driver_txns.items():
        txns.sort(key=lambda x: x["dt"])
        for i in range(len(txns)):
            window_start = txns[i]["dt"]
            window_end = window_start + timedelta(hours=24)
            window_txns = [t for t in txns if window_start <= t["dt"] <= window_end]
            if len(window_txns) > 3:
                dates = [t["dt"].strftime("%Y-%m-%d %H:%M") for t in window_txns]
                key = (driver, window_start.strftime("%Y-%m-%d"))
                if key not in flags:
                    flags[key] = {
                        "flag": 5,
                        "flag_name": "High Frequency Fills",
                        "reason": (f"Driver {driver} had {len(window_txns)} fills within "
                                   f"24 hours on {', '.join(dates[:5])}{'...' if len(dates) > 5 else ''}. "
                                   f"This pattern may warrant review."),
                        "driver": driver,
                        "fill_count": len(window_txns),
                        "dates": dates,
                    }
    return flags


# ─── FLAG 6: Wrong Fuel Type ────────────────────────────────────────────────
def check_flag6(txn, vehicle):
    product = (txn.get("Product Description") or "").upper()
    if not product:
        return None

    fleetio_fuel = (vehicle.get("fuel_type_name") or "").lower()
    txn_date = txn.get("Transaction Date - Date", "")

    is_diesel_vehicle = "diesel" in fleetio_fuel
    is_gas_vehicle = "gasoline" in fleetio_fuel or "flex" in fleetio_fuel

    product_is_diesel = "DIESEL" in product
    product_is_premium = "PREMIUM" in product
    product_is_plus = "PLUS" in product or "MIDGRADE" in product or "89 OCTANE" in product

    if is_diesel_vehicle and not product_is_diesel:
        return {
            "flag": 6,
            "flag_name": "Wrong Fuel Type",
            "reason": (f"This vehicle is designated {vehicle.get('fuel_type_name')} "
                       f"but was filled with {product} on {txn_date}."),
            "expected_fuel": vehicle.get("fuel_type_name"),
            "actual_fuel": product,
        }

    if is_gas_vehicle and product_is_diesel:
        return {
            "flag": 6,
            "flag_name": "Wrong Fuel Type",
            "reason": (f"This vehicle is designated {vehicle.get('fuel_type_name')} "
                       f"but was filled with {product} on {txn_date}."),
            "expected_fuel": vehicle.get("fuel_type_name"),
            "actual_fuel": product,
        }

    if is_gas_vehicle and (product_is_premium or product_is_plus):
        return {
            "flag": 6,
            "flag_name": "Wrong Fuel Type",
            "reason": (f"This vehicle is designated {vehicle.get('fuel_type_name')} "
                       f"but was filled with {product} on {txn_date}. "
                       f"Gas vehicles should use regular unleaded only."),
            "expected_fuel": vehicle.get("fuel_type_name"),
            "actual_fuel": product,
        }

    return None


# ─── Main ────────────────────────────────────────────────────────────────────
def run(corpay_file=None, baselines_file=None, output_file=None):
    """
    Run the full anomaly detection pipeline.
    All paths are optional — defaults to the standard data/ directory.
    Returns the report dict (also saves to output_file).
    """
    _corpay = corpay_file or DEFAULT_CORPAY_FILE
    _baselines = baselines_file or DEFAULT_BASELINES_FILE
    _output = output_file or DEFAULT_OUTPUT_FILE

    print("Loading Corpay transactions...")
    corpay_rows = load_corpay(_corpay)
    print(f"  {len(corpay_rows)} rows loaded")

    print("Loading Fleetio vehicles...")
    fleetio_vehicles = load_fleetio_vehicles()
    print(f"  {len(fleetio_vehicles)} vehicles loaded")

    print("Loading MPG baselines...")
    with open(_baselines) as f:
        baselines = json.load(f)
    print(f"  {len(baselines['vehicles'])} baselines loaded")

    print("Matching transactions...")
    vehicle_txns, equipment_txns, temp_txns, declined_txns, unmatched = match_transactions(
        corpay_rows, fleetio_vehicles
    )
    print(f"  Vehicle (matched): {len(vehicle_txns)}")
    print(f"  Equipment: {len(equipment_txns)}")
    print(f"  Temporary: {len(temp_txns)}")
    print(f"  Declined: {len(declined_txns)}")
    print(f"  Unmatched: {len(unmatched)}")

    # ── Build fill events (combine split fills) ──
    print("\nBuilding fill events (combining split fills)...")
    fill_events = build_fill_events(vehicle_txns)
    total_events = sum(len(e) for e in fill_events.values())
    total_txns_in_events = sum(len(ev["transactions"]) for evts in fill_events.values() for ev in evts)
    split_fills = sum(1 for evts in fill_events.values() for ev in evts if len(ev["transactions"]) > 1)
    print(f"  {total_events} fill events from {total_txns_in_events} transactions")
    print(f"  {split_fills} split fills detected and combined")

    # ── Compute vehicle-level period MPG (Flag 1) ──
    print("\nComputing vehicle-level period MPG...")
    mpg_results = compute_vehicle_period_mpg(fill_events, baselines)
    flagged_vehicles = {v: d for v, d in mpg_results.items() if d["flagged"]}
    print(f"  {len(mpg_results)} vehicles analyzed")
    print(f"  {len(flagged_vehicles)} vehicles flagged for low MPG")

    # ── Check odometer issues (Flag 3) ──
    print("  Checking odometer entries...")
    txn_odo_flags = check_odometer_issues(fill_events)
    print(f"  {len(txn_odo_flags)} transactions with odometer issues")

    # ── Pre-compute aggregates for other flags ──

    # Flag 2: Monthly median cost per gallon by fuel type
    fuel_prices = defaultdict(list)
    for item in vehicle_txns:
        row = item["row"]
        ppg = safe_float(row.get("Gross PPU/PPG"))
        product = (row.get("Product Description") or "").upper()
        if ppg and ppg > 0:
            fuel_prices[categorize_fuel(product)].append(ppg)

    fuel_type_medians = {}
    for cat, prices in fuel_prices.items():
        sorted_prices = sorted(prices)
        mid = len(sorted_prices) // 2
        if len(sorted_prices) % 2 == 0 and len(sorted_prices) > 0:
            fuel_type_medians[cat] = (sorted_prices[mid - 1] + sorted_prices[mid]) / 2
        elif sorted_prices:
            fuel_type_medians[cat] = sorted_prices[mid]

    print(f"\n  Fuel type medians ($/gal):")
    for cat, med in sorted(fuel_type_medians.items()):
        print(f"    {cat}: ${med:.3f} ({len(fuel_prices[cat])} transactions)")

    # Flag 4: Average fill size per vehicle (using fill events, not raw txns)
    # Exclude 1.0-gallon Corpay defaults (no odometer entered) from the average
    vehicle_avg_fill = {}
    for vname, events in fill_events.items():
        fill_sizes = [ev["total_gallons"] for ev in events
                      if ev["total_gallons"] > 0
                      and not (ev["total_gallons"] == 1.0 and ev["odometer"] is None)]
        if fill_sizes:
            vehicle_avg_fill[vname] = sum(fill_sizes) / len(fill_sizes)

    # Flag 5: High frequency fills
    print("  Checking high-frequency fills (Flag 5)...")
    flag5_results = check_flag5_bulk(vehicle_txns)

    # ── Assemble per-transaction flag results ──
    print("\nAssembling final report...")

    # Build flag5 lookup: driver+datetime -> flag
    flag5_by_driver_dt = defaultdict(list)
    for key, f5 in flag5_results.items():
        driver = f5["driver"]
        for d in f5["dates"]:
            flag5_by_driver_dt[(driver, d)].append(f5)

    flag_counts = defaultdict(int)
    flagged_groups = defaultdict(set)
    all_records = []

    for item in vehicle_txns:
        row = item["row"]
        vehicle = item["vehicle"]
        vname = vehicle.get("name", "")
        group = vehicle.get("group_name", "Unassigned")
        item_id = id(item)

        flags_for_txn = []

        # Flag 3: Odometer issues (per-transaction)
        odo_flag = txn_odo_flags.get(item_id)
        if odo_flag:
            flags_for_txn.append(odo_flag)

        # Flag 2: Cost per gallon
        f2 = check_flag2(row, fuel_type_medians)
        if f2:
            flags_for_txn.append(f2)

        # Flag 4: Small fill
        # Skip when gallons == 1.0 and no odometer — Corpay defaults to 1 gal
        # when mileage is not entered at the pump. Not a real fill amount.
        event_gallons = safe_float(row.get("Unit/Gallons"))
        odo_value = safe_float(row.get("Odometer"))
        is_corpay_default = (event_gallons == 1.0 and not odo_value)
        if not is_corpay_default:
            f4 = check_flag4(event_gallons, vehicle_avg_fill, vname)
            if f4:
                flags_for_txn.append(f4)

        # Flag 5: High frequency
        driver = row.get("Spender") or row.get("Cardholder Full Name") or "Unknown"
        dt = parse_datetime(row.get("Transaction Date - Date"), row.get("Transaction Date - Time"))
        if dt:
            dt_key = dt.strftime("%Y-%m-%d %H:%M")
            for f5 in flag5_by_driver_dt.get((driver, dt_key), []):
                flags_for_txn.append({
                    "flag": 5,
                    "flag_name": "High Frequency Fills",
                    "reason": f5["reason"],
                    "driver": driver,
                    "fill_count": f5["fill_count"],
                })
                break  # one flag5 per transaction

        # Flag 6: Wrong fuel type
        f6 = check_flag6(row, vehicle)
        if f6:
            flags_for_txn.append(f6)

        # Count flags
        for f in flags_for_txn:
            flag_counts[f["flag"]] += 1
            flagged_groups[group].add(f["flag"])

        txn_record = {
            "transaction_date": row.get("Transaction Date - Date"),
            "transaction_time": row.get("Transaction Date - Time"),
            "vehicle_name": vname,
            "fleet_group": group,
            "driver": driver,
            "vendor": row.get("Vendor") or row.get("Description"),
            "location": row.get("Address"),
            "state": row.get("State"),
            "status": row.get("Status"),
            "gallons": safe_float(row.get("Unit/Gallons")),
            "gross_price": safe_float(row.get("Gross Price")),
            "net_price": safe_float(row.get("Net Price")),
            "gross_ppg": safe_float(row.get("Gross PPU/PPG")),
            "product": row.get("Product Description"),
            "odometer": safe_float(row.get("Odometer")),
            "card_no": row.get("Card No."),
            "sub_account": row.get("Sub Account"),
            "flags": flags_for_txn,
            "flag_count": len(flags_for_txn),
        }
        all_records.append(txn_record)

    # ── Build declined transaction records ──
    declined_records = []
    for item in declined_txns:
        row = item["row"]
        vehicle = item.get("vehicle")
        declined_records.append({
            "transaction_date": row.get("Transaction Date - Date"),
            "transaction_time": row.get("Transaction Date - Time"),
            "vehicle_name": vehicle.get("name") if vehicle else None,
            "card_type": item["card_type"],
            "cardholder": row.get("Cardholder Full Name"),
            "driver": row.get("Spender") or row.get("Cardholder Full Name"),
            "vendor": row.get("Vendor") or row.get("Description"),
            "location": row.get("Address"),
            "state": row.get("State"),
            "gross_price": safe_float(row.get("Gross Price")),
            "card_no": row.get("Card No."),
            "sub_account": row.get("Sub Account"),
            "fleet_group": (vehicle.get("group_name", "Unassigned") if vehicle
                            else infer_group_from_subaccount(row.get("Sub Account"))),
        })

    # ── Build temporary card records ──
    temp_records = []
    for row in temp_txns:
        temp_records.append({
            "transaction_date": row.get("Transaction Date - Date"),
            "transaction_time": row.get("Transaction Date - Time"),
            "cardholder": row.get("Cardholder Full Name"),
            "driver": row.get("Spender") or row.get("Cardholder Full Name"),
            "vendor": row.get("Vendor") or row.get("Description"),
            "location": row.get("Address"),
            "state": row.get("State"),
            "status": row.get("Status"),
            "gallons": safe_float(row.get("Unit/Gallons")),
            "gross_price": safe_float(row.get("Gross Price")),
            "net_price": safe_float(row.get("Net Price")),
            "gross_ppg": safe_float(row.get("Gross PPU/PPG")),
            "product": row.get("Product Description"),
            "odometer": safe_float(row.get("Odometer")),
            "card_no": row.get("Card No."),
            "sub_account": row.get("Sub Account"),
            "fleet_group": infer_group_from_subaccount(row.get("Sub Account")),
        })

    # ── Group summary ──
    group_summary = {}
    for txn in all_records:
        g = txn["fleet_group"]
        if g not in group_summary:
            group_summary[g] = {"total_txns": 0, "flagged_txns": 0, "total_spend": 0,
                                "flag_types": set(), "vehicles_flagged_mpg": []}
        group_summary[g]["total_txns"] += 1
        group_summary[g]["total_spend"] += txn.get("net_price") or 0
        if txn["flag_count"] > 0:
            group_summary[g]["flagged_txns"] += 1
            for f in txn["flags"]:
                group_summary[g]["flag_types"].add(f["flag"])

    # Add vehicle-level Flag 1 to group summary
    for vname, mpg_data in mpg_results.items():
        if mpg_data["flagged"]:
            # Find which group this vehicle belongs to
            for item in vehicle_txns:
                if item["vehicle"].get("name") == vname:
                    g = item["vehicle"].get("group_name", "Unassigned")
                    if g in group_summary:
                        group_summary[g]["flag_types"].add(1)
                        if vname not in group_summary[g]["vehicles_flagged_mpg"]:
                            group_summary[g]["vehicles_flagged_mpg"].append(vname)
                    break

    for g in group_summary:
        group_summary[g]["flag_types"] = sorted(group_summary[g]["flag_types"])
        group_summary[g]["total_spend"] = round(group_summary[g]["total_spend"], 2)

    # ── MPG summary is now vehicle-level period MPG (already computed) ──
    mpg_summary = mpg_results

    # ── Build final report ──
    report = {
        "_metadata": {
            "generated": datetime.now().isoformat(),
            "corpay_file": "Corpay_Transactions.xlsx",
            "date_range": {
                "start": min((t["transaction_date"] for t in all_records if t["transaction_date"]), default=None),
                "end": max((t["transaction_date"] for t in all_records if t["transaction_date"]), default=None),
            },
            "methodology": {
                "mpg_calculation": "Vehicle-level period: (Max Odometer - Min Odometer) / Sum(All Gallons). Robust to partial fills.",
                "split_fills": "Consecutive fills with same odometer within 10 minutes are combined",
                "odometer_validation": "Missing or decreasing odometer readings flagged per transaction",
                "flag_1_note": "Flag 1 is a vehicle-level flag (not per-transaction). Vehicles whose period MPG is >20% below baseline are flagged.",
            },
        },
        "summary": {
            "total_vehicle_transactions_analyzed": len(all_records),
            "total_fill_events": total_events,
            "split_fills_combined": split_fills,
            "total_flagged_transactions": sum(1 for t in all_records if t["flag_count"] > 0),
            "total_flags": sum(flag_counts.values()),
            "flags_by_type": {
                "flag_1_fuel_efficiency_vehicle_level": len(flagged_vehicles),
                "flag_2_cost_per_gallon_outlier": flag_counts.get(2, 0),
                "flag_3_odometer_issue": flag_counts.get(3, 0),
                "flag_4_unusually_small_fill": flag_counts.get(4, 0),
                "flag_5_high_frequency_fills": flag_counts.get(5, 0),
                "flag_6_wrong_fuel_type": flag_counts.get(6, 0),
            },
            "vehicles_flagged_for_mpg": list(flagged_vehicles.keys()),
            "fuel_type_medians": {k: round(v, 3) for k, v in fuel_type_medians.items()},
            "equipment_transactions_excluded": len(equipment_txns),
            "temporary_card_transactions": len(temp_records),
            "declined_transactions": len(declined_records),
        },
        "mpg_summary_by_vehicle": mpg_summary,
        "group_summary": group_summary,
        "transactions": all_records,
        "temporary_cards": temp_records,
        "declined_transactions": declined_records,
    }

    with open(_output, "w") as f:
        json.dump(report, f, indent=2, default=str)

    # ── Print results ──
    print(f"\n{'='*70}")
    print(f"  ANOMALY DETECTION REPORT")
    print(f"{'='*70}")
    print(f"  Total vehicle transactions analyzed:  {len(all_records)}")
    print(f"  Fill events (after combining splits): {total_events}")
    print(f"  Split fills combined:                 {split_fills}")
    print(f"  Total flagged transactions:           {sum(1 for t in all_records if t['flag_count'] > 0)}")
    print(f"  Total individual flags:               {sum(flag_counts.values())}")
    print()

    print(f"  TRANSACTION-LEVEL FLAGS:")
    flag_names = {
        2: "Cost Per Gallon Outlier",
        3: "Odometer Issue (missing/invalid)",
        4: "Unusually Small Fill",
        5: "High Frequency Fills",
        6: "Wrong Fuel Type",
    }
    for fnum in (2, 3, 4, 5, 6):
        count = flag_counts.get(fnum, 0)
        print(f"    Flag {fnum} — {flag_names[fnum]}: {count}")

    print(f"\n  VEHICLE-LEVEL FLAG 1 — Fuel Efficiency (Period MPG):")
    print(f"    {len(flagged_vehicles)} of {len(mpg_results)} vehicles flagged (>20% below baseline)")
    for vname in sorted(mpg_results.keys()):
        ms = mpg_results[vname]
        if ms["period_mpg"] is None:
            continue
        baseline = ms["baseline_mpg"] or "N/A"
        flag_str = " *** FLAGGED" if ms["flagged"] else ""
        pct_str = f" ({ms['pct_diff']:+.1f}%)" if ms["pct_diff"] is not None else ""
        print(f"    {vname}: {ms['period_mpg']} MPG over {ms['total_miles']:,} mi / "
              f"{ms['total_gallons']} gal ({ms['fill_count']} fills) | baseline: {baseline}{pct_str}{flag_str}")

    print(f"\n  ADDITIONAL SECTIONS FOR MANAGER REVIEW:")
    print(f"    Temporary card transactions:  {len(temp_records)}")
    print(f"    Declined transactions:        {len(declined_records)}")
    print(f"    Equipment (excluded):         {len(equipment_txns)}")

    print(f"\n  FLEET GROUPS WITH FLAGGED ITEMS:")
    for g in sorted(group_summary.keys()):
        gs = group_summary[g]
        if gs["flagged_txns"] > 0:
            flags_str = ", ".join(f"F{f}" for f in gs["flag_types"])
            print(f"    {g}: {gs['flagged_txns']}/{gs['total_txns']} flagged "
                  f"(${gs['total_spend']:,.2f} spend) [{flags_str}]")

    print(f"\n  Report saved to: {_output}")
    return report


if __name__ == "__main__":
    run()
