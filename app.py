import base64
import hmac
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from functools import wraps
from urllib.parse import urlsplit

import requests
from flask import Flask, abort, jsonify, make_response, redirect, render_template, request, session, url_for
from requests.exceptions import ConnectionError, Timeout
from itsdangerous import BadSignature, URLSafeSerializer
from werkzeug.datastructures import CallbackDict
from flask.sessions import SessionInterface, SessionMixin


APP_DIR = os.path.dirname(os.path.abspath(__file__))
OLLAMA_URL = "http://localhost:11434"
MODEL = os.environ.get("BOARDBUDDY_MODEL", "gemma3:4b")
MAX_IMAGE_BYTES = 8 * 1024 * 1024
IMAGE_SIGNATURES = {
    "jpeg": (b"\xff\xd8\xff", "image/jpeg"),
    "png": (b"\x89PNG\r\n\x1a\n", "image/png"),
}

app = Flask(__name__, template_folder=os.path.join(APP_DIR, "templates"))
app.config["MAX_CONTENT_LENGTH"] = MAX_IMAGE_BYTES + 256 * 1024
app.config.update(
    SECRET_KEY=os.environ.get("BOARDBUDDY_SECRET_KEY"),
    BOARDBUDDY_PASSWORD=os.environ.get("BOARDBUDDY_PASSWORD"),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
)


class BoardBuddySession(CallbackDict, SessionMixin):
    def __init__(self, initial=None, sid=None, new=False):
        def on_update(_self):
            _self.modified = True

        super().__init__(initial, on_update)
        self.sid = sid
        self.new = new
        self.modified = False


class InMemorySessionInterface(SessionInterface):
    session_class = BoardBuddySession

    def __init__(self):
        self.sessions = {}

    def _serializer(self, app):
        return URLSafeSerializer(app.secret_key, salt="boardbuddy-session-id")

    def _remove_expired(self):
        now = time.time()
        for sid, (_data, expires_at) in list(self.sessions.items()):
            if expires_at <= now:
                self.sessions.pop(sid, None)

    def open_session(self, app, request):
        self._remove_expired()
        cookie_value = request.cookies.get(self.get_cookie_name(app))
        sid = None
        if cookie_value:
            try:
                sid = self._serializer(app).loads(cookie_value)
            except BadSignature:
                sid = None
        record = self.sessions.get(sid) if sid else None
        if record is None:
            return self.session_class(sid=secrets.token_urlsafe(32), new=True)
        data, _expires_at = record
        return self.session_class(data, sid=sid)

    def save_session(self, app, session, response):
        self._remove_expired()
        cookie_name = self.get_cookie_name(app)
        if not session:
            self.sessions.pop(session.sid, None)
            response.delete_cookie(
                cookie_name,
                domain=self.get_cookie_domain(app),
                path=self.get_cookie_path(app),
                secure=self.get_cookie_secure(app),
                httponly=self.get_cookie_httponly(app),
                samesite=self.get_cookie_samesite(app),
            )
            return

        if session.modified or session.new:
            expires_at = time.time() + app.permanent_session_lifetime.total_seconds()
            self.sessions[session.sid] = (dict(session), expires_at)
            cookie_value = self._serializer(app).dumps(session.sid)
            response.set_cookie(
                cookie_name,
                cookie_value,
                expires=self.get_expiration_time(app, session),
                httponly=self.get_cookie_httponly(app),
                secure=self.get_cookie_secure(app),
                samesite=self.get_cookie_samesite(app),
                domain=self.get_cookie_domain(app),
                path=self.get_cookie_path(app),
            )

    def regenerate(self, session):
        self.sessions.pop(session.sid, None)
        session.sid = secrets.token_urlsafe(32)
        session.new = True
        session.modified = True


app.session_interface = InMemorySessionInterface()

LOGIN_FAILURE_LIMIT = 5
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_LOCK_SECONDS = 5 * 60
LOGIN_FAILURES = {}


def validate_security_config(config=None):
    settings = app.config if config is None else config
    secret_key = settings.get("SECRET_KEY")
    password = settings.get("BOARDBUDDY_PASSWORD")
    if not isinstance(secret_key, str) or len(secret_key) < 32:
        raise RuntimeError("Set BOARDBUDDY_SECRET_KEY to a random secret of at least 32 characters.")
    if not isinstance(password, str) or len(password) < 16:
        raise RuntimeError("Set BOARDBUDDY_PASSWORD to a temporary password of at least 16 characters.")


validate_security_config()


def csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def request_is_authenticated():
    return session.get("authenticated") is True


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not request_is_authenticated():
            if request.path.startswith("/api/"):
                return jsonify({"error": "Authentication required."}), 401
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def safe_return_path(value):
    if (
        not value
        or not value.startswith("/")
        or value.startswith("//")
        or "\\" in value
        or any(ord(character) < 32 for character in value)
    ):
        return url_for("index")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        return url_for("index")
    return value


def login_throttle_state(client_ip, now=None):
    current_time = time.monotonic() if now is None else now
    failure_count, window_start, locked_until = LOGIN_FAILURES.get(client_ip, (0, current_time, 0))
    if locked_until > current_time:
        return True, max(1, int(locked_until - current_time))
    if current_time - window_start >= LOGIN_WINDOW_SECONDS:
        LOGIN_FAILURES.pop(client_ip, None)
        return False, 0
    return False, 0


def record_login_failure(client_ip, now=None):
    current_time = time.monotonic() if now is None else now
    failure_count, window_start, locked_until = LOGIN_FAILURES.get(client_ip, (0, current_time, 0))
    if current_time - window_start >= LOGIN_WINDOW_SECONDS:
        failure_count, window_start = 0, current_time
    failure_count += 1
    if failure_count >= LOGIN_FAILURE_LIMIT:
        locked_until = current_time + LOGIN_LOCK_SECONDS
    LOGIN_FAILURES[client_ip] = (failure_count, window_start, locked_until)


@app.context_processor
def inject_csrf_token():
    return {"csrf_token": csrf_token}


@app.before_request
def enforce_csrf():
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    if request.endpoint != "login" and not request_is_authenticated():
        if request.path.startswith("/api/"):
            return jsonify({"error": "Authentication required."}), 401
        return redirect(url_for("login", next=request.path))
    expected = session.get("csrf_token")
    supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
    if expected and supplied and hmac.compare_digest(expected, supplied):
        return None
    if request.path.startswith("/api/"):
        return jsonify({"error": "Invalid or missing CSRF token."}), 400
    abort(400, description="Invalid or missing CSRF token.")


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if request.endpoint != "static":
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def image_mime_type(data):
    for signature, mime_type in IMAGE_SIGNATURES.values():
        if data.startswith(signature):
            return mime_type
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


