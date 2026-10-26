"""TruckX single-driver location continuity audit. No network calls or file writes."""
import argparse
import json
import math
import re
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

OFF_LIKE = {'OFF_DUTY', 'SLEEPER_BERTH'}
ACTIVE_NEXT = {'ON_DUTY', 'DRIVING'}
ZIP_END = re.compile(r'(?:[\s,]+)\d{5}(?:-\d{4})?\s*$', re.I)


def parse_time(value):
    if not isinstance(value, str) or not value or value.startswith('0000-00-00'):
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None


def normalize_location(value, exclude_zip=True):
    text = str(value or '').strip()
    if exclude_zip:
        text = ZIP_END.sub('', text)
    text = text.casefold().replace('.', '').replace(',', ' ')
    return ' '.join(text.split())


def valid_coords(event):
    try:
        lat, lon = float(event.get('latitude')), float(event.get('longitude'))
        if math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon
    except (TypeError, ValueError):
        pass
    return None


def distance_meters(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 12742000 * math.asin(min(1, math.sqrt(h)))


def location_match(a, b, tolerance_meters=100, gps_matching=False, exclude_zip=True, review_radius=500):
    """Return (result, GPS distance or None, comparison method)."""
    left = normalize_location(a.get('reverse_geo_data'), exclude_zip)
    right = normalize_location(b.get('reverse_geo_data'), exclude_zip)
    if not gps_matching:
        if not left or not right:
            return 'MISSING_LOCATION', None, 'written_location'
        return ('PASS' if left == right else 'LOCATION_MISMATCH'), None, 'written_location'

    coords_a, coords_b = valid_coords(a), valid_coords(b)
    if coords_a and coords_b:
        distance = distance_meters(coords_a, coords_b)
        if distance <= tolerance_meters:
            return 'PASS', distance, 'gps_within_tolerance'
        if distance <= review_radius or (left and right and left == right):
            return 'REVIEW_GPS_DIFFERENCE', distance, 'gps_and_written_location'
        return 'LOCATION_MISMATCH', distance, 'gps_and_written_location'
    if not left or not right:
        return 'MISSING_LOCATION', None, 'written_location_fallback'
    return ('PASS' if left == right else 'LOCATION_MISMATCH'), None, 'written_location_fallback'


def transition_rule(previous, following):
    if previous in OFF_LIKE and following in ACTIVE_NEXT:
        return 'Rule 1 · Rest status → On duty / Driving'
    if previous == 'ON_DUTY' and following == 'DRIVING':
        return 'Rule 2 · On duty → Driving'
    return None


def audit(data, gps_matching=False, exclude_zip=True, tolerance_meters=100, review_radius=500, excel_approximate=False, approximate_tolerance_miles=0.5):
    if not isinstance(data, dict) or not isinstance(data.get('bulk_details'), list):
        raise ValueError('Expected a TruckX JSON object containing bulk_details (array).')
    by_id, no_id, carry = {}, [], []
    source_dates = set()
    invalid_time = 0
    duplicates = 0
    for day in data['bulk_details']:
        details = day.get('timelog_details') or {}
        day_name = details.get('event_date') or details.get('timelog_id')
        try:
            source_dates.add(date.fromisoformat(str(day_name)[:10]))
        except (ValueError, TypeError):
            pass
        for original in details.get('events') or []:
            e = dict(original)
            e['_source_day'] = str(day_name or '')
            e['_start'] = parse_time(e.get('start_time'))
            if e['_start'] is None:
                invalid_time += 1
                continue
            # Some exports repeat an event in more than one day; only retain one.
            eid = e.get('event_id')
            if eid is None:
                no_id.append(e)
            elif eid in by_id:
                duplicates += 1
                # Prefer record associated with its own date when possible.
                existing = by_id[eid]
                if e['_source_day'] == e['_start'].date().isoformat() and existing['_source_day'] != existing['_start'].date().isoformat():
                    by_id[eid] = e
            else:
                by_id[eid] = e
            if str(e.get('end_time') or '').startswith('0000-00-00'):
                carry.append(e)

    events = sorted([*by_id.values(), *no_id], key=lambda e: (e['_start'], str(e.get('event_id') or '')))
    # Avoid connecting separated dates when the input omitted a full day.
    missing_days = []
    if source_dates:
        d = min(source_dates)
        while d <= max(source_dates):
            if d not in source_dates:
                missing_days.append(d.isoformat())
            d += timedelta(days=1)

    findings = []
    for a, b in zip(events, events[1:]):
        rule = transition_rule(a.get('event_type'), b.get('event_type'))
        if not rule:
            continue
        a_day, b_day = a['_start'].date(), b['_start'].date()
        if any(a_day < date.fromisoformat(day) < b_day for day in missing_days):
            result, distance, method = 'DATA_GAP', None, 'missing_day'
            approx_distance = None
        else:
            result, distance, method = location_match(a, b, tolerance_meters, gps_matching, exclude_zip, review_radius)
            # Only Excel-derived PDF descriptions use relative bearings. TruckX JSON
            # follows its original exact written-location comparison, unchanged.
            if excel_approximate and not gps_matching and result == 'LOCATION_MISMATCH':
                from approximate_locations import compare_approximate
                approx = compare_approximate(a.get('reverse_geo_data'), b.get('reverse_geo_data'),
                                             exclude_zip, approximate_tolerance_miles)
                if approx is not None:
                    result, approx_distance, method = approx
                else:
                    approx_distance = None
            else:
                approx_distance = None
        findings.append({
            'result': result, 'date': b_day.isoformat(), 'rule': rule,
            'from_time': a.get('start_time'), 'from_status': a.get('event_type'),
            'from_location': a.get('reverse_geo_data') or '', 'from_event_id': a.get('event_id'),
            'to_time': b.get('start_time'), 'to_status': b.get('event_type'),
            'to_location': b.get('reverse_geo_data') or '', 'to_event_id': b.get('event_id'),
            'gps_distance_m': round(distance, 1) if distance is not None else None,
            'approx_distance_miles': approx_distance if 'approx_distance' in locals() else None,
            'comparison': method, 'cross_midnight': a_day != b_day,
            'from_coordinates': valid_coords(a), 'to_coordinates': valid_coords(b),
        })

    # Carry-forward data is not necessarily a new event: inspect its location
    # against the previous day's last real event without adding a fake transition.
    carry_findings = []
    for e in carry:
        day_str = e.get('_source_day')
        try:
            day = date.fromisoformat(day_str[:10])
        except (ValueError, TypeError):
            continue
        previous = [x for x in events if x['_start'].date() == day - timedelta(days=1)]
        if not previous:
            continue
        last = previous[-1]
        # Only check a carry-forward when it is actually a previous-day event
        # shown in the following day's records.
        if e['_start'].date() >= day:
            continue
        a = normalize_location(e.get('reverse_geo_data'), exclude_zip)
        b = normalize_location(last.get('reverse_geo_data'), exclude_zip)
        if not a or not b:
            result = 'MISSING_LOCATION'
        else:
            result = 'PASS' if a == b else 'CARRY_FORWARD_MISMATCH'
        carry_findings.append({'date': day_str, 'result': result,
                               'carry_event_id': e.get('event_id'), 'previous_event_id': last.get('event_id'),
                               'carry_location': e.get('reverse_geo_data'), 'previous_location': last.get('reverse_geo_data')})

    return {'findings': findings, 'carry_forward_checks': carry_findings,
            'counts': dict(Counter(x['result'] for x in findings)),
            'days': len(source_dates), 'events': len(events),
            'duplicates_removed': duplicates, 'invalid_start_times': invalid_time,
            'missing_days': missing_days, 'carry_forward_candidates': len(carry),
            'gps_matching': gps_matching, 'exclude_zip': exclude_zip}


def main():
    parser = argparse.ArgumentParser(description='TruckX single-driver location validation')
    parser.add_argument('json_file', nargs='?', default='_api_client-service_timelog_bulkdetails_.json')
    parser.add_argument('--gps-matching', action='store_true', help='Enable GPS matching (default OFF)')
    parser.add_argument('--include-zip', action='store_true', help='Compare ZIP codes (default: excluded)')
    parser.add_argument('--tolerance-meters', type=float, default=100)
    parser.add_argument('--output', help='Optional JSON report output')
    args = parser.parse_args()
    with Path(args.json_file).open(encoding='utf-8') as f:
        result = audit(json.load(f), args.gps_matching, not args.include_zip, args.tolerance_meters)
    print(json.dumps({k: v for k, v in result.items() if k not in ('findings', 'carry_forward_checks')}, indent=2))
    for row in result['findings']:
        if row['result'] != 'PASS':
            print(f"{row['result']}: {row['from_time']} {row['from_status']} ({row['from_location']}) → "
                  f"{row['to_time']} {row['to_status']} ({row['to_location']})")
    if args.output:
        Path(args.output).write_text(json.dumps(result, indent=2, default=str), encoding='utf-8')


if __name__ == '__main__':
    main()
