#!/usr/bin/env python3
"""Regression checks for SQLite locks shared by the dashboard and logger."""

import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import logger
import server


class FakeRows:
    def fetchall(self):
        return []


class FakeReader:
    def __init__(self, fail=False):
        self.fail = fail
        self.closed = False

    def execute(self, _query, _args=()):
        if self.fail:
            raise sqlite3.OperationalError("database is locked")
        return FakeRows()

    def close(self):
        self.closed = True


class StopLoop(BaseException):
    pass


class DatabaseLockTest(unittest.TestCase):
    def test_summary_releases_reader_on_success_and_error(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                reader = FakeReader(fail=fail)
                with patch.object(server.os.path, "exists", return_value=True), \
                        patch.object(server.sqlite3, "connect", return_value=reader):
                    if fail:
                        with self.assertRaises(sqlite3.OperationalError):
                            server._summary()
                    else:
                        server._summary()
                self.assertTrue(reader.closed)

    def test_history_releases_reader_on_success_and_error(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                reader = FakeReader(fail=fail)
                with patch.object(server.os.path, "exists", return_value=True), \
                        patch.object(server.sqlite3, "connect", return_value=reader):
                    if fail:
                        with self.assertRaises(sqlite3.OperationalError):
                            server.month_history("2026-10")
                    else:
                        server.month_history("2026-10")
                self.assertTrue(reader.closed)

    def test_failed_poll_rolls_back_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/telemetry.db"
            writer = sqlite3.connect(path)
            writer.execute("CREATE TABLE probe (value INTEGER)")
            writer.commit()

            def failed_poll(conn):
                conn.execute("INSERT INTO probe VALUES (1)")
                raise sqlite3.OperationalError("poll failed after write")

            with patch.object(logger, "poll_once", side_effect=failed_poll), \
                    patch.object(logger, "_failure"), \
                    patch.object(logger.time, "sleep", side_effect=StopLoop):
                with self.assertRaises(StopLoop):
                    logger.adaptive_loop(writer)
            with sqlite3.connect(path, timeout=1) as reader:
                self.assertEqual(reader.execute("SELECT COUNT(*) FROM probe").fetchone()[0], 0)
                reader.execute("INSERT INTO probe VALUES (2)")
            writer.close()


if __name__ == "__main__":
    unittest.main()
