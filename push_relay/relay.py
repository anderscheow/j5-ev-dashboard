"""Durable, narrowly scoped registration and alert-event queue.

Only this service holds Firebase send authority. The self-hosted dashboard gets
a random secret for one opaque installation and can submit only known alert
kinds to that installation. SQLite uses a persistent mounted volume.
"""

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


KINDS = frozenset((
    "charge_started", "charge_complete", "charge_stopped", "soc_below", "left_unlocked",
    "panel_open", "tyre_pressure", "volt12_low", "service_due",
))
LOCALES = frozenset(("en", "ms", "id", "zh"))
EVENT_LIFETIME = 15 * 60
MAX_PER_HOUR = 20
MAX_PER_DAY = 100
MAX_INSTALLATIONS_PER_FID = 16
MAX_CREATIONS_PER_HOUR = 120
MAX_CREATIONS_PER_DAY = 1000
MAX_ACTIVE_INSTALLATIONS = 10000
_ID = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_FID = re.compile(r"^[A-Za-z0-9_-]{16,256}$")
_SECRET = re.compile(r"^[0-9a-fA-F]{64}$")
_EVENT = re.compile(r"^[A-Za-z0-9_-]{16,80}$")


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _digest(secret):
    return hashlib.sha256(secret.encode("ascii")).hexdigest()


