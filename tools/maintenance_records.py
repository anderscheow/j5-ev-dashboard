"""The owner's maintenance log: which services were done, what each receipt
said, the receipt images, and the guide's market and model choice.

Kept here rather than on a phone, so a reinstall, a second phone or the web
dashboard all see the same log. One SQLite file beside the telemetry database
(`maintenance.db`), so the logger's writes never wait on a receipt upload.

A milestone is done when it has a record: the record is the tick. Receipt
images are stored as blobs and referenced from records by an opaque ID; an
upload no record ever claimed is collected after a day.
"""

import json
import re
import secrets
import sqlite3
import time
from contextlib import closing
from datetime import datetime


COUNTRIES = frozenset(("MY", "ID"))
RECEIPT_MAX = 10 * 1024 * 1024        # a phone photo of a long receipt, not a payload
RECORD_MAX = 64 * 1024                # the JSON body of one record
ORPHAN_AGE = 24 * 3600                # an upload left behind by a cancelled form

_ID = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_MONEY_MAX = 1e9


def valid_receipt_id(receipt_id):
    return isinstance(receipt_id, str) and bool(_ID.fullmatch(receipt_id))


def _connect(db):
    conn = sqlite3.connect(db, timeout=5)
    conn.execute("""CREATE TABLE IF NOT EXISTS maintenance_preferences (
        key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS maintenance_records (
        model TEXT NOT NULL, km INTEGER NOT NULL, record TEXT NOT NULL,
        updated INTEGER NOT NULL, PRIMARY KEY (model, km))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS maintenance_receipts (
        id TEXT PRIMARY KEY, mime TEXT NOT NULL, data BLOB NOT NULL,
        created INTEGER NOT NULL)""")
    return conn


# ---------------------------------------------------------------------------
# Validation — every write is checked whole before anything is stored
# ---------------------------------------------------------------------------

def valid_model(model):
    """The catalogue model a timetable was read for; part of a record's key."""
    if not isinstance(model, str) or not model.strip() or len(model) > 120:
        raise ValueError("invalid model")
    return model.strip()


def valid_km(km):
    try:
        value = int(km)
    except (TypeError, ValueError) as e:
        raise ValueError("invalid milestone") from e
    if not 0 < value <= 2_000_000:
        raise ValueError("invalid milestone")
    return value


def _number(value, name, low, high):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise ValueError("invalid " + name)
    return value


def _text(value, name, limit):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError("invalid " + name)
    return value


def validate_record(km, body):
    """The canonical record for milestone `km`, or ValueError before any write.

    Amounts are as the owner typed them from the receipt. `total` is the
    receipt's own bottom line and may disagree with the sum of its lines.
    """
    allowed = {"km", "at", "odo", "items", "discount", "tax", "total",
               "currency", "receipts", "notes"}
    if not isinstance(body, dict) or not set(body) <= allowed:
        raise ValueError("invalid record fields")
    if "km" in body and body["km"] != km:
        raise ValueError("record does not match its milestone")
    at = body.get("at")
    if not isinstance(at, str) or len(at) > 40:
        raise ValueError("invalid service time")
    try:
        datetime.fromisoformat(at)
    except ValueError as e:
        raise ValueError("invalid service time") from e
    odo = body.get("odo")
    if odo is not None and (type(odo) is not int or not 0 <= odo <= 2_000_000):
        raise ValueError("invalid odometer")
    items = body.get("items", [])
    if not isinstance(items, list) or len(items) > 100:
        raise ValueError("invalid items")
    lines = []
    for item in items:
        if not isinstance(item, dict) or not set(item) <= {"name", "qty", "price"}:
            raise ValueError("invalid item")
        lines.append({
            "name": _text(item.get("name", ""), "item name", 200) or "",
            "qty": _number(item.get("qty", 1), "quantity", 0, 100_000),
            "price": _number(item.get("price"), "unit price", -_MONEY_MAX, _MONEY_MAX),
        })
        if lines[-1]["price"] is None:
            raise ValueError("invalid unit price")
    currency = body.get("currency")
    if currency is not None and (not isinstance(currency, str) or not _CURRENCY.fullmatch(currency)):
        raise ValueError("invalid currency")
    receipts = body.get("receipts", [])
    if (not isinstance(receipts, list) or len(receipts) > 20 or
            not all(valid_receipt_id(r) for r in receipts) or len(set(receipts)) != len(receipts)):
        raise ValueError("invalid receipts")
    record = {
        "km": km,
        "at": at,
        "odo": odo,
        "items": lines,
        "discount": _number(body.get("discount"), "discount", 0, _MONEY_MAX),
        "tax": _number(body.get("tax"), "tax", 0, _MONEY_MAX),
        "total": _number(body.get("total"), "total", 0, _MONEY_MAX),
        "currency": currency,
        "receipts": receipts,
        "notes": _text(body.get("notes"), "notes", 1000),
    }
    return {key: value for key, value in record.items() if value not in (None, [])}


