#!/usr/bin/env python3
"""The price book: validation, dated periods, per-session overrides, and every number the
server derives from them. No car and no network: sessions come from synthetic frames at the
offsets server.decode() reads.

Run from the repo root:  python3 tools/pricing_selftest.py
"""

import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import closing
from http.server import ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(__file__))
import pricing
import server


def frame(battery, odometer, charge_mode=0):
    """A raw status blob at the offsets server.decode() reads."""
    b = bytearray(72)
    b[12:14] = (1260).to_bytes(2, "big")
    b[18:21] = odometer.to_bytes(3, "big")
    b[28] = battery
    b[44:52] = bytes([0xFF] * 8)
    b[56] = charge_mode
    return b.hex()


def session(start, kwh=20.0, soc1=80, kind="dc", end=None, ongoing=False):
    return {"start": start, "end": end or start + 1800, "kwh": kwh, "soc1": soc1,
            "type": kind, "ongoing": ongoing}


def flat(_soc):
    return 0.9


class ValidationTest(unittest.TestCase):
    def test_a_book_is_checked_whole(self):
        book = pricing.validate_book({
            "charge": [{"scope": "dc", "start": "2026-06-01", "price": 2600, "label": "ChargEV"},
                       {"start": "2026-01-01", "price": 2466}],
            "fuel": [{"start": "2026-08-01", "price": 15950, "label": "Pertamax"}],
            "fuel_kml": 13})
        self.assertEqual([p["scope"] for p in book["charge"]], ["all", "dc"])
        self.assertEqual(book["fuel"][0]["scope"], "all")
        self.assertEqual(book["fuel_kml"], 13.0)
        for bad in ({}, [], {"tariff": 1}, {"charge": {}},
                    {"charge": [{"scope": "ev", "start": "2026-01-01", "price": 1}]},
                    {"charge": [{"start": "2026-02-30", "price": 1}]},
                    {"charge": [{"start": "1 Feb", "price": 1}]},
                    {"charge": [{"start": "2026-01-01", "price": -1}]},
                    {"charge": [{"start": "2026-01-01", "price": True}]},
                    {"charge": [{"start": "2026-01-01", "price": 1, "cost": 1}]},
                    {"charge": [{"start": "2026-01-01", "price": 1},
                                {"scope": "all", "start": "2026-01-01", "price": 2}]},
                    {"fuel": [{"scope": "dc", "start": "2026-01-01", "price": 1}]},
                    {"fuel_kml": 0}):
            with self.assertRaises(ValueError, msg=bad):
                pricing.validate_book(bad)
        self.assertIsNone(pricing.validate_book({"fuel_kml": None})["fuel_kml"])

    def test_an_override_says_rate_total_or_free(self):
        self.assertEqual(pricing.validate_override({"mode": "total", "value": 120000,
                                                    "metered_kwh": 46.1, "note": "receipt"}),
                         {"mode": "total", "value": 120000.0, "metered_kwh": 46.1,
                          "note": "receipt"})
        self.assertIsNone(pricing.validate_override({"mode": "free"})["value"])
        for bad in (None, {"mode": "cheap"}, {"mode": "rate"}, {"mode": "free", "value": 5},
                    {"mode": "total", "value": 1, "metered_kwh": 0},
                    {"mode": "rate", "value": 1, "extra": 1}):
            with self.assertRaises(ValueError, msg=bad):
                pricing.validate_override(bad)

    def test_a_session_id_is_a_unix_second(self):
        self.assertEqual(pricing.valid_session_id("1780000000"), 1780000000)
        self.assertEqual(pricing.valid_session_id(1780000000), 1780000000)
        for bad in ("abc", "17", True, None, "1780000000.5"):
            with self.assertRaises(ValueError, msg=bad):
                pricing.valid_session_id(bad)


