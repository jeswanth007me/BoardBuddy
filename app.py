import base64
import hmac
import json
import os
import secrets
import time
from datetime import timedelta
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
        clean_components.append({
            "name": name[:120],
            "evidence": evidence[:500],
            "uncertainty": uncertainty[:500],
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
        "Reply as JSON only, with this exact shape: "
        '{"components":[{"name":"...","evidence":"visible visual evidence",'
        '"uncertainty":"what cannot be determined from this photo"}]}. '
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
    }

    try:
        response = requests.post(f"{OLLAMA_URL}/api/chat", json=body, timeout=(3, 120))
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

    try:
        content = response.json()["message"]["content"]
        components = parse_components(content)
    except (ValueError, KeyError, TypeError) as exc:
        message = str(exc) or "Ollama returned an unreadable response. Try again."
        return jsonify({"error": message}), 502

    return jsonify({"components": components, "model": MODEL})


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify({"error": "The upload is too large. Images must be 8 MiB or smaller."}), 413


if __name__ == "__main__":
    app.run(debug=False, **server_settings())