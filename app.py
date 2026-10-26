"""Readable Streamlit dashboard for the TruckX single-driver location audit."""
import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st
from validate_single_driver_locations import audit
from eld_metadata import extract, display

st.set_page_config(page_title='TruckX | Driver Log Validator', page_icon='🚚', layout='wide')
st.markdown('''<style>
.block-container{max-width:1500px;padding-top:1.5rem}
.hero{background:linear-gradient(115deg,#102a43,#156b80);padding:1.5rem 2rem;border-radius:18px;color:white;margin-bottom:1.4rem}
.hero h1{font-size:2.05rem;color:white;margin:0}.hero p{color:#dceff3;margin:.4rem 0 0}
[data-testid="stMetric"]{border:1px solid #d9e3eb;border-radius:12px;padding:12px;background:rgba(120,150,180,.05)}
.note{padding:1rem;border-radius:12px;border:1px solid #d8e3ec;margin:.5rem 0}
</style>''', unsafe_allow_html=True)
st.markdown('<div class="hero"><h1>🚚 TruckX Driver Log Validator</h1><p>Single-driver location continuity • Clear findings • Local JSON analysis</p></div>', unsafe_allow_html=True)

with st.sidebar:
    st.header('⚙️ Validation settings')
    gps = st.toggle('GPS Matching', value=False, help='OFF compares written locations only. ON enables coordinate distance checks.')
    exclude_zip = st.toggle('Exclude ZIP Code', value=True, help='Ignore ZIP / ZIP+4 at the end of written locations.')
    if gps:
        tolerance = st.number_input('GPS tolerance (metres)', 1, 10000, 100, step=25)
        review_radius = st.number_input('Nearby review threshold (metres)', 1, 10000, 500, step=50)
    else:
        tolerance, review_radius = 100, 500
        st.caption('GPS coordinates are not used while GPS Matching is OFF.')
    st.divider()
    st.subheader('Active transition rules')
    st.caption('Rule 1: OFF_DUTY or SLEEPER_BERTH → ON_DUTY or DRIVING.\n\nRule 2: ON_DUTY → DRIVING.')
    st.caption('Only consecutive real events are checked. Other transitions are not violations under these rules.')
    st.info('Custom location audit only — not a complete FMCSA HOS compliance determination.')

source_mode = st.radio('📂 Data source', ['TruckX JSON', 'FMCSA PDF-converted Excel'], horizontal=True)
excel_parsed = None
if source_mode == 'FMCSA PDF-converted Excel':
    from excel_adapter import convert_xlsx
    excel_file = st.file_uploader('Upload PDF-converted FMCSA Excel (.xlsx)', type=['xlsx'], key='excel_file')
    if excel_file is None:
        st.info('Upload the converted Excel file. The same validator and dashboard will be used as for JSON.')
        st.stop()
    try:
        payload, excel_parsed = convert_xlsx(excel_file.getvalue())
    except Exception as exc:
        st.error(f'Excel extraction failed: {exc}')
        st.stop()
    st.caption('Excel distance/bearing locations are compared by estimated relative offset (default 0.5 mile tolerance). Same-place nearby descriptions pass; farther offsets need review. JSON comparison is unchanged.')
    if excel_parsed['embedded_images']:
        st.warning(f"{excel_parsed['embedded_images']} embedded images detected. Image-only text is not read; verify those details manually.")
    if excel_parsed['warnings']:
        with st.expander(f"Excel extraction warnings ({len(excel_parsed['warnings'])})"):
            for warning in excel_parsed['warnings']:
                st.write(warning)
