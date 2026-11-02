"""FCM presentation for the relay's fixed alert vocabulary.

Firebase Admin signatures follow:
https://firebase.google.com/docs/reference/admin/python/firebase_admin.messaging
Notification copy comes from copy/, a snapshot of the `alerts` block of the Voltmoss
app's translation bundles (the source); sync_copy.py keeps the two in step.
"""

import json
import math
import os
import time
from pathlib import Path

from .relay import InvalidInstallation, KINDS, LOCALES


COPY_KEYS = {
    "charge_started": "ChargeStarted",
    "charge_complete": "ChargeComplete",
    "charge_stopped": "ChargeStopped",
    "soc_below": "SocBelow",
    "left_unlocked": "LeftUnlocked",
    "panel_open": "PanelOpen",
    "tyre_pressure": "TyrePressure",
    "volt12_low": "Volt12Low",
    "service_due": "ServiceDue",
}


# The pieces of "Still open: 2 doors, sunroof." — `alerts.notifyPanel*`.
PANEL_KEYS = ("notifyPanelOpenItemsBody", "notifyPanelDoor", "notifyPanelDoors",
              "notifyPanelTailgate", "notifyPanelSunroof", "notifyPanelWindow",
              "notifyPanelSeparator")

# OpenPanels in the app's lib/core/utils/alert_evaluator.dart.
PANEL_TAILGATE, PANEL_SUNROOF, PANEL_WINDOW = 16, 32, 64


class AlertCopy:
    def __init__(self, translation_dir):
        self.bundles = {}
        for locale in LOCALES:
            with (Path(translation_dir) / f"{locale}.json").open(encoding="utf-8") as f:
                alerts = json.load(f)["alerts"]
            for suffix in COPY_KEYS.values():
                if not alerts.get("notify" + suffix) or not alerts.get("notify" + suffix + "Body"):
                    raise ValueError(f"missing alert copy for {locale}/{suffix}")
            if not alerts.get("notifyVolt12LowNoReadingBody"):
                raise ValueError(f"missing low-voltage fallback copy for {locale}")
            if not alerts.get("notifyChargeStartedNoEtaBody"):
                raise ValueError(f"missing charge-start fallback copy for {locale}")
            if not all(alerts.get(key) for key in PANEL_KEYS):
                raise ValueError(f"missing open-panel copy for {locale}")
            self.bundles[locale] = alerts
        if set(COPY_KEYS) != KINDS:
            raise ValueError("alert copy and wire kinds diverged")

    def render(self, locale, kind, value):
        alerts = self.bundles[locale]
        suffix = COPY_KEYS[kind]
        title = alerts["notify" + suffix]
        if kind == "volt12_low" and (value is None or value == 0):
            body = alerts["notifyVolt12LowNoReadingBody"]
        elif kind == "panel_open" and value:
            return title, self._open_panels(alerts, int(value))
        elif kind == "charge_started":
            if value is None:
                return title, alerts["notifyChargeStartedNoEtaBody"]
            # 24-hour HH:mm, as the app and both widget extensions print it.
            minutes = int(value)
            return title, alerts["notifyChargeStartedBody"].replace(
                "{time}", f"{minutes // 60:02d}:{minutes % 60:02d}")
        else:
            body = alerts["notify" + suffix + "Body"]
        if value is not None:
            # Dart's positive round() rounds halves up, unlike Python's ties-to-even.
            body = body.replace("{pct}", str(math.floor(value + 0.5)))
            body = body.replace("{volts}", f"{value:.1f}")
        if "{" in body or "}" in body:
            raise ValueError("unfilled alert copy")
        return title, body

    @staticmethod
    def _open_panels(alerts, mask):
        """What the mask reports open, as the app words it (alert_copy.dart)."""
        doors = bin(mask & 15).count("1")
        items = []
        if doors == 1:
            items.append(alerts["notifyPanelDoor"])
        elif doors > 1:
            items.append(alerts["notifyPanelDoors"].replace("{count}", str(doors)))
        for bit, key in ((PANEL_TAILGATE, "notifyPanelTailgate"),
                         (PANEL_SUNROOF, "notifyPanelSunroof"),
                         (PANEL_WINDOW, "notifyPanelWindow")):
            if mask & bit:
                items.append(alerts[key])
        return alerts["notifyPanelOpenItemsBody"].replace(
            "{items}", alerts["notifyPanelSeparator"].join(items))


class FirebaseSender:
    def __init__(self, translation_dir):
        import firebase_admin
        from firebase_admin import credentials, messaging

        self.copy = AlertCopy(translation_dir)
        self.messaging = messaging
        project = os.environ["GOOGLE_CLOUD_PROJECT"]
        raw = os.environ.get("VOLTMOSS_FIREBASE_SERVICE_ACCOUNT_JSON")
        if raw:
            # Railway runs outside Google Cloud. Its secret variable is parsed
            # in memory; the service-account key never enters the image/volume.
            certificate = json.loads(raw)
            if certificate.get("project_id") != project:
                raise ValueError("Firebase project mismatch")
            self.app = firebase_admin.initialize_app(
                credentials.Certificate(certificate), options={"projectId": project})
        else:
            # Workload identity / ADC on Google-hosted environments.
            self.app = firebase_admin.initialize_app(options={"projectId": project})

    def send(self, event):
        m = self.messaging
        title, body = self.copy.render(event["locale"], event["kind"], event["value"])
        lifetime = max(1, event["expires_at"] - int(time.time()))
        # OS-visible notification payload: a data-only message cannot be relied
        # on to wake a terminated iOS app and present an alert.
        message = m.Message(
            fid=event["fid"],
            notification=m.Notification(title=title, body=body),
            data={
                "type": "voltmoss_alert",
                "installation_id": event["installation_id"],
                "event_id": event["event_id"],
                "kind": event["kind"],
            },
            android=m.AndroidConfig(
                priority="high", ttl=lifetime,
                notification=m.AndroidNotification(
                    channel_id="voltmoss_alerts", tag=event["kind"],
                    default_sound=True,
                ),
            ),
            apns=m.APNSConfig(
                headers={
                    "apns-push-type": "alert",
                    "apns-priority": "10",
                    "apns-collapse-id": "voltmoss." + event["kind"],
                    "apns-expiration": str(event["expires_at"]),
                },
                payload=m.APNSPayload(aps=m.Aps(sound="default", thread_id=event["kind"])),
            ),
        )
        try:
            m.send(message, app=self.app)
        except m.UnregisteredError as e:
            raise InvalidInstallation() from e
