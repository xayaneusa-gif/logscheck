#!/usr/bin/env python3
"""Validate single-driver status/location transitions in a TruckX bulk-details JSON file.

Rules implemented:
  * OFF_DUTY or SLEEPER_BERTH -> next event ON_DUTY or DRIVING:
      next event must be at the same location.
  * ON_DUTY -> next event DRIVING:
      next event must be at the same location.
  * All other status transitions are ignored by this validator.

Events are sorted by start_time (ascending) and duplicate event_id values are
counted only once. end_time is not used for this location-transition check, so
"0000-00-00 00:00:00" does not affect the comparison.

Location comparison is hybrid to avoid false positives: GPS within the configured
tolerance passes. If GPS is farther apart but reverse-geocoded location text matches,
the transition is REVIEW rather than a hard mismatch. If reverse-geocoded location
text differs, it is a mismatch. If GPS is unavailable, matching reverse-geocoded
location text passes. Adjust --tolerance-meters as needed.
"""
import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path

OFF_LIKE = {"OFF_DUTY", "SLEEPER_BERTH"}
ACTIVE_NEXT = {"ON_DUTY", "DRIVING"}


def parse_time(value):
    if not value or value.startswith("0000-00-00"):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def coord(event, key):
    try:
        value = float(event.get(key))
        if math.isfinite(value):
            return value
    except (TypeError, ValueError):
        pass
    return None


def distance_meters(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371000 * 2 * math.asin(min(1, math.sqrt(h)))


def location_match(a, b, tolerance_meters):
    a_lat, a_lon = coord(a, "latitude"), coord(a, "longitude")
    b_lat, b_lon = coord(b, "latitude"), coord(b, "longitude")
    loc_a = (a.get("reverse_geo_data") or "").strip().casefold()
    loc_b = (b.get("reverse_geo_data") or "").strip().casefold()
    same_geo_text = bool(loc_a and loc_b and loc_a == loc_b)
    if None not in (a_lat, a_lon, b_lat, b_lon):
        distance = distance_meters((a_lat, a_lon), (b_lat, b_lon))
        if distance <= tolerance_meters:
            return "PASS", distance, "gps_within_tolerance"
        # Small coordinate differences can occur around address/ZIP boundaries;
        # treat distances up to 500m as review even if reverse-geocoded text differs.
        if distance <= 500 or same_geo_text:
            return "REVIEW_SAME_AREA_DIFFERENT_GPS", distance, "gps_and_reverse_geo_data"
        if loc_a and loc_b:
            return "LOCATION_MISMATCH", distance, "gps_and_reverse_geo_data"
        return "LOCATION_MISMATCH", distance, "gps"
    if same_geo_text:
        return "PASS", None, "reverse_geo_data_only"
    if loc_a and loc_b:
        return "LOCATION_MISMATCH", None, "reverse_geo_data_only"
    return "UNVERIFIABLE_LOCATION_MISSING", None, "location_missing"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("json_file", nargs="?",
                        help="Path to TruckX bulk-details JSON (optional if the default filename is in this folder)")
    parser.add_argument("--tolerance-meters", type=float, default=100.0,
                        help="Maximum GPS distance counted as the same location (default: 100m)")
    parser.add_argument("--output", help="Optional path to save detailed findings as JSON; no report file is created by default")
    args = parser.parse_args()

    # Make the script easy to run: if no filename is supplied, use the usual
    # TruckX export name from the current working directory.
    input_path = Path(args.json_file) if args.json_file else Path("_api_client-service_timelog_bulkdetails_.json")
    if not input_path.is_file():
        print(f"ERROR: Could not find input JSON: {input_path}", file=sys.stderr)
        print("Put the TruckX JSON in this folder, or run:", file=sys.stderr)
        print("  python3 validate_single_driver_locations.py /path/to/your_truckx_file.json", file=sys.stderr)
        sys.exit(2)

    with input_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    events_by_id = {}
    no_id_events = []
    for day in data.get("bulk_details", []):
        details = day.get("timelog_details") or {}
        for event in details.get("events", []):
            event = dict(event)
            event.setdefault("source_timelog_id", details.get("timelog_id") or details.get("event_date"))
            event["_parsed_start"] = parse_time(event.get("start_time"))
            event_id = event.get("event_id")
            if event_id is None:
                no_id_events.append(event)
            elif event_id not in events_by_id:
                events_by_id[event_id] = event
            # If the same event_id appears in multiple daily records, retain one copy.

    events = list(events_by_id.values()) + no_id_events
    events = [e for e in events if e.get("_parsed_start") is not None]
    events.sort(key=lambda e: (e["_parsed_start"], str(e.get("event_id", ""))))

    findings = []
    checked = 0
    skipped_missing_location = 0
    for current, nxt in zip(events, events[1:]):
        status = current.get("event_type")
        next_status = nxt.get("event_type")
        must_check = (status in OFF_LIKE and next_status in ACTIVE_NEXT) or (status == "ON_DUTY" and next_status == "DRIVING")
        if not must_check:
            continue
        checked += 1
        match, distance, method = location_match(current, nxt, args.tolerance_meters)
        result = match
        if result == "UNVERIFIABLE_LOCATION_MISSING":
            skipped_missing_location += 1
        findings.append({
            "result": result,
            "current_event_id": current.get("event_id"),
            "current_start_time": current.get("start_time"),
            "current_status": status,
            "current_location": current.get("reverse_geo_data", ""),
            "current_latitude": current.get("latitude", ""),
            "current_longitude": current.get("longitude", ""),
            "next_event_id": nxt.get("event_id"),
            "next_start_time": nxt.get("start_time"),
            "next_status": next_status,
            "next_location": nxt.get("reverse_geo_data", ""),
            "next_latitude": nxt.get("latitude", ""),
            "next_longitude": nxt.get("longitude", ""),
            "distance_meters": round(distance, 2) if distance is not None else None,
            "comparison_method": method,
        })

    summary = {
        "source_file": str(input_path),
        "unique_event_ids": len(events_by_id),
        "events_without_id": len(no_id_events),
        "chronologically_sorted_events_checked": len(events),
        "location_transitions_checked": checked,
        "passes": sum(x["result"] == "PASS" for x in findings),
        "location_mismatches": sum(x["result"] == "LOCATION_MISMATCH" for x in findings),
        "review_same_area_different_gps": sum(x["result"] == "REVIEW_SAME_AREA_DIFFERENT_GPS" for x in findings),
        "unverifiable_missing_location": skipped_missing_location,
        "gps_tolerance_meters": args.tolerance_meters,
        "findings": findings,
    }
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
    print(json.dumps({k: v for k, v in summary.items() if k != "findings"}, indent=2))
    print("\nDetailed transitions:")
    for item in findings:
        if item["result"] != "PASS":
            print(f"{item['result']}: {item['current_start_time']} {item['current_status']} "
                  f"({item['current_location']}) -> {item['next_start_time']} {item['next_status']} "
                  f"({item['next_location']}); distance={item['distance_meters']}m")
    if args.output:
        print(f"\nFull findings written to: {args.output}")


if __name__ == "__main__":
    main()
