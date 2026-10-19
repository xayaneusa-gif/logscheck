"""Streamlit UI for single-driver TruckX location-transition checks."""
import io
import json
from collections import Counter

import pandas as pd
import streamlit as st

from validate_single_driver_locations import parse_time, location_match, OFF_LIKE, ACTIVE_NEXT

st.set_page_config(page_title="TruckX • Single Driver Audit", page_icon="🚚", layout="wide")
st.markdown('''<style>
.block-container{padding-top:1.6rem;max-width:1450px}
.hero{padding:1.4rem 1.7rem;border-radius:16px;background:linear-gradient(110deg,#10243c,#17617a);color:white;margin-bottom:1.4rem}
.hero h1{color:white;font-size:2rem;margin:0 0 .3rem}
.hero p{color:#d9edf2;margin:0}
[data-testid="stMetric"]{border:1px solid #d8e1e8;padding:12px;border-radius:12px}
</style>''', unsafe_allow_html=True)
st.markdown('<div class="hero"><h1>🚚 TruckX — Single Driver Location Audit</h1><p>Check status-to-status location continuity using your own TruckX JSON. No external service required.</p></div>', unsafe_allow_html=True)

with st.sidebar:
    st.header("⚙️ Audit settings")
    tolerance = st.number_input("GPS pass tolerance (metres)", min_value=1, max_value=10000, value=100, step=25)
    review_radius = st.number_input("Nearby review threshold (metres)", min_value=1, max_value=10000, value=500, step=50)
    st.caption("GPS differences beyond the pass tolerance may be flagged for review. These thresholds are configurable project settings, not legal standards.")
    st.divider()
    st.subheader("Rules")
    st.markdown("• OFF_DUTY → ON_DUTY / DRIVING: same location\n\n• SLEEPER_BERTH → ON_DUTY / DRIVING: same location\n\n• ON_DUTY → DRIVING: same location")
    st.caption("Only adjacent chronological events are checked. Other transitions are not location violations under this rule set.")
    st.info("Single-driver location checks only. Not a complete FMCSA HOS compliance audit; co-driver logic is not included.")

uploaded = st.file_uploader("Upload TruckX bulk-details JSON", type=["json"], help="Select the original TruckX API JSON export, not a previously generated audit report.")


def valid_coords(e):
    try:
        lat, lon = float(e.get("latitude")), float(e.get("longitude"))
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon
    except (ValueError, TypeError):
        pass
    return None


def audit(data, gps_tol, nearby):
    if not isinstance(data, dict) or not isinstance(data.get("bulk_details"), list):
        raise ValueError("Expected a TruckX JSON object with a 'bulk_details' array.")
    events_by_id, no_id = {}, []
    day_count = len(data["bulk_details"])
    for day in data["bulk_details"]:
        details = day.get("timelog_details") or {}
        for original in details.get("events", []):
            event = dict(original)
            dt = parse_time(event.get("start_time"))
            if dt is None:
                continue
            event["_start"] = dt
            event["_day"] = details.get("event_date") or details.get("timelog_id", "")
            eid = event.get("event_id")
            if eid is None:
                no_id.append(event)
            elif eid not in events_by_id:
                events_by_id[eid] = event
    events = sorted([*events_by_id.values(), *no_id], key=lambda e: (e["_start"], str(e.get("event_id", ""))))
    findings = []
    for current, nxt in zip(events, events[1:]):
        status, next_status = current.get("event_type"), nxt.get("event_type")
        rule = ((status in OFF_LIKE and next_status in ACTIVE_NEXT) or
                (status == "ON_DUTY" and next_status == "DRIVING"))
        if not rule:
            continue
        # The base validator's GPS comparison has a fixed 500m nearby-review boundary.
        # The user-configurable threshold is applied here to preserve the same semantics.
        result, dist, method = location_match(current, nxt, gps_tol)
        if dist is not None and dist > gps_tol:
            a_text = (current.get("reverse_geo_data") or "").strip().casefold()
            b_text = (nxt.get("reverse_geo_data") or "").strip().casefold()
            if dist <= nearby or (a_text and a_text == b_text):
                result = "REVIEW_SAME_AREA_DIFFERENT_GPS"
            else:
                result = "LOCATION_MISMATCH"
        row = {
            "Result": result,
            "Date": current["_start"].date().isoformat(),
            "From time": current.get("start_time", ""),
            "From status": status,
            "From location": current.get("reverse_geo_data", ""),
            "From event ID": current.get("event_id"),
            "To time": nxt.get("start_time", ""),
            "To status": next_status,
            "To location": nxt.get("reverse_geo_data", ""),
            "To event ID": nxt.get("event_id"),
            "GPS distance (m)": round(dist, 1) if dist is not None else None,
            "Comparison": method,
        }
        coords = valid_coords(current)
        row["_lat"], row["_lon"] = coords if coords else (None, None)
        findings.append(row)
    return day_count, events, pd.DataFrame(findings)

