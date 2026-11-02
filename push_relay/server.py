"""Private credential boundary for Voltmoss remote alerts.

Run behind an HTTPS reverse proxy. Registration requires Firebase App Check;
event submission requires a per-installation secret instead. The HTTP handler
never logs request paths, headers, or bodies.
"""

import json
import hmac
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .relay import ApiError, Relay, RelayStore
from .sender import FirebaseSender


_INSTALLATION = re.compile(r"^/v1/installations/([A-Za-z0-9_-]{16,64})$")
_EVENTS = re.compile(r"^/v1/installations/([A-Za-z0-9_-]{16,64})/events$")
_ACTIVITY = re.compile(r"^/v1/installations/([A-Za-z0-9_-]{16,64})/activity$")
_ACTIVITY_TOKENS = re.compile(r"^/v1/installations/([A-Za-z0-9_-]{16,64})/activity-tokens$")


def handler_for(relay, verify_app_check, allowed_app_ids, metrics_secret=""):
    if not allowed_app_ids:
        raise ValueError("App Check app ID allowlist is required")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            # BaseHTTPRequestHandler includes the request line, which can carry
            # installation IDs and is not useful in aggregate relay logs.
            return

        def _json(self, status, payload):
            body = json.dumps(payload, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            try:
                n = int(self.headers.get("Content-Length", "0"))
                if not 0 < n <= 2048:
                    raise ValueError()
                value = json.loads(self.rfile.read(n))
            except (ValueError, TypeError, json.JSONDecodeError):
                raise ApiError(400, "invalid JSON body") from None
            if not isinstance(value, dict):
                raise ApiError(400, "invalid JSON body")
            return value

        def _app_check(self):
            token = self.headers.get("X-Firebase-AppCheck", "")
            if not token or len(token) > 8192:
                raise ApiError(401, "app attestation required")
            try:
                # Firebase Admin's documented verify_token(token, app=...) is
                # installed by main(); tests inject a deterministic verifier.
                claims = verify_app_check(token)
                # The verified JWT subject is the Firebase App ID. A project
                # may contain other apps, so only these two mobile apps enroll.
                if not isinstance(claims, dict) or claims.get("sub") not in allowed_app_ids:
                    raise ValueError("unrecognized app")
            except Exception:
                raise ApiError(401, "app attestation failed") from None

        def _secret(self):
            authorization = self.headers.get("Authorization", "")
            if not authorization.startswith("Bearer ") or len(authorization) > 80:
                raise ApiError(401, "installation unauthorized")
            return authorization[7:]

        def _handle(self, action):
            try:
                action()
            except ApiError as e:
                self._json(e.status, {"error": str(e)})
            except Exception:
                # No exception text: SDK/network exceptions may embed a FID.
                self._json(500, {"error": "relay unavailable"})

        def do_GET(self):
            def run():
                path = urlsplit(self.path).path
                if path == "/healthz":
                    self._json(200, {"ok": True})
                elif path == "/internal/metrics" and metrics_secret:
                    if not hmac.compare_digest(self._secret(), metrics_secret):
                        raise ApiError(401, "unauthorized")
                    self._json(200, relay.metrics())
                else:
                    raise ApiError(404, "not found")
            self._handle(run)

        def do_POST(self):
            def run():
                path = urlsplit(self.path).path
                if path == "/v1/installations":
                    self._app_check()
                    self._json(201, relay.create(self._body()))
                elif match := _EVENTS.fullmatch(path):
                    self._json(202, relay.enqueue(match[1], self._secret(), self._body()))
                elif match := _ACTIVITY.fullmatch(path):
                    self._json(202, relay.enqueue_activity(match[1], self._secret(),
                                                           self._body()))
                else:
                    raise ApiError(404, "not found")
            self._handle(run)

        def do_PUT(self):
            def run():
                path = urlsplit(self.path).path
                if match := _ACTIVITY_TOKENS.fullmatch(path):
                    # From the app's native code, in a background wake as often as not:
                    # the installation secret is the credential, as it is for events.
                    self._json(200, relay.set_activity_tokens(match[1], self._secret(),
                                                              self._body()))
                    return
                match = _INSTALLATION.fullmatch(path)
                if not match:
                    raise ApiError(404, "not found")
                self._app_check()
                self._json(200, relay.update(match[1], self._secret(), self._body()))
            self._handle(run)

        def do_DELETE(self):
            def run():
                match = _INSTALLATION.fullmatch(urlsplit(self.path).path)
                if not match:
                    raise ApiError(404, "not found")
                self._app_check()
                self._json(200, relay.delete(match[1], self._secret()))
            self._handle(run)

    return Handler


def main():
    db = os.environ["VOLTMOSS_RELAY_DB"]
    # copy/ ships with the relay; the variable stays so a deployment can point elsewhere.
    translations = (os.environ.get("VOLTMOSS_TRANSLATIONS_DIR")
                    or str(Path(__file__).resolve().parent / "copy"))
    sender = FirebaseSender(translations)
    from firebase_admin import app_check

    relay = Relay(RelayStore(db), sender)
    verifier = lambda token: app_check.verify_token(token, app=sender.app)
    allowed_app_ids = frozenset(
        value.strip() for value in os.environ["VOLTMOSS_APP_CHECK_APP_IDS"].split(",")
        if value.strip())
    handler = handler_for(relay, verifier, allowed_app_ids,
                          os.environ.get("VOLTMOSS_RELAY_METRICS_SECRET", ""))
    server = ThreadingHTTPServer((os.environ.get("VOLTMOSS_RELAY_BIND", "127.0.0.1"),
                                  int(os.environ.get("PORT", "8099"))), handler)
    server.daemon_threads = True
    stop = threading.Event()

    def worker():
        last_cleanup = 0
        while not stop.wait(1):
            try:
                relay.drain_due()
                relay.drain_activities()
                if time.time() - last_cleanup > 3600:
                    relay.cleanup()
                    last_cleanup = time.time()
            except Exception:
                # The queue is durable; retry next tick without printing FIDs.
                pass

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        server.serve_forever()
    finally:
        stop.set()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
