"""Single-owner, durable Voltmoss alert dispatch from committed telemetry.

The HTTP summary route stays read-only. This process observes newly
committed telemetry frames, evaluates each registered phone's rules, and queues
small events for the operator's relay. It deliberately skips old catch-up
frames: a late warning about an unlocked car can be worse than no warning.
"""

import hashlib
import json
import logging
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import closing
from datetime import datetime
from zoneinfo import ZoneInfo

import push_evaluator
import push_registrations as registrations


EVENT_TTL = 15 * 60
FRAME_MAX_AGE = 120
LEASE_SECONDS = 15
LOG = logging.getLogger("carlinko.push_dispatcher")


def _connection(db):
    return registrations._connect(db)


def healthy(db, now=None):
    """Whether a dispatcher has renewed its lease recently."""
    if not os.path.exists(db):
        return False
    now = int(time.time() if now is None else now)
    try:
        with closing(_connection(db)) as conn:
            row = conn.execute("""SELECT lease_until,heartbeat_at FROM push_dispatcher_state
                WHERE singleton=1""").fetchone()
            return bool(row and row[0] > now and row[1] >= now - LEASE_SECONDS)
    except (OSError, sqlite3.Error):
        return False


def alert_snapshot(summary):
    """Only the fields Dart's alert evaluator reads from /api/summary."""
    charging = summary.get("charging") or {}
    vehicle = summary.get("vehicle") or {}
    model = str(vehicle.get("model") or "").lower()
    windows = summary.get("windows")
    windows_open = (windows != 0 if isinstance(windows, int) and
                    "j5" not in model and "jaecoo" not in model else None)
    return {
        "charging": charging.get("active") is True,
        "battery": summary.get("battery"),
        "moving": summary.get("moving") is True,
        "unlocked": summary.get("unlocked") is True,
        "doors": summary.get("doors"),
        "trunk_open": summary.get("trunk_open"),
        "sunroof_open": summary.get("sunroof_open"),
        "windows_open": windows_open,
        "tyre_status": summary.get("tyre_status"),
        "tyre_indirect": summary.get("tyre_indirect"),
        "tyre_unit": summary.get("tyre_unit"),
        "tpms": summary.get("tpms") or [],
        "volt12_status": summary.get("volt12_status"),
        "volt12": summary.get("volt12"),
        "odometer": summary.get("odometer"),
    }


class TelemetryWindow:
    """Point-in-time alert input from a bounded slice of telemetry.

    summary() decodes every stored frame. That suits a request, not judging each new frame: it
    grows with the history, to seconds a frame after a year of logging. This decodes only
    server.ALERT_WINDOW before the frame being judged, and carries the one fact older than that
    -- the last good tyre reading -- forward incrementally. The frame itself is passed in rather
    than re-read, so a same-second replacement cannot swap it underneath the evaluation.
    """

    def __init__(self, db, server_module=None):
        self.db = db
        self._server = server_module
        self._tyre = None  # (until_ts, frame): newest good tyre frame with ts < until_ts

    @property
    def server(self):
        if self._server is None:
            import server
            self._server = server
        return self._server

    def _decode(self, rows):
        return [(ts, dt, self.server.decode(raw)) for ts, dt, online, raw in rows
                if online == 1 and raw]

    def _tyre_before(self, conn, start):
        if self._tyre and self._tyre[0] <= start:
            until, frame = self._tyre
            rows = conn.execute("""SELECT ts,dt,online,raw FROM telemetry
                WHERE ts>=? AND ts<? ORDER BY ts""", (until, start)).fetchall()
            frame = self.server._last_tyre_frame(self._decode(rows)) or frame
        else:
            # First use: walk back until a reading turns up. An indirect-TPMS car never has
            # one, so this decodes its whole history once and then only the window's tail.
            frame, before = None, start
            while frame is None:
                rows = conn.execute("""SELECT ts,dt,online,raw FROM telemetry
                    WHERE ts<? ORDER BY ts DESC LIMIT 2000""", (before,)).fetchall()
                if not rows:
                    break
                frame = self.server._last_tyre_frame(self._decode(rows[::-1]))
                before = rows[-1][0]
        self._tyre = (start, frame)
        return frame

    def read(self, ts, dt, raw, now):
        start = ts - self.server.ALERT_WINDOW
        with closing(sqlite3.connect(self.db, timeout=5)) as conn:
            rows = conn.execute("""SELECT ts,dt,online,raw FROM telemetry
                WHERE ts>=? AND ts<? ORDER BY ts""", (start, ts)).fetchall()
            data = self._decode(rows) + [(ts, dt, self.server.decode(raw))]
            tyre = (None if self.server._last_tyre_frame(data)
                    else self._tyre_before(conn, start))
        return self.server.alert_summary(data, now, tyre_frame=tyre)


