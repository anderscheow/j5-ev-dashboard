#!/usr/bin/env python3
"""Keeps this dashboard a mirror of another: staging of production.

Production is the only dashboard that talks to the car -- CarLinko allows one live session per
account -- and the only one that sends push alerts. A mirror runs this in place of the logger:
every minute it pulls the telemetry production recorded since its own newest frame, and every
five minutes the owner's files (the maintenance log with its receipts, the price book, the car
photo) whenever production's copy has changed. push_alerts.db, creds.json and token.txt never
travel; production's /api/replica/* routes do not serve them.

Telemetry is merged, never deleted: production's row wins a clash on the same second, and frames
the mirror held before it began mirroring stay. A file production does not have is left as the
mirror's own, so a feature still being tried on staging keeps its data.

Configured by CARLINKO_REPLICA_OF (production's base URL) and CARLINKO_REPLICA_TOKEN (the secret
both sides share); tools/railway_start.sh runs this instead of the logger when the first is set.
"""

import json
import logging
import os
import re
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.request

import log_setup

TELEMETRY_EVERY = 60
FILES_EVERY = 300
OVERLAP = 600            # re-read the last ten minutes: a frame committed late is still caught
PAGE = 5000
FILES = ("maintenance.db", "prices.db", "car-photo.img")
_COLUMN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

LOG = logging.getLogger("carlinko.replica")


def _get(source, token, path, etag=None, timeout=120):
    """(status, body, etag) for GET `path` on the source dashboard."""
    req = urllib.request.Request(source + path, headers={"Authorization": "Bearer " + token})
    if etag:
        req.add_header("If-None-Match", etag)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), resp.headers.get("ETag")
    except urllib.error.HTTPError as e:
        with e:
            return e.code, e.read(), e.headers.get("ETag")


def _connect(db):
    conn = sqlite3.connect(db, timeout=30)
    try:
        conn.execute("PRAGMA journal_mode=WAL")    # as the dashboard reads it: see db_connect()
    except sqlite3.OperationalError:
        pass
    return conn


def _newest(conn):
    try:
        return conn.execute("SELECT max(ts) FROM telemetry").fetchone()[0] or 0
    except sqlite3.OperationalError:              # no table yet: a mirror's first run
        return 0


def sync_telemetry(db, source, token):
    """Pulls every frame newer than ten minutes before this mirror's newest. Answers how many
    rows were written."""
    conn = _connect(db)
    try:
        after, written = max(0, _newest(conn) - OVERLAP), 0
        while True:
            status, body, _etag = _get(source, token,
                                       "/api/replica/telemetry?after=%d&limit=%d" % (after, PAGE))
            if status != 200:
                raise RuntimeError("telemetry page answered %d" % status)
            page = json.loads(body)
            schema = page.get("schema")
            if schema and schema.startswith("CREATE TABLE telemetry"):
                conn.execute(schema.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1))
            rows, columns = page.get("rows") or [], page.get("columns") or []
            if rows:
                # Only columns both sides have, by name: a mirror a schema change behind still
                # takes what it can store.
                local = {r[1] for r in conn.execute("PRAGMA table_info(telemetry)")}
                keep = [i for i, c in enumerate(columns) if c in local and _COLUMN.match(c)]
                names = [columns[i] for i in keep]
                conn.executemany(
                    "INSERT OR REPLACE INTO telemetry (%s) VALUES (%s)"
                    % (",".join(names), ",".join("?" * len(names))),
                    [[row[i] for i in keep] for row in rows])
                conn.commit()
                written += len(rows)
                after = rows[-1][columns.index("ts")]
            if not page.get("more"):
                return written
    finally:
        conn.close()


def sync_files(data, source, token, etags):
    """Copies each owner file production has and has changed since the last copy; `etags`
    remembers what each copy was. Answers the names copied."""
    copied = []
    for name in FILES:
        status, body, etag = _get(source, token, "/api/replica/files/" + name, etags.get(name))
        if status in (304, 404):                  # unchanged, or production has none
            continue
        if status != 200:
            raise RuntimeError("%s answered %d" % (name, status))
        fd, tmp = tempfile.mkstemp(dir=data, prefix="." + name + ".")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(body)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, os.path.join(data, name))    # readers see the old file or the new
        except BaseException:
            os.unlink(tmp)
            raise
        etags[name] = etag
        copied.append(name)
    return copied


def main():
    log_setup.configure()
    source = os.environ.get("CARLINKO_REPLICA_OF", "").rstrip("/")
    token = os.environ.get("CARLINKO_REPLICA_TOKEN", "")
    if not source.startswith("https://") or len(token) < 32:
        LOG.error("replica needs CARLINKO_REPLICA_OF (https) and CARLINKO_REPLICA_TOKEN (32+ chars)")
        return 2
    data = os.environ.get("CARLINKO_DATA") or os.path.dirname(os.path.abspath(__file__))
    db = os.path.join(data, "carlinko.db")
    LOG.info("mirroring %s into %s", source, data)
    etags, files_due = {}, 0.0
    while True:
        try:
            written = sync_telemetry(db, source, token)
            if written:
                LOG.info("mirrored %d frames", written)
        except Exception as e:                    # never the token or the URL in a log line
            LOG.warning("telemetry sync failed: %s", type(e).__name__)
        if time.time() >= files_due:
            try:
                for name in sync_files(data, source, token, etags):
                    LOG.info("mirrored %s", name)
            except Exception as e:
                LOG.warning("file sync failed: %s", type(e).__name__)
            files_due = time.time() + FILES_EVERY
        time.sleep(TELEMETRY_EVERY)


if __name__ == "__main__":
    sys.exit(main())