else:
    uploaded = st.file_uploader('Upload original TruckX bulk-details JSON', type=['json'])
    with st.expander('📁 Or load a local file from the data folder'):
        st.caption('Optional: place a JSON file in the data/ folder beside app.py. This works when running Streamlit on your computer.')
        data_dir = Path(__file__).resolve().parent / 'data'
        choices = sorted(data_dir.glob('*.json')) if data_dir.is_dir() else []
        selected = st.selectbox('Local JSON', ['None'] + [p.name for p in choices])

    if uploaded is None and selected == 'None':
        st.info('Upload a TruckX JSON export above, or select a local JSON from the data folder to begin.')
        st.stop()

    try:
        if uploaded is not None:
            payload = json.load(uploaded)
        else:
            payload = json.loads((data_dir / selected).read_text(encoding='utf-8'))
        report = audit(payload, gps_matching=gps, exclude_zip=exclude_zip,
                       tolerance_meters=float(tolerance), review_radius=float(review_radius))
    except (ValueError, TypeError, json.JSONDecodeError, OSError) as exc:
        st.error(f'Unable to read or validate JSON: {exc}')
        st.stop()


if excel_parsed is not None:
    # Exactly the same validation function, parameters, and result schema as JSON.
    report = audit(payload, gps_matching=gps, exclude_zip=exclude_zip,
                   tolerance_meters=float(tolerance), review_radius=float(review_radius),
                   excel_approximate=True)

findings = report['findings']
counts = report['counts']
passes = counts.get('PASS', 0)
failures = counts.get('LOCATION_MISMATCH', 0)
warnings = sum(counts.get(k, 0) for k in ('REVIEW_GPS_DIFFERENCE', 'REVIEW_APPROXIMATE_LOCATION', 'MISSING_LOCATION', 'DATA_GAP'))

c = st.columns(6)
for col, title, value in zip(c, ['Days', 'Unique events', 'Checks', '🟢 Passed', '🔴 Mismatches', '🟠 Warnings'],
                             [report['days'], report['events'], len(findings), passes, failures, warnings]):
    col.metric(title, value)

if report['missing_days']:
    st.warning('Missing days in export: ' + ', '.join(report['missing_days']) + '. Transitions spanning missing days are marked DATA_GAP.')
if report['invalid_start_times']:
    st.caption(f"Skipped {report['invalid_start_times']} events with invalid/placeholder start timestamps. Removed {report['duplicates_removed']} repeated event IDs.")
if report['carry_forward_checks']:
    issues = [r for r in report['carry_forward_checks'] if r['result'] != 'PASS']
    if issues:
        st.warning(f'{len(issues)} carried-forward location checks need attention. See the Data quality tab.')

if not findings:
    st.info('No qualifying consecutive status transitions were found. Metadata and data-quality tabs remain available.')

# Keep a stable schema even for files containing no qualifying transitions.
df = pd.DataFrame(findings, columns=['date','result','from_status','to_status','from_location','to_location','gps_distance_m','approx_distance_miles','from_event_id','to_event_id','from_coordinates','to_coordinates','from_time','to_time','rule','cross_midnight','comparison'])
df['Status'] = df['result'].map({
    'PASS':'🟢 PASS', 'LOCATION_MISMATCH':'🔴 MISMATCH', 'REVIEW_APPROXIMATE_LOCATION':'🟠 APPROXIMATE REVIEW',
    'REVIEW_GPS_DIFFERENCE':'🟠 GPS REVIEW', 'MISSING_LOCATION':'🟠 MISSING LOCATION',
    'DATA_GAP':'🟠 DATA GAP'})
df['Transition'] = df['from_status'] + ' → ' + df['to_status']

st.subheader('🔎 Filter results')
# Independent date pickers deliberately have no source-file min/max constraints.
# They work across months, years, and long intervals (including dates outside the export).
source_dates = []
for day in payload.get('bulk_details', []):
    raw = (day.get('timelog_details') or {}).get('event_date')
    try:
        source_dates.append(date.fromisoformat(str(raw)[:10]))
    except (ValueError, TypeError):
        pass
available_dates = sorted(date.fromisoformat(d) for d in df['date'].dropna().unique())
if not source_dates and not available_dates:
    st.error('No valid log dates were found in the uploaded data.')
    st.stop()
