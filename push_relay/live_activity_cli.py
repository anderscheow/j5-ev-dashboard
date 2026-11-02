"""Send a charging Live Activity push to a simulator or phone -- a developer tool.

Start (iOS 17.2+, the push-to-start token), then update and end (the activity's
own update token). Settings -> Developer -> "Live Activity push tokens" in a
debug build shows both tokens and the FID, and copies these commands.

The FCM credential is the relay's, injected rather than saved:

    railway run --service push-relay -- \\
      python3 -m push_relay.live_activity_cli start --fid FID --token PUSH_TO_START

    ... update --fid FID --token UPDATE_TOKEN --soc 64 --finish-in 73
    ... end    --fid FID --token UPDATE_TOKEN --phase complete --soc 100

`--dry-run` prints the FCM message without sending. `--validate` asks FCM to
check it (and the credential) without delivering anything.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import live_activity


def _parser():
    p = argparse.ArgumentParser(prog="live_activity_cli", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("event", choices=live_activity.EVENTS)
    p.add_argument("--token", required=True,
                   help="push-to-start token for start, the activity's update token otherwise")
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--fid", help="the app's Firebase Installation ID")
    target.add_argument("--fcm-token", help="the app's FCM registration token")
    p.add_argument("--phase", choices=live_activity.PHASES,
                   help="default: charging, or complete for end")
    p.add_argument("--soc", type=int, default=None, help="battery percent (default 64; 100 on end)")
    p.add_argument("--finish-in", type=int, default=None,
                   help="minutes until the car expects to finish (omit while estimating)")
    p.add_argument("--estimating", action="store_true", help="the first minutes of a charge")
    p.add_argument("--target", type=int, default=80, help="charge-target tick, 0 for none")
    p.add_argument("--rate", type=float, default=6.1, help="kW")
    p.add_argument("--mode", default="ac", choices=("ac", "dc"))
    p.add_argument("--lang", default="en", choices=("en", "ms", "id", "zh"))
    p.add_argument("--ago", type=int, default=0, help="minutes since the car reported (stale tests)")
    p.add_argument("--dismiss-in", type=int, default=30, help="end: minutes the result stays up")
    p.add_argument("--vehicle", default=None, help="start: the attributes' vehicle label")
    p.add_argument("--alert", nargs=2, metavar=("TITLE", "BODY"),
                   help="light the screen with this banner; a start always carries one, "
                        "by default the charge_started notification's own words")
    p.add_argument("--priority", type=int, choices=(5, 10), default=10)
    out = p.add_mutually_exclusive_group()
    out.add_argument("--dry-run", action="store_true", help="print the message, send nothing")
    out.add_argument("--validate", action="store_true", help="FCM validates, delivers nothing")
    return p


def build(args, messaging, now=None):
    now = int(time.time() if now is None else now)
    phase = args.phase or ("complete" if args.event == "end" else "charging")
    soc = args.soc if args.soc is not None else (100 if phase == "complete" else 64)
    updated = now - args.ago * 60
    live = phase == "charging"
    state = live_activity.content_state(
        phase, soc, updated_at=updated,
        target_pct=args.target or None,
        rate_kw=args.rate if live else None,
        mode=args.mode,
        finish_at=updated + args.finish_in * 60 if live and args.finish_in else None,
        estimating=live and args.estimating,
        lang=args.lang,
    )
    fields = live_activity.aps(
        args.event, state, now=now, vehicle_label=args.vehicle,
        dismissal_at=now + args.dismiss_in * 60 if args.event == "end" else None)
    alert = args.alert
    if args.event == "start" and not alert:
        from .sender import AlertCopy
        alert = live_activity.start_alert(
            AlertCopy(Path(__file__).resolve().parent / "copy"), args.lang, state,
            local_minute_of_day=lambda ts: time.localtime(ts).tm_hour * 60 + time.localtime(ts).tm_min)
    return live_activity.message(
        messaging, live_activity_token=args.token, aps_fields=fields,
        fid=args.fid, token=args.fcm_token, alert=alert, priority=args.priority)


def main(argv=None):
    args = _parser().parse_args(argv)
    from firebase_admin import messaging
    msg = build(args, messaging)
    if args.dry_run:
        from firebase_admin._messaging_encoder import MessageEncoder
        print(json.dumps(MessageEncoder().default(msg), indent=2))
        return 0
    import firebase_admin
    from firebase_admin import credentials
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    raw = os.environ.get("VOLTMOSS_FIREBASE_SERVICE_ACCOUNT_JSON")
    if not project or not raw:
        print("GOOGLE_CLOUD_PROJECT and VOLTMOSS_FIREBASE_SERVICE_ACCOUNT_JSON are not set: "
              "run under `railway run --service push-relay`.", file=sys.stderr)
        return 2
    certificate = json.loads(raw)
    if certificate.get("project_id") != project:
        print("Firebase project mismatch", file=sys.stderr)
        return 2
    app = firebase_admin.initialize_app(credentials.Certificate(certificate),
                                        options={"projectId": project})
    message_id = messaging.send(msg, app=app, dry_run=args.validate)
    print(("validated " if args.validate else "sent ") + message_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
