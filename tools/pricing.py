"""The owner's price book: what charging and fuel cost, and from when.

One tariff and one petrol price in creds.json used to price every session the
server had ever logged, so a price rise rewrote last year's bills, and a pump
price that moves weekly could only ever be right for one week. Here a price
takes effect on a date and holds until the next one, and a session can be
pinned to what its receipt actually said.

Charge sessions are never stored -- server.py re-derives them from telemetry
on every request -- so an override is keyed by the start time the server
reported for its session and matched back by overlap, not equality. A later
change to the session detector that moves a start by a frame still finds its
override; one that no longer lands on any session is reported as an orphan
rather than quietly pricing nothing.

One SQLite file beside the telemetry database (`prices.db`), like the
maintenance log, so the logger's writes never wait on an edit. Every write is
validated whole before anything is stored.
"""

import math
import os
import re
import sqlite3
import time
from bisect import bisect_left, bisect_right
from contextlib import closing


KINDS = ("charge", "fuel")
CHARGE_SCOPES = ("all", "ac", "dc")
MODES = ("rate", "total", "free")
BOOK_MAX = 64 * 1024            # the JSON body of a price book
OVERRIDE_MAX = 4 * 1024         # the JSON body of one override
BULK_MAX = 500                  # sessions one bulk edit may name
PERIODS_MAX = 500               # periods per kind
PRICE_MAX = 1e7                 # per kWh or per litre: IDR pump prices with room to spare
AMOUNT_MAX = 1e9                # one receipt's total
MATCH_LEAD = 600                # an override this far before a session's start still belongs to it
PAID_DAYS = 90                  # the window "price actually paid" averages over
PAID_MIN_KWH = 20.0             # less than this into the pack is too little to call an average

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ID = re.compile(r"^\d{9,10}$")


class UnknownSession(LookupError):
    """A write named a session ID no logged charge session covers."""


def _connect(db):
    conn = sqlite3.connect(db, timeout=5)
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS price_periods (
            kind TEXT NOT NULL, scope TEXT NOT NULL, start TEXT NOT NULL,
            price REAL NOT NULL, label TEXT NOT NULL,
            PRIMARY KEY (kind, scope, start))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS session_prices (
            start INTEGER PRIMARY KEY, mode TEXT NOT NULL, value REAL,
            metered_kwh REAL, note TEXT NOT NULL, updated INTEGER NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS price_settings (
            key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
    except BaseException:
        conn.close()                    # nobody else holds it yet to close it
        raise
    return conn


def num(x):
    """A price as JSON: whole numbers without a trailing .0, so an IDR tariff reads as before."""
    return int(x) if x is not None and x == int(x) else x


def day_of(ts):
    """The owner's calendar day for a unix time; periods take effect at local midnight."""
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _midnight(day):
    return int(time.mktime(time.strptime(day, "%Y-%m-%d")))


# ---------------------------------------------------------------------------
# Validation — every write is checked whole before anything is stored
# ---------------------------------------------------------------------------

def _number(value, name, low, high):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not low <= value <= high):
        raise ValueError("%s must be a number from %g to %g" % (name, low, high))
    return float(value)


def _text(value, name, limit):
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError("%s must be text of at most %d characters" % (name, limit))
    return value.strip()


def valid_date(value, name="start"):
    if not isinstance(value, str) or not _DATE.match(value):
        raise ValueError("%s must be YYYY-MM-DD" % name)
    try:
        time.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise ValueError("%s is not a date" % name) from None
    return value


def valid_session_id(value):
    """A session's ID is the unix second the server reported it starting."""
    if isinstance(value, bool):
        raise ValueError("invalid session id")
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str) or not _ID.match(value):
        raise ValueError("invalid session id")
    return int(value)


def _periods(items, kind):
    if not isinstance(items, list) or len(items) > PERIODS_MAX:
        raise ValueError("%s must be a list of at most %d periods" % (kind, PERIODS_MAX))
    fields = {"scope", "start", "price", "label"} if kind == "charge" else {"start", "price", "label"}
    out, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("each %s period must be an object" % kind)
        extra = set(item) - fields
        if extra:
            raise ValueError("unknown %s period field %s" % (kind, sorted(extra)[0]))
        scope = item.get("scope", "all")
        if scope not in CHARGE_SCOPES:
            raise ValueError("scope must be all, ac or dc")
        start = valid_date(item.get("start"))
        if (scope, start) in seen:
            raise ValueError("two %s periods start on %s" % (kind, start))
        seen.add((scope, start))
        out.append({"scope": scope, "start": start,
                    "price": _number(item.get("price"), "price", 0, PRICE_MAX),
                    "label": _text(item.get("label"), "label", 60)})
    return sorted(out, key=lambda p: (p["scope"], p["start"]))


