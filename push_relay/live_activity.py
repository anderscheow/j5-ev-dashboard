"""ActivityKit pushes for the charging Live Activity, sent through FCM.

Today this is a developer tool: `live_activity_cli.py` starts, updates and
ends the Live Activity on a simulator or a phone, so it can be seen without a
real charge. Phase 5 of ai.local/plans/2026-10-28-charging-live-activity.md
puts the same payloads behind a relay endpoint the dashboard calls.

The content state is the shape every producer shares --
`ChargeActivityContent.toJson` in the app's lib/entities/charge_activity.dart, plus the
language -- and `ChargeActivityAttributes.ContentState` decodes it with the
same snake_case keys and epoch-second times. FCM's own reference for the
message: https://firebase.google.com/docs/cloud-messaging/customize-messages/live-activity
"""

# The Swift type ActivityKit matches a push-to-start against; renaming the
# struct breaks remote start without a compile error anywhere.
ATTRIBUTES_TYPE = "ChargeActivityAttributes"
PHASES = ("charging", "complete", "stopped")
EVENTS = ("start", "update", "end")


def content_state(phase, soc, *, updated_at, target_pct=None, rate_kw=None, mode=None,
                  finish_at=None, estimating=False, lang="en"):
    """The ContentState map. Times are whole epoch seconds."""
    if phase not in PHASES:
        raise ValueError(f"phase must be one of {PHASES}")
    if not 0 <= soc <= 100:
        raise ValueError("soc must be 0-100")
    return {
        "phase": phase,
        "soc": int(soc),
        "updated_at": int(updated_at),
        "target_pct": target_pct,
        "rate_kw": rate_kw,
        "mode": mode,
        "finish_at": None if finish_at is None else int(finish_at),
        "estimating": bool(estimating),
        "lang": lang,
    }


def stale_at(state):
    """When the activity should read as out of date -- `ChargeActivityContent.staleAt`:
    a quarter-hour past the finish, or half an hour after the last reading without one."""
    if state["finish_at"] is not None:
        return state["finish_at"] + 15 * 60
    return state["updated_at"] + 30 * 60


def aps(event, state, *, now, vehicle_label=None, dismissal_at=None):
    """The `aps` dictionary for one ActivityKit push, alert excluded (FCM takes the
    alert through `ApsAlert`, which owns the key).

    A start names the attributes type and asks iOS for the new activity's update
    token (`input-push-token`), which it hands the app in the background so later
    updates have something to address."""
    if event not in EVENTS:
        raise ValueError(f"event must be one of {EVENTS}")
    out = {
        "timestamp": int(now),
        "event": event,
        "content-state": state,
    }
    if event == "start":
        out["attributes-type"] = ATTRIBUTES_TYPE
        out["attributes"] = {"vehicleLabel": vehicle_label}
        out["input-push-token"] = 1
    if event == "end":
        if dismissal_at is not None:
            out["dismissal-date"] = int(dismissal_at)
    else:
        out["stale-date"] = stale_at(state)
    return out


def start_alert(copy, lang, state, *, local_minute_of_day):
    """The banner a push-to-start carries: the charge_started notification's own
    words, so the remote start and the alert say one thing. `local_minute_of_day`
    turns the finish into the phone's clock time, which only the caller knows."""
    finish = state["finish_at"]
    return copy.render(lang, "charge_started",
                       None if finish is None else local_minute_of_day(finish))


def message(messaging, *, live_activity_token, aps_fields, fid=None, token=None,
            alert=None, priority=10):
    """An FCM message carrying one ActivityKit push.

    FCM documents the app's registration `token` as the target beside the
    `live_activity_token`; the relay addresses phones by FID, so either is taken.
    Priority 10 is immediate and counts against iOS's update budget; 5 is for the
    routine percentage ticks."""
    if (fid is None) == (token is None):
        raise ValueError("exactly one of fid or token")
    # iOS drops a push-to-start that carries no alert: liveactivitiesd logs
    # "Received start without an alert configuration" and nothing appears.
    if aps_fields.get("event") == "start" and not alert:
        raise ValueError("a push-to-start needs an alert")
    title, body = alert if alert else (None, None)
    return messaging.Message(
        fid=fid,
        token=token,
        apns=messaging.APNSConfig(
            live_activity_token=live_activity_token,
            headers={"apns-priority": str(priority)},
            payload=messaging.APNSPayload(
                aps=messaging.Aps(
                    alert=messaging.ApsAlert(title=title, body=body) if alert else None,
                    custom_data=aps_fields,
                ),
            ),
        ),
    )
