import io
import json
import os
import re
import secrets
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("BOARDBUDDY_SECRET_KEY", secrets.token_hex(32))
os.environ.setdefault("BOARDBUDDY_PASSWORD", secrets.token_urlsafe(24))

import app


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"test-image-data"
VALID_MODEL_JSON = json.dumps({
    "components": [{
        "name": "ESP32 DevKit board",
        "evidence": "A metal shield and two rows of header pins are visible.",
        "uncertainty": "The exact board revision cannot be read.",
    }],
})


class BoardBuddyApiTests(unittest.TestCase):
    def setUp(self):
        app.app.config["TESTING"] = True
        self.client = app.app.test_client()
        app.LOGIN_FAILURES.clear()
        app.app.session_interface.sessions.clear()

    def get(self, path, **kwargs):
        return self.client.get(path, base_url="https://localhost", **kwargs)

    def post(self, path, **kwargs):
        return self.client.post(path, base_url="https://localhost", **kwargs)

    def csrf_from_login(self):
        response = self.get("/login")
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode("ascii")

    def login(self, next_path="/"):
        csrf = self.csrf_from_login()
        return self.post("/login", data={
            "csrf_token": csrf,
            "password": os.environ["BOARDBUDDY_PASSWORD"],
            "next": next_path,
        })

    def authenticate_session(self):
        with self.client.session_transaction(base_url="https://localhost") as flask_session:
            flask_session["authenticated"] = True
            flask_session["csrf_token"] = secrets.token_urlsafe(32)

    def csrf_for_authenticated_session(self):
        with self.client.session_transaction(base_url="https://localhost") as flask_session:
            return flask_session["csrf_token"]

    def upload(self, payload=PNG_BYTES, filename="bench.png"):
        csrf = self.csrf_for_authenticated_session()
        return self.post(
            "/api/analyze",
            data={"image": (io.BytesIO(payload), filename)},
            content_type="multipart/form-data",
            headers={"X-CSRF-Token": csrf},
        )

    def test_camera_page_includes_live_preview_and_upload_fallback(self):
        self.authenticate_session()
        response = self.get("/camera")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'id="live-video"', response.data)
        self.assertIn(b'id="capture-analyze"', response.data)
        self.assertIn(b'id="fallback-image"', response.data)
        self.assertIn(b'name="csrf-token"', response.data)

    def test_all_protected_routes_deny_anonymous_visitors(self):
        home = self.get("/")
        camera = self.get("/camera")
        health = self.get("/api/health")
        analysis = self.post("/api/analyze", data={
            "image": (io.BytesIO(PNG_BYTES), "bench.png"),
        }, content_type="multipart/form-data")
        self.assertEqual(home.status_code, 302)
        self.assertIn("/login", home.location)
        self.assertEqual(camera.status_code, 302)
        self.assertIn("/login", camera.location)
        self.assertEqual(health.status_code, 401)
        self.assertEqual(analysis.status_code, 401)

    def test_password_login_redirects_and_sets_secure_session_cookie(self):
        response = self.login("/camera")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith("/camera"))
        set_cookie = response.headers["Set-Cookie"]
        self.assertIn("Secure", set_cookie)
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("SameSite=Lax", set_cookie)
        self.assertEqual(self.get("/camera").status_code, 200)
        self.assertEqual(self.get("/").status_code, 200)

    def test_session_data_is_server_side_and_logout_revokes_session(self):
        response = self.login("/")
        self.assertEqual(response.status_code, 302)
        cookie = response.headers["Set-Cookie"].split(";", 1)[0].split("=", 1)[1]
        self.assertNotIn("authenticated", cookie)
        self.assertEqual(len(app.app.session_interface.sessions), 1)
        camera_page = self.get("/camera")
        token = re.search(rb'<meta name="csrf-token" content="([^"]+)"', camera_page.data).group(1).decode("ascii")
        logout = self.post("/logout", data={"csrf_token": token})
        self.assertEqual(logout.status_code, 302)
        self.assertEqual(len(app.app.session_interface.sessions), 0)
        self.assertEqual(self.get("/camera").status_code, 302)

    def test_login_rejects_external_redirect_target(self):
        response = self.login("https://example.com/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith("/"))
        with app.app.test_request_context("/login"):
            self.assertEqual(app.safe_return_path("//example.com/"), "/")
            self.assertEqual(app.safe_return_path("/\\example.com/"), "/")

    def test_login_requires_csrf_and_throttles_bad_passwords(self):
        token = self.csrf_from_login()
        missing_csrf = self.post("/login", data={"password": "wrong-password"})
        self.assertEqual(missing_csrf.status_code, 400)
        for _ in range(app.LOGIN_FAILURE_LIMIT):
            response = self.post("/login", data={"csrf_token": token, "password": "incorrect"})
            self.assertEqual(response.status_code, 401)
        locked = self.post("/login", data={"csrf_token": token, "password": "incorrect"})
        self.assertEqual(locked.status_code, 429)
        self.assertIn("Retry-After", locked.headers)

    def test_authenticated_health_route_works(self):
        self.authenticate_session()
        with patch("app.requests.get") as get_tags:
            get_tags.return_value = Mock(
                json=lambda: {"models": [{"name": "gemma3:4b"}]},
                raise_for_status=lambda: None,
            )
            response = self.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["model_available"])

    @patch("app.requests.post")
    def test_authenticated_analysis_requires_csrf(self, post):
        self.authenticate_session()
        response = self.post(
            "/api/analyze",
            data={"image": (io.BytesIO(PNG_BYTES), "bench.png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)
        post.assert_not_called()

    def test_loopback_server_defaults_to_http(self):
        self.assertEqual(
            app.server_settings({}),
            {"host": "127.0.0.1", "port": 5000, "ssl_context": None},
        )

    def test_network_bind_requires_https(self):
        with self.assertRaisesRegex(RuntimeError, "trusted HTTPS"):
            app.server_settings({"BOARDBUDDY_HOST": "0.0.0.0"})

    def test_network_bind_uses_configured_certificate_pair(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cert = Path(temp_dir) / "boardbuddy.pem"
            key = Path(temp_dir) / "boardbuddy-key.pem"
            cert.write_text("test certificate", encoding="utf-8")
            key.write_text("test key", encoding="utf-8")
            config = app.server_settings({
                "BOARDBUDDY_HOST": "0.0.0.0",
                "BOARDBUDDY_TLS_CERT": os.fspath(cert),
                "BOARDBUDDY_TLS_KEY": os.fspath(key),
            })
            self.assertEqual(config["host"], "0.0.0.0")
            self.assertEqual(config["ssl_context"], (os.fspath(cert), os.fspath(key)))

    @patch("app.requests.post")
    def test_supported_image_is_sent_to_local_ollama(self, post):
        self.authenticate_session()
        post.return_value = Mock(
            status_code=200,
            json=lambda: {"message": {"content": VALID_MODEL_JSON}},
            raise_for_status=lambda: None,
        )
        response = self.upload()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["components"][0]["name"], "ESP32 DevKit board")
        self.assertEqual(post.call_args.args[0], "http://localhost:11434/api/chat")
        self.assertTrue(post.call_args.kwargs["json"]["messages"][0]["images"][0])

    @patch("app.requests.post")
    def test_unsupported_image_is_rejected_without_ollama_call(self, post):
        self.authenticate_session()
        response = self.upload(b"not an image")
        self.assertEqual(response.status_code, 415)
        post.assert_not_called()

    @patch("app.requests.post")
    def test_oversized_image_is_rejected(self, post):
        self.authenticate_session()
        response = self.upload(PNG_BYTES + b"x" * app.MAX_IMAGE_BYTES)
        self.assertEqual(response.status_code, 413)
        post.assert_not_called()

    @patch("app.requests.post")
    def test_malformed_model_json_returns_readable_error(self, post):
        self.authenticate_session()
        post.return_value = Mock(
            status_code=200,
            json=lambda: {"message": {"content": "not JSON"}},
            raise_for_status=lambda: None,
        )
        response = self.upload()
        self.assertEqual(response.status_code, 502)
        self.assertIn("invalid JSON", response.json["error"])

    @patch("app.requests.post")
    def test_missing_model_returns_pull_guidance(self, post):
        self.authenticate_session()
        ollama_response = Mock(status_code=404, json=lambda: {"error": "model not found"})
        ollama_response.raise_for_status.side_effect = app.requests.HTTPError("not found")
        post.return_value = ollama_response
        response = self.upload()
        self.assertEqual(response.status_code, 502)
        self.assertIn("ollama pull gemma3:4b", response.json["error"])

    @patch("app.requests.post", side_effect=app.ConnectionError("connection refused"))
    @patch("app.requests.get", side_effect=app.ConnectionError("connection refused"))
    def test_unavailable_ollama_has_actionable_status(self, get, post):
        self.authenticate_session()
        health = self.get("/api/health")
        self.assertEqual(health.json["ollama"], "offline")
        response = self.upload()
        self.assertEqual(response.status_code, 503)
        self.assertIn("Start Ollama", response.json["error"])


if __name__ == "__main__":
    unittest.main()
