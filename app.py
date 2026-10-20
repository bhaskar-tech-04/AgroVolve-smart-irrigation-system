import os
import time
import secrets
import sqlite3
from functools import wraps
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("AGRIFLOW_DB_PATH", BASE_DIR / "agriflow.db"))
SIMULATION = os.environ.get("AGRIFLOW_SIMULATION", "false").strip().lower() in ("1", "true", "yes", "on")
DEVICE_TOKEN = os.environ.get("AGRIFLOW_DEVICE_TOKEN", "")
SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "")
if not SECRET_KEY:
    # For local development only. Set FLASK_SECRET_KEY in production.
    SECRET_KEY = "local-dev-" + secrets.token_hex(24)

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "false").lower() == "true",
    MAX_CONTENT_LENGTH=1 * 1024 * 1024,
)

state = {
    "valve_open": False,
    "flow_lpm": 0.0,
    "total_litres": 0.0,
    "remaining_seconds": 0,
    "target_seconds": 0,
    "started_at": None,
    "last_telemetry": None,
    "device_online": False,
    "last_command": "STOP",
    "last_device_seen": 0,
}
last_sim_tick = time.time()


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()


init_db()


def logged_in(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def device_auth():
    # No device commands are accepted unless an explicit token is configured.
    if not DEVICE_TOKEN:
        return False
    supplied = request.headers.get("X-Device-Token", "")
    return secrets.compare_digest(supplied, DEVICE_TOKEN)


def tick_simulation():
    global last_sim_tick
    now = time.time()
    elapsed = max(0.0, now - last_sim_tick)
    last_sim_tick = now
    if SIMULATION and state["valve_open"]:
        state["remaining_seconds"] = max(0, int(state["target_seconds"] - (now - (state["started_at"] or now))))
        if state["remaining_seconds"] <= 0:
            state["valve_open"] = False
            state["flow_lpm"] = 0.0
            state["last_command"] = "STOP"
        else:
            # Demo-only flow. Never presented as a real sensor reading.
            state["flow_lpm"] = 3.2
            state["total_litres"] += 3.2 * elapsed / 60.0
    elif not state["valve_open"]:
        state["flow_lpm"] = 0.0


@app.route("/")
def home():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if len(name) < 2:
            flash("Please enter your name.", "error")
        elif "@" not in email or len(email) > 254:
            flash("Please enter a valid email address.", "error")
        elif len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
        else:
            try:
                with db() as conn:
                    cur = conn.execute(
                        "INSERT INTO users(name,email,password_hash) VALUES(?,?,?)",
                        (name, email, generate_password_hash(password, method="pbkdf2:sha256"))
                    )
                    conn.commit()
                    session.clear()
                    session["user_id"] = cur.lastrowid
                    session["user_name"] = name
                    session["lang"] = request.form.get("lang", "en")
                    return redirect(url_for("dashboard"))
            except sqlite3.IntegrityError:
                flash("An account with this email already exists.", "error")
    return render_template("auth.html", mode="register")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        with db() as conn:
            user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            session["lang"] = request.form.get("lang", "en")
            return redirect(url_for("dashboard"))
        flash("Email or password is incorrect.", "error")
    return render_template("auth.html", mode="login")


@app.route("/logout", methods=["POST"])
@logged_in
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@logged_in
def dashboard():
    return render_template("index.html", user_name=session.get("user_name", "Farmer"))


@app.route("/api/status")
@logged_in
def api_status():
    tick_simulation()
    now = time.time()
    online = (SIMULATION or (state["last_device_seen"] and now - state["last_device_seen"] < 45))
    return jsonify({
        "mode": "SIMULATION" if SIMULATION else "HARDWARE",
        "valve_open": state["valve_open"],
        "flow_lpm": round(float(state["flow_lpm"]), 2),
        "total_litres": round(float(state["total_litres"]), 3),
        "remaining_seconds": int(state["remaining_seconds"]),
        "device_online": bool(online),
        "last_telemetry": state["last_telemetry"],
        "message": "Demo values only" if SIMULATION else ("Device online" if online else "Waiting for ESP32 telemetry"),
    })


@app.route("/api/control", methods=["POST"])
@logged_in
def api_control():
    tick_simulation()
    data = request.get_json(silent=True) or {}
    action = str(data.get("action", "")).upper()
    if action == "START":
        try:
            duration = int(data.get("duration_seconds", 60))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Duration must be a number."}), 400
        if not 1 <= duration <= 86400:
            return jsonify({"ok": False, "error": "Duration must be between 1 and 86400 seconds."}), 400
        state["target_seconds"] = duration
        state["remaining_seconds"] = duration
        state["started_at"] = time.time()
        state["valve_open"] = True
        state["last_command"] = "START"
        if SIMULATION:
            state["flow_lpm"] = 3.2
        return jsonify({"ok": True, "message": "Start command queued.", "mode": "SIMULATION" if SIMULATION else "HARDWARE"})
    if action == "STOP":
        state["valve_open"] = False
        state["remaining_seconds"] = 0
        state["flow_lpm"] = 0.0
        state["last_command"] = "STOP"
        return jsonify({"ok": True, "message": "Stop command queued."})
    return jsonify({"ok": False, "error": "Action must be START or STOP."}), 400


@app.route("/api/device/command")
def device_command():
    if not device_auth():
        return jsonify({"ok": False, "error": "Device token invalid or not configured."}), 401
    tick_simulation()
    # ESP32 polls this endpoint. Hardware firmware must enforce its own local timeout.
    return jsonify({
        "ok": True,
        "command": state["last_command"],
        "duration_seconds": int(state["target_seconds"]),
        "remaining_seconds": int(state["remaining_seconds"]),
        "server_time": int(time.time()),
    })


@app.route("/api/device/telemetry", methods=["POST"])
def device_telemetry():
    if not device_auth():
        return jsonify({"ok": False, "error": "Device token invalid or not configured."}), 401
    data = request.get_json(silent=True) or {}
    try:
        flow = max(0.0, min(float(data.get("flow_lpm", 0)), 500.0))
        litres = max(0.0, min(float(data.get("total_litres", 0)), 1_000_000.0))
        remaining = max(0, min(int(data.get("remaining_seconds", 0)), 86400))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Invalid telemetry values."}), 400
    state["flow_lpm"] = flow
    state["total_litres"] = litres
    state["remaining_seconds"] = remaining
    state["valve_open"] = bool(data.get("valve_open", False))
    state["last_telemetry"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    state["last_device_seen"] = time.time()
    return jsonify({"ok": True})


@app.route("/health")
def health():
    return jsonify({"ok": True, "mode": "SIMULATION" if SIMULATION else "HARDWARE"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
