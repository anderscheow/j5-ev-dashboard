"""Offline relay contract tests; no Firebase account or credentials required."""

import json
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .relay import (ApiError, InvalidActivityToken, InvalidInstallation,
                    MAX_INSTALLATIONS_PER_FID, MAX_PER_HOUR, Relay, RelayStore)
from .sender import AlertCopy, FirebaseSender
from .server import handler_for


# The relay's snapshot of the app's alert copy (sync_copy.py).
COPY = Path(__file__).resolve().parent / "copy"
FID = "fid0123456789ABCDEFGHIJKLMNOP"


class FakeSender:
    def __init__(self):
        self.sent = []
        self.error = None

    def send(self, row):
        if self.error:
            raise self.error
        self.sent.append(row)

    def send_activity(self, row, token):
        if self.error:
            raise self.error
        self.sent.append({**row, "token": token})


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "relay.db")
        self.time = [1_780_000_000]
        self.sender = FakeSender()
        self.relay = Relay(RelayStore(self.path), self.sender, now=lambda: self.time[0])
        self.installation = self.relay.create({"fid": FID, "platform": "ios", "locale": "en"})
        self.id = self.installation["installation_id"]
        self.secret = self.installation["sender_secret"]

    def event(self, name="event_123456789012345", kind="soc_below", value=19):
        return {"event_id": name, "kind": kind, "value": value, "occurred_at": self.time[0]}

    def test_create_is_private_and_secret_is_scoped(self):
        self.assertEqual(len(self.secret), 64)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        with self.relay.store.connect() as conn:
            row = conn.execute("SELECT * FROM installations WHERE id=?", (self.id,)).fetchone()
        self.assertEqual(row["fid"], FID)
        self.assertNotEqual(row["secret_hash"], self.secret)
        self.assertNotIn(self.secret, Path(self.path).read_bytes().decode("latin1"))
        with self.assertRaises(ApiError) as result:
            self.relay.enqueue(self.id, "0" * 64, self.event())
        self.assertEqual(result.exception.status, 401)

    def test_deduplicates_and_retries_without_losing_event(self):
        first = self.relay.enqueue(self.id, self.secret, self.event())
        self.assertEqual(first, {"accepted": True, "duplicate": False})
        self.assertTrue(self.relay.enqueue(self.id, self.secret, self.event())["duplicate"])
        self.sender.error = RuntimeError("transient")
        self.assertEqual(self.relay.drain_due(), 0)
        self.time[0] += 61
        self.sender.error = None
        self.assertEqual(self.relay.drain_due(), 1)
        self.assertEqual(len(self.sender.sent), 1)
        self.assertEqual(self.sender.sent[0]["kind"], "soc_below")

    def test_invalid_fid_retires_installation(self):
        self.relay.enqueue(self.id, self.secret, self.event())
        self.sender.error = InvalidInstallation()
        self.relay.drain_due()
        self.assertEqual(self.relay.metrics()["installations"], 0)

    def test_stale_invalid_values_and_rate_limit(self):
        for kind, value in (("soc_below", 101), ("volt12_low", 21),
                            ("left_unlocked", 5), ("unknown", None)):
            with self.assertRaises(ApiError):
                self.relay.enqueue(self.id, self.secret, self.event(kind=kind, value=value))
        stale = self.event()
        stale["occurred_at"] -= 901
        with self.assertRaises(ApiError) as result:
            self.relay.enqueue(self.id, self.secret, stale)
        self.assertEqual(result.exception.status, 410)
        for i in range(MAX_PER_HOUR):
            self.relay.enqueue(self.id, self.secret,
                               self.event(name=f"event_{i:020d}"))
        with self.assertRaises(ApiError) as result:
            self.relay.enqueue(self.id, self.secret, self.event(name="event_over_limit_12345"))
        self.assertEqual(result.exception.status, 429)

    def test_revoke_removes_queued_events(self):
        self.relay.enqueue(self.id, self.secret, self.event())
        self.relay.delete(self.id, self.secret)
        self.assertEqual(self.relay.delete(self.id, self.secret), {"ok": True})
        self.assertEqual(self.relay.drain_due(), 0)
        with self.assertRaises(ApiError):
            self.relay.enqueue(self.id, self.secret, self.event())
        with self.assertRaises(ApiError) as result:
            self.relay.delete(self.id, "0" * 64)
        self.assertEqual(result.exception.status, 401)

    def test_installation_count_is_bounded_per_fid(self):
        for _ in range(MAX_INSTALLATIONS_PER_FID - 1):
            self.relay.create({"fid": FID, "platform": "ios", "locale": "en"})
        with self.assertRaises(ApiError) as result:
            self.relay.create({"fid": FID, "platform": "ios", "locale": "en"})
        self.assertEqual(result.exception.status, 429)

    def test_update_cannot_bypass_fid_limit(self):
        other = self.relay.create({"fid": "other0123456789ABCDEFGHIJKLMNOP", "platform": "ios", "locale": "en"})
        for _ in range(MAX_INSTALLATIONS_PER_FID - 1):
            self.relay.create({"fid": FID, "platform": "ios", "locale": "en"})
        with self.assertRaises(ApiError) as result:
            self.relay.update(other["installation_id"], other["sender_secret"],
                              {"fid": FID, "locale": "en"})
        self.assertEqual(result.exception.status, 429)

    def test_global_creation_rate_and_capacity_are_durable(self):
        with patch("push_relay.relay.MAX_CREATIONS_PER_HOUR", 2):
            self.relay.create({"fid": "other0123456789ABCDEFGHIJKLMNOP", "platform": "ios", "locale": "en"})
            another = Relay(RelayStore(self.path), FakeSender(), now=lambda: self.time[0])
            with self.assertRaises(ApiError) as result:
                another.create({"fid": "third0123456789ABCDEFGHIJKLMNOP", "platform": "ios", "locale": "en"})
            self.assertEqual(result.exception.status, 429)
        with patch("push_relay.relay.MAX_ACTIVE_INSTALLATIONS", 2):
            with self.assertRaises(ApiError) as result:
                self.relay.create({"fid": "fourth0123456789ABCDEFGHIJKLMNOP", "platform": "ios", "locale": "en"})
            self.assertEqual(result.exception.status, 429)

    def test_copy_renders_every_kind(self):
        copy = AlertCopy(COPY)
        for locale in ("en", "ms", "id", "zh"):
            for kind in ("charge_started", "charge_complete", "charge_stopped",
                         "soc_below", "left_unlocked", "panel_open",
                         "tyre_pressure", "volt12_low", "service_due"):
                title, body = copy.render(locale, kind, 11.85)
                self.assertTrue(title and body)
                self.assertNotIn("{", body)
        self.assertIn("11.8", copy.render("en", "volt12_low", 11.85)[1])

    def test_panel_open_carries_what_is_open(self):
        for name, value in (("event_panels_mask_00001", 1 | 32), ("event_panels_none_00002", None)):
            self.relay.enqueue(self.id, self.secret,
                               self.event(name=name, kind="panel_open", value=value))
        for value in (0, 128, 2.5, -1):
            with self.assertRaises(ApiError) as result:
                self.relay.enqueue(self.id, self.secret,
                                   self.event(name="event_panels_bad_00003",
                                              kind="panel_open", value=value))
            self.assertEqual(result.exception.status, 400)

    def test_panel_open_names_what_is_open(self):
        # The same words the app's lib/ui/screens/settings/alert_copy.dart renders
        # (its test/widgets/alert_copy_test.dart). The mask arrives as the REAL the
        # queue stored.
        copy = AlertCopy(COPY)
        self.assertEqual(copy.render("en", "panel_open", 33.0),
                         ("Car left open", "Still open: 1 door, sunroof."))
        self.assertEqual(copy.render("en", "panel_open", float(2 | 8 | 16 | 64))[1],
                         "Still open: 2 doors, tailgate, a window.")
        self.assertEqual(copy.render("zh", "panel_open", float(1 | 4 | 32))[1],
                         "仍开着：2 扇车门、天窗。")
        # A dashboard older than the mask: a sentence, and no "or" in it.
        body = copy.render("en", "panel_open", None)[1]
        self.assertEqual(body, "Check the doors, windows, sunroof and tailgate.")
        for locale in ("en", "ms", "id", "zh"):
            for mask in (None, 1, 15, 127):
                title, body = copy.render(locale, "panel_open", mask)
                self.assertTrue(title and body)
                self.assertNotIn("{", body)

    def test_charge_started_carries_the_finish_on_the_phones_clock(self):
        for name, value in (("event_started_finish_001", 16 * 60 + 45),
                            ("event_started_midnight_02", 0),
                            ("event_started_no_eta_0003", None)):
            self.relay.enqueue(self.id, self.secret,
                               self.event(name=name, kind="charge_started", value=value))
        for value in (1440, -1, 12.5):
            with self.assertRaises(ApiError) as result:
                self.relay.enqueue(self.id, self.secret,
                                   self.event(name="event_started_bad_000004",
                                              kind="charge_started", value=value))
            self.assertEqual(result.exception.status, 400)
        # The same words the app's lib/ui/screens/settings/alert_copy.dart renders. The
        # minutes arrive as the REAL the queue stored.
        copy = AlertCopy(COPY)
        self.assertEqual(copy.render("en", "charge_started", 1005.0),
                         ("Charging started", "The car expects to finish around 16:45."))
        self.assertEqual(copy.render("en", "charge_started", 5.0)[1],
                         "The car expects to finish around 00:05.")
        for locale in ("en", "ms", "id", "zh"):
            bundle = json.loads((COPY / f"{locale}.json")
                                .read_text(encoding="utf-8"))["alerts"]
            title, body = copy.render(locale, "charge_started", None)
            self.assertEqual((title, body), (bundle["notifyChargeStarted"],
                                             bundle["notifyChargeStartedNoEtaBody"]))
            self.assertNotIn("{", copy.render(locale, "charge_started", 1005.0)[1])

    def test_low_12v_without_a_reading_uses_generic_copy(self):
        for name, value in (("event_no_reading_000001", None), ("event_zero_volts_000002", 0)):
            self.relay.enqueue(self.id, self.secret,
                               self.event(name=name, kind="volt12_low", value=value))
        with self.relay.store.connect() as conn:
            values = [row[0] for row in conn.execute("SELECT value FROM events")]
        self.assertEqual(values, [None, None])
        copy = AlertCopy(COPY)
        for locale in ("en", "ms", "id", "zh"):
            bundle = json.loads((COPY / f"{locale}.json")
                                .read_text(encoding="utf-8"))["alerts"]
            title, body = copy.render(locale, "volt12_low", None)
            self.assertEqual(title, bundle["notifyVolt12Low"])
            self.assertEqual(body, bundle["notifyVolt12LowNoReadingBody"])
            self.assertNotIn("{", body)


