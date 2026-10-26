"""Extract source-backed ELD, carrier, driver and shipment metadata; no compliance conclusions."""
from datetime import date
import json
import pandas as pd


def display(value):
    if value is None or value == '' or value == [] or value == {}:
        return 'Not provided'
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def extract(payload, start: date, end: date):
    daily, events, unidentified, inspections, trips = [], [], [], [], []
    for item in payload.get('bulk_details', []):
        log = item.get('timelog_details') or {}
        raw = log.get('event_date') or log.get('timelog_id')
        try:
            d = date.fromisoformat(str(raw)[:10])
        except (TypeError, ValueError):
            continue
        if not start <= d <= end:
            continue
        f = item.get('form_details') or {}
        hos = item.get('hosinfo') or {}
        shift, cycle = hos.get('shift') or {}, hos.get('cycle') or {}
        sign = item.get('timelog_sign') or {}
        base = {'Date': d.isoformat(), 'Driver': f.get('driver_name'), 'Driver UUID': f.get('driver_uuid'),
                'Co-driver': f.get('codriver_name'), 'Co-driver UUID': f.get('codriver_uuid'),
                'Carrier': f.get('carrier_name'), 'USDOT number': f.get('dot'),
                'Truck number': f.get('truck_number'), 'Truck UUID': f.get('truck_uuid'),
                'Trailer numbers': ', '.join(map(str, f.get('trailer_numbers') or [])),
                'Trailer UUIDs': ', '.join(map(str, f.get('trailer_uuids') or [])),
                'Shipping document': f.get('shipping_doc'), 'Shipping document UUID': f.get('shipping_document_uuid'),
                'Start location': f.get('start_location'), 'Destination': f.get('destination_location'),
                'Delivery date': f.get('delivery_date'), 'Cycle type': f.get('cycle_type'),
                'Miles driven': f.get('miles_driven'), 'Start odometer': f.get('start_odometer'),
                'Odometer reading': f.get('odometer_reading'), 'Odometer edited': f.get('odometer_edited'),
                'Miles edited': f.get('miles_edited'), 'Start engine hours': f.get('start_engine_hours'),
                'Total engine hours': f.get('total_engine_hours'), 'Shift start time': f.get('shift_start_time'),
                'Exempt driver status': f.get('exempt_driver_status'),
                'Terminal address': display(f.get('terminal_address')),
                'Office address': display(f.get('office_address')),
                'Driver comment': f.get('comment'), 'Signature present': bool(sign.get('signature')),
                'Signer name': sign.get('name'), 'Signer company': sign.get('company'),
                'ELD model': log.get('eld_model'), 'HOS timezone': hos.get('timezone'),
                'Day complete': hos.get('dayComplete'),
                'Events': len(log.get('events') or []),
                'Unidentified events': len(f.get('unidentified_driver_events') or []),
                'DVIR defects': len((item.get('dvir_details') or {}).get('defects') or []),
                'Post-trip defects': len((item.get('post_trip_data') or {}).get('defects') or []),
                'Trip inspections': len(item.get('trip_details') or [])}
        for field in ('drivingMins', 'drivingMinsMax', 'shiftMinsMax', 'totalShiftMins',
                      'breakViolation', 'drivingViolation', 'shiftViolation', 'shiftStarted',
                      'minsWithoutBreak', 'contDutyMins', 'atBreakMins', 'adverseDrivingEvent',
                      'splitWindowAnchor', 'cycleResetFound'):
            base['Shift ' + field] = shift.get(field)
        for field in ('cycleDutyMins', 'cycleMinsMax', 'cycleDay', 'cycleViolation',
                      'cycleStarted', 'cycleResetFound', 'dutyMinsToday'):
            base['Cycle ' + field] = cycle.get(field)
        daily.append(base)
        for e in log.get('events') or []:
            events.append({'Log date': d.isoformat(), 'Driver': f.get('driver_name'),
                           'Co-driver': f.get('codriver_name'), 'Shipping document': f.get('shipping_doc'),
                           **{k: display(v) if isinstance(v, (dict, list)) else v for k, v in e.items()}})
        for e in f.get('unidentified_driver_events') or []:
            unidentified.append({'Log date': d.isoformat(), 'Driver': f.get('driver_name'), **e})
        for kind, key in [('DVIR', 'dvir_details'), ('Post-trip', 'post_trip_data')]:
            obj = item.get(key) or {}
            inspections.append({'Log date': d.isoformat(), 'Type': kind, 'Driver': f.get('driver_name'),
                                'Completed at': obj.get('completedAt'), 'Signature present': bool(obj.get('signature')),
                                'Defect count': len(obj.get('defects') or []),
                                'Defects': display(obj.get('defects')),
                                'Latitude': obj.get('latitude'), 'Longitude': obj.get('longitude')})
        for trip in item.get('trip_details') or []:
            trips.append({'Log date': d.isoformat(), 'Driver': f.get('driver_name'), **trip,
                          'defects': display(trip.get('defects'))})
    return {k: pd.DataFrame(v) for k, v in [('daily', daily), ('events', events),
                                            ('unidentified', unidentified), ('inspections', inspections),
                                            ('trips', trips)]}
