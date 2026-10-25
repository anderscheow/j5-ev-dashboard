#!/usr/bin/env bash
# Railway entrypoint: one service runs both the dashboard and the logger,
# sharing the /data volume (Railway volumes attach to a single service only).
#
# creds.json never ships in the image (.dockerignore excludes it). Instead the
# CREDS_JSON service variable is written into the volume on every boot, so
# updating the variable + redeploying is how you rotate credentials.
set -e

DATA="${CARLINKO_DATA:-/data}"
mkdir -p "$DATA"

if [ -n "$CREDS_JSON" ]; then
  printf '%s' "$CREDS_JSON" > "$DATA/creds.json"
  echo "railway_start: wrote $DATA/creds.json from CREDS_JSON"
elif [ ! -f "$DATA/creds.json" ]; then
  echo "railway_start: WARNING — no CREDS_JSON variable and no $DATA/creds.json in the volume" >&2
fi

python logger.py --adaptive &
if [ "${VOLTMOSS_PUSH_DISPATCHER:-0}" = "1" ]; then
  # Restarted like tools/run.py does: a dead dispatcher only withdraws push_alerts from
  # /api/status, which is safe but silent, so it must not stay dead until the next deploy.
  (while true; do python push_dispatcher.py; echo "railway_start: push dispatcher exited; restarting in 15 s" >&2; sleep 15; done) &
fi
exec python server.py "${PORT:-8088}"