class RatesTest(unittest.TestCase):
    def book(self, charge=(), fuel=(), kml=None, overrides=()):
        return pricing.PriceBook(pricing.validate_book({"charge": list(charge), "fuel": list(fuel),
                                                        "fuel_kml": kml}),
                                 list(overrides), 2540, 16250, 12.0)

    def test_a_session_takes_its_own_type_then_all_then_the_tariff(self):
        book = self.book(charge=[{"start": "2026-01-01", "price": 2000},
                                 {"scope": "ac", "start": "2026-03-01", "price": 1500}])
        self.assertEqual(book.charge_rate("ac", "2026-03-01")[:2], (1500, "period"))
        self.assertEqual(book.charge_rate("ac", "2026-02-28")[:2], (2000, "period"))
        self.assertEqual(book.charge_rate("dc", "2026-05-01")[:2], (2000, "period"))
        self.assertEqual(book.charge_rate(None, "2026-05-01")[:2], (2000, "period"))
        self.assertEqual(book.charge_rate("dc", "2025-12-31")[:2], (2540, "default"))

    def test_fuel_holds_until_the_next_period(self):
        book = self.book(fuel=[{"start": "2026-06-10", "price": 16250},
                               {"start": "2026-08-01", "price": 15950}], kml=13)
        self.assertEqual(book.fuel_rate("2026-07-31"), (16250, "period"))
        self.assertEqual(book.fuel_rate("2026-08-01"), (15950, "period"))
        # the earliest price also covers the days before it, never creds.json's
        self.assertEqual(self.book(fuel=[{"start": "2026-06-10", "price": 9000}]).fuel_rate(
            "2026-01-01"), (9000, "period"))
        self.assertEqual(self.book().fuel_rate("2026-01-01"), (16250, "default"))
        self.assertEqual(book.kml, 13.0)
        self.assertEqual(self.book().kml, 12.0)

    def test_overrides_price_their_session_and_nothing_else(self):
        t = 1780000000
        book = self.book(charge=[{"start": "2020-01-01", "price": 2000}], overrides=[
            (t, {"mode": "rate", "value": 3000, "metered_kwh": None, "note": ""}),
            (t + 10000, {"mode": "total", "value": 50000, "metered_kwh": 25.0, "note": ""}),
            (t + 20000 - 300, {"mode": "free", "value": None, "metered_kwh": None, "note": ""})])
        sess = [session(t), session(t + 10000), session(t + 20000), session(t + 30000)]
        book.price_sessions(sess, flat)
        rate, total, free, period = sess
        self.assertEqual((rate["price"], rate["price_source"]), (3000, "override"))
        self.assertAlmostEqual(rate["cost"], 20 / 0.9 * 3000)
        self.assertEqual((total["billed"], total["cost"], total["price"]), (25.0, 50000, 2000))
        self.assertEqual((free["cost"], free["price"]), (0.0, 0.0))
        self.assertEqual((period["price_source"], period["period"]["start"]), ("period", "2020-01-01"))
        self.assertIsNone(period["override"])

    def test_an_override_follows_a_start_that_moved_and_is_orphaned_when_none_does(self):
        t = 1780000000
        keep = {"mode": "rate", "value": 1, "metered_kwh": None, "note": ""}
        book = self.book(overrides=[(t - 300, keep), (t + 60, dict(keep, value=2)),
                                    (t + 5000, dict(keep, value=3))])
        sess = [session(t)]
        book.price_sessions(sess, flat)
        self.assertEqual(sess[0]["price"], 2)                    # nearest the start wins
        self.assertEqual([sid for sid, _o in book.orphans(sess, t - 3600, t + 7200)],
                         [t - 300, t + 5000])
        self.assertEqual(book.orphans(sess, t + 4000, t + 7200)[0][0], t + 5000)

    def test_paid_average_needs_enough_charging_and_skips_old_and_ongoing(self):
        now = 1780000000
        book = self.book(charge=[{"start": "2020-01-01", "price": 1800}])
        sess = [session(now - 100 * 86400, kwh=40), session(now - 86400, kwh=15),
                session(now - 3600, kwh=30, ongoing=True)]
        book.price_sessions(sess, flat)
        self.assertIsNone(pricing.paid_average(sess, now))
        sess.append(session(now - 2 * 86400, kwh=10))
        book.price_sessions(sess, flat)
        paid = pricing.paid_average(sess, now)
        self.assertEqual((paid["sessions"], paid["days"]), (2, 90))
        self.assertAlmostEqual(paid["per_kwh"], 1800)
        self.assertAlmostEqual(paid["per_pack_kwh"], 1800 / 0.9)

    def test_petrol_km_are_priced_at_the_pump_price_of_their_day(self):
        t0 = int(time.mktime(time.strptime("2026-07-30 08:00", "%Y-%m-%d %H:%M")))
        points = [(t0, 1000), (t0 + 3600, 1100),                  # 100 km before the change
                  (t0 + 3 * 86400, 1100), (t0 + 3 * 86400 + 3600, 1150)]
        self.assertAlmostEqual(self.book().fuel_cost(points), 150 * 16250 / 12)
        book = self.book(fuel=[{"start": "2026-08-01", "price": 12000}])
        self.assertAlmostEqual(book.fuel_cost(points), 150 * 12000 / 12)   # earliest backfills
        book = self.book(fuel=[{"start": "2026-07-01", "price": 15000},
                               {"start": "2026-08-01", "price": 12000}])
        self.assertAlmostEqual(book.fuel_cost(points), 100 * 15000 / 12 + 50 * 12000 / 12)


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = os.path.join(self.temp.name, "prices.db")

    def test_reading_an_unpriced_server_creates_nothing(self):
        self.assertEqual(pricing.read_book(self.db)["charge"], [])
        self.assertEqual(pricing.read_overrides(self.db), [])
        self.assertFalse(os.path.exists(self.db))

    def test_a_part_left_out_of_a_write_is_left_alone(self):
        pricing.put_book(self.db, pricing.validate_book({
            "charge": [{"start": "2026-01-01", "price": 2466.5}], "fuel_kml": 14}))
        pricing.put_book(self.db, pricing.validate_book({"fuel": [{"start": "2026-08-01",
                                                                   "price": 15950}]}))
        book = pricing.read_book(self.db)
        self.assertEqual(book["charge"], [{"scope": "all", "start": "2026-01-01",
                                           "price": 2466.5, "label": ""}])
        self.assertEqual(book["fuel"], [{"start": "2026-08-01", "price": 15950, "label": ""}])
        self.assertEqual(book["fuel_kml"], 14.0)
        pricing.put_book(self.db, {"charge": [], "fuel_kml": None})
        self.assertEqual(pricing.read_book(self.db)["charge"], [])
        self.assertIsNone(pricing.read_book(self.db)["fuel_kml"])

    def test_a_session_never_carries_two_overrides(self):
        o = pricing.validate_override({"mode": "rate", "value": 1})
        pricing.put_overrides(self.db, [(1000, 900, 2000)], o)
        pricing.put_overrides(self.db, [(1100, 900, 2000)], dict(o, value=2.0))
        self.assertEqual([(sid, ov["value"]) for sid, ov in pricing.read_overrides(self.db)],
                         [(1100, 2)])
        self.assertEqual(pricing.delete_overrides(self.db, [(900, 2000)]), 1)
        self.assertEqual(pricing.read_overrides(self.db), [])