def_start = min(source_dates + available_dates)
def_end = max(source_dates + available_dates)
f1, f2, f3, f4 = st.columns([1, 1, 1, 2])
with f1:
    start = st.date_input('From date', value=def_start, key='filter_start_date')
with f2:
    end = st.date_input('To date (inclusive)', value=def_end, key='filter_end_date')
with f3:
    only_problems = st.checkbox('Show only problems', value=True)
with f4:
    text_search = st.text_input('Search location or event ID', placeholder='e.g. Modesto')
if start > end:
    st.error('From date must be on or before To date.')
    st.stop()
selected_days = (end - start).days + 1
st.caption(f'Selected interval: **{selected_days:,} calendar days** ({start} → {end}). '
           f'Export coverage: {def_start} → {def_end}. '
           'Dates outside the uploaded export cannot be checked until their records are included.')
filtered = df[(df['date'] >= start.isoformat()) & (df['date'] <= end.isoformat())].copy()
if only_problems:
    filtered = filtered[filtered['result'] != 'PASS']
if text_search.strip():
    q = text_search.strip().casefold()
    fields = ['from_location', 'to_location', 'from_event_id', 'to_event_id']
    mask = pd.Series(False, index=filtered.index)
    for field in fields:
        mask |= filtered[field].fillna('').astype(str).str.casefold().str.contains(q, regex=False)
    filtered = filtered[mask]

show_cols = {'date':'Date', 'Status':'Result', 'Transition':'Status transition',
             'from_location':'Previous location', 'to_location':'Next location', 'gps_distance_m':'GPS distance (m)'}

tab_overview, tab_driver, tab_map, tab_problems, tab_all, tab_quality = st.tabs(['📊 Overview', '👤 Driver & dated shipments', '📍 Location overview', '🔍 Problems & details', '📋 All checks', '🧩 Data quality'])
with tab_overview:
    st.markdown('#### Daily results')
    by_day = df.assign(Passed=df['result'].eq('PASS').astype(int),
                       Mismatches=df['result'].eq('LOCATION_MISMATCH').astype(int),
                       Warnings=df['result'].isin(['REVIEW_GPS_DIFFERENCE', 'MISSING_LOCATION', 'DATA_GAP']).astype(int))
    daily = by_day.groupby('date', as_index=False)[['Passed', 'Mismatches', 'Warnings']].sum()
    st.dataframe(daily.rename(columns={'date':'Date'}), hide_index=True, use_container_width=True)
    if not daily.empty:
        st.bar_chart(daily.set_index('date')[['Passed', 'Mismatches', 'Warnings']])
    st.caption('Counts represent custom location checks, not an official HOS audit result.')