def _secure_file(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    for suffix in ("-journal", "-wal", "-shm"):
        journal = str(path) + suffix
        try:
            fd = os.open(journal, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError:
            continue
        try:
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)


class RelayStore:
    def __init__(self, path):
        self.path = path
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS installations (
                    id TEXT PRIMARY KEY,
                    fid TEXT NOT NULL,
                    secret_hash TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    locale TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    last_seen INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active'
                );
                CREATE TABLE IF NOT EXISTS events (
                    installation_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    value REAL,
                    occurred_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt INTEGER NOT NULL,
                    lease_until INTEGER NOT NULL DEFAULT 0,
                    sent_at INTEGER,
                    PRIMARY KEY (installation_id, event_id),
                    FOREIGN KEY (installation_id) REFERENCES installations(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS events_due ON events(state, next_attempt);
                CREATE TABLE IF NOT EXISTS registration_attempts (
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS registrations_recent
                    ON registration_attempts(created_at);
                CREATE TABLE IF NOT EXISTS installation_tombstones (
                    id TEXT PRIMARY KEY,
                    secret_hash TEXT NOT NULL,
                    deleted_at INTEGER NOT NULL
                );
            """)

    @contextmanager
    def connect(self):
        _secure_file(self.path)
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()


class Relay:
    def __init__(self, store, sender, *, now=time.time):
        self.store = store
        self.sender = sender
        self.now = now

    def create(self, body):
        if not isinstance(body, dict) or set(body) != {"fid", "platform", "locale"}:
            raise ApiError(400, "invalid installation")
        fid, platform, locale = body["fid"], body["platform"], body["locale"]
        if not isinstance(fid, str) or not _FID.fullmatch(fid):
            raise ApiError(400, "invalid FID")
        if (not isinstance(platform, str) or platform not in ("ios", "android") or
                not isinstance(locale, str) or locale not in LOCALES):
            raise ApiError(400, "invalid platform or locale")
        installation_id = secrets.token_urlsafe(24)
        secret = secrets.token_hex(32)
        now = int(self.now())
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute("SELECT COUNT(*) FROM installations").fetchone()[0]
            hourly = conn.execute("SELECT COUNT(*) FROM registration_attempts WHERE created_at>=?",
                                  (now-3600,)).fetchone()[0]
            daily = conn.execute("SELECT COUNT(*) FROM registration_attempts WHERE created_at>=?",
                                 (now-86400,)).fetchone()[0]
            if (active >= MAX_ACTIVE_INSTALLATIONS or hourly >= MAX_CREATIONS_PER_HOUR or
                    daily >= MAX_CREATIONS_PER_DAY):
                raise ApiError(429, "registration capacity reached")
            count = conn.execute("SELECT COUNT(*) FROM installations WHERE fid=?",
                                 (fid,)).fetchone()[0]
            if count >= MAX_INSTALLATIONS_PER_FID:
                raise ApiError(429, "installation limit")
            conn.execute("""INSERT INTO installations
                (id, fid, secret_hash, platform, locale, created_at, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (installation_id, fid, _digest(secret), platform, locale, now, now))
            conn.execute("INSERT INTO registration_attempts(created_at) VALUES (?)", (now,))
        return {"installation_id": installation_id, "sender_secret": secret}

    def _authorize(self, conn, installation_id, secret):
        if not isinstance(installation_id, str) or not _ID.fullmatch(installation_id):
            raise ApiError(404, "installation unavailable")
        row = conn.execute("SELECT * FROM installations WHERE id=?", (installation_id,)).fetchone()
        if (not row or row["status"] != "active" or not isinstance(secret, str) or
                not _SECRET.fullmatch(secret) or
                not hmac.compare_digest(row["secret_hash"], _digest(secret))):
            raise ApiError(401, "installation unauthorized")
        return row

    def update(self, installation_id, secret, body):
        if not isinstance(body, dict) or set(body) != {"fid", "locale"}:
            raise ApiError(400, "invalid installation update")
        fid, locale = body["fid"], body["locale"]
        if (not isinstance(fid, str) or not _FID.fullmatch(fid) or
                not isinstance(locale, str) or locale not in LOCALES):
            raise ApiError(400, "invalid FID or locale")
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._authorize(conn, installation_id, secret)
            count = conn.execute("SELECT COUNT(*) FROM installations WHERE fid=? AND id<>?",
                                 (fid, installation_id)).fetchone()[0]
            if count >= MAX_INSTALLATIONS_PER_FID:
                raise ApiError(429, "installation limit")
            conn.execute("UPDATE installations SET fid=?,locale=?,last_seen=? WHERE id=?",
                         (fid, locale, int(self.now()), installation_id))
        return {"ok": True}

    def delete(self, installation_id, secret):
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not isinstance(installation_id, str) or not _ID.fullmatch(installation_id):
                raise ApiError(404, "installation unavailable")
            row = conn.execute("SELECT secret_hash FROM installations WHERE id=?",
                               (installation_id,)).fetchone()
            if row is None:
                row = conn.execute("SELECT secret_hash FROM installation_tombstones WHERE id=?",
                                   (installation_id,)).fetchone()
                if (row and isinstance(secret, str) and _SECRET.fullmatch(secret) and
                        hmac.compare_digest(row["secret_hash"], _digest(secret))):
                    return {"ok": True}
                raise ApiError(401, "installation unauthorized")
            self._authorize(conn, installation_id, secret)
            conn.execute("""INSERT INTO installation_tombstones(id,secret_hash,deleted_at)
                VALUES (?,?,?)""", (installation_id, row["secret_hash"], int(self.now())))
            conn.execute("DELETE FROM installations WHERE id=?", (installation_id,))
        return {"ok": True}

    def enqueue(self, installation_id, secret, body):
        if not isinstance(body, dict) or set(body) != {"event_id", "kind", "value", "occurred_at"}:
            raise ApiError(400, "invalid event")
        event_id, kind = body["event_id"], body["kind"]
        if (not isinstance(event_id, str) or not _EVENT.fullmatch(event_id) or
                not isinstance(kind, str) or kind not in KINDS):
            raise ApiError(400, "invalid event")
        value = body["value"]
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
            raise ApiError(400, "invalid event value")
        if kind in ("charge_complete", "charge_stopped", "soc_below"):
            if value is None or not 0 <= value <= 100:
                raise ApiError(400, "invalid percent")
        elif kind == "volt12_low":
            if value is not None and not 0 <= value <= 20:
                raise ApiError(400, "invalid voltage")
            # A low status can be reported without a usable measured voltage.
            # Treat the sentinel 0 V as absent, never as a displayed reading.
            if value == 0:
                value = None
        elif kind == "charge_started":
            # The car's estimated finish, as minutes past midnight on the phone's
            # clock: the dashboard holds the phone's time zone and this relay
            # does not. Null when the car gave no estimate.
            if value is not None and (value != int(value) or not 0 <= value < 1440):
                raise ApiError(400, "invalid time")
        elif kind == "panel_open":
            # What is open, as the app's OpenPanels mask (doors 1/2/4/8,
            # tailgate 16, sunroof 32, window 64). Null from a dashboard that
            # predates the mask, which keeps the generic copy.
            if value is not None and (value != int(value) or not 1 <= value <= 127):
                raise ApiError(400, "invalid panels")
        elif value is not None:
            raise ApiError(400, "unexpected value")
        occurred = body["occurred_at"]
        now = int(self.now())
        if type(occurred) is not int or occurred > now + 120 or occurred < now - EVENT_LIFETIME:
            raise ApiError(410, "event expired")
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._authorize(conn, installation_id, secret)
            if conn.execute("SELECT 1 FROM events WHERE installation_id=? AND event_id=?",
                            (installation_id, event_id)).fetchone():
                return {"accepted": True, "duplicate": True}
            hour = conn.execute("""SELECT COUNT(*) FROM events WHERE installation_id=?
                AND occurred_at>=?""", (installation_id, now - 3600)).fetchone()[0]
            day = conn.execute("""SELECT COUNT(*) FROM events WHERE installation_id=?
                AND occurred_at>=?""", (installation_id, now - 86400)).fetchone()[0]
            if hour >= MAX_PER_HOUR or day >= MAX_PER_DAY:
                raise ApiError(429, "event rate limit")
            conn.execute("""INSERT INTO events
                (installation_id,event_id,kind,value,occurred_at,expires_at,next_attempt)
                VALUES (?,?,?,?,?,?,?)""",
                (installation_id,event_id,kind,value,occurred,occurred+EVENT_LIFETIME,now))
        return {"accepted": True, "duplicate": False}

    def drain_due(self, limit=20):
        """At-least-once send: a crash after FCM accepts may retry one event."""
        now = int(self.now())
        sent = 0
        for _ in range(limit):
            with self.store.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("""SELECT e.*,i.fid,i.platform,i.locale FROM events e
                    JOIN installations i ON i.id=e.installation_id
                    WHERE e.state IN ('pending','sending') AND e.next_attempt<=?
                    AND e.lease_until<=? ORDER BY e.next_attempt LIMIT 1""", (now, now)).fetchone()
                if row is None:
                    break
                if row["expires_at"] <= now or row["attempts"] >= 5:
                    conn.execute("UPDATE events SET state='expired' WHERE installation_id=? AND event_id=?",
                                 (row["installation_id"], row["event_id"]))
                    continue
                conn.execute("""UPDATE events SET state='sending', attempts=attempts+1,
                    lease_until=? WHERE installation_id=? AND event_id=?""",
                    (now+60, row["installation_id"], row["event_id"]))
            try:
                self.sender.send(dict(row))
            except InvalidInstallation:
                with self.store.connect() as conn:
                    conn.execute("DELETE FROM installations WHERE id=?", (row["installation_id"],))
            except Exception:
                delay = min(60 * (2 ** row["attempts"]), 300)
                with self.store.connect() as conn:
                    conn.execute("""UPDATE events SET state='pending',next_attempt=?,lease_until=0
                        WHERE installation_id=? AND event_id=?""",
                        (now+delay, row["installation_id"], row["event_id"]))
            else:
                sent += 1
                with self.store.connect() as conn:
                    conn.execute("""UPDATE events SET state='sent',sent_at=?,lease_until=0
                        WHERE installation_id=? AND event_id=?""",
                        (int(self.now()), row["installation_id"], row["event_id"]))
        return sent

    def cleanup(self, stale_days=90):
        now = int(self.now())
        with self.store.connect() as conn:
            conn.execute("DELETE FROM installations WHERE last_seen<?", (now-stale_days*86400,))
            conn.execute("DELETE FROM events WHERE expires_at<?", (now-7*86400,))
            conn.execute("DELETE FROM registration_attempts WHERE created_at<?", (now-86400,))
            conn.execute("DELETE FROM installation_tombstones WHERE deleted_at<?", (now-7*86400,))

    def metrics(self):
        with self.store.connect() as conn:
            installs = conn.execute("SELECT COUNT(*) FROM installations").fetchone()[0]
            states = dict(conn.execute("SELECT state,COUNT(*) FROM events GROUP BY state").fetchall())
        return {"installations": installs, "events": states}


class InvalidInstallation(Exception):
    """FCM reports that a FID is no longer registered."""
