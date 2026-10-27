#!/usr/bin/env python3
"""Pure alert parity and durable dispatcher checks, with no live relay."""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
import push_dispatcher as dispatch
import push_evaluator as evaluator
import push_registrations as registrations
import server


SECRET = "a" * 64
RULES = {"v": 1, "rules": {"soc_below": {"on": True, "t": 20}}}
SERVICE = {"last_odo": None, "last_date": None,
           "interval_km": None, "interval_months": None}


def binding(installation_id, *, rules=RULES, plus=True, service=SERVICE, time_zone="UTC"):
    return registrations.validate(installation_id, {
        "sender_secret": SECRET, "rules": rules, "locale": "en",
        "service": service, "plus": plus, "time_zone": time_zone,
    })


def frame(battery, odometer, *, volt12=12.6, unlocked=False, doors=0, tyre=None,
          charge_state=0, charge_power=0.0):
    """A raw status blob at the offsets server.decode() reads."""
    b = bytearray(72)
    b[2] = doors
    b[3] = 1 if unlocked else 0
    b[12:14] = round(volt12 * 100).to_bytes(2, "big")
    b[18:21] = odometer.to_bytes(3, "big")
    b[28] = battery
    b[44:52] = bytes(tyre or [0xFF] * 8)
    b[57] = charge_state
    b[62:64] = round(charge_power * 10).to_bytes(2, "big")
    return b.hex()


def snapshot(**changes):
    value = {"charging": False, "battery": 60, "moving": False,
             "unlocked": False, "doors": 0, "trunk_open": False,
             "sunroof_open": False, "windows_open": None, "tyre_status": "Normal",
             "tyre_indirect": True, "tyre_unit": "psi", "tpms": [],
             "volt12_status": "ok", "volt12": 12.6, "odometer": 12000}
    value.update(changes)
    return value