# All metadata is extracted directly from the uploaded JSON; unavailable fields remain blank.
with tab_driver:
    st.markdown('#### 👤 Driver, carrier, shipping & ELD/HOS records')
    st.caption('Source metadata and ELD/HOS flags, not an FMCSA certification or legal compliance verdict.')
    records = extract(payload, start, end)
    daily = records['daily']
    if daily.empty:
        st.info('No daily records in the selected date range. Upload an export covering those dates.')
    else:
        def distinct(field):
            vals = daily[field].dropna().astype(str).str.strip()
            vals = vals[(vals != '') & (vals != 'None')]
            return ', '.join(dict.fromkeys(vals)) or 'Not provided'
        st.markdown('##### 👤 Driver identity (shown once)')
        def stable(field):
            vals = [str(v).strip() for v in daily[field].dropna().tolist() if str(v).strip() and str(v).strip().lower() not in ('none','nan')]
            unique = list(dict.fromkeys(vals))
            if len(unique)>1:
                st.warning(f'{field} changes within the selected logs: {", ".join(unique[:5])}. Check whether multiple drivers or extraction issues are present.')
            return unique[0] if len(unique)==1 else ('Multiple values — review' if unique else 'Not provided')
        a,b,c=st.columns(3)
        with a:
            for field in ('Driver','Driver UUID','Co-driver','Co-driver UUID'):
                st.write(f'**{field}:** {stable(field)}')
        with b:
            for field in ('Carrier','USDOT number','ELD model'):
                st.write(f'**{field}:** {stable(field)}')
        with c:
            st.info('Shipping documents, vehicle/trailer details, mileage, destinations and signatures can change by day. See the dated shipment history below.')
        st.markdown('##### 📦 Shipping documents & trip history — date specific')
        shipment_columns=['Date','Shipping document','Shipping document UUID','Delivery date',
            'Truck number','Trailer numbers','Start location','Destination','Miles driven',
            'Odometer reading','Signature present']
        shipments=daily[shipment_columns].copy()
        st.dataframe(shipments.astype(str).replace({'None':'','nan':''}),hide_index=True,use_container_width=True)
        st.download_button('⬇️ Export dated shipping history CSV',shipments.to_csv(index=False).encode('utf-8-sig'),
            file_name='truckx_shipping_history.csv',mime='text/csv',key='shipping_history_export')
        st.caption('Each row belongs to its log date. Repeated documents remain visible on the dates they appear; blank cells mean the export did not provide a value.')
        k = st.columns(5)
        for col, label, val in zip(k,
            ['Days in export', 'Events', 'Unsigned days', 'Unidentified events', 'DVIR / post-trip defects'],
            [len(daily), len(records['events']), int((~daily['Signature present']).sum()),
             int(daily['Unidentified events'].sum()),
             int(daily['DVIR defects'].sum() + daily['Post-trip defects'].sum())]):
            col.metric(label, val)
        st.divider()
        st.markdown('##### 📑 Records & downloadable registers')
        reg1, reg2, reg3, reg4, reg5 = st.tabs([
            '🚚 Daily / shipping', '⏱️ ELD event log', '📋 HOS values',
            '🛠️ DVIR / inspections', '❔ Unidentified driving'])
        def show_table(frame, name, columns=None):
            """Render arbitrary source data safely on Arrow/Streamlit, including mixed cells."""
            if frame.empty:
                st.info('No records of this type in the selected period.')
                return
            requested = list(dict.fromkeys(columns)) if columns else list(dict.fromkeys(frame.columns))
            view = frame.loc[:, ~frame.columns.duplicated()].copy()
            for field in requested:
                if field not in view.columns:
                    view[field] = None
            view = view.loc[:, requested].copy()
            import json as _json
            from datetime import datetime as _datetime, date as _date
            def safe_value(v):
                if isinstance(v, (dict, list, tuple, set)):
                    return _json.dumps(v, ensure_ascii=False, default=str)
                if isinstance(v, (_datetime, _date)):
                    return v.isoformat()
                if v is None:
                    return None
                try:
                    if pd.isna(v):
                        return None
                except (TypeError, ValueError):
                    pass
                return str(v)
            # String columns avoid Arrow failures from mixed int/string/date/dict values.
            view = view.apply(lambda col: col.map(safe_value))
            st.dataframe(view, use_container_width=True, hide_index=True)
            st.download_button('⬇️ Export ' + name + ' CSV',
                view.to_csv(index=False).encode('utf-8-sig'),
                file_name='truckx_' + name + '.csv', mime='text/csv', key='export_' + name)
        with reg1:
            st.caption('Daily identity, carrier, USDOT, equipment, shipping documents, mileage, addresses, and signatures.')
            show_table(daily, 'daily_shipping', [
                'Date','Driver','Co-driver','Carrier','USDOT number','Shipping document',
                'Shipping document UUID','Start location','Destination','Delivery date',
                'Truck number','Trailer numbers','Miles driven','Odometer reading','Signature present'])
        with reg2:
            st.caption('All available original event fields: status, timestamps, coordinates, origin, edits, diagnostics, IDs and remarks.')
            show_table(records['events'], 'eld_events')
        with reg3:
            st.caption('HOS calculations and violation flags are values supplied by TruckX, not independently verified FMCSA findings.')
            hoscols = list(dict.fromkeys(
                ['Date','Driver','Cycle type','HOS timezone','Day complete'] +
                [x for x in daily.columns if x.startswith('Shift ') or x.startswith('Cycle ')]))
            show_table(daily, 'hos_values', hoscols)
        with reg4:
            st.markdown('**DVIR and post-trip reports**')
            show_table(records['inspections'], 'dvir_posttrip')
            st.markdown('**Pre-/post-trip inspection records**')
            show_table(records['trips'], 'trip_inspections')
        with reg5:
            show_table(records['unidentified'], 'unidentified_events')
        st.divider()
        if excel_parsed is not None:
            st.markdown('##### 📗 Additional Excel source records')
            st.caption('Original intermediate positions, diagnostics, annotations, and daily metadata are available here; they are not interpreted as duty-status changes.')
            for label, key in [('Intermediate INT events', 'intermediate_events'), ('Diagnostics', 'diagnostics'), ('Other ELD events', 'other_events'), ('Annotations', 'annotations')]:
                rows = [{'Date': d['timelog_details']['event_date'], **entry}
                        for d in payload['bulk_details']
                        if start.isoformat() <= d['timelog_details']['event_date'] <= end.isoformat()
                        for entry in d['excel_source'][key]]
                with st.expander(f'{label} ({len(rows)})'):
                    if rows:
                        show_table(pd.DataFrame(rows), 'excel_' + key)
        st.markdown('##### 🔎 Inspect every field from a specific day')
        dates = sorted(daily['Date'].unique())
        chosen = st.selectbox('Log date', dates, key='metadata_day')
        chosen_rows = [d for d in payload.get('bulk_details', [])
                       if str((d.get('timelog_details') or {}).get('event_date') or
                              (d.get('timelog_details') or {}).get('timelog_id'))[:10] == chosen]
        if chosen_rows:
            item = chosen_rows[0]
            for title, key in [('Driver / carrier / shipping / vehicle', 'form_details'),
                               ('HOS data', 'hosinfo'), ('ELD log metadata', 'timelog_details'),
                               ('Signatures', 'timelog_sign'), ('DVIR', 'dvir_details'),
                               ('Post-trip', 'post_trip_data'), ('Inspection records', 'trip_details'),
                               ('Markers', 'markers'), ('DVIR carry-forward', 'dvir_carry_forward_data')]:
                with st.expander(title):
                    content = item.get(key) or {}
                    if key == 'timelog_details':
                        content = {k:v for k,v in content.items() if k != 'events'}
                        content['event_count'] = len((item.get('timelog_details') or {}).get('events') or [])
                    st.json(json.loads(json.dumps(content, default=str)))
        st.caption('Blank and missing values are not inferred. Exports may omit fields needed for a complete FMCSA ELD review.')