def _local_date(ts, time_zone):
    """The phone's calendar day at ts: the service reminder counts days on the phone, and a
    dashboard on UTC would otherwise move a due date by one near midnight."""
    try:
        zone = ZoneInfo(time_zone)
    except (ValueError, KeyError):
        zone = ZoneInfo("UTC")
    return datetime.fromtimestamp(ts, zone).date()


class RelayClient:
    """Send only to the configured HTTPS relay; never follow redirects."""

    def __init__(self, base_url):
        parsed = urllib.parse.urlsplit(base_url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or
                parsed.password or parsed.query or parsed.fragment):
            raise ValueError("HTTPS relay URL required")
        self.base_url = base_url.rstrip("/")

    def send(self, installation_id, secret, event):
        if not registrations.valid_id(installation_id):
            raise ValueError("invalid installation ID")
        request = urllib.request.Request(
            self.base_url + "/v1/installations/" + installation_id + "/events",
            data=json.dumps(event, separators=(",", ":")).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + secret},
            method="POST")
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(request, timeout=8) as response:
                return response.status
        except urllib.error.HTTPError as error:
            return error.code


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, msg, headers, newurl):
        return None


class Dispatcher:
    def __init__(self, telemetry_db, push_db, relay, *, summary_reader=None,
                 now=time.time, owner=None):
        self.telemetry_db = telemetry_db
        self.push_db = push_db
        self.relay = relay
        self.summary_reader = summary_reader or TelemetryWindow(telemetry_db).read
        self.now = now
        self.owner = owner or uuid.uuid4().hex

    def _lease(self, now):
        """Take or renew the singleton lease; False when another live worker holds it.

        Renewed before every frame and every relay send, so a slow send cannot let the lease
        lapse unnoticed: a worker that finds it taken stops, and the new owner carries on."""
        with closing(_connection(self.push_db)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("""SELECT owner,lease_until FROM push_dispatcher_state
                WHERE singleton=1""").fetchone()
            if row[1] > now and row[0] != self.owner:
                return False
            conn.execute("""UPDATE push_dispatcher_state SET owner=?,lease_until=?,heartbeat_at=?
                WHERE singleton=1""", (self.owner, now + LEASE_SECONDS, now))
        return True

    def _pending_rows(self, cursor_ts):
        if not os.path.exists(self.telemetry_db):
            return []
        with closing(sqlite3.connect(self.telemetry_db, timeout=5)) as conn:
            try:
                return conn.execute("""SELECT ts,dt,online,raw FROM telemetry
                    WHERE ts>=? ORDER BY ts LIMIT 100""", (cursor_ts,)).fetchall()
            except sqlite3.OperationalError:
                return []

    def run_once(self):
        now = int(self.now())
        if not self._lease(now):
            return False
        with closing(_connection(self.push_db)) as conn:
            cursor = conn.execute("""SELECT cursor_ts,cursor_digest
                FROM push_dispatcher_state WHERE singleton=1""").fetchone()
        if cursor[0] == 0:
            # An established dashboard may have years of telemetry. Old frames
            # are never notification evidence; start at the fresh window.
            cutoff = now - FRAME_MAX_AGE - 1
            with closing(_connection(self.push_db)) as conn, conn:
                conn.execute("""UPDATE push_dispatcher_state SET cursor_ts=?
                    WHERE singleton=1 AND cursor_ts=0""", (cutoff,))
                cursor = conn.execute("""SELECT cursor_ts,cursor_digest
                    FROM push_dispatcher_state WHERE singleton=1""").fetchone()
        for ts, dt, online, raw in self._pending_rows(cursor[0]):
            digest = hashlib.sha256(raw.encode()).hexdigest() if raw else ""
            if ts > cursor[0] or (ts == cursor[0] and digest != cursor[1]):
                now = int(self.now())
                if not self._lease(now):
                    return False
                usable = (online == 1 and raw and 0 <= now - ts <= FRAME_MAX_AGE)
                summary = self.summary_reader(ts, dt, raw, now) if usable else None
                self._evaluate_frame(ts, digest, summary, now)
                cursor = (ts, digest)
        self.drain()
        return True

    def _evaluate_frame(self, ts, digest, summary, now):
        snapshot = alert_snapshot(summary) if summary else None
        if snapshot and (type(snapshot["battery"]) is not int or
                         not 0 <= snapshot["battery"] <= 100):
            snapshot = None
        with closing(_connection(self.push_db)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute("""SELECT cursor_ts,cursor_digest FROM push_dispatcher_state
                WHERE singleton=1""").fetchone()
            if ts < cursor[0] or (ts == cursor[0] and digest == cursor[1]):
                return
            if snapshot:
                installations = conn.execute("""SELECT installation_id,rules_json,
                    service_json,plus,memory_json,time_zone FROM push_installations""").fetchall()
                for (installation_id, rules_json, service_json, plus, memory_json,
                     time_zone) in installations:
                    rules = json.loads(rules_json)["rules"]
                    if not rules:
                        continue
                    memory = json.loads(memory_json) if memory_json else {}
                    events, next_memory = push_evaluator.evaluate(
                        rules, memory, snapshot, json.loads(service_json),
                        today=_local_date(ts, time_zone))
                    conn.execute("""UPDATE push_installations SET memory_json=?
                        WHERE installation_id=?""", (json.dumps(next_memory), installation_id))
                    if not plus or not memory_json:
                        continue  # track the condition, but baseline silently
                    for event in events:
                        event_id = "e_" + hashlib.sha256(
                            (installation_id + ":" + str(ts) + ":" + digest + ":" +
                             event["kind"]).encode()).hexdigest()[:40]
                        conn.execute("""INSERT OR IGNORE INTO push_outbox
                            (event_id,installation_id,kind,value,occurred_at,expires_at,next_attempt)
                            VALUES (?,?,?,?,?,?,?)""", (
                            event_id, installation_id, event["kind"], event["value"],
                            ts, ts + EVENT_TTL, now))
            conn.execute("""UPDATE push_dispatcher_state SET cursor_ts=?,cursor_digest=?
                WHERE singleton=1""", (ts, digest))

    def drain(self, limit=20):
        for _ in range(limit):
            now = int(self.now())
            if not self._lease(now):
                return  # another worker took over while a send was slow; it drains now
            with closing(_connection(self.push_db)) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("""SELECT o.event_id,o.installation_id,o.kind,o.value,
                    o.occurred_at,o.expires_at,o.attempts,i.sender_secret
                    FROM push_outbox o JOIN push_installations i
                    ON i.installation_id=o.installation_id
                    WHERE o.state IN ('pending','sending') AND o.next_attempt<=?
                    ORDER BY o.next_attempt LIMIT 1""", (now,)).fetchone()
                if not row:
                    break
                event_id, installation_id, kind, value, occurred, expiry, attempts, secret = row
                if expiry <= now or attempts >= 5:
                    conn.execute("UPDATE push_outbox SET state='expired' WHERE event_id=?",
                                 (event_id,))
                    continue
                conn.execute("""UPDATE push_outbox SET state='sending',attempts=attempts+1,
                    next_attempt=? WHERE event_id=?""", (now + 30, event_id))
            event = {"event_id": event_id, "kind": kind, "value": value,
                     "occurred_at": occurred}
            try:
                code = self.relay.send(installation_id, secret, event)
            except (OSError, TimeoutError, urllib.error.URLError):
                code = 503
            with closing(_connection(self.push_db)) as conn, conn:
                if code == 202:
                    conn.execute("UPDATE push_outbox SET state='sent' WHERE event_id=?",
                                 (event_id,))
                elif code in (401, 404):
                    # The relay no longer knows this phone: it signed out, switched server or
                    # turned alerts off, and revoked at the relay -- the one place it can always
                    # reach. Drop the binding so nothing more is evaluated or queued for it.
                    conn.execute("DELETE FROM push_installations WHERE installation_id=?",
                                 (installation_id,))
                    conn.execute("DELETE FROM push_outbox WHERE installation_id=?",
                                 (installation_id,))
                elif code in (400, 410):
                    conn.execute("UPDATE push_outbox SET state='rejected' WHERE event_id=?",
                                 (event_id,))
                else:
                    delay = min(30 * (2 ** attempts), 300)
                    conn.execute("""UPDATE push_outbox SET state='pending',next_attempt=?
                        WHERE event_id=?""", (now + delay, event_id))


def main():
    import server
    relay_url = os.environ.get("VOLTMOSS_PUSH_RELAY_URL", "")
    if not relay_url:
        raise SystemExit("push dispatcher requires HTTPS relay configuration")
    dispatcher = Dispatcher(server.DB, server.PUSH_DB, RelayClient(relay_url))
    while True:
        if not server.push_configured():
            time.sleep(5)  # first-run setup may create creds.json later
            continue
        try:
            dispatcher.run_once()
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as error:
            # Never log telemetry, per-device secrets, or exception text.
            LOG.warning("dispatcher tick failed: %s", type(error).__name__)
        time.sleep(2)


if __name__ == "__main__":
    main()
