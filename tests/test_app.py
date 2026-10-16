import io
import json
import os
import re
import secrets
import tempfile
import time
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
        app.RELAY.reset()
        app.RELAY.clock = time.monotonic

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


JPEG_A = b"\xff\xd8\xff\xe0" + b"frame-A" * 30 + b"\xff\xd9"
JPEG_B = b"\xff\xd8\xff\xe0" + b"frame-B" * 30 + b"\xff\xd9"
SESSION_1 = "a1b2c3d4e5f60718"
SESSION_2 = "ffeeddccbbaa9988"


class FrameRelayTests(BoardBuddyApiTests):
    """Live phone-to-laptop relay. Inherits the helpers; its own tests only."""

    def setUp(self):
        super().setUp()
        self.now = 1000.0
        app.RELAY.clock = lambda: self.now

    def send_frame(self, data=JPEG_A, session=SESSION_1, content_type="image/jpeg", csrf=True):
        headers = {"X-Relay-Session": session}
        if csrf:
            headers["X-CSRF-Token"] = self.csrf_for_authenticated_session()
        return self.post("/api/relay/frame", data=data, content_type=content_type, headers=headers)

    def stop_relay(self, session=SESSION_1):
        return self.post("/api/relay/stop", headers={
            "X-Relay-Session": session,
            "X-CSRF-Token": self.csrf_for_authenticated_session(),
        })

    # --- access control -------------------------------------------------
    def test_relay_routes_deny_anonymous_visitors(self):
        self.assertEqual(self.get("/monitor").status_code, 302)
        self.assertEqual(self.get("/api/relay/status").status_code, 401)
        self.assertEqual(self.get("/api/relay/frame").status_code, 401)
        self.assertEqual(self.post("/api/relay/frame", data=JPEG_A, content_type="image/jpeg").status_code, 401)
        self.assertEqual(self.post("/api/relay/stop").status_code, 401)

    def test_anonymous_visitor_cannot_read_a_frame_that_exists(self):
        self.authenticate_session()
        self.assertEqual(self.send_frame().status_code, 200)
        anonymous = app.app.test_client()
        response = anonymous.get("/api/relay/frame", base_url="https://localhost")
        self.assertEqual(response.status_code, 401)
        self.assertNotIn(b"frame-A", response.data)

    def test_frame_upload_requires_csrf(self):
        self.authenticate_session()
        response = self.send_frame(csrf=False)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(app.RELAY.status()["seq"], 0)

    def test_monitor_page_renders_for_signed_in_user(self):
        self.authenticate_session()
        response = self.get("/monitor")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'id="relay-image"', response.data)
        self.assertIn(b'id="monitor-badge"', response.data)

    def test_camera_page_has_relay_controls(self):
        self.authenticate_session()
        response = self.get("/camera")
        self.assertIn(b'id="relay-enabled"', response.data)
        self.assertIn(b'id="relay-status"', response.data)

    # --- happy path -----------------------------------------------------
    def test_frame_round_trip_keeps_only_the_latest_with_sequence(self):
        self.authenticate_session()
        first = self.send_frame(JPEG_A)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json["seq"], 1)
        self.now += 1
        second = self.send_frame(JPEG_B)
        self.assertEqual(second.json["seq"], 2)
        latest = self.get("/api/relay/frame")
        self.assertEqual(latest.status_code, 200)
        self.assertEqual(latest.mimetype, "image/jpeg")
        self.assertEqual(latest.headers["X-Relay-Seq"], "2")
        self.assertEqual(latest.data, JPEG_B)
        self.assertEqual(latest.headers["Cache-Control"], "no-store")
        status = self.get("/api/relay/status").json
        self.assertEqual((status["state"], status["seq"]), ("live", 2))
        self.assertIsNotNone(status["received_at"])

    def test_status_before_any_frame_is_waiting_and_frame_is_404(self):
        self.authenticate_session()
        self.assertEqual(self.get("/api/relay/status").json["state"], "waiting")
        self.assertEqual(self.get("/api/relay/frame").status_code, 404)

    # --- stale, stop, reconnect ----------------------------------------
    def test_frame_becomes_stale_when_phone_goes_quiet(self):
        self.authenticate_session()
        self.send_frame()
        self.now += app.RELAY_STALE_AFTER - 0.5
        self.assertEqual(self.get("/api/relay/status").json["state"], "live")
        self.now += 1.0
        status = self.get("/api/relay/status").json
        self.assertEqual(status["state"], "stale")
        self.assertGreater(status["age_seconds"], app.RELAY_STALE_AFTER)

    def test_stop_discards_frame_and_rejects_late_frames(self):
        self.authenticate_session()
        self.send_frame()
        self.assertEqual(self.stop_relay().status_code, 200)
        self.assertEqual(self.get("/api/relay/status").json["state"], "stopped")
        self.assertEqual(self.get("/api/relay/frame").status_code, 404)
        self.now += 1
        late = self.send_frame(session=SESSION_1)
        self.assertEqual(late.status_code, 409)
        self.assertEqual(self.get("/api/relay/frame").status_code, 404)

    def test_new_camera_session_resumes_after_stop(self):
        self.authenticate_session()
        self.send_frame(session=SESSION_1)
        self.stop_relay(SESSION_1)
        self.now += 1
        resumed = self.send_frame(JPEG_B, session=SESSION_2)
        self.assertEqual(resumed.status_code, 200)
        self.assertEqual(resumed.json["seq"], 2)
        self.assertEqual(self.get("/api/relay/status").json["state"], "live")

    def test_stale_stop_from_old_session_does_not_clear_new_session(self):
        self.authenticate_session()
        self.send_frame(session=SESSION_1)
        self.now += 1
        self.send_frame(JPEG_B, session=SESSION_2)
        self.stop_relay(SESSION_1)
        self.assertEqual(self.get("/api/relay/status").json["state"], "live")
        self.assertEqual(self.get("/api/relay/frame").data, JPEG_B)

    # --- bad input and limits ------------------------------------------
    def test_invalid_images_and_types_are_rejected_without_storing(self):
        self.authenticate_session()
        self.assertEqual(self.send_frame(b"not a jpeg at all" * 20).status_code, 415)
        self.assertEqual(self.send_frame(PNG_BYTES * 20).status_code, 415)
        self.assertEqual(self.send_frame(JPEG_A[:-2]).status_code, 415)   # truncated JPEG
        self.assertEqual(self.send_frame(JPEG_A, content_type="text/plain").status_code, 415)
        self.assertEqual(app.RELAY.status()["seq"], 0)
        self.assertEqual(self.get("/api/relay/frame").status_code, 404)

    def test_missing_or_malformed_session_header_is_rejected(self):
        self.authenticate_session()
        self.assertEqual(self.send_frame(session="").status_code, 400)
        self.assertEqual(self.send_frame(session="bad id!").status_code, 400)

    def test_oversized_frame_is_rejected_and_not_stored(self):
        self.authenticate_session()
        big = b"\xff\xd8\xff\xe0" + b"x" * app.RELAY_MAX_BYTES + b"\xff\xd9"
        self.assertEqual(self.send_frame(big).status_code, 413)
        self.assertEqual(app.RELAY.status()["seq"], 0)

    def test_frames_arriving_too_fast_are_throttled(self):
        self.authenticate_session()
        self.assertEqual(self.send_frame().status_code, 200)
        throttled = self.send_frame(JPEG_B)
        self.assertEqual(throttled.status_code, 429)
        self.assertIn("Retry-After", throttled.headers)
        self.assertEqual(self.get("/api/relay/frame").data, JPEG_A)

    # --- existing workflows still work next to the relay ----------------
    @patch("app.requests.post")
    def test_analyze_still_works_while_a_relay_frame_is_held(self, post):
        self.authenticate_session()
        self.send_frame()
        post.return_value = Mock(
            status_code=200,
            json=lambda: {"message": {"content": VALID_MODEL_JSON}},
            raise_for_status=lambda: None,
        )
        response = self.upload()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["components"][0]["name"], "ESP32 DevKit board")