BACKGROUND_CABLE_TERMS = (
    "power cord", "power cable", "laptop charger", "charger cable",
    "ac adapter", "wall plug", "mains cord", "mains cable", "extension cord",
    "hdmi", "ethernet", "vga", "displayport", "headphone", "audio cable",
    "power strip", "power lead", "laptop power", "charger wire", "power supply cord",
    "ac power", "mains wire", "charging cable"
)

def is_background_cable(name, evidence=""):
    combined = (str(name) + " " + str(evidence)).lower()
    # Relevant project cables (breadboard jumper wires, microcontroller USB data cable) are preserved
    if any(k in combined for k in ("jumper", "dupont", "breadboard wire", "usb cable", "usb data", "type-c cable", "micro-usb", "micro usb")):
        return False
    return any(term in combined for term in BACKGROUND_CABLE_TERMS)

def parse_components(content):
    try:
        payload = json.loads(content)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("The model returned invalid JSON. Try another photo.") from exc

    components = payload.get("components") if isinstance(payload, dict) else None
    if not isinstance(components, list):
        raise ValueError("The model response did not include a component list.")

    clean_components = []
    for item in components:
        if not isinstance(item, dict):
            raise ValueError("The model returned an unreadable component entry.")
        fields = (item.get("name"), item.get("evidence"), item.get("uncertainty"))
        if not all(isinstance(value, str) for value in fields):
            raise ValueError("A component is missing its name, evidence, or uncertainty note.")
        name, evidence, uncertainty = (value.strip() for value in fields)
        if not name or not evidence or not uncertainty:
            raise ValueError("A component has an empty required field. Try another photo.")
        if name.lower() in ("none", "no components", "n/a", "unknown", "nothing"):
            continue
        # Exclude ordinary background cables unless explicitly relevant to project wiring
        lower_name = name.lower()
        is_circuit_wire = any(k in lower_name for k in ("jumper", "dupont", "breadboard wire", "usb cable", "usb data", "type-c", "micro-usb", "micro usb"))
        is_generic_or_bg_cable = (
            is_background_cable(name, evidence)
            or (any(term in lower_name for term in ("cable", "wire", "cord")) and not is_circuit_wire)
        )
        if is_generic_or_bg_cable:
            continue

        # For ambiguous or generic detections, explicitly note uncertainty and require manual confirmation
        is_ambiguous = any(k in lower_name for k in ("unclear", "ambiguous", "unidentified", "generic", "unknown", "possible", "unconfirmed", "suspected", "ic", "module", "chip")) or any(k in uncertainty.lower() for k in ("unclear", "cannot determine", "uncertain", "not clearly", "blurry", "cannot verify", "obscured"))
        final_uncertainty = uncertainty[:500]
        if is_ambiguous and not any(k in final_uncertainty.lower() for k in ("manual", "confirm")):
            final_uncertainty = f"{final_uncertainty} (Ambiguous detection: manual user confirmation required.)"[:500]

        clean_components.append({
            "name": name[:120],
            "evidence": evidence[:500],
            "uncertainty": final_uncertainty,
            "confirmed": False,
        })
    return clean_components


@app.get("/")
@login_required
def index():
    return render_template("index.html")