with tab_map:
    st.markdown('#### 📍 Location overview')
    st.caption('Original simple map: red location dots only. No arrows, connecting lines, or route predictions.')
    # Coordinates are used only to place dots on the map, never for matching when GPS is OFF.
    map_points = []
    for row in filtered.to_dict('records'):
        for key in ('from_coordinates', 'to_coordinates'):
            coordinates = row.get(key)
            if isinstance(coordinates, (list, tuple)) and len(coordinates) == 2:
                lat, lon = coordinates
                try:
                    lat, lon = float(lat), float(lon)
                    if -90 <= lat <= 90 and -180 <= lon <= 180:
                        map_points.append({'lat': lat, 'lon': lon})
                except (TypeError, ValueError):
                    pass
    if map_points:
        st.map(pd.DataFrame(map_points), latitude='lat', longitude='lon',
               color='#FF4B4B', size=35)
    else:
        st.info('No GPS points in the current filtered results. Turn off "Show only problems" if all checks pass.')

with tab_problems:
    problem_rows = filtered[filtered['result'] != 'PASS']
    st.caption(f'{len(problem_rows)} problem or review records match your filters.')
    if problem_rows.empty:
        st.success('No problems in the selected view.')
    else:
        st.dataframe(problem_rows[list(show_cols)].rename(columns=show_cols).astype(str), use_container_width=True, hide_index=True)
        options = list(problem_rows.index)
        chosen = st.selectbox('Inspect a finding', options, format_func=lambda i: (
            f"{problem_rows.loc[i, 'date']} · {problem_rows.loc[i, 'Status']} · "
            f"{problem_rows.loc[i, 'Transition']} · {problem_rows.loc[i, 'to_event_id']}"))
        row = problem_rows.loc[chosen]
        left, right = st.columns(2)
        with left:
            st.markdown('##### Previous event')
            st.write('**Status:**', row['from_status'])
            st.write('**Time:**', row['from_time'])
            st.write('**Location:**', row['from_location'] or '(missing)')
            st.write('**Event ID:**', row['from_event_id'])
        with right:
            st.markdown('##### Next event')
            st.write('**Status:**', row['to_status'])
            st.write('**Time:**', row['to_time'])
            st.write('**Location:**', row['to_location'] or '(missing)')
            st.write('**Event ID:**', row['to_event_id'])
        st.info(f"**Rule:** {row['rule']}" + (' · Crosses midnight' if row['cross_midnight'] else ''))
        if row['comparison'] in ('approximate_offset_within_tolerance', 'approximate_offset_needs_review'):
            st.caption(f"Approximate written-location displacement: {row.get('approx_distance_miles', 0)} miles (same reference place). This is not GPS verification.")
        if row['result'] == 'LOCATION_MISMATCH':
            st.error('Written locations do not match under your selected settings, or GPS and written locations differ beyond the review threshold.')
        elif row['result'] == 'MISSING_LOCATION':
            st.warning('The selected comparison method cannot verify one or both locations.')
        elif row['result'] == 'REVIEW_GPS_DIFFERENCE':
            st.warning(f"GPS positions are {row['gps_distance_m']} metres apart; review the original records. This is not proof of an edit.")
        elif row['result'] == 'DATA_GAP':
            st.warning('A day is missing between these records; they cannot be treated as reliably consecutive.')
        with st.expander('Technical details'):
            st.json({'comparison':row['comparison'], 'gps_distance_m':row['gps_distance_m'],
                     'from_coordinates':row['from_coordinates'], 'to_coordinates':row['to_coordinates']})

