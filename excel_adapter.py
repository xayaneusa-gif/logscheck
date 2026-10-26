"""Input adapter only: PDF-converted XLSX -> canonical TruckX-like records.
All location decisions are made exclusively by validate_single_driver_locations.audit.
"""
from fmcsa_excel import parse_xlsx, STATUS


def convert_xlsx(blob):
    parsed = parse_xlsx(blob)
    days = []
    for index, section in enumerate(parsed['days']):
        m = section['metadata']
        events = []
        for event in section['events']:
            code = str(event['Event']).strip().upper()
            if code not in STATUS:
                continue  # INT and other non-duty records remain available as source metadata
            events.append({
                'event_id': f"excel-{event['Source sheet']}-{event['Source row']}",
                'event_type': STATUS[code], 'start_time': event['Time'],
                'reverse_geo_data': event['Location'],
                'latitude': event['Latitude'] or None,
                'longitude': event['Longitude'] or None,
                'source_row': event['Source row'], 'source_sheet': event['Source sheet'],
                'record_status': event['Record status'], 'record_origin': event['Record origin'],
                'sequence_id': event['Sequence ID'],
            })
        days.append({
            'form_details': {
                'driver_name': m.get('Driver'), 'driver_uuid': m.get('Driver ID'),
                'codriver_name': m.get('Co-driver'), 'codriver_uuid': m.get('Co-driver ID'),
                'carrier_name': m.get('Carrier'), 'dot': m.get('USDOT'),
                'truck_number': m.get('Truck tractor ID'),
                'trailer_numbers': [m['Trailer ID']] if m.get('Trailer ID') else [],
                'shipping_doc': m.get('Shipping ID'),
                'odometer_reading': m.get('Start/end odometer'),
                'start_engine_hours': m.get('Start/end engine hours'),
                'exempt_driver_status': m.get('Exempt driver status'),
            },
            'timelog_details': {'event_date': m['Date'], 'events': events,
                                'eld_model': m.get('ELD ID'),
                                'source_sheet': m.get('Source sheet')},
            'hosinfo': {'timezone': m.get('Timezone')},
            'excel_source': {'metadata': m, 'diagnostics': section['diagnostics'],
                             'other_events': section['other'],
                             'annotations': section['annotations'],
                             'intermediate_events': [e for e in section['events'] if str(e['Event']).upper() == 'INT']},
        })
    return {'bulk_details': days}, parsed
