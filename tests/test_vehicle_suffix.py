"""
Unit tests for the vehicle suffix matcher (anomaly_detection.vehicle_suffix)
and the unmatched-row collection in match_transactions.

Run standalone (no pytest dependency):
    python3 tests/test_vehicle_suffix.py

Covers the W2 bug class: Fleetio names split on hyphen and Corpay last
names split on whitespace produced different keys, so hyphen-joined Corpay
suffixes (e.g. "GMC-VAN-4650") never matched Fleetio "11-GMC-Van-4650".
The single extractor (last run of digits, zero-padded to 4) must recover
those while preserving the names that already matched.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

from anomaly_detection import vehicle_suffix, match_transactions  # noqa: E402


def check(name, expected):
    got = vehicle_suffix(name)
    assert got == expected, f"vehicle_suffix({name!r}) = {got!r}, expected {expected!r}"


def test_fleetio_names():
    # Real Fleetio / baseline names all end in a 4-digit unit number.
    check("09-Honda-Suv-1694", "1694")
    check("11-GMC-Van-4650", "4650")
    check("19-Ram-Truck-0537", "0537")
    check("2023-Ram-Van-6754", "6754")   # leading "2023" must NOT win; trailing run does
    check("23-Ford-BoxTruck-0244", "0244")


def test_corpay_whitespace_names():
    # Corpay "Cardholder Last Name" values: number after a space.
    check("RAM-TRUCK 4554", "4554")
    check("RAM VAN 5260", "5260")
    check("TOYO-TRUCK 664", "0664")       # 3-digit pads to 4
    check("RAM-TRUCK 537", "0537")        # matches Fleetio 19-Ram-Truck-0537


def test_corpay_hyphen_joined_names():
    # The bug class: number hyphen-joined to the body. Old whitespace split
    # returned the whole token; the digit-run extractor recovers the number.
    check("GMC-VAN-4650", "4650")         # matches Fleetio 11-GMC-Van-4650
    check("FORD-VAN-0389", "0389")
    check("HONDA-SUV-1694", "1694")       # matches Fleetio 09-Honda-Suv-1694


def test_no_digit_and_empty():
    check("TEMPORARY", "")
    check("Honda", "")
    check("", "")
    check(None, "")


def test_cross_delimiter_consistency():
    # The same unit, however the two systems delimit it, must key identically.
    assert vehicle_suffix("11-GMC-Van-4650") == vehicle_suffix("GMC-VAN 4650") == vehicle_suffix("GMC-VAN-4650") == "4650"
    assert vehicle_suffix("09-Honda-Suv-1694") == vehicle_suffix("HONDA-SUV 1694") == "1694"


def test_match_transactions_recovers_and_buckets():
    fleetio = [
        {"name": "11-GMC-Van-4650", "group_name": "Max Zimmerman"},
        {"name": "09-Honda-Suv-1694", "group_name": "Max Zimmerman"},
    ]
    corpay = [
        # hyphen-joined suffix that previously missed -> must now match Van-4650
        {"Cardholder First Name": "VEHICLE", "Cardholder Last Name": "GMC-VAN-4650",
         "Status": "", "Net Price": 80.0},
        # whitespace suffix that always matched -> must still match Honda-1694
        {"Cardholder First Name": "VEHICLE", "Cardholder Last Name": "HONDA-SUV 1694",
         "Status": "", "Net Price": 50.0},
        # genuinely unknown unit -> must land in unmatched, not vanish
        {"Cardholder First Name": "VEHICLE", "Cardholder Last Name": "RAM-TRUCK 9999",
         "Status": "", "Net Price": 60.0},
    ]
    vehicle_txns, equip, temp, declined, unmatched = match_transactions(corpay, fleetio)

    matched_names = sorted(item["vehicle"]["name"] for item in vehicle_txns)
    assert matched_names == ["09-Honda-Suv-1694", "11-GMC-Van-4650"], matched_names
    assert len(unmatched) == 1, unmatched
    assert unmatched[0]["Cardholder Last Name"] == "RAM-TRUCK 9999"


def run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  PASS  {t.__name__}")
    print(f"\n{len(tests)} suffix/matcher tests passed.")


if __name__ == "__main__":
    run_all()
