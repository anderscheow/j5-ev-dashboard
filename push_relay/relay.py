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

# The charging Live Activity. The dashboard posts what the activity should show; the phone posts
# the ActivityKit tokens that address it. Only the newest state per phone is worth sending, so the
# queue keeps one row per installation instead of one per event.
ACTIVITY_EVENTS = frozenset(("start", "update", "end"))
ACTIVITY_PHASES = frozenset(("charging", "complete", "stopped"))
ACTIVITY_KEYS = frozenset(("phase", "soc", "updated_at", "target_pct", "rate_kw", "mode",
                           "finish_at", "estimating"))
ACTIVITY_LIFETIME = 10 * 60
MAX_ACTIVITY_PER_HOUR = 90
_TOKEN = re.compile(r"^[0-9a-f]{16,512}$")


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
                CREATE TABLE IF NOT EXISTS activity_pushes (
                    installation_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    content TEXT NOT NULL,
                    alert_minute INTEGER,
                    occurred_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt INTEGER NOT NULL,
                    lease_until INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (installation_id) REFERENCES installations(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS activity_due ON activity_pushes(state, next_attempt);
                CREATE TABLE IF NOT EXISTS activity_log (
                    installation_id TEXT NOT NULL,
                    at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS activity_recent ON activity_log(installation_id, at);
            """)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(installations)")}
            # The phone's ActivityKit tokens: push-to-start (one per install, iOS 17.2+) and the
            # update token of the activity on screen, with when it was last set.
            for column, kind in (("la_start_token", "TEXT"), ("la_update_token", "TEXT"),
                                 ("la_update_at", "INTEGER NOT NULL DEFAULT 0")):
                if column not in columns:
                    conn.execute(f"ALTER TABLE installations ADD COLUMN {column} {kind}")

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

    def set_activity_tokens(self, installation_id, secret, body):
        """The phone's ActivityKit tokens. Either may be sent alone; null forgets it.

        Called by the app's native code, which may run in a background wake with no Flutter and
        no App Check, so the installation secret is the whole credential -- the same one the
        dashboard holds, and a token only ever addresses this installation's own app."""
        if (not isinstance(body, dict) or not body or
                not set(body) <= {"push_to_start", "update"}):
            raise ApiError(400, "invalid activity tokens")
        for value in body.values():
            if value is not None and (not isinstance(value, str) or not _TOKEN.fullmatch(value)):
                raise ApiError(400, "invalid activity token")
        now = int(self.now())
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._authorize(conn, installation_id, secret)
            if "push_to_start" in body:
                conn.execute("UPDATE installations SET la_start_token=? WHERE id=?",
                             (body["push_to_start"], installation_id))
            if "update" in body:
                conn.execute("UPDATE installations SET la_update_token=?,la_update_at=? WHERE id=?",
                             (body["update"], now, installation_id))
            conn.execute("UPDATE installations SET last_seen=? WHERE id=?",
                         (now, installation_id))
        return {"ok": True}

    @staticmethod
    def _activity_content(content, now):
        if not isinstance(content, dict) or set(content) != ACTIVITY_KEYS:
            raise ApiError(400, "invalid activity content")
        soc, updated = content["soc"], content["updated_at"]
        if (content["phase"] not in ACTIVITY_PHASES or type(soc) is not int or
                not 0 <= soc <= 100 or type(updated) is not int or
                not now - 86400 <= updated <= now + 120 or
                type(content["estimating"]) is not bool):
            raise ApiError(400, "invalid activity content")
        target, rate, finish = content["target_pct"], content["rate_kw"], content["finish_at"]
        if target is not None and (type(target) is not int or not 1 <= target <= 100):
            raise ApiError(400, "invalid activity target")
        if rate is not None and (type(rate) not in (int, float) or not math.isfinite(rate) or
                                 not 0 <= rate <= 1000):
            raise ApiError(400, "invalid activity rate")
        if content["mode"] not in (None, "ac", "dc"):
            raise ApiError(400, "invalid activity mode")
        if finish is not None and (type(finish) is not int or not updated <= finish <= updated + 86400):
            raise ApiError(400, "invalid activity finish")
        return {key: content[key] for key in sorted(ACTIVITY_KEYS)}

    def enqueue_activity(self, installation_id, secret, body):
        """What the charging Live Activity should show now, from the dashboard.

        Latest wins: an update that lands before an unsent start keeps the start (with the newer
        content), and anything else replaces whatever was waiting."""
        # Nothing that identifies the car: no model, plate or VIN reaches the relay, so a
        # remote start carries no vehicle label.
        if (not isinstance(body, dict) or
                not {"event_id", "event", "content", "occurred_at"} <= set(body) <=
                {"event_id", "event", "content", "occurred_at", "alert_minute"}):
            raise ApiError(400, "invalid activity")
        event_id, event = body["event_id"], body["event"]
        if (not isinstance(event_id, str) or not _EVENT.fullmatch(event_id) or
                event not in ACTIVITY_EVENTS):
            raise ApiError(400, "invalid activity")
        minute = body.get("alert_minute")
        if minute is not None and (type(minute) is not int or not 0 <= minute < 1440):
            raise ApiError(400, "invalid time")
        now = int(self.now())
        occurred = body["occurred_at"]
        if type(occurred) is not int or occurred > now + 120 or occurred < now - ACTIVITY_LIFETIME:
            raise ApiError(410, "activity expired")
        content = json.dumps(self._activity_content(body["content"], now), separators=(",", ":"))
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._authorize(conn, installation_id, secret)
            waiting = conn.execute("""SELECT event_id,event,state,alert_minute
                FROM activity_pushes WHERE installation_id=?""", (installation_id,)).fetchone()
            if waiting and waiting["event_id"] == event_id:
                return {"accepted": True, "duplicate": True}
            recent = conn.execute("""SELECT COUNT(*) FROM activity_log WHERE installation_id=?
                AND at>=?""", (installation_id, now - 3600)).fetchone()[0]
            if recent >= MAX_ACTIVITY_PER_HOUR:
                raise ApiError(429, "activity rate limit")
            if (event == "update" and waiting and waiting["event"] == "start" and
                    waiting["state"] == "pending"):
                event, minute = "start", waiting["alert_minute"]
            conn.execute("""INSERT OR REPLACE INTO activity_pushes
                (installation_id,event_id,event,content,alert_minute,occurred_at,expires_at,
                 next_attempt) VALUES (?,?,?,?,?,?,?,?)""",
                (installation_id, event_id, event, content, minute, occurred,
                 occurred + ACTIVITY_LIFETIME, now))
            conn.execute("INSERT INTO activity_log(installation_id,at) VALUES (?,?)",
                         (installation_id, now))
        return {"accepted": True, "duplicate": False}

    def drain_activities(self, limit=20):
        """Send the waiting activity states. A start needs the push-to-start token and anything
        else the update token; a phone without the one it needs (Android, iOS before 17.2, Live
        Activities switched off) is skipped rather than retried."""
        now = int(self.now())
        sent = 0
        for _ in range(limit):
            with self.store.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("""SELECT a.*,i.fid,i.platform,i.locale,i.la_start_token,
                    i.la_update_token FROM activity_pushes a
                    JOIN installations i ON i.id=a.installation_id
                    WHERE a.state IN ('pending','sending') AND a.next_attempt<=?
                    AND a.lease_until<=? ORDER BY a.next_attempt LIMIT 1""",
                    (now, now)).fetchone()
                if row is None:
                    break
                key = (row["installation_id"], row["event_id"])
                token = row["la_start_token"] if row["event"] == "start" else row["la_update_token"]
                if row["expires_at"] <= now or row["attempts"] >= 4 or not token:
                    conn.execute("""UPDATE activity_pushes SET state=? WHERE installation_id=?
                        AND event_id=?""", ("skipped" if not token else "expired", *key))
                    continue
                conn.execute("""UPDATE activity_pushes SET state='sending',attempts=attempts+1,
                    lease_until=? WHERE installation_id=? AND event_id=?""", (now + 60, *key))
            started = int(self.now())
            try:
                self.sender.send_activity(dict(row), token)
            except InvalidActivityToken:
                # APNs no longer knows the token -- the activity ended, or the app was
                # reinstalled. Forget it so nothing more is addressed to it.
                column = "la_start_token" if row["event"] == "start" else "la_update_token"
                with self.store.connect() as conn:
                    conn.execute(f"UPDATE installations SET {column}=NULL WHERE id=?", (key[0],))
                    conn.execute("""UPDATE activity_pushes SET state='rejected',lease_until=0
                        WHERE installation_id=? AND event_id=?""", key)
            except Exception:
                delay = min(15 * (2 ** row["attempts"]), 120)
                with self.store.connect() as conn:
                    conn.execute("""UPDATE activity_pushes SET state='pending',next_attempt=?,
                        lease_until=0 WHERE installation_id=? AND event_id=?""",
                        (now + delay, *key))
            else:
                sent += 1
                with self.store.connect() as conn:
                    conn.execute("""UPDATE activity_pushes SET state='sent',lease_until=0
                        WHERE installation_id=? AND event_id=?""", key)
                    if row["event"] == "start":
                        # A push-to-start makes a new activity, which reports its own update
                        # token; the old one addresses an activity that is gone. One the phone
                        # posted after this send began belongs to the new activity and stays.
                        conn.execute("""UPDATE installations SET la_update_token=NULL
                            WHERE id=? AND la_update_at<?""", (key[0], started))
        return sent

    def cleanup(self, stale_days=90):
        now = int(self.now())
        with self.store.connect() as conn:
            conn.execute("DELETE FROM installations WHERE last_seen<?", (now-stale_days*86400,))
            conn.execute("DELETE FROM events WHERE expires_at<?", (now-7*86400,))
            conn.execute("DELETE FROM registration_attempts WHERE created_at<?", (now-86400,))
            conn.execute("DELETE FROM installation_tombstones WHERE deleted_at<?", (now-7*86400,))
            conn.execute("DELETE FROM activity_pushes WHERE expires_at<? AND state NOT IN "
                         "('pending','sending')", (now-86400,))
            conn.execute("DELETE FROM activity_log WHERE at<?", (now-86400,))

    def metrics(self):
        with self.store.connect() as conn:
            installs = conn.execute("SELECT COUNT(*) FROM installations").fetchone()[0]
            states = dict(conn.execute("SELECT state,COUNT(*) FROM events GROUP BY state").fetchall())
            activities = dict(conn.execute(
                "SELECT state,COUNT(*) FROM activity_pushes GROUP BY state").fetchall())
        return {"installations": installs, "events": states, "activities": activities}


class InvalidInstallation(Exception):
    """FCM reports that a FID is no longer registered."""


class InvalidActivityToken(Exception):
    """APNs no longer accepts a Live Activity token."""
