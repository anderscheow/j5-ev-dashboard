#!/usr/bin/env python3
"""Staging mirrors production: the token-only /api/replica/* routes, and tools/replica.py pulling
from them. A real HTTP server over temporary data directories; no car, no network.

Run from the repo root:  python3 tools/replica_selftest.py
"""

import os
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import closing
from http.server import ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
import replica
import server

TOKEN = "t" * 40
SCHEMA = ("CREATE TABLE telemetry (ts INTEGER PRIMARY KEY, dt TEXT, battery INTEGER, "
          "range_km INTEGER, odo_guess INTEGER, tyre_raw TEXT, online INTEGER, raw TEXT)")


def frames(db, rows):
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute(SCHEMA.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1))
        conn.executemany("INSERT OR REPLACE INTO telemetry (ts, dt, battery, online, raw) "
                         "VALUES (?, '', ?, 1, 'ab')", rows)


def table(db, sql="SELECT ts, battery FROM telemetry ORDER BY ts"):
    with closing(sqlite3.connect(db)) as conn:
        return conn.execute(sql).fetchall()


class ReplicaTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.src = os.path.join(temp.name, "production")
        self.dst = os.path.join(temp.name, "staging")
        os.makedirs(self.src)
        os.makedirs(self.dst)
        self.src_db = os.path.join(self.src, "carlinko.db")
        self.dst_db = os.path.join(self.dst, "carlinko.db")
        for target, value in (("_DATA", self.src), ("DB", self.src_db),
                              ("REPLICA_TOKEN", TOKEN), ("REPLICA_OF", "")):
            p = patch.object(server, target, value)
            p.start()
            self.addCleanup(p.stop)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        self.url = "http://127.0.0.1:%d" % httpd.server_port

    def status(self, path, token=TOKEN):
        req = urllib.request.Request(self.url + path)
        if token is not None:
            req.add_header("Authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            with e:
                return e.code

    def test_the_routes_answer_only_the_token(self):
        frames(self.src_db, [(1000, 50)])
        self.assertEqual(self.status("/api/replica/telemetry"), 200)
        self.assertEqual(self.status("/api/replica/telemetry", token=None), 401)
        self.assertEqual(self.status("/api/replica/telemetry", token="x" * 40), 401)
        with patch.object(server, "REPLICA_TOKEN", ""):
            self.assertEqual(self.status("/api/replica/telemetry"), 404)

    def test_alerts_and_credentials_never_leave(self):
        for name in ("push_alerts.db", "creds.json", "token.txt"):
            with open(os.path.join(self.src, name), "w") as f:
                f.write("secret")
            self.assertEqual(self.status("/api/replica/files/" + name), 404, name)
        self.assertEqual(self.status("/api/replica/files/../creds.json"), 404)

    def test_telemetry_merges_pages_and_catches_a_late_frame(self):
        frames(self.src_db, [(1000 + i, 60 + i) for i in range(10)])
        # the mirror's own frames from before it mirrored; 1005 clashes with production's
        frames(self.dst_db, [(500, 1), (1005, 99)])
        with patch.object(replica, "PAGE", 3):
            self.assertEqual(replica.sync_telemetry(self.dst_db, self.url, TOKEN), 10)
            rows = table(self.dst_db)
            self.assertEqual(rows[0], (500, 1))                   # its own frame stays
            self.assertIn((1005, 65), rows)                       # production wins the clash
            self.assertEqual(len(rows), 11)
            # Running again rewrites only the overlap; nothing is lost or doubled.
            replica.sync_telemetry(self.dst_db, self.url, TOKEN)
            self.assertEqual(table(self.dst_db), rows)
            # A frame committed late, older than the mirror's newest, is still caught.
            frames(self.src_db, [(999, 7)])
            replica.sync_telemetry(self.dst_db, self.url, TOKEN)
            self.assertIn((999, 7), table(self.dst_db))

    def test_a_fresh_mirror_takes_the_table_from_production(self):
        frames(self.src_db, [(1000, 50), (1001, 51)])
        replica.sync_telemetry(self.dst_db, self.url, TOKEN)
        self.assertEqual(table(self.dst_db), [(1000, 50), (1001, 51)])

    def test_owner_files_copy_when_changed_and_stay_where_production_has_none(self):
        with closing(sqlite3.connect(os.path.join(self.src, "maintenance.db"))) as conn, conn:
            conn.execute("CREATE TABLE maintenance_records (km INTEGER)")
            conn.execute("INSERT INTO maintenance_records VALUES (10000)")
        with closing(sqlite3.connect(os.path.join(self.dst, "prices.db"))) as conn, conn:
            conn.execute("CREATE TABLE price_periods (price REAL)")
            conn.execute("INSERT INTO price_periods VALUES (2540)")
        etags = {}
        self.assertEqual(replica.sync_files(self.dst, self.url, TOKEN, etags),
                         ["maintenance.db"])
        self.assertEqual(table(os.path.join(self.dst, "maintenance.db"),
                               "SELECT km FROM maintenance_records"), [(10000,)])
        # production has no price book: staging's own stays
        self.assertEqual(table(os.path.join(self.dst, "prices.db"),
                               "SELECT price FROM price_periods"), [(2540,)])
        # unchanged: nothing is sent again
        self.assertEqual(replica.sync_files(self.dst, self.url, TOKEN, etags), [])
        with closing(sqlite3.connect(os.path.join(self.src, "maintenance.db"))) as conn, conn:
            conn.execute("INSERT INTO maintenance_records VALUES (20000)")
        self.assertEqual(replica.sync_files(self.dst, self.url, TOKEN, etags),
                         ["maintenance.db"])
        self.assertEqual(len(table(os.path.join(self.dst, "maintenance.db"),
                                   "SELECT km FROM maintenance_records")), 2)
        self.assertFalse([f for f in os.listdir(self.dst) if f.startswith(".")])

    def test_a_mirror_never_polls_the_car(self):
        with patch.object(server, "REPLICA_OF", "https://production.example"):
            self.assertEqual(server.live_poll(), (False, "mirror"))

    def test_the_follower_refuses_to_start_half_configured(self):
        with patch.dict(os.environ, {"CARLINKO_REPLICA_OF": "http://plain.example",
                                     "CARLINKO_REPLICA_TOKEN": TOKEN}):
            self.assertEqual(replica.main(), 2)
        with patch.dict(os.environ, {"CARLINKO_REPLICA_OF": "https://ok.example",
                                     "CARLINKO_REPLICA_TOKEN": "short"}):
            self.assertEqual(replica.main(), 2)


if __name__ == "__main__":
    unittest.main()