if uploaded is None:
    st.info("👆 Upload your original `_api_client-service_timelog_bulkdetails_.json` file to start. Results are displayed here; no large report file is created automatically.")
    with st.expander("How the audit works"):
        st.markdown("Events from all days are combined, deduplicated by event ID and sorted by start time. The `0000-00-00 00:00:00` **end-time** placeholder is ignored. Each qualifying consecutive status transition is compared by GPS and reverse-geocoded location. Statuses remain distinct; SLEEPER_BERTH and OFF_DUTY share only the same transition-check rules.")
    st.stop()

try:
    payload = json.load(uploaded)
    days, events, df = audit(payload, float(tolerance), float(review_radius))
except (json.JSONDecodeError, ValueError, TypeError, AttributeError) as exc:
    st.error(f"Unable to process the uploaded JSON: {exc}")
    st.stop()

counts = Counter(df["Result"]) if not df.empty else Counter()
passes = counts["PASS"]
reviews = counts["REVIEW_SAME_AREA_DIFFERENT_GPS"]
mismatches = counts["LOCATION_MISMATCH"]
missing = counts["UNVERIFIABLE_LOCATION_MISSING"]
cols = st.columns(6)
for col, label, val in zip(cols, ["Days", "Events", "Transitions", "Passed", "Review", "Potential mismatch"], [days, len(events), len(df), passes, reviews, mismatches]):
    col.metric(label, val)
if missing:
    st.warning(f"{missing} transitions could not be verified because of missing location information.")

st.subheader("🔎 Explore transitions")
if df.empty:
    st.warning("No qualifying status transitions found in the uploaded data.")
    st.stop()

f1, f2, f3 = st.columns([2, 2, 2])
with f1:
    result_filter = st.multiselect("Result", options=sorted(df["Result"].unique()), default=sorted(df["Result"].unique()))
with f2:
    dates = sorted(df["Date"].unique())
    date_filter = st.multiselect("Date", options=dates, default=dates)
with f3:
    status_filter = st.multiselect("From status", options=sorted(df["From status"].unique()), default=sorted(df["From status"].unique()))
filtered = df[df["Result"].isin(result_filter) & df["Date"].isin(date_filter) & df["From status"].isin(status_filter)]
st.caption(f"Showing {len(filtered)} of {len(df)} checked transitions. Click a row to inspect it below.")
visible_cols = [c for c in filtered.columns if not c.startswith("_")]
st.dataframe(filtered[visible_cols], use_container_width=True, hide_index=True, height=440)

st.subheader("📍 Location overview")
map_df = filtered.dropna(subset=["_lat", "_lon"])[["_lat", "_lon"]].rename(columns={"_lat":"lat", "_lon":"lon"})
if not map_df.empty:
    st.map(map_df, size=35)
else:
    st.caption("No usable GPS coordinates available for the selected transitions.")

st.subheader("⬇️ Export only if needed")
left, right = st.columns(2)
with left:
    st.download_button("Download filtered results (CSV)", filtered[visible_cols].to_csv(index=False).encode("utf-8"), "truckx_location_results.csv", "text/csv", use_container_width=True)
with right:
    st.download_button("Download all results (JSON)", df[visible_cols].to_json(orient="records", indent=2).encode("utf-8"), "truckx_location_results.json", "application/json", use_container_width=True)
st.caption("This tool checks custom location-transition rules only. A matching city/ZIP is not proof of the exact same parking spot. Review flagged transitions using original records and operational context.")