def validate_book(body):
    """The parts of a price book to replace. `charge` and `fuel` each replace that whole list
    (an empty list clears it); `fuel_kml` is the petrol car the savings are measured against,
    null to fall back to creds.json. A part left out is left as it is."""
    if not isinstance(body, dict):
        raise ValueError("price book must be an object")
    extra = set(body) - {"charge", "fuel", "fuel_kml"}
    if extra:
        raise ValueError("unknown field %s" % sorted(extra)[0])
    if not body:
        raise ValueError("nothing to save")
    book = {kind: _periods(body[kind], kind) for kind in KINDS if kind in body}
    if "fuel_kml" in body:
        kml = body["fuel_kml"]
        book["fuel_kml"] = None if kml is None else _number(kml, "fuel_kml", 1, 100)
    return book


def validate_override(body):
    """What one session cost: `rate` (price per kWh at the meter), `total` (the receipt's bottom
    line) or `free`. `metered_kwh` is the receipt's kWh, which replaces the estimate from the
    SoC rise; `note` is the owner's."""
    if not isinstance(body, dict):
        raise ValueError("price must be an object")
    extra = set(body) - {"mode", "value", "metered_kwh", "note"}
    if extra:
        raise ValueError("unknown field %s" % sorted(extra)[0])
    mode = body.get("mode")
    if mode not in MODES:
        raise ValueError("mode must be rate, total or free")
    if mode == "free":
        if body.get("value") not in (None, 0):
            raise ValueError("a free session has no value")
        value = None
    else:
        value = _number(body.get("value"), "value", 0,
                        PRICE_MAX if mode == "rate" else AMOUNT_MAX)
    metered = body.get("metered_kwh")
    return {"mode": mode, "value": value,
            "metered_kwh": None if metered is None else _number(metered, "metered_kwh", 0.1, 500),
            "note": _text(body.get("note"), "note", 200)}


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def read_book(db):
    """Every period, oldest first within each scope, and the comparison car's economy. An owner
    who never priced anything has no file, and reading does not make one."""
    if not os.path.exists(db):
        return {"charge": [], "fuel": [], "fuel_kml": None}
    with closing(_connect(db)) as conn:
        rows = conn.execute("SELECT kind, scope, start, price, label FROM price_periods "
                            "ORDER BY kind, scope, start").fetchall()
        kml = conn.execute("SELECT value FROM price_settings WHERE key = 'fuel_kml'").fetchone()
    return {
        "charge": [{"scope": scope, "start": start, "price": num(price), "label": label}
                   for kind, scope, start, price, label in rows if kind == "charge"],
        "fuel": [{"start": start, "price": num(price), "label": label}
                 for kind, _scope, start, price, label in rows if kind == "fuel"],
        "fuel_kml": float(kml[0]) if kml else None,
    }


def put_book(db, book):
    """Replaces the parts `book` names, all or nothing."""
    with closing(_connect(db)) as conn, conn:
        for kind in KINDS:
            if kind in book:
                conn.execute("DELETE FROM price_periods WHERE kind = ?", (kind,))
                conn.executemany("INSERT INTO price_periods VALUES (?, ?, ?, ?, ?)",
                                 [(kind, p["scope"], p["start"], p["price"], p["label"])
                                  for p in book[kind]])
        if "fuel_kml" in book:
            if book["fuel_kml"] is None:
                conn.execute("DELETE FROM price_settings WHERE key = 'fuel_kml'")
            else:
                conn.execute("INSERT OR REPLACE INTO price_settings VALUES ('fuel_kml', ?)",
                             (repr(book["fuel_kml"]),))


def read_overrides(db):
    """Every override as (session id, override), oldest first."""
    if not os.path.exists(db):
        return []
    with closing(_connect(db)) as conn:
        rows = conn.execute("SELECT start, mode, value, metered_kwh, note FROM session_prices "
                            "ORDER BY start").fetchall()
    return [(start, {"mode": mode, "value": num(value), "metered_kwh": metered, "note": note})
            for start, mode, value, metered, note in rows]


def put_overrides(db, sessions, override):
    """Pins each of `sessions` -- (id, lo, hi): the session's ID and the window an override for
    it may sit in -- to `override`. Whatever was keyed inside a window before is replaced, so a
    session never carries two."""
    now = int(time.time())
    with closing(_connect(db)) as conn, conn:
        for sid, lo, hi in sessions:
            conn.execute("DELETE FROM session_prices WHERE start BETWEEN ? AND ?", (lo, hi))
            conn.execute("INSERT INTO session_prices VALUES (?, ?, ?, ?, ?, ?)",
                         (sid, override["mode"], override["value"], override["metered_kwh"],
                          override["note"], now))


def delete_overrides(db, windows):
    """Clears every override keyed inside any of `windows` [(lo, hi)]. Answers how many went."""
    with closing(_connect(db)) as conn, conn:
        return sum(conn.execute("DELETE FROM session_prices WHERE start BETWEEN ? AND ?",
                                (lo, hi)).rowcount for lo, hi in windows)


def window(session):
    """Where an override for `session` may be keyed: from MATCH_LEAD before its start to its
    last rise."""
    return session["start"] - MATCH_LEAD, session["end"]


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------

