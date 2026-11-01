"""Python mirror of Voltmoss's charging Live Activity planner, for remote updates.

lib/core/utils/charge_activity.dart decides when the app starts, updates and ends the activity
from its own 5 s poll; this makes the same decisions from each committed frame, so the activity
stays current while the app is closed. Keep the two in step: the content shape is
ChargeActivityContent.toJson, and the relay validates it key for key.
"""

import push_evaluator

FINISH_MOVE = 2 * 60    # a finish that moved less than this is the same finish
RATE_KW = 0.5           # a rate that changed less than this is the same rate on screen
REFRESH = 10 * 60       # the "Updated" stamp and the stale time are refreshed at least this often
# Remote only: at most one routine update every two minutes. A start or an end goes at once.
# The app polls every 5 s and needs no such floor; a push has a budget.
UPDATE_GAP = 2 * 60


def content(snapshot, ts, target_pct, phase="charging"):
    """The activity's content for one frame (chargeActivityContent).

    The finish is quoted only once the car's estimate has settled, as the charge_started alert
    waits for it, and it is the frame's time plus the car's minutes to go."""
    elapsed = snapshot.get("charge_elapsed_min")
    remaining = snapshot.get("charge_remaining_min")
    live = phase == "charging"
    settled = elapsed is not None and elapsed >= push_evaluator.CHARGE_SETTLE_MIN
    rate = snapshot.get("charge_rate_kw")
    if type(rate) not in (int, float) or not 0 <= rate <= 1000:
        rate = None
    return {
        "phase": phase,
        "soc": snapshot["battery"],
        "updated_at": int(ts),
        "target_pct": target_pct,
        "rate_kw": float(rate) if live and rate is not None else None,
        "mode": snapshot.get("charge_mode") if snapshot.get("charge_mode") in ("ac", "dc") else None,
        "finish_at": (int(ts) + int(remaining) * 60
                      if live and settled and remaining and remaining > 0 else None),
        "estimating": bool(live and elapsed is not None and not settled),
    }


def ended_phase(snapshot, target_pct):
    """complete when the car said so (state 2) or the battery reached the owner's target -- or
    100 % with none -- and stopped otherwise: the charge_complete / charge_stopped split."""
    if snapshot.get("charge_state") == 2 or snapshot["battery"] >= (target_pct or 100):
        return "complete"
    return "stopped"


def changed(shown, nxt):
    """Whether nxt differs from shown enough to redraw (chargeActivityChanged)."""
    for key in ("phase", "soc", "mode", "estimating", "target_pct"):
        if shown.get(key) != nxt.get(key):
            return True
    a, b = shown.get("finish_at"), nxt.get("finish_at")
    if (a is None) != (b is None):
        return True
    if a is not None and abs(b - a) >= FINISH_MOVE:
        return True
    if abs((shown.get("rate_kw") or 0) - (nxt.get("rate_kw") or 0)) >= RATE_KW:
        return True
    return nxt["updated_at"] - shown["updated_at"] >= REFRESH


def target_pct(rules):
    """The tick on the bar: the charge-target alert threshold while that rule is on."""
    if not (rules.get("charge_complete") or {}).get("on"):
        return None
    return int(round(push_evaluator._threshold(rules, "charge_complete")))


def plan(memory, snapshot, ts, target):
    """Return (step, content, next memory); step is start, update, end or None.

    memory is {"shown": the content last sent, "sent_at": when}, or {} before the first frame."""
    shown = memory.get("shown")
    charging = snapshot["charging"]
    if not shown or shown.get("phase") != "charging":
        if not charging:
            return None, None, memory
        nxt = content(snapshot, ts, target)
        return "start", nxt, {"shown": nxt, "sent_at": int(ts)}
    if not charging:
        nxt = content(snapshot, ts, target, phase=ended_phase(snapshot, target))
        return "end", nxt, {"shown": nxt, "sent_at": int(ts)}
    nxt = content(snapshot, ts, target)
    if not changed(shown, nxt) or ts - memory.get("sent_at", 0) < UPDATE_GAP:
        return None, None, memory
    return "update", nxt, {"shown": nxt, "sent_at": int(ts)}
