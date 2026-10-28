#!/usr/bin/env python3
"""HTTP and storage contract checks for optional Voltmoss push bindings."""

import json
import os
import sqlite3
import stat
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
import server
import push_registrations as push
import push_dispatcher


INSTALLATION = "test_installation_0123456789"
SECRET = "a" * 64
BODY = {
    "sender_secret": SECRET,
    "rules": {"v": 1, "rules": {"soc_below": {"on": True, "t": 20}}},
    "locale": "en",
    "service": {"last_odo": None, "last_date": None,
                "interval_km": None, "interval_months": None},
    "plus": True,
    "time_zone": "Asia/Kuala_Lumpur",
}


class PushContractTest(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.TemporaryDirectory()
        self.addCleanup(self.data.cleanup)
        for name, value in (("_DATA", self.data.name),
                            ("PUSH_DB", os.path.join(self.data.name, "push_alerts.db")),
                            ("DEMO", False)):
            p = patch.object(server, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(server, "is_configured", return_value=True)
        p.start()
        self.addCleanup(p.stop)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.base = "http://127.0.0.1:%d" % self.httpd.server_port

    def request(self, method, path, body=None, cookie=None):
        headers = {"Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.load(e)

    def enable(self):
        server.set_dashboard_password("password")
        env = patch.dict(os.environ, {"VOLTMOSS_PUSH_RELAY_URL": "https://relay.example.test",
                                   "VOLTMOSS_PUSH_HTTPS": "1",
                                   "VOLTMOSS_PUSH_DISPATCHER": "1"})
        env.start()
        self.addCleanup(env.stop)
        health = patch.object(push_dispatcher, "healthy", return_value=True)
        health.start()
        self.addCleanup(health.stop)
        return "sid=" + server.make_session()

    def test_disabled_by_default_and_demo(self):
        _, status = self.request("GET", "/api/status")
        self.assertNotIn("push_alerts", status["capabilities"])
        self.assertEqual(self.request("GET", "/api/push/status")[1]["enabled"], False)
        self.assertEqual(self.request("PUT", "/api/push/installations/" + INSTALLATION, BODY)[0], 404)
        with patch.object(server, "DEMO", True):
            self.assertNotIn("push_alerts", self.request("GET", "/api/status")[1]["capabilities"])

    def test_requires_gated_https_and_session(self):
        with patch.dict(os.environ, {"VOLTMOSS_PUSH_RELAY_URL": "https://relay.example.test",
                                     "VOLTMOSS_PUSH_HTTPS": "1",
                                     "VOLTMOSS_PUSH_DISPATCHER": "1"}):
            self.assertNotIn("push_alerts", self.request("GET", "/api/status")[1]["capabilities"])
        with patch.dict(os.environ, {"VOLTMOSS_PUSH_RELAY_URL": "http://relay.example.test",
                                     "VOLTMOSS_PUSH_HTTPS": "1",
                                     "VOLTMOSS_PUSH_DISPATCHER": "1"}):
            server.set_dashboard_password("password")
            self.assertNotIn("push_alerts", self.request("GET", "/api/status")[1]["capabilities"])
        cookie = self.enable()
        _, status = self.request("GET", "/api/status")
        self.assertIn("push_alerts", status["capabilities"])
        # Advertised with push_alerts and never without it: a phone names charge_started in its
        # binding only when the dashboard lists the capability.
        self.assertIn("push_charge_started", status["capabilities"])
        self.assertEqual(status["api_version"], server.API_VERSION)
        self.assertEqual(self.request("PUT", "/api/push/installations/" + INSTALLATION, BODY)[0], 401)
        self.assertEqual(self.request("PUT", "/api/push/installations/" + INSTALLATION, BODY, cookie)[0], 200)

    def test_capability_tracks_live_worker_but_revocation_remains_available(self):
        cookie = self.enable()
        path = "/api/push/installations/" + INSTALLATION
        self.assertEqual(self.request("PUT", path, BODY, cookie)[0], 200)
        with patch.object(push_dispatcher, "healthy", return_value=False):
            capabilities = self.request("GET", "/api/status")[1]["capabilities"]
            self.assertNotIn("push_alerts", capabilities)
            self.assertNotIn("push_charge_started", capabilities)
            self.assertEqual(self.request("GET", "/api/push/status", cookie=cookie)[1],
                             {"enabled": False, "registered_count": 1})
            self.assertEqual(self.request("DELETE", path, cookie=cookie)[0], 200)

    def test_idempotent_write_validation_and_delete(self):
        cookie = self.enable()
        path = "/api/push/installations/" + INSTALLATION
        with self.assertLogs("carlinko.server", level="INFO") as logs:
            self.assertEqual(self.request("PUT", path, BODY, cookie)[0], 200)
            self.assertEqual(self.request("PUT", path, BODY, cookie)[0], 200)
        self.assertNotIn(SECRET, "\n".join(logs.output))
        self.assertNotIn(INSTALLATION, "\n".join(logs.output))
        self.assertEqual(self.request("GET", "/api/push/status", cookie=cookie)[1]["registered_count"], 1)
        bad = {**BODY, "rules": {"v": 1, "rules": {"unknown": {"on": True}}}}
        self.assertEqual(self.request("PUT", path, bad, cookie)[0], 400)
        started = {**BODY, "rules": {"v": 1, "rules": {"charge_started": {"on": True}}}}
        self.assertEqual(self.request("PUT", path, started, cookie)[0], 200)
        self.assertEqual(self.request("PUT", path, BODY, cookie)[0], 200)
        bad_date = {**BODY, "service": {**BODY["service"], "last_date": "2026-02-30"}}
        self.assertEqual(self.request("PUT", path, bad_date, cookie)[0], 400)
        bad_months = {**BODY, "service": {**BODY["service"], "interval_months": 1000000}}
        self.assertEqual(self.request("PUT", path, bad_months, cookie)[0], 400)
        for zone in ("Mars/Olympus_Mons", "../etc/passwd", "", 8):
            self.assertEqual(self.request("PUT", path, {**BODY, "time_zone": zone}, cookie)[0], 400)
        missing_zone = {k: v for k, v in BODY.items() if k != "time_zone"}
        self.assertEqual(self.request("PUT", path, missing_zone, cookie)[0], 400)
        self.assertEqual(self.request("GET", "/api/push/status", cookie=cookie)[1]["registered_count"], 1)
        self.assertEqual(self.request("DELETE", path, cookie=cookie)[0], 200)
        self.assertEqual(self.request("DELETE", path, cookie=cookie)[0], 200)
        self.assertEqual(self.request("GET", "/api/push/status", cookie=cookie)[1]["registered_count"], 0)

    def test_database_and_active_journal_are_private(self):
        db = server.PUSH_DB
        push.put(db, push.validate(INSTALLATION, BODY))
        self.assertEqual(stat.S_IMODE(os.stat(db).st_mode), 0o600)
        conn = sqlite3.connect(db)
        try:
            conn.execute("UPDATE push_installations SET sender_secret=? WHERE installation_id=?",
                         ("b" * 64, INSTALLATION))
            journal = db + "-journal"
            self.assertTrue(os.path.exists(journal))
            self.assertEqual(stat.S_IMODE(os.stat(journal).st_mode), 0o600)
            conn.rollback()
        finally:
            conn.close()
        os.chmod(db, 0o644)
        self.assertEqual(push.count(db), 1)
        self.assertEqual(stat.S_IMODE(os.stat(db).st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