with tab_all:
    st.caption(f'{len(filtered)} of {len(df)} checks shown. Turn off “Show only problems” to see passes.')
    st.dataframe(filtered[list(show_cols)].rename(columns=show_cols).astype(str), use_container_width=True, hide_index=True)

with tab_quality:
    st.write('**Repeated event IDs removed:**', report['duplicates_removed'])
    st.write('**Events with invalid start time skipped:**', report['invalid_start_times'])
    st.write('**Days missing from export:**', ', '.join(report['missing_days']) or 'None')
    st.write('**Carry-forward candidates found:**', report['carry_forward_candidates'])
    st.caption('Carry-forward rows are not counted as extra transitions. Some exports omit them; validation continues across midnight using real events.')
    if report['carry_forward_checks']:
        carry_df = pd.DataFrame(report['carry_forward_checks'])
        st.dataframe(carry_df.astype(str), use_container_width=True, hide_index=True)
    else:
        st.caption('No comparable carry-forward records found.')

st.divider()
st.subheader('⬇️ Export (optional)')
export = filtered.drop(columns=['from_coordinates', 'to_coordinates'], errors='ignore')
st.download_button('Download filtered checks as CSV', export.to_csv(index=False).encode('utf-8'),
                   file_name='truckx_v2_filtered_checks.csv', mime='text/csv')
st.caption('Your original JSON is never modified. No audit report is written unless you choose to download one.')