class PriceBook:
    """Prices as they stood on any day, and the owner's per-session overrides.

    A charge session takes the newest period of its own type (AC or DC) that had started by its
    day, then the newest `all` period, then creds.json's tariff. Fuel has one track.
    """

    def __init__(self, book, overrides, tariff, fuel, kml):
        self.tariff, self.fuel_default = tariff, fuel
        self.kml = book.get("fuel_kml") or kml
        self._charge = {scope: self._track(p for p in book.get("charge", []) if p["scope"] == scope)
                        for scope in CHARGE_SCOPES}
        self._fuel = self._track(book.get("fuel", []))
        self.overrides = sorted(overrides, key=lambda o: o[0])
        self._keys = [sid for sid, _o in self.overrides]

    @staticmethod
    def _track(periods):
        periods = sorted(periods, key=lambda p: p["start"])
        return [p["start"] for p in periods], periods

    @staticmethod
    def _at(track, day):
        starts, periods = track
        i = bisect_right(starts, day)
        return periods[i - 1] if i else None

    def charge_rate(self, kind, day):
        """(price per billed kWh, 'period' or 'default', the period) for a session of `kind`
        ('ac', 'dc' or None) that started on `day`."""
        period = (self._at(self._charge[kind], day) if kind in ("ac", "dc") else None) \
            or self._at(self._charge["all"], day)
        return (period["price"], "period", period) if period else (self.tariff, "default", None)

    def fuel_rate(self, day):
        """(price per litre, 'period' or 'default') on `day`."""
        period = self._at(self._fuel, day)
        return (period["price"], "period") if period else (self.fuel_default, "default")

    def _claims(self, sessions):
        """The index of the override each session carries, or None: the unclaimed one keyed
        inside its window, nearest its start."""
        claimed, out = set(), []
        for s in sessions:
            lo, hi = window(s)
            free = [i for i in range(bisect_left(self._keys, lo), bisect_right(self._keys, hi))
                    if i not in claimed]
            best = min(free, key=lambda i: abs(self._keys[i] - s["start"]), default=None)
            if best is not None:
                claimed.add(best)
            out.append(best)
        return out

    def orphans(self, sessions, t0, t1):
        """Overrides keyed in [t0, t1) that none of `sessions` carries -- their session is gone,
        most likely because the detector now draws it differently."""
        claimed = set(i for i in self._claims(sessions) if i is not None)
        return [(sid, o) for i, (sid, o) in enumerate(self.overrides)
                if t0 <= sid < t1 and i not in claimed]

    def price_sessions(self, sessions, efficiency):
        """Prices every session in place. Each needs `start`, `end`, `kwh` (into the pack), `soc1`
        and `type`, and gains `billed` (kWh at the meter: the receipt's when it was given, else
        `kwh` over `efficiency(soc1)`), `price` per billed kWh, `cost`, `price_source`
        (override, period or default), and `period` / `override`, whichever priced it."""
        for s, i in zip(sessions, self._claims(sessions)):
            override = self.overrides[i][1] if i is not None else None
            billed = (override or {}).get("metered_kwh") or s["kwh"] / efficiency(s["soc1"])
            period = None
            if override is None:
                price, source, period = self.charge_rate(s.get("type"), day_of(s["start"]))
                cost = billed * price
            elif override["mode"] == "rate":
                price, source, cost = override["value"], "override", billed * override["value"]
            elif override["mode"] == "total":
                cost, source = override["value"], "override"
                price = cost / billed if billed else 0.0
            else:
                price, source, cost = 0.0, "override", 0.0
            s.update(billed=billed, price=price, cost=cost, price_source=source,
                     period=period, override=override)

    def fuel_cost(self, points):
        """What a petrol car at this book's economy would have spent over `points`, [(unix time,
        odometer)] oldest first. The distance is split at every fuel period's start, so km driven
        before a price change are priced at the price before it."""
        if len(points) < 2:
            return 0.0
        stamps = [ts for ts, _odo in points]
        cuts = [_midnight(start) for start in self._fuel[0]]
        cuts = [cut for cut in cuts if stamps[0] < cut <= stamps[-1]]
        cost, at, odo = 0.0, stamps[0], points[0][1]
        for cut in cuts + [None]:
            end = points[-1][1] if cut is None else points[bisect_left(stamps, cut) - 1][1]
            cost += max(0, end - odo) * self.fuel_rate(day_of(at))[0] / self.kml
            at, odo = cut, end
        return cost


def load(db, tariff, fuel, kml):
    """The price book in `db` over creds.json's `tariff`, petrol price `fuel` and economy `kml`."""
    return PriceBook(read_book(db), read_overrides(db), tariff, fuel, kml)


def paid_average(sessions, now, days=PAID_DAYS):
    """What charging actually cost over the last `days`, from priced sessions: per kWh into the
    pack (what consumption multiplies) and per billed kWh (what a tariff is quoted in). None when
    too little went in to call it an average."""
    recent = [s for s in sessions if s["start"] >= now - days * 86400 and not s.get("ongoing")]
    kwh = sum(s["kwh"] for s in recent)
    if kwh < PAID_MIN_KWH:
        return None
    billed = sum(s["billed"] for s in recent)
    cost = sum(s["cost"] for s in recent)
    return {"per_pack_kwh": cost / kwh, "per_kwh": cost / billed if billed else 0.0,
            "sessions": len(recent), "days": days}
