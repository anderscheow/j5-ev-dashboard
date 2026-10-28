#!/usr/bin/env python3
"""The maintenance log: storage rules and the HTTP routes that reach them."""

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
import maintenance_records as maint
import server

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
RECORD = {
    "at": "2026-10-20T09:30:00.000",
    "odo": 19876,
    "items": [{"name": "Cabin filter", "qty": 1, "price": 85},
              {"name": "Wiper", "qty": 2, "price": 40}],
    "tax": 13.2,
    "total": 178.2,
    "currency": "MYR",
    "notes": "INV-0042",
}


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.db = os.path.join(self.dir.name, "maintenance.db")

    def test_a_record_is_the_tick_and_round_trips(self):
        receipt = maint.add_receipt(self.db, JPEG)["id"]
        record = maint.validate_record(20000, dict(RECORD, receipts=[receipt]))
        maint.put_record(self.db, "Jaecoo J5 EV", record)
        log = maint.log(self.db)
        self.assertEqual(log["records"], [dict(record, model="Jaecoo J5 EV")])
        self.assertEqual(maint.receipt(self.db, receipt), ("image/jpeg", JPEG))

    def test_an_edit_that_drops_a_receipt_deletes_it(self):
        first = maint.add_receipt(self.db, JPEG)["id"]
        second = maint.add_receipt(self.db, PNG)["id"]
        maint.put_record(self.db, "M", maint.validate_record(1000, dict(RECORD, receipts=[first, second])))
        maint.put_record(self.db, "M", maint.validate_record(1000, dict(RECORD, receipts=[second])))
        self.assertIsNone(maint.receipt(self.db, first))
        self.assertEqual(maint.receipt(self.db, second)[0], "image/png")

    def test_deleting_a_record_takes_its_receipts(self):
        receipt = maint.add_receipt(self.db, JPEG)["id"]
        maint.put_record(self.db, "M", maint.validate_record(1000, dict(RECORD, receipts=[receipt])))
        self.assertTrue(maint.delete_record(self.db, "M", 1000))
        self.assertEqual(maint.log(self.db)["records"], [])
        self.assertIsNone(maint.receipt(self.db, receipt))
        self.assertFalse(maint.delete_record(self.db, "M", 1000))

    def test_a_record_may_only_name_uploaded_receipts(self):
        record = maint.validate_record(1000, dict(RECORD, receipts=["x" * 24]))
        with self.assertRaisesRegex(ValueError, "unknown receipt"):
            maint.put_record(self.db, "M", record)
        self.assertEqual(maint.log(self.db)["records"], [])

    def test_an_unclaimed_upload_is_collected_after_a_day(self):
        orphan = maint.add_receipt(self.db, JPEG, now=1_000_000)["id"]
        kept = maint.add_receipt(self.db, JPEG, now=1_000_000)["id"]
        maint.put_record(self.db, "M", maint.validate_record(1000, dict(RECORD, receipts=[kept])))
        maint.add_receipt(self.db, PNG, now=1_000_000 + maint.ORPHAN_AGE + 1)
        self.assertIsNone(maint.receipt(self.db, orphan))
        self.assertIsNotNone(maint.receipt(self.db, kept))

    def test_preferences_set_and_clear(self):
        maint.put_preferences(self.db, maint.validate_preferences({"country": "ID", "model": "Chery Tiggo 9"}))
        self.assertEqual(maint.log(self.db)["model"], "Chery Tiggo 9")
        maint.put_preferences(self.db, maint.validate_preferences({"model": None}))
        log = maint.log(self.db)
        self.assertEqual((log["country"], log["model"]), ("ID", None))

    def test_bad_input_is_refused_before_any_write(self):
        for body in ({}, dict(RECORD, at="yesterday"), dict(RECORD, odo=-1),
                     dict(RECORD, tax=-5), dict(RECORD, currency="ringgit"),
                     dict(RECORD, items=[{"name": "x"}]), dict(RECORD, extra=1),
                     dict(RECORD, km=40000), dict(RECORD, odo=True)):
            with self.assertRaises(ValueError, msg=body):
                maint.validate_record(20000, body)
        for prefs in ({}, {"country": "SG"}, {"model": ""}, {"colour": "red"}):
            with self.assertRaises(ValueError, msg=prefs):
                maint.validate_preferences(prefs)
        for blob in (b"", b"GIF89a" + b"\x00" * 10, b"\x00" * (maint.RECEIPT_MAX + 1)):
            with self.assertRaises(ValueError):
                maint.add_receipt(self.db, blob)