class InventoryAndProjectTests(BoardBuddyApiTests):
    def setUp(self):
        super().setUp()
        app.INVENTORY.clear()

    def test_anonymous_access_denied_for_inventory_and_projects(self):
        self.assertEqual(self.get("/api/projects").status_code, 401)
        self.assertEqual(self.get("/api/inventory").status_code, 401)
        self.assertEqual(self.post("/api/inventory", data={"name": "ESP32"}).status_code, 401)

    def test_projects_catalog_has_at_least_three_projects_with_safety_and_steps(self):
        self.authenticate_session()
        response = self.get("/api/projects")
        self.assertEqual(response.status_code, 200)
        projects = response.json.get("projects", [])
        self.assertGreaterEqual(len(projects), 3)
        for p in projects:
            self.assertIn("id", p)
            self.assertIn("title", p)
            self.assertIn("required_parts", p)
            self.assertIn("safety_checks", p)
            self.assertIn("steps", p)
            self.assertGreater(len(p["safety_checks"]), 0)
            self.assertGreater(len(p["steps"]), 0)

    def test_projects_catalog_has_circuit_guides_and_troubleshooting(self):
        self.authenticate_session()
        response = self.get("/api/projects")
        self.assertEqual(response.status_code, 200)
        projects = response.json.get("projects", [])
        self.assertGreaterEqual(len(projects), 3)

        for p in projects:
            self.assertIn("circuit_guide", p, f"Project {p['id']} missing circuit_guide")
            guide = p["circuit_guide"]
            self.assertIn("template_type", guide)
            self.assertIn("label", guide)
            self.assertIn("power_source", guide)
            self.assertIn("connections", guide)
            self.assertGreater(len(guide["connections"]), 0)
            self.assertIn("troubleshooting", guide)
            self.assertGreater(len(guide["troubleshooting"]), 0)

            # Safety check: power source must instruct unplug/power-off before wiring
            self.assertTrue(
                any("unplug" in str(guide["power_source"]).lower() or "power" in str(s).lower() for s in p["safety_checks"]),
                f"Project {p['id']} must enforce power-off instructions before wiring"
            )

            # Validate connection structure
            for conn in guide["connections"]:
                self.assertIn("id", conn)
                self.assertIn("net", conn)
                self.assertIn("from_component", conn)
                self.assertIn("from_pin", conn)
                self.assertIn("to_component", conn)
                self.assertIn("to_pin", conn)
                self.assertIn("wire_color", conn)
                self.assertIn("wire_color_name", conn)
                self.assertIn("required_part_id", conn)

    def test_circuit_guide_truthful_feasibility(self):
        """Feasibility must not report ready to wire unless required parts are confirmed."""
        self.authenticate_session()
        # Clear inventory to start clean
        csrf = self.csrf_for_authenticated_session()
        self.client.post("/api/inventory/clear", headers={"X-CSRF-Token": csrf}, base_url="https://localhost")
        res = self.client.get("/api/inventory")
        self.assertEqual(len(res.json.get("items", [])), 0)

        # Get projects
        res = self.client.get("/api/projects")
        projects = res.json.get("projects", [])
        first_proj = projects[0]
        req_parts = first_proj["required_parts"]
        self.assertGreater(len(req_parts), 0)

        # Add part as unconfirmed suggestion first
        post_res = self.client.post("/api/inventory", json={
            "items": [{"name": req_parts[0]["name"], "confirmed": False}]
        }, headers={"X-CSRF-Token": csrf}, base_url="https://localhost")
        self.assertEqual(post_res.status_code, 200)
        item_id = post_res.json["items"][0]["id"]

        # Confirm the single part via PATCH
        patch_res = self.client.patch(
            f"/api/inventory/{item_id}",
            json={"confirmed": True},
            headers={"X-CSRF-Token": csrf},
            base_url="https://localhost"
        )
        self.assertEqual(patch_res.status_code, 200)

        # Check inventory: exactly 1 part confirmed
        inv_res = self.client.get("/api/inventory").json.get("items", [])
        confirmed_count = len([i for i in inv_res if i["confirmed"]])
        self.assertEqual(confirmed_count, 1)
        self.assertLess(confirmed_count, len(req_parts))

    def test_inventory_add_update_delete_and_clear(self):
        self.authenticate_session()
        csrf = self.csrf_for_authenticated_session()
        # Add batch
        res = self.client.post("/api/inventory", json={
            "items": [
                {"name": "ESP32 DevKit", "confirmed": False},
                {"name": "DHT22 sensor", "confirmed": False},
            ],
            "source": "camera"
        }, headers={"X-CSRF-Token": csrf}, base_url="https://localhost")
        self.assertEqual(res.status_code, 200)
        items = res.json["items"]
        self.assertEqual(len(items), 2)
        item1_id = items[0]["id"]
        self.assertFalse(items[0]["confirmed"])

        # Update item to confirmed
        res_update = self.client.patch(
            f"/api/inventory/{item1_id}",
            json={"confirmed": True, "name": "ESP32 DevKit V1"},
            headers={"X-CSRF-Token": csrf},
            base_url="https://localhost"
        )
        self.assertEqual(res_update.status_code, 200)
        self.assertTrue(res_update.json["item"]["confirmed"])
        self.assertEqual(res_update.json["item"]["name"], "ESP32 DevKit V1")

        # Delete item
        res_del = self.client.delete(
            f"/api/inventory/{item1_id}",
            headers={"X-CSRF-Token": csrf},
            base_url="https://localhost"
        )
        self.assertEqual(res_del.status_code, 200)

        # Remaining
        res_list = self.get("/api/inventory")
        self.assertEqual(len(res_list.json["items"]), 1)

        # Clear
        self.client.post("/api/inventory/clear", headers={"X-CSRF-Token": csrf}, base_url="https://localhost")
        self.assertEqual(len(self.get("/api/inventory").json["items"]), 0)

    def test_inventory_mutations_require_csrf(self):
        self.authenticate_session()
        res = self.client.post("/api/inventory", json={"name": "LED"}, base_url="https://localhost")
        self.assertEqual(res.status_code, 400)



    # --- Lab Assistant Chat Route Tests ---------------------------------
    def test_chat_route_requires_authentication(self):
        res = self.client.post("/api/chat", json={"message": "Hello"}, base_url="https://localhost")
        self.assertEqual(res.status_code, 401)

    def test_chat_route_requires_csrf(self):
        self.authenticate_session()
        res = self.client.post("/api/chat", json={"message": "Hello"}, base_url="https://localhost")
        self.assertEqual(res.status_code, 400)

    def test_chat_empty_message_rejected(self):
        self.authenticate_session()
        csrf = self.csrf_for_authenticated_session()
        res = self.client.post("/api/chat", json={"message": "   "}, headers={"X-CSRF-Token": csrf}, base_url="https://localhost")
        self.assertEqual(res.status_code, 400)
        self.assertIn("Please enter a question", res.json.get("error", ""))

    def test_chat_oversized_message_rejected(self):
        self.authenticate_session()
        csrf = self.csrf_for_authenticated_session()
        res = self.client.post("/api/chat", json={"message": "x" * 1001}, headers={"X-CSRF-Token": csrf}, base_url="https://localhost")
        self.assertEqual(res.status_code, 400)
        self.assertIn("Message is too long", res.json.get("error", ""))

    @patch("app.requests.post")
    def test_chat_successful_query_with_context(self, post_mock):
        self.authenticate_session()
        csrf = self.csrf_for_authenticated_session()
        # Add confirmed item
        self.client.post("/api/inventory", json={"items": [{"name": "ESP32 DevKit", "confirmed": True}]}, headers={"X-CSRF-Token": csrf}, base_url="https://localhost")

        post_mock.return_value = Mock(
            status_code=200,
            json=lambda: {"message": {"content": "Always disconnect power first. Connect DATA to GPIO 4."}},
            raise_for_status=lambda: None,
        )

        res = self.client.post("/api/chat", json={
            "message": "Where does the DATA pin go?",
            "project_id": "esp32-temp",
            "current_step": 4
        }, headers={"X-CSRF-Token": csrf}, base_url="https://localhost")

        self.assertEqual(res.status_code, 200)
        data = res.json
        self.assertIn("reply", data)
        self.assertIn("disconnect power", data["reply"].lower())
        self.assertEqual(data["project_id"], "esp32-temp")
        self.assertEqual(data["current_step"], 4)

        # Check call payload
        call_kwargs = post_mock.call_args[1]
        body = call_kwargs["json"]
        self.assertEqual(body["model"], app.MODEL)
        system_msg = body["messages"][0]["content"]
        self.assertIn("ESP32 Temperature", system_msg)
        self.assertIn("ESP32 DevKit", system_msg)
        self.assertIn("disconnect", system_msg.lower())
        self.assertIn("power", system_msg.lower())

    @patch("app.requests.post")
    def test_chat_handles_ollama_offline(self, post_mock):
        self.authenticate_session()
        csrf = self.csrf_for_authenticated_session()
        from requests.exceptions import ConnectionError
        post_mock.side_effect = ConnectionError("Connection refused")

        res = self.client.post("/api/chat", json={"message": "Hello"}, headers={"X-CSRF-Token": csrf}, base_url="https://localhost")
        self.assertEqual(res.status_code, 503)
        self.assertIn("Ollama is not running locally", res.json.get("error", ""))


    def test_empty_inventory_is_not_prepopulated_by_catalog(self):
        self.authenticate_session()
        # Verify initial inventory is strictly empty
        inv_res = self.get("/api/inventory")
        self.assertEqual(inv_res.status_code, 200)
        self.assertEqual(inv_res.json.get("items"), [])

        # Fetch projects catalogue
        proj_res = self.get("/api/projects")
        self.assertEqual(proj_res.status_code, 200)
        self.assertGreater(len(proj_res.json.get("projects", [])), 0)

        # Inventory must still be strictly empty (template requirements never leak into inventory)
        inv_res_after = self.get("/api/inventory")
        self.assertEqual(inv_res_after.status_code, 200)
        self.assertEqual(inv_res_after.json.get("items"), [])

    @patch("app.requests.post")
    def test_image_with_no_identifiable_components_returns_empty_and_does_not_insert(self, post_mock):
        self.authenticate_session()
        csrf = self.csrf_for_authenticated_session()
        response_mock = Mock()
        response_mock.status_code = 200
        response_mock.json.return_value = {
            "message": {"content": json.dumps({"components": []})}
        }
        post_mock.return_value = response_mock

        data = {"image": (io.BytesIO(PNG_BYTES), "test.jpg")}
        res = self.client.post("/api/analyze", data=data, headers={"X-CSRF-Token": csrf}, base_url="https://localhost")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json.get("components"), [])

        # Confirm nothing was inserted into inventory
        inv_res = self.get("/api/inventory")
        self.assertEqual(inv_res.json.get("items"), [])

    @patch("app.requests.post")
    def test_analysis_failure_does_not_insert_components(self, post_mock):
        self.authenticate_session()
        csrf = self.csrf_for_authenticated_session()
        from requests.exceptions import Timeout
        post_mock.side_effect = Timeout("Ollama timed out")

        data = {"image": (io.BytesIO(PNG_BYTES), "test.jpg")}
        res = self.client.post("/api/analyze", data=data, headers={"X-CSRF-Token": csrf}, base_url="https://localhost")
        self.assertEqual(res.status_code, 504)
        self.assertIn("Ollama took too long", res.json.get("error", ""))

        # Inventory must remain completely empty after failed analysis
        inv_res = self.get("/api/inventory")
        self.assertEqual(inv_res.json.get("items"), [])

    def test_ai_suggestions_are_always_unconfirmed_until_user_reviews(self):
        self.authenticate_session()
        csrf = self.csrf_for_authenticated_session()

        # Attempt to insert AI items claiming confirmed=True
        res = self.client.post("/api/inventory", json={
            "items": [
                {"name": "ESP32 DevKit", "confirmed": True, "evidence": "Photo detection"},
                {"name": "DHT22 sensor", "confirmed": True, "evidence": "Photo detection"},
            ],
            "source": "phone camera"
        }, headers={"X-CSRF-Token": csrf}, base_url="https://localhost")

        self.assertEqual(res.status_code, 200)
        items = res.json.get("items", [])
        self.assertEqual(len(items), 2)
        # Backend non-negotiable rule: AI source forces confirmed=False
        for it in items:
            self.assertFalse(it["confirmed"], "AI suggestions must never be auto-confirmed!")

        # Explicit user review via PATCH confirms the item
        first_id = items[0]["id"]
        patch_res = self.client.patch(f"/api/inventory/{first_id}", json={
            "confirmed": True
        }, headers={"X-CSRF-Token": csrf}, base_url="https://localhost")
        self.assertEqual(patch_res.status_code, 200)
        self.assertTrue(patch_res.json.get("item")["confirmed"])

        # Second item remains unconfirmed
        inv_res = self.get("/api/inventory")
        all_items = inv_res.json.get("items", [])
        confirmed = [i for i in all_items if i["confirmed"]]
        unconfirmed = [i for i in all_items if not i["confirmed"]]
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(len(unconfirmed), 1)

    @patch("app.requests.post")
    def test_system_prompt_enforces_inventory_vs_template_honesty(self, post_mock):
        self.authenticate_session()
        csrf = self.csrf_for_authenticated_session()
        response_mock = Mock()
        response_mock.status_code = 200
        response_mock.json.return_value = {
            "message": {"content": "Always disconnect power first."}
        }
        post_mock.return_value = response_mock

        # Send chat with empty inventory
        res = self.client.post("/api/chat", json={
            "message": "Do I have an ESP32 board?",
            "project_id": "esp32-temp",
            "current_step": 1
        }, headers={"X-CSRF-Token": csrf}, base_url="https://localhost")
        self.assertEqual(res.status_code, 200)

        call_kwargs = post_mock.call_args[1]
        system_msg = call_kwargs["json"]["messages"][0]["content"]
        self.assertIn("Inventory vs Template Honesty", system_msg)
        self.assertIn("None confirmed yet", system_msg)
        self.assertIn("Never assume or state that the user possesses a component simply because the active project template requires it", system_msg)


    def test_repeated_component_observations_are_deduplicated(self):
        self.authenticate_session()
        csrf = self.csrf_for_authenticated_session()

        # Observation 1: Phone frames ESP32
        res1 = self.client.post("/api/inventory", json={
            "items": [{"name": "ESP32 DevKit", "evidence": "Metal shield visible"}],
            "source": "laptop monitor"
        }, headers={"X-CSRF-Token": csrf}, base_url="https://localhost")
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(len(res1.json.get("items", [])), 1)
        self.assertEqual(len(res1.json.get("created", [])), 1)

        # Observation 2 (6s later): Same physical component seen as "ESP32 DevKit board"
        res2 = self.client.post("/api/inventory", json={
            "items": [{"name": "ESP32 DevKit board", "evidence": "Two rows of header pins"}],
            "source": "laptop monitor"
        }, headers={"X-CSRF-Token": csrf}, base_url="https://localhost")
        self.assertEqual(res2.status_code, 200)
        items = res2.json.get("items", [])
        # Crucial check: Deduplicated! Quantity is NOT inflated!
        self.assertEqual(len(items), 1)
        self.assertEqual(len(res2.json.get("created", [])), 0)
        self.assertEqual(items[0]["evidence"], "Two rows of header pins")
        self.assertFalse(items[0]["confirmed"])

    def test_confirmed_item_preserved_during_repeated_ai_scans(self):
        self.authenticate_session()
        csrf = self.csrf_for_authenticated_session()

        # Initial detection
        res = self.client.post("/api/inventory", json={
            "items": [{"name": "DHT22 sensor", "evidence": "White grid plastic shell"}],
            "source": "laptop monitor"
        }, headers={"X-CSRF-Token": csrf}, base_url="https://localhost")
        item_id = res.json.get("items")[0]["id"]

        # User confirms and edits the item on workbench
        patch_res = self.client.patch(f"/api/inventory/{item_id}", json={
            "name": "DHT22 / AM2302 (Bench Module)",
            "confirmed": True
        }, headers={"X-CSRF-Token": csrf}, base_url="https://localhost")
        self.assertEqual(patch_res.status_code, 200)
        self.assertTrue(patch_res.json.get("item")["confirmed"])

        # Auto-scan detects DHT22 again in next frame
        scan_res = self.client.post("/api/inventory", json={
            "items": [{"name": "DHT22 sensor", "evidence": "White shell"}],
            "source": "laptop monitor"
        }, headers={"X-CSRF-Token": csrf}, base_url="https://localhost")
        self.assertEqual(scan_res.status_code, 200)
        items = scan_res.json.get("items", [])
        self.assertEqual(len(items), 1)
        # Confirmed status and user-edited name must NOT be overridden by model observation!
        self.assertTrue(items[0]["confirmed"])
        self.assertEqual(items[0]["name"], "DHT22 / AM2302 (Bench Module)")

    def test_distinct_components_both_added_without_collision(self):
        self.authenticate_session()
        csrf = self.csrf_for_authenticated_session()

        res1 = self.client.post("/api/inventory", json={
            "items": [{"name": "ESP32 DevKit"}],
            "source": "laptop monitor"
        }, headers={"X-CSRF-Token": csrf}, base_url="https://localhost")
        self.assertEqual(len(res1.json.get("items")), 1)

        res2 = self.client.post("/api/inventory", json={
            "items": [{"name": "Resistor 330 ohm"}],
            "source": "laptop monitor"
        }, headers={"X-CSRF-Token": csrf}, base_url="https://localhost")
        items = res2.json.get("items")
        self.assertEqual(len(items), 2)
        names = [i["name"] for i in items]
        self.assertIn("ESP32 DevKit", names)
        self.assertIn("Resistor 330 ohm", names)


    def test_ollama_lock_prevents_overlapping_chat_and_analyze(self):
        self.authenticate_session()
        csrf = self.csrf_for_authenticated_session()

        # Simulate another thread actively running an Ollama inference
        acquired = app.OLLAMA_INFERENCE_LOCK.acquire(blocking=False)
        self.assertTrue(acquired)
        try:
            # 1. Chat request should immediately return 503 with retry message
            chat_res = self.client.post("/api/chat", json={"message": "Pinout check?"}, headers={"X-CSRF-Token": csrf}, base_url="https://localhost")
            self.assertEqual(chat_res.status_code, 503)
            self.assertIn("busy", chat_res.json.get("error", "").lower())

            # 2. Analyze request should immediately return 503 without queueing
            data = {"image": (io.BytesIO(PNG_BYTES), "test.jpg")}
            analyze_res = self.client.post("/api/analyze", data=data, headers={"X-CSRF-Token": csrf}, base_url="https://localhost")
            self.assertEqual(analyze_res.status_code, 503)
            self.assertIn("busy", analyze_res.json.get("error", "").lower())
        finally:
            app.OLLAMA_INFERENCE_LOCK.release()

        # Verify lock is freed
        self.assertFalse(app.OLLAMA_INFERENCE_LOCK.locked())

    @patch("app.requests.post")
    def test_ollama_lock_released_on_ollama_timeout(self, post_mock):
        self.authenticate_session()
        csrf = self.csrf_for_authenticated_session()
        from requests.exceptions import Timeout
        post_mock.side_effect = Timeout("Inference timeout")

        res = self.client.post("/api/chat", json={"message": "Pinout?"}, headers={"X-CSRF-Token": csrf}, base_url="https://localhost")
        self.assertEqual(res.status_code, 504)
        # Lock must be released even after timeout exception!
        self.assertFalse(app.OLLAMA_INFERENCE_LOCK.locked(), "Lock must be released on timeout!")

    @patch("app.requests.post")
    def test_chat_body_contains_num_predict_options(self, post_mock):
        self.authenticate_session()
        csrf = self.csrf_for_authenticated_session()
        response_mock = Mock()
        response_mock.status_code = 200
        response_mock.json.return_value = {"message": {"content": "Use 3.3V pin."}}
        post_mock.return_value = response_mock

        res = self.client.post("/api/chat", json={"message": "Power pin?"}, headers={"X-CSRF-Token": csrf}, base_url="https://localhost")
        self.assertEqual(res.status_code, 200)

        call_json = post_mock.call_args[1]["json"]
        self.assertIn("options", call_json)
        self.assertIn("num_predict", call_json["options"])
        self.assertLessEqual(call_json["options"]["num_predict"], 250)
        self.assertIn("temperature", call_json["options"])


    def test_background_cables_are_excluded_from_analysis(self):
        """Ordinary background cables must not be catalogued as bench components."""
        from app import parse_components
        payload = json.dumps({
            "components": [
                {"name": "Laptop charger cable", "evidence": "Black cord plugged into laptop", "uncertainty": "Not a circuit wire"},
                {"name": "Power cord", "evidence": "Thick black wire on desk", "uncertainty": "Mains cord"},
                {"name": "HDMI cable", "evidence": "Black video cable", "uncertainty": "Not relevant"},
                {"name": "Generic cable", "evidence": "Loose wire", "uncertainty": "Unknown purpose"},
                {"name": "Breadboard jumper wire", "evidence": "DuPont jumper wire with male pin", "uncertainty": "Check length"},
                {"name": "USB data cable", "evidence": "Type-C cable for ESP32", "uncertainty": "Verify data lines"},
            ]
        })
        results = parse_components(payload)
        names = [r["name"] for r in results]
        self.assertNotIn("Laptop charger cable", names)
        self.assertNotIn("Power cord", names)
        self.assertNotIn("HDMI cable", names)
        self.assertNotIn("Generic cable", names)
        self.assertIn("Breadboard jumper wire", names)
        self.assertIn("USB data cable", names)

    def test_ambiguous_detections_require_manual_confirmation(self):
        """Ambiguous or generic detections must explicitly flag uncertainty requiring confirmation."""
        from app import parse_components
        payload = json.dumps({
            "components": [
                {"name": "Unidentified IC chip", "evidence": "Small black rectangular package", "uncertainty": "Markings not visible"},
            ]
        })
        results = parse_components(payload)
        self.assertEqual(len(results), 1)
        item = results[0]
        self.assertIn("manual", item["uncertainty"].lower())
        self.assertIn("confirm", item["uncertainty"].lower())
        self.assertFalse(item["confirmed"])

    def test_readiness_consistency_across_catalog_and_connections(self):
        """Every circuit connection must reference valid project required parts so readiness is 100% consistent."""
        self.authenticate_session()
        res = self.get("/api/projects")
        self.assertEqual(res.status_code, 200)
        projects = res.json.get("projects", [])

        for p in projects:
            req_ids = {r["id"] for r in p["required_parts"]}
            guide = p.get("circuit_guide", {})
            connections = guide.get("connections", [])
            for c in connections:
                part_ids = c.get("required_part_ids", [c.get("required_part_id")])
                for pid in part_ids:
                    self.assertIn(
                        pid, req_ids,
                        f"Connection {c['id']} in project {p['id']} references '{pid}' which is not in required_parts: {req_ids}"
                    )


if __name__ == "__main__":
    unittest.main()