class AlertParityTest(unittest.TestCase):
    def test_charge_crossing_and_stop_match_dart(self):
        rules = {"charge_complete": {"on": True, "t": 80},
                 "charge_stopped": {"on": True}}
        events, memory = evaluator.evaluate(
            rules, {"charging": True, "soc": 79},
            snapshot(charging=True, battery=82), SERVICE)
        self.assertEqual(events, [{"kind": "charge_complete", "value": 82}])
        events, _ = evaluator.evaluate(rules, memory, snapshot(battery=82), SERVICE)
        self.assertEqual(events, [])
        events, _ = evaluator.evaluate(rules, {"charging": True, "soc": 40},
                                       snapshot(battery=34), SERVICE)
        self.assertEqual(events, [{"kind": "charge_stopped", "value": 34}])

    def test_conditions_and_rearming_match_dart(self):
        rules = {kind: {"on": True} for kind in registrations.KINDS
                 if kind not in ("charge_complete", "charge_stopped", "soc_below")}
        first = snapshot(unlocked=True, doors=1, tyre_status="Check tyres",
                         volt12_status="low", volt12=11.7)
        events, memory = evaluator.evaluate(rules, {"charging": False, "soc": 60},
                                            first, SERVICE)
        self.assertEqual({e["kind"] for e in events},
                         {"left_unlocked", "panel_open", "tyre_pressure", "volt12_low"})
        repeat, memory = evaluator.evaluate(rules, memory, first, SERVICE)
        self.assertEqual(repeat, [])
        _, memory = evaluator.evaluate(rules, memory, snapshot(), SERVICE)
        rearmed, _ = evaluator.evaluate(rules, memory, first, SERVICE)
        self.assertEqual({e["kind"] for e in rearmed},
                         {"left_unlocked", "panel_open", "tyre_pressure", "volt12_low"})

    def test_service_due_needs_last_odometer(self):
        rules = {"service_due": {"on": True}}
        events, _ = evaluator.evaluate(rules, {}, snapshot(), SERVICE)
        self.assertEqual(events, [])
        configured = {**SERVICE, "last_odo": 3000, "interval_km": 10000}
        events, _ = evaluator.evaluate(rules, {}, snapshot(odometer=12001), configured)
        self.assertEqual(events, [{"kind": "service_due", "value": None}])

    def test_soc_threshold_only_when_not_charging(self):
        rules = {"soc_below": {"on": True, "t": 20}}
        events, _ = evaluator.evaluate(rules, {}, snapshot(battery=19, charging=True), SERVICE)
        self.assertEqual(events, [])
        events, _ = evaluator.evaluate(rules, {}, snapshot(battery=19), SERVICE)
        self.assertEqual(events, [{"kind": "soc_below", "value": 19}])

    def test_parked_panels_tyres_and_voltage(self):
        rules = {"left_unlocked": {"on": True}, "panel_open": {"on": True},
                 "tyre_pressure": {"on": True}, "volt12_low": {"on": True}}
        # A car being driven is not "left" anywhere: neither unlocked nor open counts.
        driving = snapshot(moving=True, unlocked=True, doors=1, windows_open=True)
        events, _ = evaluator.evaluate(rules, {}, driving, SERVICE)
        self.assertEqual(events, [])
        observed = snapshot(unlocked=True, windows_open=True,
                            tyre_indirect=False, tpms=[{"valid": True, "psi": 1.8}],
                            tyre_unit="bar", volt12_status=None, volt12=11.8)
        events, _ = evaluator.evaluate(rules, {}, observed, SERVICE)
        self.assertEqual({event["kind"] for event in events},
                         {"left_unlocked", "panel_open", "tyre_pressure", "volt12_low"})
        indirect = snapshot(tyre_indirect=True, tpms=[{"valid": True, "psi": 1.8}],
                            tyre_unit="bar")
        events, _ = evaluator.evaluate(rules, {}, indirect, SERVICE)
        self.assertEqual(events, [])

    def test_panel_open_says_what_is_open(self):
        rules = {"panel_open": {"on": True}}
        # Two doors and the sunroof; byte 2's undecoded high bits are no door.
        events, _ = evaluator.evaluate(
            rules, {}, snapshot(doors=1 | 4 | 64, sunroof_open=True), SERVICE)
        self.assertEqual(events, [{"kind": "panel_open", "value": 1 | 4 | 32}])
        events, _ = evaluator.evaluate(
            rules, {}, snapshot(trunk_open=True, windows_open=True), SERVICE)
        self.assertEqual(events, [{"kind": "panel_open", "value": 16 | 64}])
        # A silent field is not "closed", and not "open" either.
        events, _ = evaluator.evaluate(
            rules, {}, snapshot(doors=None, trunk_open=None), SERVICE)
        self.assertEqual(events, [])

    def test_low_12v_status_without_a_reading_has_no_value(self):
        rules = {"volt12_low": {"on": True}}
        for volts in (0, None, 0.0):
            events, _ = evaluator.evaluate(
                rules, {}, snapshot(volt12_status="low", volt12=volts), SERVICE)
            self.assertEqual(events, [{"kind": "volt12_low", "value": None}])
        events, _ = evaluator.evaluate(
            rules, {}, snapshot(volt12_status="critical", volt12=11.7), SERVICE)
        self.assertEqual(events, [{"kind": "volt12_low", "value": 11.7}])

    def test_service_due_counts_days_on_the_phone(self):
        # Due 31 March. On 2 March that is 29 days away (due); on 1 March, 30 (not yet).
        rules = {"service_due": {"on": True}}
        service = {"last_odo": 0, "last_date": "2026-01-31",
                   "interval_km": 100000, "interval_months": 2}
        ts = int(datetime(2026, 3, 1, 16, 30, tzinfo=timezone.utc).timestamp())
        self.assertEqual(dispatch._local_date(ts, "UTC"), date(2026, 3, 1))
        self.assertEqual(dispatch._local_date(ts, "Asia/Kuala_Lumpur"), date(2026, 3, 2))
        due, _ = evaluator.evaluate(rules, {}, snapshot(), service,
                                    today=dispatch._local_date(ts, "Asia/Kuala_Lumpur"))
        self.assertEqual(due, [{"kind": "service_due", "value": None}])
        early, _ = evaluator.evaluate(rules, {}, snapshot(), service,
                                      today=dispatch._local_date(ts, "UTC"))
        self.assertEqual(early, [])

    def test_missing_odometer_reads_as_zero_like_dart(self):
        rules = {"service_due": {"on": True}}
        by_date = {"last_odo": 0, "last_date": "2025-01-01",
                   "interval_km": 10000, "interval_months": 12}
        events, _ = evaluator.evaluate(rules, {}, snapshot(odometer=None), by_date,
                                       today=date(2026, 3, 1))
        self.assertEqual(events, [{"kind": "service_due", "value": None}])

    def test_windows_byte_is_unknown_on_jaecoo(self):
        summary = {"vehicle": {"model": "Jaecoo J5 EV"}, "charging": {"active": False},
                   "battery": 50, "windows": 16}
        self.assertIsNone(dispatch.alert_snapshot(summary)["windows_open"])
        summary["vehicle"]["model"] = "Omoda E5"
        self.assertTrue(dispatch.alert_snapshot(summary)["windows_open"])