class SessionTypeTest(unittest.TestCase):
    def test_the_connector_byte_decides_then_the_power(self):
        data = [(100, "", {"charge_mode": 16}), (200, "", {"charge_mode": 16}),
                (300, "", {"charge_mode": 1}), (5000, "", {"charge_mode": 1})]
        sess = [{"start": 50, "end": 350, "avg": 3.0}, {"start": 4000, "end": 6000, "avg": 50.0},
                {"start": 9000, "end": 9900, "avg": 40.0}, {"start": 9900, "end": 9990, "avg": 6.6}]
        server.type_sessions(sess, data)
        self.assertEqual([s["type"] for s in sess], ["dc", "ac", "dc", "ac"])


class ServerTest(unittest.TestCase):
    """Sessions, summaries and routes over a synthetic telemetry database: a DC fast charge in
    the morning two days ago and an AC home charge that evening -- one day, so one month."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        data = self.temp.name
        db = os.path.join(data, "carlinko.db")
        for target, value in (("_DATA", data), ("DB", db),
                              ("PRICE_DB", os.path.join(data, "prices.db")),
                              ("TARIFF_IDR", 2540), ("PETROL_RP_L", 16250.0),
                              ("PETROL_KM_L", 12.0), ("CAP_KWH", 58.9),
                              ("_summary_cache", None)):
            p = patch.object(server, target, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(server, "_gated", lambda: False)
        p.start()
        self.addCleanup(p.stop)
        server._TELE.update(ts=None, n=0, data=[], memo={})
        self.addCleanup(server._TELE.update, ts=None, n=0, data=[], memo={})

        midnight = int(time.mktime(time.localtime(time.time() - 2 * 86400)[:3] + (0, 0, 0, 0, 0, -1)))
        rows, odo, soc = [], 1000, 50
        t = midnight + 8 * 3600
        for _ in range(12):                                   # drive 60 km
            odo += 5; soc -= 1; t += 300; rows.append((t, frame(soc, odo)))
        for _ in range(6):                                    # parked
            t += 300; rows.append((t, frame(soc, odo)))
        self.dc_start = t
        for _ in range(8):                                    # DC: +7 % per 5 min
            soc += 7; t += 300; rows.append((t, frame(soc, odo, 16)))
        for _ in range(8):                                    # unplugged, parked
            t += 300; rows.append((t, frame(soc, odo)))
        t = midnight + 18 * 3600
        for _ in range(12):                                   # drive 60 km that evening
            odo += 5; soc -= 1; t += 300; rows.append((t, frame(soc, odo)))
        for _ in range(6):
            t += 300; rows.append((t, frame(soc, odo)))
        self.ac_start = t
        for _ in range(15):                                   # AC: +1 % per 5 min
            soc += 1; t += 300; rows.append((t, frame(soc, odo, 1)))
        for _ in range(8):
            t += 300; rows.append((t, frame(soc, odo)))
        self.month = time.strftime("%Y-%m", time.localtime(self.dc_start))
        with closing(sqlite3.connect(db)) as conn:
            conn.execute("CREATE TABLE telemetry(ts INTEGER PRIMARY KEY,dt TEXT,battery INTEGER,"
                         "range_km INTEGER,odo_guess INTEGER,tyre_raw TEXT,online INTEGER,raw TEXT)")
            conn.executemany("INSERT INTO telemetry(ts,dt,online,raw) VALUES (?,?,1,?)",
                             [(ts, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)), raw)
                              for ts, raw in rows])
            conn.commit()

    def charges(self, month=None):
        listing = server.month_charges(month or self.month)
        return {s["type"]: s for s in listing["sessions"]}, listing

    def test_sessions_are_typed_and_priced_by_period(self):
        by_type, _ = self.charges()
        self.assertEqual(set(by_type), {"dc", "ac"})
        self.assertEqual(by_type["dc"]["price_source"], "default")
        server.pricing.put_book(server.PRICE_DB, pricing.validate_book({"charge": [
            {"scope": "dc", "start": "2020-01-01", "price": 3000, "label": "Fast"},
            {"scope": "ac", "start": "2020-01-01", "price": 1500, "label": "Home"}]}))
        by_type, listing = self.charges()
        self.assertEqual((by_type["dc"]["price"], by_type["dc"]["period"]["label"]), (3000, "Fast"))
        self.assertEqual(by_type["ac"]["price"], 1500)
        # kwh_billed is rounded to 0.1 kWh on the way out, the cost is not
        self.assertAlmostEqual(by_type["ac"]["cost"], by_type["ac"]["kwh_billed"] * 1500, delta=80)
        self.assertAlmostEqual(listing["totals"]["cost"],
                               sum(s["cost"] for s in listing["sessions"]), delta=1)
        days = server.month_history(self.month)["days"]
        calendar = [c for d in days for c in d["charges"]]
        self.assertEqual(sorted(c["id"] for c in calendar),
                         sorted(s["id"] for s in listing["sessions"]))
        self.assertEqual(sorted(c["cost"] for c in calendar),
                         sorted(s["cost"] for s in listing["sessions"]))

    def test_summary_reprices_lifetime_and_running_cost_from_what_was_paid(self):
        server.pricing.put_book(server.PRICE_DB, pricing.validate_book({
            "charge": [{"scope": "ac", "start": "2020-01-01", "price": 1000}],
            "fuel": [{"start": "2020-01-01", "price": 10000}], "fuel_kml": 10}))
        out = server.summary()
        by_type, listing = self.charges()
        self.assertEqual(out["lifetime"]["cost"], listing["totals"]["cost"])
        self.assertEqual(out["tariff"], 2540)                        # no DC period: creds
        self.assertEqual(out["fuel_price"], {"per_l": 10000, "kml": 10.0, "source": "period"})
        basis = out["insights"]["price_basis"]
        self.assertEqual((basis["basis"], basis["sessions"]), ("paid", 2))
        self.assertEqual(out["lifetime"]["petrol_cost"], round(out["lifetime"]["km"] * 10000 / 10))
        self.assertEqual(out["lifetime"]["saved"],
                         max(0, out["lifetime"]["petrol_cost"] - out["lifetime"]["cost"]))
        self.assertEqual([h["id"] for h in out["charging"]["history"]][::-1],
                         [s["id"] for s in listing["sessions"]])

    def test_an_unreadable_price_book_prices_from_creds(self):
        with open(server.PRICE_DB, "w") as f:
            f.write("not a database")
        self.assertEqual(server._book().charge_rate("dc", "2026-01-01")[:2], (2540, "default"))
        self.assertEqual(server.summary()["tariff"], 2540)

    def request(self, method, path, body=None):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            data = None if body is None else json.dumps(body).encode()
            req = urllib.request.Request("http://127.0.0.1:%d%s" % (httpd.server_port, path),
                                         data=data, method=method,
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return resp.status, json.loads(resp.read())
            except urllib.error.HTTPError as e:
                with e:
                    return e.code, json.loads(e.read())
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_routes_store_whole_bodies_and_find_their_session(self):
        status, book = self.request("PUT", "/api/prices", {"charge": [
            {"scope": "dc", "start": "2020-01-01", "price": 3000}]})
        self.assertEqual((status, book["charge"][0]["price"]), (200, 3000))
        self.assertEqual(book["defaults"]["tariff"], 2540)
        status, _ = self.request("PUT", "/api/prices", {"charge": [], "fuel_kml": -1})
        self.assertEqual(status, 400)
        self.assertEqual(self.request("GET", "/api/prices")[1]["charge"][0]["price"], 3000)

        by_type, _ = self.charges()
        dc = by_type["dc"]["id"]
        status, _ = self.request("PUT", "/api/charges/%d/price" % (dc + 120),
                                 {"mode": "total", "value": 99000, "metered_kwh": 33})
        self.assertEqual(status, 200)
        by_type, _ = self.charges()
        self.assertEqual((by_type["dc"]["cost"], by_type["dc"]["kwh_billed"]), (99000, 33.0))
        self.assertEqual(by_type["dc"]["price_source"], "override")
        self.assertEqual(self.request("PUT", "/api/charges/%d/price" % (dc - 86400),
                                      {"mode": "free"})[0], 404)
        self.assertEqual(self.request("PUT", "/api/charges/%d/price" % dc, {"mode": "x"})[0], 400)

        status, result = self.request("POST", "/api/charges/prices", {
            "ids": [s["id"] for s in by_type.values()], "price": {"mode": "free"}})
        self.assertEqual((status, result["sessions"]), (200, 2))
        self.assertEqual({s["cost"] for s in self.charges()[0].values()}, {0})
        self.assertEqual(self.request("DELETE", "/api/charges/%d/price" % dc)[0], 200)
        self.assertEqual(self.charges()[0]["dc"]["price_source"], "period")

        stray = dc + 5 * 86400                                  # an override no session claims
        pricing.put_overrides(server.PRICE_DB, [(stray, stray, stray)],
                              pricing.validate_override({"mode": "free"}))
        listing = server.month_charges(time.strftime("%Y-%m", time.localtime(stray)))
        self.assertIn(stray, [o["id"] for o in listing["orphans"]])
        self.assertEqual(self.request("DELETE", "/api/charges/%d/price" % stray)[0], 200)
        self.assertEqual(self.request("DELETE", "/api/charges/%d/price" % stray)[0], 404)

    def test_the_capability_is_advertised_only_where_the_routes_answer(self):
        self.assertIn("price_book", server.status_capabilities())
        with patch.object(server, "DEMO", True):
            self.assertNotIn("price_book", server.status_capabilities())
            self.assertEqual(self.request("GET", "/api/prices")[0], 404)


if __name__ == "__main__":
    unittest.main()