@app.get("/camera")
@login_required
def camera():
    return render_template("camera.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request_is_authenticated():
        return redirect(url_for("index"))

    next_path = safe_return_path(request.args.get("next") or request.form.get("next"))
    if request.method == "GET":
        return render_template("login.html", next_path=next_path)

    client_ip = request.remote_addr or "unknown"
    locked, retry_after = login_throttle_state(client_ip)
    if locked:
        response = make_response(
            render_template("login.html", next_path=next_path, error="Too many attempts. Wait five minutes and try again."),
            429,
        )
        response.headers["Retry-After"] = str(retry_after)
        return response

    supplied_password = request.form.get("password", "")
    expected_password = app.config.get("BOARDBUDDY_PASSWORD") or ""
    if not hmac.compare_digest(supplied_password, expected_password):
        record_login_failure(client_ip)
        return render_template("login.html", next_path=next_path, error="Password not accepted."), 401

    LOGIN_FAILURES.pop(client_ip, None)
    session.clear()
    app.session_interface.regenerate(session)
    session["authenticated"] = True
    session["csrf_token"] = secrets.token_urlsafe(32)
    session.permanent = True
    return redirect(next_path)


@app.post("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))


def server_settings(environ=None):
    settings = os.environ if environ is None else environ
    host = settings.get("BOARDBUDDY_HOST", "127.0.0.1")
    try:
        port = int(settings.get("BOARDBUDDY_PORT", "5000"))
    except ValueError as exc:
        raise RuntimeError("BOARDBUDDY_PORT must be a valid TCP port.") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("BOARDBUDDY_PORT must be between 1 and 65535.")

    cert_path = settings.get("BOARDBUDDY_TLS_CERT")
    key_path = settings.get("BOARDBUDDY_TLS_KEY")
    if bool(cert_path) != bool(key_path):
        raise RuntimeError("Set both BOARDBUDDY_TLS_CERT and BOARDBUDDY_TLS_KEY.")

    ssl_context = None
    if cert_path and key_path:
        cert_path = os.path.abspath(os.path.join(APP_DIR, cert_path)) if not os.path.isabs(cert_path) else cert_path
        key_path = os.path.abspath(os.path.join(APP_DIR, key_path)) if not os.path.isabs(key_path) else key_path
        if not os.path.isfile(cert_path) or not os.path.isfile(key_path):
            raise RuntimeError("The configured BoardBuddy TLS certificate or key file does not exist.")
        ssl_context = (cert_path, key_path)
    elif host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError(
            "Network access requires trusted HTTPS. Configure both BOARDBUDDY_TLS_CERT "
            "and BOARDBUDDY_TLS_KEY; do not expose BoardBuddy over plain HTTP."
        )

    return {"host": host, "port": port, "ssl_context": ssl_context}


@app.get("/api/health")
@login_required
def health():
    try:
        response = requests.get(f"{OLLAMA_URL}/api/tags", timeout=(2, 4))
        response.raise_for_status()
        model_names = [item.get("name", "") for item in response.json().get("models", [])]
        available = any(name == MODEL or name.startswith(f"{MODEL}:") for name in model_names)
        return jsonify({"ollama": "online", "model": MODEL, "model_available": available})
    except (ConnectionError, Timeout):
        return jsonify({"ollama": "offline", "model": MODEL, "model_available": False})
    except (requests.RequestException, ValueError, AttributeError):
        return jsonify({"ollama": "error", "model": MODEL, "model_available": False})


@app.post("/api/analyze")
@login_required
def analyze():
    uploaded = request.files.get("image")
    if uploaded is None or not uploaded.filename:
        return jsonify({"error": "Choose a photo before analyzing."}), 400

    image_bytes = uploaded.stream.read(MAX_IMAGE_BYTES + 1)
    if not image_bytes:
        return jsonify({"error": "The selected file is empty."}), 400
    if len(image_bytes) > MAX_IMAGE_BYTES:
        return jsonify({"error": "Images must be 8 MiB or smaller."}), 413

    mime_type = image_mime_type(image_bytes)
    if mime_type is None:
        return jsonify({"error": "Use a JPEG, PNG, or WebP image."}), 415

    prompt = (
        "Inspect this electronics-lab photo. Identify only components that are visibly "
        "supported by the image; do not infer hidden parts or electrical connections. "
        "If the image does not clearly show electronics hardware or components, or is blurry or dark, "
        "return an empty components array: {\"components\": []}. "
        "Do not create confident, specific component identifications from generic visual evidence. "
        "If an IC, module, or sensor does not have clearly legible markings, identify it generically "
        "(e.g. 'Unidentified IC / module') and state explicit uncertainty noting that manual user confirmation is required. "
        "Exclude ordinary background cables (such as power cords, laptop charger wires, audio cables, or loose appliance cords); "
        "only include cables if they are breadboard jumper wires or microcontroller USB cables directly relevant to an electronics project. "
        "Reply as JSON only, with this exact shape: "
        '{"components":[{"name":"...","evidence":"visible visual evidence",'
        '"uncertainty":"what cannot be determined from this photo and requires manual confirmation"}]}. '
        "Use an empty components array if no component can be identified. Do not include "
        "confidence percentages or claim electrical verification."
    )
    body = {
        "model": MODEL,
        "stream": False,
        "format": "json",
        "messages": [{
            "role": "user",
            "content": prompt,
            "images": [base64.b64encode(image_bytes).decode("ascii")],
        }],
        "options": {
            "num_predict": 250,
            "temperature": 0.1,
        },
    }

    acquired = OLLAMA_INFERENCE_LOCK.acquire(blocking=False)
    if not acquired:
        return jsonify({"error": "Local AI is currently busy with a chat question. Frame analysis skipped."}), 503

    try:
        response = requests.post(f"{OLLAMA_URL}/api/chat", json=body, timeout=(3, 35))
        response.raise_for_status()
    except ConnectionError:
        return jsonify({"error": "Ollama is not reachable on localhost:11434. Start Ollama and try again."}), 503
    except Timeout:
        return jsonify({"error": "Ollama took too long to respond. Try again with a smaller or clearer photo."}), 504
    except requests.HTTPError:
        detail = ""
        try:
            detail = response.json().get("error", "")
        except (ValueError, AttributeError):
            pass
        if response.status_code == 404:
            message = f"Model {MODEL} was not found by Ollama. Run: ollama pull {MODEL}"
        else:
            message = f"Ollama returned HTTP {response.status_code}."
        return jsonify({"error": message, "detail": detail[:300]}), 502
    except requests.RequestException:
        return jsonify({"error": "Could not complete the local Ollama request."}), 502
    finally:
        OLLAMA_INFERENCE_LOCK.release()

    try:
        content = response.json()["message"]["content"]
        components = parse_components(content)
    except (ValueError, KeyError, TypeError) as exc:
        message = str(exc) or "Ollama returned an unreadable response. Try again."
        return jsonify({"error": message}), 502

    return jsonify({"components": components, "model": MODEL})


# --- Project Catalog & Inventory Store --------------------------------------

PROJECTS_CATALOG = [
    {
        "id": "esp32-temp",
        "title": "ESP32 Temperature & Humidity Monitor",
        "difficulty": "Beginner · 6 parts",
        "summary": "A low-voltage bench build using an ESP32 DevKit and a DHT22 sensor to monitor ambient climate.",
        "required_parts": [
            {"id": "esp32", "name": "ESP32 DevKit board", "quantity": "1", "aliases": ["esp32", "esp-32"]},
            {"id": "dht22", "name": "DHT22 / AM2302 sensor", "quantity": "1", "aliases": ["dht22", "am2302"]},
            {"id": "resistor", "name": "10 kΩ pull-up resistor", "quantity": "1", "aliases": ["resistor", "10k", "10 k", "pull-up", "ohm"]},
            {"id": "breadboard", "name": "Breadboard", "quantity": "1", "aliases": ["breadboard", "solderless board"]},
            {"id": "jumpers", "name": "Male-to-male jumper wires", "quantity": "3+", "aliases": ["jumper", "dupont wire", "dupont cable"]},
            {"id": "usb", "name": "USB data cable for your board", "quantity": "1", "aliases": ["usb cable", "usb-c cable", "micro-usb cable", "type-c cable"]},
        ],
        "optional_note": "A 10 kΩ pull-up resistor may be required between DATA and 3V3 if your DHT22 is a bare sensor without a built-in resistor module.",
        "safety_checks": [
            "Disconnect USB and all power sources before inserting or moving jumper wires.",
            "Verify the exact ESP32 and DHT22 pinouts; some modules swap VCC and GND positions.",
            "Connect VCC strictly to 3.3V (never use 5V or mains voltage).",
            "Notice: Image analysis does not verify electrical connections; visually inspect every pin before plugging in."
        ],
        "steps": [
            "Unplug the ESP32 from USB and disconnect every power source before making connections.",
            "Identify the exact ESP32 board and DHT22 module pinout labels from their documentation.",
            "Place the ESP32 across the center divider of the breadboard so opposing pins do not short.",
            "Mount the DHT22 sensor in separate breadboard rows within easy reach.",
            "Connect sensor VCC to ESP32 3V3, GND to ESP32 GND, and DATA to GPIO 4. Leave any NC pin unconnected.",
            "If using a bare DHT22 without an integrated resistor, add a 10 kΩ resistor between DATA and 3V3.",
            "Visually inspect every connection against the pinout diagram. Only then connect USB power to program."
        ],
        "circuit_guide": {
            "template_type": "Supported Reference Circuit",
            "label": "ESP32 DevKit to DHT22 Interface (3.3V)",
            "power_source": "3.3V DC (Unplug USB before wiring)",
            "breadboard_layout_summary": "ESP32 placed across center breadboard divider. DHT22 placed in separate rows with common 3.3V and GND rails.",
            "connections": [
                {
                    "id": "c1",
                    "net": "VCC (+3.3V)",
                    "from_component": "ESP32 DevKit",
                    "from_pin": "3V3",
                    "to_component": "DHT22 Sensor",
                    "to_pin": "Pin 1 (VCC)",
                    "wire_color": "#ef4444",
                    "wire_color_name": "Red",
                    "required_part_id": "esp32",
                    "required_part_ids": ["esp32", "dht22"],
                    "notes": "Connect strictly to 3.3V. Never connect to 5V or VIN."
                },
                {
                    "id": "c2",
                    "net": "GND (0V)",
                    "from_component": "ESP32 DevKit",
                    "from_pin": "GND",
                    "to_component": "DHT22 Sensor",
                    "to_pin": "Pin 4 (GND)",
                    "wire_color": "#0f172a",
                    "wire_color_name": "Black",
                    "required_part_id": "esp32",
                    "required_part_ids": ["esp32", "dht22"],
                    "notes": "Common ground return path."
                },
                {
                    "id": "c3",
                    "net": "DATA Signal",
                    "from_component": "ESP32 DevKit",
                    "from_pin": "GPIO 4",
                    "to_component": "DHT22 Sensor",
                    "to_pin": "Pin 2 (DATA)",
                    "wire_color": "#f59e0b",
                    "wire_color_name": "Yellow",
                    "required_part_id": "dht22",
                    "required_part_ids": ["esp32", "dht22"],
                    "notes": "Bidirectional 1-wire communication line."
                },
                {
                    "id": "c4",
                    "net": "DATA Pull-Up",
                    "from_component": "10kΩ Resistor",
                    "from_pin": "Lead 1",
                    "to_component": "ESP32 3V3 Rail",
                    "to_pin": "3V3 (+)",
                    "wire_color": "#ef4444",
                    "wire_color_name": "Red",
                    "required_part_id": "resistor",
                    "required_part_ids": ["esp32", "resistor"],
                    "notes": "Pull-up resistor keeps idle data line high."
                },
                {
                    "id": "c5",
                    "net": "DATA Pull-Up",
                    "from_component": "10kΩ Resistor",
                    "from_pin": "Lead 2",
                    "to_component": "DHT22 Sensor",
                    "to_pin": "Pin 2 (DATA / GPIO4)",
                    "wire_color": "#3b82f6",
                    "wire_color_name": "Blue",
                    "required_part_id": "resistor",
                    "required_part_ids": ["resistor", "dht22"],
                    "notes": "Required if using bare 4-pin DHT22 without breakout pull-up."
                }
            ],
            "troubleshooting": [
                "Sensor outputs NaN or checksum error: Ensure 10kΩ pull-up resistor is installed between 3V3 and DATA pin.",
                "ESP32 reboots or USB disconnects: Immediately unplug USB! Check with multimeter that 3V3 and GND pins are not bridged.",
                "Pinout variation check: Some 3-pin breakout modules reverse VCC and DATA or label them [-, +, S]. Verify silkscreen markings before applying power.",
                "Pin 3 (NC): Pin 3 on bare DHT22 is Not Connected; leave it unconnected."
            ]
        }
    },
    {
        "id": "led-blinker",
        "title": "LED Logic Indicator & Current Limiter",
        "difficulty": "Beginner · 4 parts",
        "summary": "A fundamental safe bench circuit demonstrating diode polarity and current-limiting resistor protection.",
        "required_parts": [
            {"id": "esp32", "name": "ESP32 DevKit or 3.3V power", "quantity": "1", "aliases": ["esp32", "esp-32", "power supply", "battery"]},
            {"id": "led", "name": "Light Emitting Diode (LED)", "quantity": "1", "aliases": ["led", "light emitting diode", "diode"]},
            {"id": "resistor", "name": "Current-limiting resistor (220Ω - 330Ω)", "quantity": "1", "aliases": ["resistor", "220", "330", "ohm"]},
            {"id": "breadboard", "name": "Breadboard", "quantity": "1", "aliases": ["breadboard", "solderless board"]},
            {"id": "jumpers", "name": "Jumper wires", "quantity": "2+", "aliases": ["jumper", "dupont wire", "dupont cable"]},
        ],
        "optional_note": "Can be driven directly from GPIO 2 on ESP32 or from a regulated 3.3V power rail.",
        "safety_checks": [
            "Always use a current-limiting resistor; powering an LED directly from 3.3V/5V will destroy the LED or GPIO pin.",
            "Observe LED polarity: longer leg is Anode (+), shorter leg or flat edge is Cathode (-).",
            "Power down before placing or moving components."
        ],
        "steps": [
            "Disconnect power before placing components on the breadboard.",
            "Insert the LED into two separate tie-point rows on the breadboard.",
            "Place a 220Ω or 330Ω resistor in series with the LED cathode (shorter lead) connected to ground.",
            "Connect a jumper wire from the LED anode (longer lead) to GPIO 2 (or a 3.3V output pin).",
            "Verify the complete series path: Output Pin -> Anode -> Cathode -> Resistor -> GND.",
            "Verify polarity one last time before applying power."
        ],
        "circuit_guide": {
            "template_type": "Supported Reference Circuit",
            "label": "Current-Limited LED Indicator (3.3V Logic)",
            "power_source": "3.3V DC (Unplug USB before wiring)",
            "breadboard_layout_summary": "LED mounted on rows 10-11. Resistor connects cathode row to GND rail. Jumper connects anode row to GPIO 2.",
            "connections": [
                {
                    "id": "c1",
                    "net": "Logic Drive",
                    "from_component": "ESP32 DevKit",
                    "from_pin": "GPIO 2",
                    "to_component": "LED",
                    "to_pin": "Anode (+) [Long leg]",
                    "wire_color": "#22c55e",
                    "wire_color_name": "Green",
                    "required_part_id": "led",
                    "required_part_ids": ["esp32", "led"],
                    "notes": "High logic level (3.3V) turns LED on."
                },
                {
                    "id": "c2",
                    "net": "Current Limiter",
                    "from_component": "LED",
                    "from_pin": "Cathode (-) [Short leg/flat rim]",
                    "to_component": "220Ω - 330Ω Resistor",
                    "to_pin": "Lead 1",
                    "wire_color": "#94a3b8",
                    "wire_color_name": "Silver (Direct Row)",
                    "required_part_id": "resistor",
                    "required_part_ids": ["led", "resistor"],
                    "notes": "Limits forward diode current to ~5-10 mA."
                },
                {
                    "id": "c3",
                    "net": "GND Return",
                    "from_component": "220Ω - 330Ω Resistor",
                    "from_pin": "Lead 2",
                    "to_component": "ESP32 DevKit",
                    "to_pin": "GND",
                    "wire_color": "#0f172a",
                    "wire_color_name": "Black",
                    "required_part_id": "esp32",
                    "required_part_ids": ["resistor", "esp32"],
                    "notes": "Return path to common ground."
                }
            ],
            "troubleshooting": [
                "LED does not illuminate: Reverse LED orientation. Anode (longer leg) must connect to GPIO pin; cathode (flat side) to resistor and GND.",
                "LED burns out or ESP32 resets: A current-limiting resistor is MANDATORY. Never connect LED directly across 3.3V and GND.",
                "GPIO pin choice: On ESP32, GPIO 2 is also connected to the on-board blue LED on many devkits."
            ]
        }
    },
    {
        "id": "ldr-sensor",
        "title": "Light-Sensing Nightlight (LDR Divider)",
        "difficulty": "Beginner · 5 parts",
        "summary": "An analog voltage-divider reading light levels with a photoresistor (LDR) to trigger visual feedback.",
        "required_parts": [
            {"id": "esp32", "name": "ESP32 DevKit board", "quantity": "1", "aliases": ["esp32", "esp-32"]},
            {"id": "ldr", "name": "Photoresistor / LDR", "quantity": "1", "aliases": ["ldr", "photoresistor", "photocell", "light sensor"]},
            {"id": "resistor", "name": "10 kΩ pull-down resistor", "quantity": "1", "aliases": ["resistor", "10k", "ohm"]},
            {"id": "breadboard", "name": "Breadboard", "quantity": "1", "aliases": ["breadboard", "solderless board"]},
            {"id": "jumpers", "name": "Male-to-male jumper wires", "quantity": "4+", "aliases": ["jumper", "dupont wire", "dupont cable"]},
        ],
        "optional_note": "Add an LED with 220Ω resistor to create an automatic dark-detecting nightlight.",
        "safety_checks": [
            "Never connect 5V to the analog input pins of an ESP32 (maximum rated voltage is 3.3V).",
            "Double-check that the divider voltage originates from the 3V3 pin, not VIN or 5V.",
            "Disconnect USB power before adjusting the resistor bridge."
        ],
        "steps": [
            "Disconnect USB power completely before wiring the voltage divider.",
            "Insert the LDR and a 10 kΩ resistor into the breadboard so they share a common junction row.",
            "Connect the free leg of the LDR to the 3.3V rail with a jumper wire.",
            "Connect the free leg of the 10 kΩ resistor to the GND rail with a jumper wire.",
            "Connect a jumper wire from the shared junction row to ESP32 GPIO 34 (ADC1 input).",
            "Trace the voltage divider: 3V3 -> LDR -> Junction (GPIO 34) -> 10k Resistor -> GND.",
            "Plug in USB power and read ADC analog values as ambient light changes."
        ],
        "circuit_guide": {
            "template_type": "Supported Reference Circuit",
            "label": "Photoresistor (LDR) Voltage Divider (ADC1)",
            "power_source": "3.3V DC (Unplug USB before wiring)",
            "breadboard_layout_summary": "LDR from 3.3V rail to junction row. 10kΩ resistor from junction row to GND. Jumper from junction row to GPIO 34.",
            "connections": [
                {
                    "id": "c1",
                    "net": "VCC (+3.3V)",
                    "from_component": "ESP32 DevKit",
                    "from_pin": "3V3",
                    "to_component": "LDR (Photoresistor)",
                    "to_pin": "Lead 1",
                    "wire_color": "#ef4444",
                    "wire_color_name": "Red",
                    "required_part_id": "ldr",
                    "required_part_ids": ["esp32", "ldr"],
                    "notes": "Powers top half of divider bridge."
                },
                {
                    "id": "c2",
                    "net": "Divider Junction",
                    "from_component": "LDR (Photoresistor)",
                    "from_pin": "Lead 2",
                    "to_component": "10kΩ Resistor",
                    "to_pin": "Lead 1",
                    "wire_color": "#94a3b8",
                    "wire_color_name": "Direct Row",
                    "required_part_id": "resistor",
                    "required_part_ids": ["ldr", "resistor"],
                    "notes": "Shared junction row forms voltage divider output."
                },
                {
                    "id": "c3",
                    "net": "Analog Sense",
                    "from_component": "Divider Junction Row",
                    "from_pin": "Junction",
                    "to_component": "ESP32 DevKit",
                    "to_pin": "GPIO 34 (ADC1)",
                    "wire_color": "#f59e0b",
                    "wire_color_name": "Yellow",
                    "required_part_id": "jumpers",
                    "required_part_ids": ["esp32", "jumpers"],
                    "notes": "Connect only to ADC1 pins (GPIO 32-39). Maximum 3.3V."
                },
                {
                    "id": "c4",
                    "net": "GND (0V)",
                    "from_component": "10kΩ Resistor",
                    "from_pin": "Lead 2",
                    "to_component": "ESP32 DevKit",
                    "to_pin": "GND",
                    "wire_color": "#0f172a",
                    "wire_color_name": "Black",
                    "required_part_id": "esp32",
                    "required_part_ids": ["resistor", "esp32"],
                    "notes": "Completes bottom half of voltage divider to GND."
                }
            ],
            "troubleshooting": [
                "ADC reading stuck at 4095: Verify 10kΩ resistor is properly grounded and divider junction connects between LDR and resistor.",
                "ADC reading stuck at 0: Ensure 3.3V is connected to LDR and check for loose breadboard jumpers.",
                "Input voltage check: Measure voltage at GPIO 34 with a multimeter in daylight; ensure it does not exceed 3.3V."
            ]
        }
    },
    {
        "id": "transistor-driver",
        "title": "Transistor Switch & Flyback Protection",
        "difficulty": "Intermediate · 5 parts",
        "summary": "A low-side BJT/MOSFET driver circuit with flyback diode protection for driving higher-current loads safely.",
        "required_parts": [
            {"id": "transistor", "name": "NPN Transistor (2N2222 / 2N3904)", "quantity": "1", "aliases": ["transistor", "2n2222", "2n3904", "npn", "bjt"]},
            {"id": "diode", "name": "Flyback diode (1N4001 or 1N4148)", "quantity": "1", "aliases": ["diode", "1n4001", "1n4148", "rectifier"]},
            {"id": "resistor", "name": "1 kΩ Base resistor", "quantity": "1", "aliases": ["resistor", "1k", "ohm"]},
            {"id": "breadboard", "name": "Breadboard", "quantity": "1", "aliases": ["breadboard", "solderless board"]},
            {"id": "jumpers", "name": "Jumper wires", "quantity": "4+", "aliases": ["jumper", "dupont wire", "dupont cable"]},
        ],
        "optional_note": "Protects microcontrollers from reverse inductive kickback spikes when driving motors, relays, or solenoids.",
        "safety_checks": [
            "Disconnect all power supplies and USB before placing or moving components on the breadboard.",
            "Verify transistor pinout (Collector, Base, Emitter) using the specific manufacturer package sheet.",
            "Always install the flyback diode with cathode (banded end) pointing to the positive voltage supply.",
            "Keep high-current load grounds connected to common ground, but never feed inductive kickback into GPIO pins."
        ],
        "steps": [
            "Disconnect all power supplies from the breadboard.",
            "Identify the Collector, Base, and Emitter leads of your NPN transistor.",
            "Connect the Emitter pin directly to common ground (GND).",
            "Connect the 1 kΩ current-limiting resistor between your control GPIO pin and the transistor Base.",
            "Connect the negative side of the load to the transistor Collector.",
            "Install the flyback diode directly across the load terminals, with the banded end (cathode) to positive.",
            "Inspect all orientations thoroughly before connecting external load power."
        ],
        "circuit_guide": {
            "template_type": "Supported Reference Circuit",
            "label": "Low-Side NPN Transistor Switch with Flyback Diode",
            "power_source": "3.3V Logic + External Load DC Supply (Unplug and disconnect all power before wiring)",
            "breadboard_layout_summary": "2N2222 NPN transistor mounted in rows 15-17. 1kΩ base resistor on row 16. Flyback diode across inductive load terminals.",
            "connections": [
                {
                    "id": "c1",
                    "net": "Base Drive",
                    "from_component": "ESP32 DevKit",
                    "from_pin": "Control GPIO",
                    "to_component": "1kΩ Resistor",
                    "to_pin": "Lead 1",
                    "wire_color": "#3b82f6",
                    "wire_color_name": "Blue",
                    "required_part_id": "resistor",
                    "required_part_ids": ["resistor"],
                    "notes": "Current-limiting resistor protects GPIO pin."
                },
                {
                    "id": "c2",
                    "net": "Base Connection",
                    "from_component": "1kΩ Resistor",
                    "from_pin": "Lead 2",
                    "to_component": "NPN Transistor (2N2222)",
                    "to_pin": "Base (Pin 2)",
                    "wire_color": "#94a3b8",
                    "wire_color_name": "Direct Row",
                    "required_part_id": "transistor",
                    "required_part_ids": ["resistor", "transistor"],
                    "notes": "Controls transistor switching state."
                },
                {
                    "id": "c3",
                    "net": "Emitter Ground",
                    "from_component": "NPN Transistor (2N2222)",
                    "from_pin": "Emitter (Pin 1)",
                    "to_component": "Common Ground Rail",
                    "to_pin": "GND",
                    "wire_color": "#0f172a",
                    "wire_color_name": "Black",
                    "required_part_id": "transistor",
                    "required_part_ids": ["transistor"],
                    "notes": "Low-side return to ground."
                },
                {
                    "id": "c4",
                    "net": "Switched Collector",
                    "from_component": "NPN Transistor (2N2222)",
                    "from_pin": "Collector (Pin 3)",
                    "to_component": "Load Terminal (-)",
                    "to_pin": "Negative Lead",
                    "wire_color": "#a855f7",
                    "wire_color_name": "Purple",
                    "required_part_id": "transistor",
                    "required_part_ids": ["transistor"],
                    "notes": "Collector pulls load to ground when active."
                },
                {
                    "id": "c5",
                    "net": "Flyback Cathode",
                    "from_component": "Flyback Diode (1N4001)",
                    "from_pin": "Cathode (Silver Band)",
                    "to_component": "Load Positive (+)",
                    "to_pin": "V+ Supply Rail",
                    "wire_color": "#ef4444",
                    "wire_color_name": "Red",
                    "required_part_id": "diode",
                    "required_part_ids": ["diode"],
                    "notes": "CRITICAL: Banded cathode MUST point to positive supply rail."
                },
                {
                    "id": "c6",
                    "net": "Flyback Anode",
                    "from_component": "Flyback Diode (1N4001)",
                    "from_pin": "Anode (Plain End)",
                    "to_component": "Load Terminal (-)",
                    "to_pin": "Collector Node",
                    "wire_color": "#a855f7",
                    "wire_color_name": "Purple",
                    "required_part_id": "diode",
                    "required_part_ids": ["diode"],
                    "notes": "Clamps inductive kickback spikes safely."
                }
            ],
            "troubleshooting": [
                "Transistor overheats immediately: Check transistor pinout! 2N2222 in TO-92 is E-B-C with flat side facing you. BC547 is C-B-E. Check datasheet for your exact part.",
                "Dead short on power-up: Check diode orientation immediately! If diode cathode is connected to ground instead of positive, it will short the power supply.",
                "Shared ground missing: The microcontroller GND and external load supply GND must be connected together for the transistor to switch properly."
            ]
        }
    }
]




# Mutex to ensure single-threaded local Ollama inference on CPU
OLLAMA_INFERENCE_LOCK = threading.Lock()

def is_same_component(existing_name, incoming_name):
    """Determine whether two component names refer to the same physical component."""
    clean1 = re.sub(r"[^a-z0-9]", "", str(existing_name).lower())
    clean2 = re.sub(r"[^a-z0-9]", "", str(incoming_name).lower())
    if not clean1 or not clean2:
        return False
    if clean1 == clean2 or clean1 in clean2 or clean2 in clean1:
        return True

    stop_words = {"sensor", "board", "module", "component", "the", "a", "an", "part", "device", "wires", "wire"}
    t1 = set(re.findall(r"[a-z0-9]+", str(existing_name).lower())) - stop_words
    t2 = set(re.findall(r"[a-z0-9]+", str(incoming_name).lower())) - stop_words
    if t1 and t2:
        if t1 == t2 or t1.issubset(t2) or t2.issubset(t1):
            return True
        overlap = t1 & t2
        if any(any(c.isdigit() for c in tok) for tok in overlap):
            return True
    return False

class InventoryStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._items = []
        self._next_id = 1

    def get_all(self):
        with self._lock:
            return list(self._items)

    def add(self, item_or_items, source="manual"):
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        items_to_add = item_or_items if isinstance(item_or_items, list) else [item_or_items]
        created = []
        with self._lock:
            for raw in items_to_add:
                if not isinstance(raw, dict):
                    continue
                name = str(raw.get("name", "")).strip()[:120]
                if not name or name.lower() in ("none", "no components", "n/a", "unknown", "nothing"):
                    continue
                evidence = str(raw.get("evidence", "Added by user."))[:500]
                is_ai_source = source in ("phone camera", "laptop monitor", "photo upload", "ai")
                if is_ai_source and (is_background_cable(name, evidence) or (name.lower() in ("cable", "wire", "cord") and not any(k in name.lower() for k in ("jumper", "dupont", "usb")))):
                    continue
                uncertainty = str(raw.get("uncertainty", "Confirm identity and pinout before wiring."))[:500]
                # Non-negotiable rule: AI detections must NEVER be auto-confirmed
                is_ai_source = source in ("phone camera", "laptop monitor", "photo upload", "ai")
                confirmed = False if is_ai_source else bool(raw.get("confirmed", False))

                # Deduplication: do not create duplicates or inflate count for repeated observations
                existing = None
                for it in self._items:
                    if is_same_component(it["name"], name):
                        existing = it
                        break

                if existing is not None:
                    # Item already observed: update timestamp and observation metadata without duplicating
                    existing["updated_at"] = now_iso
                    # Do not modify name or confirmation status of user-confirmed items
                    if not existing["confirmed"]:
                        if evidence and evidence != "Added by user.":
                            existing["evidence"] = evidence
                        if uncertainty:
                            existing["uncertainty"] = uncertainty
                    continue

                item = {
                    "id": self._next_id,
                    "name": name,
                    "evidence": evidence,
                    "uncertainty": uncertainty,
                    "confirmed": confirmed,
                    "source": str(raw.get("source", source))[:32],
                    "created_at": now_iso,
                }
                self._next_id += 1
                self._items.append(item)
                created.append(item)
        return created

    def update(self, item_id, updates):
        with self._lock:
            for item in self._items:
                if item["id"] == item_id:
                    if "name" in updates:
                        name = str(updates["name"]).strip()[:120]
                        if name:
                            item["name"] = name
                    if "confirmed" in updates:
                        item["confirmed"] = bool(updates["confirmed"])
                    if "evidence" in updates:
                        item["evidence"] = str(updates["evidence"])[:500]
                    if "uncertainty" in updates:
                        item["uncertainty"] = str(updates["uncertainty"])[:500]
                    return dict(item)
        return None

    def delete(self, item_id):
        with self._lock:
            initial_len = len(self._items)
            self._items = [item for item in self._items if item["id"] != item_id]
            return len(self._items) < initial_len

    def clear(self):
        with self._lock:
            self._items.clear()


INVENTORY = InventoryStore()


@app.get("/api/projects")
@login_required
def get_projects():
    return jsonify({"projects": PROJECTS_CATALOG})


@app.get("/api/inventory")
@login_required
def get_inventory():
    return jsonify({"items": INVENTORY.get_all()})


@app.post("/api/inventory")
@login_required
def add_inventory():
    data = request.get_json(silent=True) or {}
    items_data = data.get("items")
    if items_data is None:
        items_data = [data] if "name" in data else []
    created = INVENTORY.add(items_data, source=data.get("source", "manual"))
    return jsonify({"ok": True, "created": created, "items": INVENTORY.get_all()})


@app.patch("/api/inventory/<int:item_id>")
@login_required
def update_inventory_item(item_id):
    data = request.get_json(silent=True) or {}
    updated = INVENTORY.update(item_id, data)
    if updated is None:
        return jsonify({"error": "Item not found."}), 404
    return jsonify({"ok": True, "item": updated})


@app.delete("/api/inventory/<int:item_id>")
@login_required
def delete_inventory_item(item_id):
    deleted = INVENTORY.delete(item_id)
    if not deleted:
        return jsonify({"error": "Item not found."}), 404
    return jsonify({"ok": True})


@app.post("/api/inventory/clear")
@login_required
def clear_inventory():
    INVENTORY.clear()
    return jsonify({"ok": True})


@app.post("/api/chat")
@login_required
def lab_assistant_chat():
    data = request.get_json(silent=True) or {}
    user_message = str(data.get("message", "")).strip()
    if not user_message:
        return jsonify({"error": "Please enter a question or message for the lab assistant."}), 400
    if len(user_message) > 1000:
        return jsonify({"error": "Message is too long (maximum 1000 characters)."}), 400

    project_id = str(data.get("project_id", "")).strip()
    current_step_idx = data.get("current_step")
    try:
        current_step_num = int(current_step_idx) if current_step_idx is not None else 1
    except (ValueError, TypeError):
        current_step_num = 1

    selected_project = None
    for p in PROJECTS_CATALOG:
        if p["id"] == project_id:
            selected_project = p
            break
    if selected_project is None and PROJECTS_CATALOG:
        selected_project = PROJECTS_CATALOG[0]

    all_inventory = INVENTORY.get_all()
    confirmed = [item["name"] for item in all_inventory if item.get("confirmed")]
    unconfirmed = [item["name"] for item in all_inventory if not item.get("confirmed")]

    steps = selected_project.get("steps", []) if selected_project else []
    step_text = steps[current_step_num - 1] if selected_project and 1 <= current_step_num <= len(steps) else "General overview"
    safety_checks = "; ".join(selected_project.get("safety_checks", [])) if selected_project else "Always disconnect power before wiring."
    required_parts = [r["name"] for r in selected_project.get("required_parts", [])] if selected_project else []

    circuit_guide = selected_project.get("circuit_guide") if selected_project else None
    circuit_summary = ""
    if circuit_guide:
        conn_lines = [
            f"  - {c['from_component']} ({c['from_pin']}) -> {c['to_component']} ({c['to_pin']}) [{c['wire_color_name']} wire, {c['net']}]"
            for c in circuit_guide.get("connections", [])
        ]
        trouble_lines = [f"  - {t}" for t in circuit_guide.get("troubleshooting", [])]
        circuit_summary = (
            f"- Circuit Template: {circuit_guide.get('label', '')} ({circuit_guide.get('template_type', 'Reference')})\n"
            f"- Power Source: {circuit_guide.get('power_source', '3.3V DC')}\n"
            f"- Verified Pin Connections:\n" + "\n".join(conn_lines) + "\n"
            f"- Troubleshooting & Diagnostics:\n" + "\n".join(trouble_lines) + "\n"
        )

    system_prompt = (
        "You are BoardBuddy, an expert AI electronics workbench assistant running locally with Gemma 3 4B on the engineer's laptop.\n"
        "You are pair-programming and pair-wiring with an engineer at an electronics lab bench.\n\n"
        f"BENCH CONTEXT:\n"
        f"- Active Project: {selected_project.get('title', 'General Hardware') if selected_project else 'Bench'}\n"
        f"- Summary: {selected_project.get('summary', '') if selected_project else ''}\n"
        f"- Required Parts: {', '.join(required_parts)}\n"
        f"- Confirmed Inventory: {', '.join(confirmed) if confirmed else 'None confirmed yet'}\n"
        f"- Unconfirmed AI Suggestions: {', '.join(unconfirmed) if unconfirmed else 'None'}\n"
        f"- Current Step ({current_step_num} of {len(steps)}): {step_text}\n"
        f"- Project Safety Rules: {safety_checks}\n"
        f"{circuit_summary}\n"
        "STRICT SAFETY & RELIABILITY RULES:\n"
        "1. Power Safety: ALWAYS advise the user to disconnect USB and power supplies before moving any wires or parts.\n"
        "2. Pinout Honesty: If asked about specific pin numbers, package pins (e.g. TO-92 transistor pins E/B/C, DHT22 pinout, ESP32 pin labels, diode cathode bands), state standard patterns but EXPLICITLY WARN that pin arrangements vary by manufacturer/module and the user MUST check their exact datasheet or PCB silkscreen.\n"
        "3. Verification: Never claim that an image or software verifies physical electrical continuity or short-circuit safety. Instruct the user to use a multimeter (continuity/diode mode).\n"
        "4. Voltage Limit: Strictly low voltage (3.3V or 5V USB). Never recommend mains voltage (110V/230V).\n"
        "5. Inventory vs Template Honesty: Never assume or state that the user possesses a component simply because the active project template requires it. Components only exist on the bench if explicitly listed under Confirmed Inventory or Unconfirmed AI Suggestions. When confirmed inventory is empty, explicitly state that no physical components have been confirmed on the bench yet, and remind the user to capture frames and confirm parts.\n"
        "6. Tone & Style: Be concise, direct, helpful, and technically accurate. Format using short bullet points where appropriate."
    )

    body = {
        "model": MODEL,
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "options": {
            "num_predict": 180,
            "temperature": 0.2,
        },
    }

    acquired = OLLAMA_INFERENCE_LOCK.acquire(blocking=False)
    if not acquired:
        return jsonify({"error": "Local AI is currently busy analyzing a camera frame. Please retry in a few seconds."}), 503

    try:
        response = requests.post(f"{OLLAMA_URL}/api/chat", json=body, timeout=(3, 25))
        response.raise_for_status()
        reply_content = response.json()["message"]["content"]
    except ConnectionError:
        return jsonify({"error": "Ollama is not running locally on port 11434. Start Ollama and try again."}), 503
    except Timeout:
        return jsonify({"error": "Ollama took too long to answer (>25s). Try asking a shorter question."}), 504
    except (requests.RequestException, KeyError, ValueError) as exc:
        return jsonify({"error": f"Could not get response from local model: {str(exc)}"}), 502
    finally:
        OLLAMA_INFERENCE_LOCK.release()

    return jsonify({
        "reply": reply_content,
        "model": MODEL,
        "project_id": selected_project["id"] if selected_project else None,
        "current_step": current_step_num,
    })


# --- Live camera relay -------------------------------------------------------
# The phone posts small JPEG frames; the laptop polls for the newest one. Only the
# latest frame is kept, in memory, and it is discarded when the phone stops.

RELAY_MAX_BYTES = 1024 * 1024        # one relay frame; phone frames are normally ~30-100 KB
RELAY_MIN_INTERVAL = 0.1             # seconds; faster posts are rejected with HTTP 429
RELAY_STALE_AFTER = 3.0              # seconds without a new frame before the laptop shows "stale"
RELAY_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class FrameRelay:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        with self._lock:
            self._frame = None
            self._seq = 0
            self._received_at = None      # monotonic seconds, for ages
            self._received_iso = None     # wall-clock UTC, for display
            self._session_id = None
            self._stopped_id = None
            self._stopped = False

    def put(self, data, session_id):
        with self._lock:
            now = self.clock()
            if session_id == self._stopped_id:
                return "stopped", None
            if (
                session_id == self._session_id
                and self._received_at is not None
                and now - self._received_at < RELAY_MIN_INTERVAL
            ):
                return "too_fast", None
            self._frame = data
            self._seq += 1
            self._received_at = now
            self._received_iso = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
            self._session_id = session_id
            self._stopped = False
            return "ok", (self._seq, self._received_iso)

    def stop(self, session_id):
        with self._lock:
            if session_id != self._session_id and self._frame is not None:
                return False              # a different phone session owns the current frame
            self._stopped_id = session_id
            self._frame = None
            self._received_at = None
            self._received_iso = None
            self._stopped = True
            return True

    def frame(self):
        with self._lock:
            if self._frame is None:
                return None
            return self._frame, self._seq

    def status(self):
        with self._lock:
            if self._frame is None:
                state, age = ("stopped" if self._stopped else "waiting"), None
            else:
                age = max(0.0, self.clock() - self._received_at)
                state = "live" if age <= RELAY_STALE_AFTER else "stale"
            return {
                "state": state,
                "seq": self._seq,
                "age_seconds": None if age is None else round(age, 2),
                "received_at": self._received_iso,
                "stale_after_seconds": RELAY_STALE_AFTER,
            }


RELAY = FrameRelay()


def relay_session_id():
    value = request.headers.get("X-Relay-Session", "")
    return value if RELAY_ID_PATTERN.match(value) else None


def looks_like_jpeg(data):
    return len(data) >= 100 and data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9")


@app.post("/api/relay/frame")
@login_required
def relay_frame():
    session_id = relay_session_id()
    if session_id is None:
        return jsonify({"error": "Missing or invalid X-Relay-Session header."}), 400
    if request.mimetype != "image/jpeg":
        return jsonify({"error": "Relay frames must be sent as image/jpeg."}), 415
    if request.content_length is not None and request.content_length > RELAY_MAX_BYTES:
        return jsonify({"error": "Relay frame is too large."}), 413
    data = request.stream.read(RELAY_MAX_BYTES + 1)
    if len(data) > RELAY_MAX_BYTES:
        return jsonify({"error": "Relay frame is too large."}), 413
    if not looks_like_jpeg(data):
        return jsonify({"error": "Relay frames must be valid JPEG images."}), 415

    outcome, details = RELAY.put(data, session_id)
    if outcome == "stopped":
        return jsonify({"error": "This camera session was stopped. Start the camera again."}), 409
    if outcome == "too_fast":
        response = jsonify({"error": "Frames are arriving too quickly."})
        response.status_code = 429
        response.headers["Retry-After"] = "1"
        return response
    seq, received_at = details
    return jsonify({"ok": True, "seq": seq, "received_at": received_at})


@app.post("/api/relay/stop")
@login_required
def relay_stop():
    session_id = relay_session_id()
    if session_id is None:
        return jsonify({"error": "Missing or invalid X-Relay-Session header."}), 400
    RELAY.stop(session_id)
    return jsonify({"ok": True})


@app.get("/api/relay/status")
@login_required
def relay_status():
    return jsonify(RELAY.status())


@app.get("/api/relay/frame")
@login_required
def relay_latest_frame():
    latest = RELAY.frame()
    if latest is None:
        return jsonify({"error": "No camera frame is available."}), 404
    data, seq = latest
    response = make_response(data)
    response.headers["Content-Type"] = "image/jpeg"
    response.headers["X-Relay-Seq"] = str(seq)
    return response


@app.get("/monitor")
@login_required
def monitor():
    return render_template("monitor.html")


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify({"error": "The upload is too large. Images must be 8 MiB or smaller."}), 413


if __name__ == "__main__":
    app.run(debug=False, **server_settings())