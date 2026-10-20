# AgriFlow Multilingual Web App

Includes:
- Registration and login pages
- English, Hindi, and Marathi UI language choices
- Flask + SQLite user accounts (passwords hashed using Werkzeug)
- Dashboard with valve control, timer, flow rate, total litres
- ESP32 device endpoints for telemetry and command polling
- Starter ESP32 firmware
- Render deployment blueprint

## Run locally on Mac

```bash
cd AgriFlow_Multilingual_Project
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 app.py
```

Open http://127.0.0.1:5000 and register a new account.

Default local mode is simulation. Simulation values are demo data, not real sensor readings.

## Deploy on Render

1. Upload the contents of this folder to a GitHub repository. `app.py`, `requirements.txt`, and `render.yaml` should be at repository root.
2. In Render, create a Web Service from that repository.
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn app:app`
5. Set environment variables:
   - `AGRIFLOW_SIMULATION=false` is the default. Dashboard will show `--` for flow/total/timer until ESP32 telemetry is received. To intentionally use demo mode, set `AGRIFLOW_SIMULATION=true`.
   - `FLASK_SECRET_KEY` to a long random secret (Render can generate one).
   - `AGRIFLOW_DEVICE_TOKEN` to a long random secret. Use the exact same value in ESP32 firmware.
6. For a real deployment, use persistent storage for SQLite (`AGRIFLOW_DB_PATH` pointing to a mounted persistent disk) or replace SQLite with a managed database. Without persistent storage, user accounts may be lost during redeploy/restart.
7. Add dashboard authentication/rate limiting/HTTPS before exposing valve control to real irrigation hardware. This starter uses login but does not implement email verification, password reset, rate limiting, or CSRF protection; do not treat it as a production security audit.

## ESP32 setup

Install ArduinoJson via Arduino IDE Library Manager. Edit `esp32_agriflow.ino`:
- `WIFI_SSID`, `WIFI_PASSWORD`
- `BASE_URL` = your Render service URL, no trailing slash
- `DEVICE_TOKEN` = exact `AGRIFLOW_DEVICE_TOKEN` from Render

Check relay active level and input compatibility. The sketch uses GPIO 23 for relay input and GPIO 27 for flow pulses. Calibrate `PULSES_PER_LITRE` with a measured volume. The sensor signal must be safe for 3.3V ESP32 GPIO.

## Critical hardware caveats

- This package is not physically tested with your exact parts.
- The API command state is in-memory and may reset when Render sleeps/restarts; this is not a reliable irrigation safety controller.
- Render free services can sleep; polling commands can be delayed.
- ESP32 local timeout is required, but the sample firmware must be reviewed and bench-tested. In particular, a cloud STOP command needs to reach the ESP32; test the complete START/STOP behavior before connecting the valve.
- Test with valve power disconnected first. Verify valve voltage/current, battery voltage, buck output, relay contact ratings, relay polarity, sensor output voltage, fusing and waterproofing.
- Never power the solenoid valve from an ESP32 GPIO or a breadboard.
