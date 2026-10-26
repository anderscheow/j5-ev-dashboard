"""Logging set-up shared by the dashboard, the push dispatcher and the telemetry logger.

Plain `time LEVEL name message` lines on stderr by default, which is what a terminal,
`journalctl` and `docker compose logs` expect.

`CARLINKO_LOG_FORMAT=json` writes one JSON object per line to stdout instead, for hosts that
read the severity from the line rather than the stream. Railway files every stderr line as an
error, so plain logging painted each `INFO` request line red; it is detected by the
`RAILWAY_ENVIRONMENT_NAME` variable Railway sets on every deployment, and gets JSON unless
`CARLINKO_LOG_FORMAT=text` says otherwise.
"""
import json
import logging
import os
import sys

# Railway accepts debug, info, warn and error.
_RAILWAY_LEVELS = {"WARNING": "warn", "CRITICAL": "error"}


class _JsonFormatter(logging.Formatter):
    def format(self, record):
        line = {"level": _RAILWAY_LEVELS.get(record.levelname, record.levelname.lower()),
                "message": record.getMessage(), "logger": record.name}
        if record.exc_info:
            line["exception"] = self.formatException(record.exc_info)
        return json.dumps(line, ensure_ascii=False)


def _format():
    explicit = os.environ.get("CARLINKO_LOG_FORMAT", "").strip().lower()
    if explicit:
        return explicit
    return "json" if os.environ.get("RAILWAY_ENVIRONMENT_NAME") else "text"


def configure():
    level = os.environ.get("CARLINKO_LOG_LEVEL", "INFO").upper()
    if _format() == "json":
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_JsonFormatter())
        logging.basicConfig(level=level, handlers=[handler])
    else:
        logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