class FakeRelay:
    def __init__(self, statuses=(202,)):
        self.statuses = list(statuses)
        self.sent = []
        self.during_send = None

    def send(self, installation_id, secret, event):
        self.sent.append((installation_id, secret, event))
        hook, self.during_send = self.during_send, None
        if hook:
            hook()
        return self.statuses.pop(0) if self.statuses else 202


class DispatcherTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.telemetry = os.path.join(self.temp.name, "telemetry.db")
        self.push = os.path.join(self.temp.name, "push.db")
        with closing(sqlite3.connect(self.telemetry)) as conn:
            conn.execute("CREATE TABLE telemetry(ts INTEGER PRIMARY KEY,dt TEXT,online INTEGER,raw TEXT)")
            conn.commit()
        self.now = 1770000000
        self.latest = {"updated": "", "battery": 25, "online": True,
                       "charging": {"active": False}, "vehicle": {"model": "Omoda E5"},
                       "moving": False, "unlocked": False, "doors": 0,
                       "trunk_open": False, "sunroof_open": False, "windows": 0,
                       "tyre_status": "Normal", "tyre_indirect": True,
                       "tyre_unit": "psi", "tpms": [], "volt12_status": "ok",
                       "volt12": 12.6, "odometer": 12000}
        self.snapshots = {}
        self.relay = FakeRelay()
        self.worker = dispatch.Dispatcher(
            self.telemetry, self.push, self.relay,
            summary_reader=self.reader, now=lambda: self.now,
            owner="owner-one")

    def reader(self, ts, dt, raw, now):
        return self.snapshots[ts]

    def row(self, ts, raw, *, battery=None, online=1):
        dt = "2026-02-02 00:%02d:%02d" % ((ts // 60) % 60, ts % 60)
        with closing(sqlite3.connect(self.telemetry)) as conn:
            conn.execute("INSERT OR REPLACE INTO telemetry VALUES (?,?,?,?)",
                         (ts, dt, online, raw))
            conn.commit()
        self.latest["updated"] = dt
        if battery is not None:
            self.latest["battery"] = battery
        self.snapshots[ts] = dict(self.latest)

    def test_first_frame_baselines_then_crossing_sends_once(self):
        registrations.put(self.push, binding("installation_1234567890"))
        self.row(self.now - 20, "aaa", battery=25)
        self.assertTrue(self.worker.run_once())
        self.assertEqual(self.relay.sent, [])
        self.row(self.now - 10, "bbb", battery=15)
        self.worker.run_once()
        self.assertEqual(len(self.relay.sent), 1)
        self.assertEqual(self.relay.sent[0][2]["kind"], "soc_below")
        self.worker.run_once()
        self.assertEqual(len(self.relay.sent), 1)
        self.row(self.now - 5, "bbb", battery=15)  # touch of same condition
        self.worker.run_once()
        self.assertEqual(len(self.relay.sent), 1)
        with closing(registrations._connect(self.push)) as conn:
            self.assertEqual(conn.execute("SELECT state FROM push_outbox").fetchone()[0], "sent")

    def test_queued_fresh_rows_are_evaluated_in_order(self):
        registrations.put(self.push, binding("installation_1234567890"))
        self.row(self.now - 20, "aaa", battery=25)
        self.row(self.now - 15, "bbb", battery=15)
        self.worker.run_once()
        self.assertEqual(len(self.relay.sent), 1)
        self.assertEqual(self.relay.sent[0][2]["kind"], "soc_below")

    def test_stale_frame_and_second_owner_do_not_dispatch(self):
        registrations.put(self.push, binding("installation_1234567890"))
        self.row(self.now - 300, "old", battery=15)
        self.worker.run_once()
        self.assertEqual(self.relay.sent, [])
        other = dispatch.Dispatcher(self.telemetry, self.push, self.relay,
                                    summary_reader=self.reader,
                                    now=lambda: self.now, owner="owner-two")
        self.assertFalse(other.run_once())
        self.assertTrue(dispatch.healthy(self.push, now=self.now))

    def test_retry_uses_same_event_id_and_lapsed_plus_tracks_memory(self):
        registrations.put(self.push, binding("installation_1234567890"))
        registrations.put(self.push, binding("installation_abcdefghij", plus=False))
        self.relay.statuses = [503, 202]
        self.row(self.now - 20, "aaa", battery=25)
        self.worker.run_once()
        self.row(self.now - 10, "bbb", battery=15)
        self.worker.run_once()
        self.assertEqual(len(self.relay.sent), 1)
        self.now += 31
        self.worker.run_once()
        self.assertEqual(len(self.relay.sent), 2)
        self.assertEqual(self.relay.sent[0][2]["event_id"],
                         self.relay.sent[1][2]["event_id"])
        with closing(registrations._connect(self.push)) as conn:
            memory = conn.execute("""SELECT memory_json FROM push_installations
                WHERE installation_id=?""", ("installation_abcdefghij",)).fetchone()[0]
        self.assertIn("soc_below", json.loads(memory)["fired"])

    def test_two_phones_keep_distinct_thresholds(self):
        registrations.put(self.push, binding("installation_1234567890"))
        low = {"v": 1, "rules": {"soc_below": {"on": True, "t": 10}}}
        registrations.put(self.push, binding("installation_abcdefghij", rules=low))
        self.row(self.now - 20, "aaa", battery=25)
        self.worker.run_once()
        self.row(self.now - 10, "bbb", battery=15)
        self.worker.run_once()
        self.assertEqual([sent[0] for sent in self.relay.sent],
                         ["installation_1234567890"])
        self.row(self.now - 5, "ccc", battery=5)
        self.worker.run_once()
        self.assertEqual([sent[0] for sent in self.relay.sent],
                         ["installation_1234567890", "installation_abcdefghij"])

    def test_new_worker_takes_over_after_lease_expires(self):
        self.row(self.now - 10, "aaa", battery=25)
        self.worker.run_once()
        replacement = dispatch.Dispatcher(self.telemetry, self.push, self.relay,
                                          summary_reader=self.reader,
                                          now=lambda: self.now, owner="owner-two")
        self.assertFalse(replacement.run_once())
        self.now += dispatch.LEASE_SECONDS + 1
        self.assertTrue(replacement.run_once())

    def test_slow_send_hands_over_without_a_second_sender(self):
        registrations.put(self.push, binding("installation_1234567890"))
        registrations.put(self.push, binding("installation_abcdefghij"))
        self.row(self.now - 20, "aaa", battery=25)
        self.worker.run_once()
        replacement = dispatch.Dispatcher(self.telemetry, self.push, self.relay,
                                          summary_reader=self.reader,
                                          now=lambda: self.now, owner="owner-two")

        def outlive_the_lease():
            self.now += dispatch.LEASE_SECONDS + 1
            self.assertTrue(replacement.run_once())

        self.relay.during_send = outlive_the_lease
        self.row(self.now - 10, "bbb", battery=15)
        self.assertTrue(self.worker.run_once())
        # The slow send finished, the replacement sent the other event, and the original worker
        # stopped at its next lease check rather than draining alongside it.
        self.assertEqual(sorted(sent[0] for sent in self.relay.sent),
                         ["installation_1234567890", "installation_abcdefghij"])
        self.assertEqual(len({sent[2]["event_id"] for sent in self.relay.sent}), 2)
        self.assertFalse(self.worker.run_once())
        with closing(registrations._connect(self.push)) as conn:
            self.assertEqual(conn.execute("SELECT owner FROM push_dispatcher_state").fetchone()[0],
                             "owner-two")
            self.assertEqual({row[0] for row in conn.execute("SELECT state FROM push_outbox")},
                             {"sent"})

    def test_relay_revocation_drops_the_binding(self):
        registrations.put(self.push, binding("installation_1234567890"))
        registrations.put(self.push, binding("installation_abcdefghij"))
        self.relay.statuses = [401, 202]
        self.row(self.now - 20, "aaa", battery=25)
        self.worker.run_once()
        self.row(self.now - 10, "bbb", battery=15)
        self.worker.run_once()
        self.assertEqual(registrations.count(self.push), 1)
        with closing(registrations._connect(self.push)) as conn:
            self.assertEqual([row[0] for row in conn.execute(
                "SELECT state FROM push_outbox")], ["sent"])

    def test_low_12v_without_a_reading_is_queued(self):
        rules = {"v": 1, "rules": {"volt12_low": {"on": True}}}
        registrations.put(self.push, binding("installation_1234567890", rules=rules))
        self.row(self.now - 20, "aaa")
        self.worker.run_once()
        self.latest.update(volt12_status="low", volt12=0)
        self.row(self.now - 10, "bbb")
        self.worker.run_once()
        self.assertEqual([(sent[2]["kind"], sent[2]["value"]) for sent in self.relay.sent],
                         [("volt12_low", None)])

    def test_service_day_follows_each_phone_time_zone(self):
        rules = {"v": 1, "rules": {"service_due": {"on": True}}}
        service = {"last_odo": 0, "last_date": "2026-01-31",
                   "interval_km": 100000, "interval_months": 2}
        registrations.put(self.push, binding("installation_utc_123456", rules=rules,
                                             service=service, time_zone="UTC"))
        registrations.put(self.push, binding("installation_kl_1234567", rules=rules,
                                             service=service, time_zone="Asia/Kuala_Lumpur"))
        # 23:00 in Kuala Lumpur on 1 March: 30 days to go everywhere, so both baseline quietly.
        first = int(datetime(2026, 3, 1, 15, 0, tzinfo=timezone.utc).timestamp())
        self.now = first + 5
        self.row(first, "aaa")
        self.worker.run_once()
        # 00:30 on 2 March there, still 1 March in UTC.
        second = int(datetime(2026, 3, 1, 16, 30, tzinfo=timezone.utc).timestamp())
        self.now = second + 5
        self.row(second, "bbb")
        self.worker.run_once()
        self.assertEqual([sent[0] for sent in self.relay.sent], ["installation_kl_1234567"])


class TelemetryWindowTest(unittest.TestCase):
    """The bounded reader must agree with summary() on every field an alert reads."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = os.path.join(self.temp.name, "carlinko.db")
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("CREATE TABLE telemetry(ts INTEGER PRIMARY KEY,dt TEXT,online INTEGER,raw TEXT)")
            conn.commit()
        p = patch.object(server, "DB", self.db)
        p.start()
        self.addCleanup(p.stop)
        self.window = dispatch.TelemetryWindow(self.db, server)
        self.checked = []

    def insert(self, ts, raw, online=1):
        dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("INSERT OR REPLACE INTO telemetry VALUES (?,?,?,?)", (ts, dt, online, raw))
            conn.commit()
        return dt

    def check(self, ts, raw):
        dt = self.insert(ts, raw)
        now = ts + 5
        with patch("time.time", return_value=now):
            full = dispatch.alert_snapshot(server.summary())
            bounded = dispatch.alert_snapshot(self.window.read(ts, dt, raw, now))
        self.assertEqual(bounded, full)
        self.checked.append(full)
        return full

    def test_window_matches_full_summary(self):
        start = 1_780_000_000
        # A soft tyre seen nine days ago, outside the window, then only sleeping sensors.
        self.insert(start, frame(70, 1000, tyre=[20, 20, 20, 20, 80, 80, 80, 80]))
        ts, odo, battery = start, 1000, 70
        for hour in range(1, 9 * 24):
            ts = start + hour * 3600
            odo += 3 if hour % 5 == 0 else 0
            battery = max(30, battery - (1 if hour % 7 == 0 else 0))
            # One dip below 12.5 V four days from the end makes the 7-day status "low".
            volts = 12.3 if hour == 9 * 24 - 96 else 12.6
            self.insert(ts, frame(battery, odo, volt12=volts))
        parked = self.check(ts + 60, frame(battery, odo))
        self.assertEqual(parked["tyre_status"], "Check tyres")
        self.assertEqual(parked["volt12_status"], "low")
        self.assertFalse(parked["charging"])
        # A slow AC charge the car does not flag: only the SoC-rise detector can see it.
        ts += 60
        for minute in range(1, 41):
            ts += 60
            charging = self.check(ts, frame(battery + minute // 4, odo))
        self.assertTrue(charging["charging"])
        # Then the car's own charging flag, then a drive.
        ts += 60
        self.assertTrue(self.check(ts, frame(battery + 11, odo, charge_state=1,
                                             charge_power=6.6))["charging"])
        ts += 60
        self.check(ts, frame(battery + 11, odo, unlocked=True, doors=1))
        ts += 60
        driving = self.check(ts, frame(battery + 11, odo + 1, unlocked=True))
        self.assertTrue(driving["moving"])
        self.assertGreater(len(self.checked), 40)


if __name__ == "__main__":
    unittest.main()