class ActivityTests(unittest.TestCase):
    """The charging Live Activity: tokens from the phone, states from the dashboard."""

    START, UPDATE = "a" * 64, "b" * 64

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.time = [1_780_000_000]
        self.sender = FakeSender()
        self.relay = Relay(RelayStore(str(Path(self.temp.name) / "relay.db")), self.sender,
                           now=lambda: self.time[0])
        created = self.relay.create({"fid": FID, "platform": "ios", "locale": "ms"})
        self.id, self.secret = created["installation_id"], created["sender_secret"]

    def content(self, phase="charging", soc=40, **changes):
        return {"phase": phase, "soc": soc, "updated_at": self.time[0], "target_pct": 80,
                "rate_kw": 6.1, "mode": "ac", "finish_at": None, "estimating": True,
                **changes}

    def post(self, event, n=1, **extra):
        return self.relay.enqueue_activity(self.id, self.secret, {
            "event_id": f"activity_{n:016d}", "event": event, "content": self.content(),
            "occurred_at": self.time[0], **extra})

    def tokens(self, **body):
        return self.relay.set_activity_tokens(self.id, self.secret, body)

    def test_a_phone_without_the_token_it_needs_is_skipped_not_retried(self):
        self.post("start")
        self.assertEqual(self.relay.drain_activities(), 0)
        self.assertEqual(self.relay.metrics()["activities"], {"skipped": 1})
        self.tokens(update=self.UPDATE)
        self.post("start", n=2)  # still no push-to-start token: an update token cannot start
        self.relay.drain_activities()
        self.assertEqual(self.sender.sent, [])

    def test_start_uses_push_to_start_and_retires_the_old_update_token(self):
        self.tokens(push_to_start=self.START, update=self.UPDATE)
        self.time[0] += 10
        self.post("start", alert_minute=None)
        self.assertEqual(self.relay.drain_activities(), 1)
        sent = self.sender.sent.pop()
        self.assertEqual((sent["event"], sent["token"], sent["locale"]),
                         ("start", self.START, "ms"))
        # The update token addressed the last activity; the new one reports its own.
        self.post("update", n=2)
        self.relay.drain_activities()
        self.assertEqual(self.sender.sent, [])
        self.tokens(update="c" * 64)
        self.post("update", n=3)
        self.relay.drain_activities()
        self.assertEqual(self.sender.sent.pop()["token"], "c" * 64)

    def test_only_the_newest_state_is_sent_and_an_unsent_start_stays_a_start(self):
        self.tokens(push_to_start=self.START, update=self.UPDATE)
        self.post("start", alert_minute=600)
        self.relay.enqueue_activity(self.id, self.secret, {
            "event_id": "activity_9999999999999999", "event": "update",
            "content": self.content(soc=41, estimating=False, finish_at=self.time[0] + 600),
            "occurred_at": self.time[0]})
        self.assertEqual(self.relay.drain_activities(), 1)
        sent = self.sender.sent.pop()
        self.assertEqual((sent["event"], sent["alert_minute"]), ("start", 600))
        self.assertEqual(json.loads(sent["content"])["soc"], 41)

    def test_a_refused_token_is_forgotten_and_alerts_are_untouched(self):
        self.tokens(update=self.UPDATE)
        self.sender.error = InvalidActivityToken()
        self.post("update")
        self.relay.drain_activities()
        self.sender.error = None
        self.post("update", n=2)
        self.relay.drain_activities()
        self.assertEqual(self.sender.sent, [])
        # The installation itself survives: its alerts still go out.
        self.relay.enqueue(self.id, self.secret, {"event_id": "event_123456789012345",
                                                  "kind": "left_unlocked", "value": None,
                                                  "occurred_at": self.time[0]})
        self.assertEqual(self.relay.drain_due(), 1)

    def test_a_failed_send_retries_then_gives_up(self):
        self.tokens(update=self.UPDATE)
        self.sender.error = OSError()
        self.post("update")
        for _ in range(6):
            self.relay.drain_activities()
            self.time[0] += 200
        self.assertEqual(self.relay.metrics()["activities"], {"expired": 1})

    def test_validation(self):
        with self.assertRaises(ApiError) as e:
            self.tokens(push_to_start="not hex")
        self.assertEqual(e.exception.status, 400)
        with self.assertRaises(ApiError) as e:
            self.relay.set_activity_tokens(self.id, "0" * 64, {"update": self.UPDATE})
        self.assertEqual(e.exception.status, 401)
        self.tokens(update=None)  # null forgets
        bad = [{"soc": 101}, {"phase": "warp"}, {"mode": "plug"}, {"rate_kw": float("nan")},
               {"finish_at": self.time[0] - 1}, {"estimating": 1}, {"extra": 1}]
        for change in bad:
            with self.subTest(change=change), self.assertRaises(ApiError) as e:
                self.relay.enqueue_activity(self.id, self.secret, {
                    "event_id": "activity_1234567890123456", "event": "update",
                    "content": self.content(**change), "occurred_at": self.time[0]})
            self.assertEqual(e.exception.status, 400)
        for extra in ({"alert_minute": 1440}, {"vehicle_label": "Tiggo 7"}):
            with self.subTest(extra=extra), self.assertRaises(ApiError) as e:
                self.post("start", **extra)
            self.assertEqual(e.exception.status, 400)
        with self.assertRaises(ApiError) as e:
            self.relay.enqueue_activity(self.id, self.secret, {
                "event_id": "activity_1234567890123456", "event": "update",
                "content": self.content(), "occurred_at": self.time[0] - 3600})
        self.assertEqual(e.exception.status, 410)

    def test_revoking_drops_the_waiting_state(self):
        self.tokens(update=self.UPDATE)
        self.post("update")
        self.relay.delete(self.id, self.secret)
        self.assertEqual(self.relay.drain_activities(), 0)


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        relay = Relay(RelayStore(str(Path(self.temp.name) / "relay.db")), FakeSender())
        def verify(token):
            if token == "valid":
                return {"sub": "voltmoss-ios"}
            if token == "other-app":
                return {"sub": "some-other-firebase-app"}
            raise ValueError()

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                          handler_for(relay, verify, {"voltmoss-ios"}))
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def request(self, method, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        request = Request(f"http://127.0.0.1:{self.httpd.server_port}{path}",
                          data=data, method=method,
                          headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urlopen(request) as response:
                return response.status, json.load(response)
        except HTTPError as e:
            with e:
                return e.code, json.load(e)

    def test_registration_needs_app_check_and_event_needs_secret(self):
        body = {"fid": FID, "platform": "android", "locale": "ms"}
        self.assertEqual(self.request("POST", "/v1/installations", body)[0], 401)
        self.assertEqual(self.request("POST", "/v1/installations", body,
                                      {"X-Firebase-AppCheck": "other-app"})[0], 401)
        status, binding = self.request("POST", "/v1/installations", body,
                                       {"X-Firebase-AppCheck": "valid"})
        self.assertEqual(status, 201)
        installation_id = binding["installation_id"]
        event = {"event_id": "event_123456789012345", "kind": "left_unlocked",
                 "value": None, "occurred_at": int(time.time())}
        path = f"/v1/installations/{installation_id}/events"
        self.assertEqual(self.request("POST", path, event)[0], 401)
        self.assertEqual(self.request("POST", path, event,
                                      {"Authorization": "Bearer " + binding["sender_secret"]})[0], 202)
        self.assertEqual(self.request("DELETE", f"/v1/installations/{installation_id}",
                                      headers={"Authorization": "Bearer " + binding["sender_secret"]})[0], 401)

    def test_activity_routes_take_the_installation_secret(self):
        status, binding = self.request("POST", "/v1/installations",
                                       {"fid": FID, "platform": "ios", "locale": "en"},
                                       {"X-Firebase-AppCheck": "valid"})
        base = f"/v1/installations/{binding['installation_id']}"
        auth = {"Authorization": "Bearer " + binding["sender_secret"]}
        tokens = {"push_to_start": "a" * 64}
        self.assertEqual(self.request("PUT", base + "/activity-tokens", tokens)[0], 401)
        self.assertEqual(self.request("PUT", base + "/activity-tokens", tokens, auth)[0], 200)
        now = int(time.time())
        activity = {"event_id": "activity_1234567890123456", "event": "start",
                    "content": {"phase": "charging", "soc": 30, "updated_at": now,
                                "target_pct": None, "rate_kw": None, "mode": None,
                                "finish_at": None, "estimating": False},
                    "occurred_at": now}
        self.assertEqual(self.request("POST", base + "/activity", activity)[0], 401)
        self.assertEqual(self.request("POST", base + "/activity", activity, auth)[0], 202)


class SenderTests(unittest.TestCase):
    def test_fcm_message_targets_only_fid_with_small_data(self):
        class RecordingMessaging:
            class UnregisteredError(Exception):
                pass

            def __init__(self):
                self.message = None

            def __getattr__(self, name):
                if name in ("Message", "Notification", "AndroidConfig", "AndroidNotification",
                            "APNSConfig", "APNSPayload", "Aps"):
                    return lambda **kwargs: {"type": name, **kwargs}
                raise AttributeError(name)

            def send(self, message, app):
                self.message = message

        sender = FirebaseSender.__new__(FirebaseSender)
        sender.copy = AlertCopy(COPY)
        sender.messaging = RecordingMessaging()
        sender.app = object()
        sender.send({
            "fid": FID, "locale": "en", "kind": "soc_below", "value": 19,
            "installation_id": "installation_1234567890", "event_id": "event_123456789012345",
            "occurred_at": int(time.time()), "expires_at": int(time.time()) + 900,
        })
        message = sender.messaging.message
        self.assertEqual(message["fid"], FID)
        self.assertEqual(set(message["data"]),
                         {"type", "installation_id", "event_id", "kind"})
        self.assertEqual(message["android"]["notification"]["channel_id"], "voltmoss_alerts")
        self.assertEqual(message["apns"]["headers"]["apns-push-type"], "alert")
        self.assertIn("19%", message["notification"]["body"])


class ActivitySenderTests(unittest.TestCase):
    class Recording:
        class UnregisteredError(Exception):
            pass

        def __init__(self):
            self.message = None

        def __getattr__(self, name):
            if name in ("Message", "APNSConfig", "APNSPayload", "Aps", "ApsAlert"):
                return lambda **kwargs: {"type": name, **kwargs}
            raise AttributeError(name)

        def send(self, message, app):
            self.message = message

    def sender(self):
        sender = FirebaseSender.__new__(FirebaseSender)
        sender.copy = AlertCopy(COPY)
        sender.messaging = self.Recording()
        sender.app = object()
        return sender

    def row(self, event, phase="charging", **changes):
        content = {"phase": phase, "soc": 64, "updated_at": 1000, "target_pct": 80,
                   "rate_kw": 6.1, "mode": "ac", "finish_at": 5000, "estimating": False}
        return {"fid": FID, "locale": "id", "event": event, "content": json.dumps(content),
                "alert_minute": None, **changes}

    def test_a_start_carries_the_charge_started_words_and_no_sound(self):
        sender = self.sender()
        sender.send_activity(self.row("start", alert_minute=16 * 60 + 44), "tok")
        apns = sender.messaging.message["apns"]
        self.assertEqual(apns["live_activity_token"], "tok")
        self.assertEqual(apns["headers"]["apns-priority"], "10")
        aps = apns["payload"]["aps"]
        self.assertIn("16:44", aps["alert"]["body"])
        self.assertNotIn("sound", aps)
        fields = aps["custom_data"]
        self.assertEqual(fields["event"], "start")
        self.assertEqual(fields["content-state"]["lang"], "id")

    def test_updates_are_quiet_and_a_complete_end_clears_after_half_an_hour(self):
        sender = self.sender()
        sender.send_activity(self.row("update"), "tok")
        apns = sender.messaging.message["apns"]
        self.assertEqual(apns["headers"]["apns-priority"], "5")
        self.assertIsNone(apns["payload"]["aps"]["alert"])
        with patch("time.time", return_value=2000):
            sender.send_activity(self.row("end", phase="complete"), "tok")
        fields = sender.messaging.message["apns"]["payload"]["aps"]["custom_data"]
        self.assertEqual(fields["dismissal-date"], 2000 + 30 * 60)
        sender.send_activity(self.row("end", phase="stopped"), "tok")
        fields = sender.messaging.message["apns"]["payload"]["aps"]["custom_data"]
        self.assertNotIn("dismissal-date", fields)

    def test_a_refused_activity_token_is_not_a_dead_installation(self):
        sender = self.sender()
        def refuse(message, app):
            raise sender.messaging.UnregisteredError()
        sender.messaging.send = refuse
        with self.assertRaises(InvalidActivityToken):
            sender.send_activity(self.row("update"), "tok")


class LiveActivityTests(unittest.TestCase):
    """The ActivityKit payloads the developer CLI (and later Phase 5) sends."""

    class Recording:
        """Stands in for firebase_admin.messaging: each constructor returns its kwargs."""

        def __getattr__(self, name):
            if name in ("Message", "APNSConfig", "APNSPayload", "Aps", "ApsAlert"):
                return lambda **kwargs: {"type": name, **kwargs}
            raise AttributeError(name)

    def test_state_is_the_shape_swift_decodes(self):
        from .live_activity import content_state
        state = content_state("charging", 64, updated_at=1000.4, target_pct=80, rate_kw=6.1,
                              mode="ac", finish_at=5380, lang="ms")
        # ChargeActivityContent.toJson's keys, plus the language.
        self.assertEqual(set(state), {"phase", "soc", "updated_at", "target_pct", "rate_kw",
                                      "mode", "finish_at", "estimating", "lang"})
        self.assertEqual((state["updated_at"], state["finish_at"]), (1000, 5380))
        for bad in (("warp", 50), ("charging", 101)):
            with self.assertRaises(ValueError):
                content_state(*bad, updated_at=0)

    def test_start_names_the_type_and_asks_for_an_update_token(self):
        from .live_activity import aps, content_state
        state = content_state("charging", 22, updated_at=100, estimating=True)
        start = aps("start", state, now=100, vehicle_label="Tiggo 7")
        self.assertEqual(start["attributes-type"], "ChargeActivityAttributes")
        self.assertEqual(start["attributes"], {"vehicleLabel": "Tiggo 7"})
        self.assertEqual(start["input-push-token"], 1)
        # Estimating: no finish, so stale half an hour after the reading.
        self.assertEqual(start["stale-date"], 100 + 30 * 60)

    def test_update_goes_stale_after_the_finish_and_end_carries_its_dismissal(self):
        from .live_activity import aps, content_state
        state = content_state("charging", 64, updated_at=100, finish_at=4480)
        update = aps("update", state, now=100)
        self.assertNotIn("attributes-type", update)
        self.assertEqual(update["stale-date"], 4480 + 15 * 60)
        done = content_state("complete", 100, updated_at=200)
        end = aps("end", done, now=200, dismissal_at=2000)
        self.assertEqual((end["event"], end["dismissal-date"]), ("end", 2000))
        self.assertNotIn("stale-date", end)

    def test_the_cli_builds_one_fcm_message_with_the_live_activity_token(self):
        from .live_activity_cli import _parser, build
        args = _parser().parse_args(
            ["update", "--fid", FID, "--token", "abcd", "--soc", "70", "--finish-in", "40"])
        msg = build(args, self.Recording(), now=1000)
        self.assertEqual(msg["fid"], FID)
        self.assertIsNone(msg["token"])
        apns = msg["apns"]
        self.assertEqual(apns["live_activity_token"], "abcd")
        self.assertEqual(apns["headers"], {"apns-priority": "10"})
        fields = apns["payload"]["aps"]["custom_data"]
        self.assertEqual(fields["content-state"]["soc"], 70)
        self.assertEqual(fields["content-state"]["finish_at"], 1000 + 40 * 60)
        # The alert key belongs to ApsAlert; custom data may not repeat it.
        self.assertNotIn("alert", fields)
        args = _parser().parse_args(["end", "--fcm-token", "fcm", "--token", "abcd"])
        end = build(args, self.Recording(), now=1000)
        state = end["apns"]["payload"]["aps"]["custom_data"]["content-state"]
        self.assertEqual((state["phase"], state["soc"], state["rate_kw"]), ("complete", 100, None))

    def test_a_start_always_carries_a_banner(self):
        from .live_activity import aps, content_state, message
        state = content_state("charging", 22, updated_at=100, estimating=True)
        with self.assertRaises(ValueError):
            message(self.Recording(), live_activity_token="t", fid=FID,
                    aps_fields=aps("start", state, now=100))
        from .live_activity_cli import _parser, build
        args = _parser().parse_args(["start", "--fid", FID, "--token", "t", "--estimating"])
        alert = build(args, self.Recording(), now=100)["apns"]["payload"]["aps"]["alert"]
        self.assertEqual((alert["title"], alert["body"]),
                         ("Charging started", "Your car is plugged in and charging."))
        args = _parser().parse_args(
            ["start", "--fid", FID, "--token", "t", "--finish-in", "60", "--lang", "zh"])
        alert = build(args, self.Recording(), now=100)["apns"]["payload"]["aps"]["alert"]
        self.assertEqual(alert["title"], "开始充电")
        self.assertRegex(alert["body"], r"\d\d:\d\d")

    def test_the_real_encoder_accepts_it(self):
        try:
            from firebase_admin import messaging
            from firebase_admin._messaging_encoder import MessageEncoder
        except ImportError:
            self.skipTest("firebase-admin not installed")
        from .live_activity_cli import _parser, build
        args = _parser().parse_args(
            ["start", "--fid", FID, "--token", "abcd", "--estimating", "--alert", "Charging", "Started"])
        encoded = MessageEncoder().default(build(args, messaging, now=1000))
        self.assertEqual(encoded["apns"]["live_activity_token"], "abcd")
        aps = encoded["apns"]["payload"]["aps"]
        self.assertEqual((aps["event"], aps["alert"]["title"]), ("start", "Charging"))


if __name__ == "__main__":
    unittest.main()
