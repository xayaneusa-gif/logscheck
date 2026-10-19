# TruckX Single-Driver Location Audit (Streamlit)

A local web dashboard to validate custom **single-driver location/status transitions** from TruckX bulk-details JSON. This is **not** a full HOS compliance calculator and does not include co-driver logic.

## Run on Ubuntu

```bash
cd ~/Downloads/truckx_streamlit_app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

If you already use a working Python environment, you can omit the `venv` steps and run `pip install -r requirements.txt` there.

Open the local URL Streamlit prints, usually `http://localhost:8501`.

## How to use

1. Upload your original TruckX `bulk_details` JSON export from your computer.
2. Adjust the GPS pass and review thresholds in the sidebar, if needed.
3. View summary counts, filter transitions by result/date/status, and see event locations on the map.
4. Optionally download CSV or JSON results using the buttons. **No large audit file is created by default.**

## Rules

- `OFF_DUTY` → `ON_DUTY` or `DRIVING`: same location required.
- `SLEEPER_BERTH` → `ON_DUTY` or `DRIVING`: same location required.
- `ON_DUTY` → `DRIVING`: same location required.
- Other adjacent status transitions: no location check under these custom rules.

Events across days are deduplicated by `event_id` and ordered by `start_time`. `end_time` is not used, so `0000-00-00 00:00:00` is ignored. The script preserves distinct HOS statuses; it does not equate `ON_DUTY` with `OFF_DUTY`.

### Results

- **PASS:** GPS distance within the pass tolerance, or GPS unavailable but the reverse-geocoded text matches.
- **REVIEW_SAME_AREA_DIFFERENT_GPS:** GPS differs, but the distance is within the nearby review threshold or reverse-geocoded text matches.
- **LOCATION_MISMATCH:** GPS exceeds both thresholds and reverse-geocoded text differs (or other location evidence disagrees).
- **UNVERIFIABLE_LOCATION_MISSING:** not enough location information.

Thresholds are engineering heuristics, not regulatory requirements. City-level geocoding alone does not prove that two points are the same exact location.

## Files

- `app.py` — Streamlit user interface and audit orchestration.
- `validate_single_driver_locations.py` — reusable validation logic and optional CLI.
- `requirements.txt` — Python packages.
- `README.md` — setup and usage instructions.

The original driver data is intentionally **not bundled** in this app package. Upload it locally in the interface.
