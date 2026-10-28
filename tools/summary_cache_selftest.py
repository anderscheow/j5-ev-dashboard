#!/usr/bin/env python3
"""Checks that /api/summary is computed once for concurrent callers and never served stale."""

import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
import server


class SlowSummary:
    """Stands in for _summary(): counts its calls and takes long enough to overlap."""

    def __init__(self, delay=0.2):
        self.delay = delay
        self.calls = 0

    def __call__(self):
        self.calls += 1
        time.sleep(self.delay)
        return {"battery": 80, "history": [{"km": 1}], "call": self.calls}


class SummaryCacheTest(unittest.TestCase):
    def setUp(self):
        server._summary_cache = None
        self.addCleanup(setattr, server, "_summary_cache", None)
        self.state = ("frame-1",)
        state_patch = patch.object(server, "_summary_inputs", lambda: self.state)
        state_patch.start()
        self.addCleanup(state_patch.stop)
        self.fake = SlowSummary()
        summary_patch = patch.object(server, "_summary", self.fake)
        summary_patch.start()
        self.addCleanup(summary_patch.stop)

    def test_concurrent_callers_share_one_computation(self):
        results = []
        threads = [threading.Thread(target=lambda: results.append(server.summary()))
                   for _ in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(self.fake.calls, 1)
        self.assertEqual(len(results), 12)
        self.assertTrue(all(r == results[0] for r in results))
        self.assertEqual(results[0]["api_version"], server.API_VERSION)

    def test_each_caller_gets_its_own_copy(self):
        first = server.summary()
        first["history"].append({"km": 99})
        first["battery"] = 0
        second = server.summary()
        self.assertEqual(self.fake.calls, 1)
        self.assertEqual(second["battery"], 80)
        self.assertEqual(second["history"], [{"km": 1}])

    def test_a_new_frame_is_never_answered_from_the_cache(self):
        server.summary()
        self.state = ("frame-2",)  # a frame was committed, or creds.json rewritten
        self.assertEqual(server.summary()["call"], 2)

    def test_an_answer_older_than_the_ttl_is_recomputed(self):
        server.summary()
        with patch.object(server, "SUMMARY_TTL", 0.0):
            self.assertEqual(server.summary()["call"], 2)

    def test_a_failure_is_not_cached(self):
        def boom():
            raise RuntimeError("decode failed")
        with patch.object(server, "_summary", boom):
            with self.assertRaises(RuntimeError):
                server.summary()
        self.assertEqual(server.summary()["call"], 1)


class SummaryInputsTest(unittest.TestCase):
    def test_inputs_follow_the_database_its_log_and_the_credentials(self):
        with tempfile.TemporaryDirectory() as data, \
                patch.object(server, "_DATA", data), \
                patch.object(server, "DB", os.path.join(data, "carlinko.db")):
            self.assertEqual(server._summary_inputs(), (None, None, None))
            with open(os.path.join(data, "carlinko.db"), "w") as f:
                f.write("frames")
            database, wal, creds = server._summary_inputs()
            self.assertIsNotNone(database)
            self.assertIsNone(wal)
            self.assertIsNone(creds)
            # A web login or first-run setup writes creds.json after the server started.
            with open(os.path.join(data, "creds.json"), "w") as f:
                f.write("{}")
            self.assertIsNotNone(server._summary_inputs()[2])


if __name__ == "__main__":
    unittest.main()