class RoutesTest(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.TemporaryDirectory()
        self.addCleanup(self.data.cleanup)
        for name, value in (("_DATA", self.data.name), ("DEMO", False),
                            ("MAINT_DB", os.path.join(self.data.name, "maintenance.db"))):
            p = patch.object(server, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.base = "http://127.0.0.1:%d" % self.httpd.server_port

    def call(self, method, path, body=None, raw=None, cookie=None):
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        headers = {"Cookie": cookie} if cookie else {}
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                payload = response.read()
                ctype = response.headers.get("Content-Type", "")
                return response.status, (json.loads(payload) if "json" in ctype else payload)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_the_log_round_trips_over_http(self):
        status, upload = self.call("POST", "/api/maintenance/receipts", raw=JPEG)
        self.assertEqual(status, 200)
        status, stored = self.call("PUT", "/api/maintenance/records/20000?model=Jaecoo%20J5%20EV",
                                   dict(RECORD, receipts=[upload["id"]]))
        self.assertEqual(status, 200)
        self.assertEqual(stored["model"], "Jaecoo J5 EV")
        self.assertEqual(self.call("PUT", "/api/maintenance/preferences", {"country": "MY"})[0], 200)

        status, log = self.call("GET", "/api/maintenance/records")
        self.assertEqual(status, 200)
        self.assertEqual(log["country"], "MY")
        self.assertEqual([r["km"] for r in log["records"]], [20000])
        self.assertEqual(self.call("GET", "/api/maintenance/receipts/" + upload["id"]), (200, JPEG))

        self.assertEqual(self.call("DELETE", "/api/maintenance/records/20000?model=Jaecoo%20J5%20EV")[0], 200)
        self.assertEqual(self.call("GET", "/api/maintenance/records")[1]["records"], [])
        self.assertEqual(self.call("GET", "/api/maintenance/receipts/" + upload["id"])[0], 404)

    def test_bad_writes_are_400_and_unknown_routes_404(self):
        self.assertEqual(self.call("PUT", "/api/maintenance/records/20000", RECORD)[0], 400)
        self.assertEqual(self.call("PUT", "/api/maintenance/records/x?model=M", RECORD)[0], 400)
        self.assertEqual(self.call("POST", "/api/maintenance/receipts", raw=b"not an image")[0], 400)
        self.assertEqual(self.call("PUT", "/api/maintenance/unknown", {})[0], 404)

    def test_a_gated_dashboard_refuses_every_route_without_a_session(self):
        server.set_dashboard_password("test-password")
        for method, path, body in (("GET", "/api/maintenance/records", None),
                                   ("PUT", "/api/maintenance/records/1000?model=M", RECORD),
                                   ("PUT", "/api/maintenance/preferences", {"country": "MY"}),
                                   ("DELETE", "/api/maintenance/records/1000?model=M", None)):
            self.assertEqual(self.call(method, path, body)[0], 401, (method, path))
        self.assertEqual(self.call("POST", "/api/maintenance/receipts", raw=JPEG)[0], 401)
        self.assertEqual(self.call("GET", "/api/maintenance/receipts/" + "x" * 24)[0], 401)

    def test_receipt_ids_are_kept_out_of_the_request_log(self):
        with self.assertLogs("carlinko.server", level="INFO") as captured:
            self.call("GET", "/api/maintenance/receipts/" + "s" * 24)
        self.assertFalse(any("s" * 24 in line for line in captured.output))


if __name__ == "__main__":
    unittest.main()