def validate_preferences(body):
    """`country` and `model` may each be set; a null model clears the choice."""
    if not isinstance(body, dict) or not body or not set(body) <= {"country", "model"}:
        raise ValueError("invalid preferences")
    out = {}
    if "country" in body:
        if body["country"] not in COUNTRIES:
            raise ValueError("invalid country")
        out["country"] = body["country"]
    if "model" in body:
        out["model"] = None if body["model"] is None else valid_model(body["model"])
    return out


def sniff_image(blob):
    """The image type by its magic bytes — never by the Content-Type header."""
    if blob[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return "image/webp"
    if blob[4:8] == b"ftyp" and blob[8:12] in (b"heic", b"heix", b"mif1", b"msf1", b"hevc"):
        return "image/heic"
    return None


# ---------------------------------------------------------------------------
# Reads and writes
# ---------------------------------------------------------------------------

def log(db):
    """Everything: the guide preferences and every record, by model."""
    with closing(_connect(db)) as conn:
        prefs = dict(conn.execute("SELECT key, value FROM maintenance_preferences"))
        rows = conn.execute(
            "SELECT model, record FROM maintenance_records ORDER BY model, km").fetchall()
    return {
        "country": prefs.get("country"),
        "model": prefs.get("model"),
        "records": [dict(json.loads(record), model=model) for model, record in rows],
    }


def put_preferences(db, prefs):
    with closing(_connect(db)) as conn, conn:
        for key, value in prefs.items():
            if value is None:
                conn.execute("DELETE FROM maintenance_preferences WHERE key = ?", (key,))
            else:
                conn.execute("INSERT OR REPLACE INTO maintenance_preferences VALUES (?, ?)",
                             (key, value))


def put_record(db, model, record):
    """Stores `record`, and deletes any receipt its previous version named that
    this one does not. Every receipt it names must already be uploaded."""
    with closing(_connect(db)) as conn, conn:
        for receipt_id in record.get("receipts", []):
            if not conn.execute("SELECT 1 FROM maintenance_receipts WHERE id = ?",
                                (receipt_id,)).fetchone():
                raise ValueError("unknown receipt")
        row = conn.execute("SELECT record FROM maintenance_records WHERE model = ? AND km = ?",
                           (model, record["km"])).fetchone()
        previous = json.loads(row[0]).get("receipts", []) if row else []
        conn.execute("INSERT OR REPLACE INTO maintenance_records VALUES (?, ?, ?, ?)",
                     (model, record["km"], json.dumps(record), int(time.time())))
        for receipt_id in set(previous) - set(record.get("receipts", [])):
            conn.execute("DELETE FROM maintenance_receipts WHERE id = ?", (receipt_id,))
    return dict(record, model=model)


def delete_record(db, model, km):
    """Forgets milestone `km` for `model` — it is no longer done — and its receipts."""
    with closing(_connect(db)) as conn, conn:
        row = conn.execute("SELECT record FROM maintenance_records WHERE model = ? AND km = ?",
                           (model, km)).fetchone()
        if row is None:
            return False
        conn.execute("DELETE FROM maintenance_records WHERE model = ? AND km = ?", (model, km))
        for receipt_id in json.loads(row[0]).get("receipts", []):
            conn.execute("DELETE FROM maintenance_receipts WHERE id = ?", (receipt_id,))
    return True


def add_receipt(db, blob, now=None):
    """Stores one receipt image and answers its ID. Uploads that no record has
    claimed within a day — a form the owner backed out of — go first."""
    if not 0 < len(blob) <= RECEIPT_MAX:
        raise ValueError("receipt must be 1..%d bytes" % RECEIPT_MAX)
    mime = sniff_image(blob)
    if mime is None:
        raise ValueError("only JPEG, PNG, WebP or HEIC")
    now = int(now if now is not None else time.time())
    receipt_id = secrets.token_urlsafe(18)
    with closing(_connect(db)) as conn, conn:
        claimed = set()
        for (record,) in conn.execute("SELECT record FROM maintenance_records"):
            claimed.update(json.loads(record).get("receipts", []))
        for (old,) in conn.execute("SELECT id FROM maintenance_receipts WHERE created < ?",
                                   (now - ORPHAN_AGE,)).fetchall():
            if old not in claimed:
                conn.execute("DELETE FROM maintenance_receipts WHERE id = ?", (old,))
        conn.execute("INSERT INTO maintenance_receipts VALUES (?, ?, ?, ?)",
                     (receipt_id, mime, sqlite3.Binary(blob), now))
    return {"id": receipt_id, "bytes": len(blob)}


def receipt(db, receipt_id):
    """(mime, bytes) for an uploaded receipt, or None."""
    if not valid_receipt_id(receipt_id):
        return None
    with closing(_connect(db)) as conn:
        row = conn.execute("SELECT mime, data FROM maintenance_receipts WHERE id = ?",
                           (receipt_id,)).fetchone()
    return (row[0], bytes(row[1])) if row else None
