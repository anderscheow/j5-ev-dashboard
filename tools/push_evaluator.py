"""Pure mirror of Voltmoss's Dart alert evaluator for dashboard push events.

Inputs are the alert-relevant fields of /api/summary. Keep the conditions in
step with lib/core/utils/alert_evaluator.dart and its Swift/Kotlin mirrors.
"""

from datetime import date, timedelta


DEFAULT_THRESHOLDS = {"charge_complete": 80, "soc_below": 20}
DEFAULT_SERVICE_KM = 10000
DEFAULT_SERVICE_MONTHS = 12

# charge_started waits this long into a charge, by the car's own flag, before it speaks: the car's
# first time-to-done is taken before the charger has ramped up (see server._charge_elapsed_min).
CHARGE_SETTLE_MIN = 5
# ...and stays quiet about a charge older than this: a cold start or an upgrade mid-charge is not
# the moment a charge started.
CHARGE_RECENT_MIN = 60


def _threshold(rules, kind):
    entry = rules.get(kind) or {}
    return entry.get("t", DEFAULT_THRESHOLDS.get(kind, 0))


def _on(rules, kind):
    return (rules.get(kind) or {}).get("on") is True


def _service_due(config, odometer, today):
    last = config.get("last_odo")
    if last is None:
        return False
    # Dart's summary mapper reads a missing odometer as 0; mirror it, so only the date can fire.
    odometer = odometer or 0
    interval = config.get("interval_km")
    remaining = last + (DEFAULT_SERVICE_KM if interval is None else interval) - odometer
    if remaining < 1000:
        return True
    last_date = config.get("last_date")
    if last_date is None:
        return False
    started = date.fromisoformat(last_date)
    months = config.get("interval_months")
    month_index = started.year * 12 + started.month - 1 + (
        DEFAULT_SERVICE_MONTHS if months is None else int(months))
    # Dart DateTime(y, m + months, day) rolls a day on a short month forward.
    try:
        due = date(month_index // 12, month_index % 12 + 1, 1) + timedelta(days=started.day - 1)
    except (OverflowError, ValueError):
        return False
    return (due - today).days < 30


def _tyre_problem(snapshot):
    status = str(snapshot.get("tyre_status") or "").lower()
    if status and status != "normal":
        return True
    if snapshot.get("tyre_indirect"):
        return False
    unit = snapshot.get("tyre_unit")
    factor = {"bar": 14.5038, "kpa": 0.145038}.get(unit, 1)
    for wheel in snapshot.get("tpms") or ():
        if not wheel.get("valid") or wheel.get("psi") is None:
            continue
        psi = wheel["psi"] * factor
        if psi < 28 or psi > 40:
            return True
    return False


def _volt12_low(snapshot):
    status = snapshot.get("volt12_status")
    if status is not None:
        return str(status).lower() in ("low", "critical")
    voltage = snapshot.get("volt12") or 0
    return 0 < voltage < 11.9


# What panel_open found open, as one number -- the event's value, which the push relay renders as
# "Still open: 2 doors, sunroof". Mirrors OpenPanels in Voltmoss's lib/core/utils/alert_evaluator.dart:
# the low four bits are the car's own door bits as reported (counted, never named -- which corner each
# is was tested on an Omoda E5 only), then the tailgate, the sunroof and a window.
PANEL_DOORS, PANEL_TAILGATE, PANEL_SUNROOF, PANEL_WINDOW = 15, 16, 32, 64


def _open_panels(snapshot):
    # Null is not "closed": only a field that actually says true counts.
    doors = snapshot.get("doors")
    return ((doors & PANEL_DOORS if isinstance(doors, int) else 0) |
            (PANEL_TAILGATE if snapshot.get("trunk_open") is True else 0) |
            (PANEL_SUNROOF if snapshot.get("sunroof_open") is True else 0) |
            (PANEL_WINDOW if snapshot.get("windows_open") is True else 0))


def evaluate(rules, memory, snapshot, service, today=None):
    """Return (events, next memory); events are {kind, value} dictionaries."""
    today = today or date.today()
    events = []
    fired = set()
    previous_fired = set(memory.get("fired") or ())
    charging = snapshot["charging"]
    battery = snapshot["battery"]

    def level(kind, active, value=None, raise_event=True):
        if active:
            fired.add(kind)
            if raise_event and _on(rules, kind) and kind not in previous_fired:
                events.append({"kind": kind, "value": value})

    # Once per charge, quoting the car's time-to-done once it can be trusted. A server or frame
    # without the car's flag has no elapsed time: announce the transition itself, with no estimate.
    elapsed = snapshot.get("charge_elapsed_min")
    remaining = snapshot.get("charge_remaining_min")
    level("charge_started", charging and (elapsed is None or elapsed >= CHARGE_SETTLE_MIN),
          remaining if elapsed is not None and remaining and remaining > 0 else None,
          raise_event=(memory.get("charging") is False if elapsed is None
                       else elapsed <= CHARGE_RECENT_MIN))

    ended = memory.get("charging") is True and not charging
    target = _threshold(rules, "charge_complete")
    at_target = battery >= target
    previous_soc = memory.get("soc")
    crossed = previous_soc is not None and previous_soc < target
    level("charge_complete", at_target and (charging or ended), battery,
          raise_event=crossed or ended)
    if ended and not at_target and _on(rules, "charge_stopped"):
        events.append({"kind": "charge_stopped", "value": battery})

    level("soc_below", not charging and battery < _threshold(rules, "soc_below"), battery)
    parked = not snapshot.get("moving", False)
    level("left_unlocked", parked and snapshot.get("unlocked") is True)
    panels = _open_panels(snapshot)
    level("panel_open", parked and panels != 0, panels)
    level("tyre_pressure", _tyre_problem(snapshot))
    # A low status can arrive without a usable reading (0 V is the decoder's "absent"). The
    # event still goes out, with no number, and the relay picks the copy that quotes none.
    volts = snapshot.get("volt12")
    level("volt12_low", _volt12_low(snapshot),
          volts if type(volts) in (int, float) and 0 < volts <= 20 else None)
    level("service_due", _service_due(service, snapshot.get("odometer"), today))
    # The relay's service copy has no numeric placeholder; Dart carries kmRemaining.
    return events, {"v": 2, "charging": charging, "soc": battery,
                    "fired": sorted(fired)}
