#!/usr/bin/env bash
# Deploys the push relay to Railway from this folder alone.
#
#   RAILWAY_PROJECT_ID=<id> \
#   VOLTMOSS_APP_TRANSLATIONS=/path/to/j5_ev_app/assets/translations \
#     push_relay/deploy_railway.sh
#
# The build context is push_relay/ and nothing else: the dashboard beside it is a
# separate service and never goes into this image. The folder is copied to a
# temporary directory together with a railway.toml, so nothing is left behind.
#
# The service needs RAILWAY_DOCKERFILE_PATH=Dockerfile as a Railway variable: on a
# first upload Railway ignored the builder in railway.toml and fell back to
# auto-detection, which cannot build this (README.md -> "Deploy on Railway").
#
# Before uploading, the relay's notification copy is checked against the app's
# translation bundles, its source. Set VOLTMOSS_SKIP_COPY_CHECK=1 to deploy
# without that check (only when the app checkout is not at hand and its alert
# copy is known not to have changed).
#
# Secrets are never passed here; they are Railway variables on the service.
set -euo pipefail

: "${RAILWAY_PROJECT_ID:?set RAILWAY_PROJECT_ID to the ID of the relay project}"
SERVICE="${RAILWAY_SERVICE:-push-relay}"
ENVIRONMENT="${RAILWAY_ENVIRONMENT:-production}"

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

if [ "${VOLTMOSS_SKIP_COPY_CHECK:-}" = "1" ]; then
  echo "deploy_railway: skipping the alert copy check (VOLTMOSS_SKIP_COPY_CHECK=1)" >&2
else
  : "${VOLTMOSS_APP_TRANSLATIONS:?set VOLTMOSS_APP_TRANSLATIONS to the assets/translations folder of the app, or VOLTMOSS_SKIP_COPY_CHECK=1}"
  (cd "$ROOT" && python3 -m push_relay.sync_copy "$VOLTMOSS_APP_TRANSLATIONS" --check)
fi

CONTEXT="$(mktemp -d)"
trap 'rm -rf "$CONTEXT"' EXIT
rsync -a --exclude '__pycache__' --exclude 'deploy_railway.sh' "$HERE/" "$CONTEXT/"

# One replica is a requirement, not a choice: the queue is SQLite on the
# /data volume, and Railway allows a volume on one replica only.
cat > "$CONTEXT/railway.toml" <<'TOML'
[build]
builder = "DOCKERFILE"
dockerfilePath = "Dockerfile"

[deploy]
healthcheckPath = "/healthz"
healthcheckTimeout = 60
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 10
TOML

MESSAGE="push relay $(git -C "$ROOT" rev-parse --short HEAD)"
if [ -n "$(git -C "$ROOT" status --porcelain -- push_relay)" ]; then
  MESSAGE="$MESSAGE+local"
fi

cd "$CONTEXT"
railway up --ci \
  --project "$RAILWAY_PROJECT_ID" \
  --environment "$ENVIRONMENT" \
  --service "$SERVICE" \
  --message "$MESSAGE"
