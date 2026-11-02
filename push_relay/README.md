# Voltmoss push relay

An optional service that delivers dashboard alerts ("car left unlocked", "charge
complete", …) to the Voltmoss phone app as push notifications.

The dashboard decides *when* to alert, because it sees every frame the car reports.
The relay does the *delivering*: it is the only component that holds a Firebase
send credential, so a self-hosted dashboard never needs one.

```text
CarLinko → dashboard logger → SQLite → dashboard push dispatcher
                                         │  installation ID + sender secret
                                         ▼
                                  push relay ──► FCM ──► APNs / Android ──► phone
```

**You do not need this to run a dashboard.** The relay is run once by whoever
publishes the Voltmoss app. A dashboard owner who wants push alerts only points their
dashboard at an existing relay (see [Connect a dashboard](#connect-a-dashboard)).

It accepts a fixed vocabulary of alert kinds and renders the notification text
itself, in the phone's language. It never receives a vehicle's telemetry, VIN,
location, dashboard address or cookies, and it never logs request paths, headers
or bodies.

## Contents

| File | What it is |
| --- | --- |
| `server.py` | HTTP API and the delivery worker. Entry point: `python3 -m push_relay.server`. |
| `relay.py` | Installations, the event queue and rate limits, stored in SQLite. |
| `sender.py` | Turns an event into an FCM message, using the copy in `copy/`. |
| `copy/` | The notification copy in English, Malay, Indonesian and Simplified Chinese. |
| `sync_copy.py` | Keeps `copy/` in step with the app's translation bundles. |
| `live_activity.py`, `live_activity_cli.py` | Developer tool that drives the iOS charging Live Activity. |
| `test_relay.py` | Tests. No credentials needed. |
| `Dockerfile`, `deploy_railway.sh` | Container image, and a Railway deploy script. |

## Before you start

You need:

1. **A Firebase project with both Voltmoss apps** (iOS and Android) registered in it.
2. **Cloud Messaging configured**, including an APNs authentication key (`.p8`) for iOS.
3. **App Check enabled** for both apps: App Attest with DeviceCheck fallback on iOS,
   Play Integrity on Android, and debug providers for development builds. The relay
   accepts a registration only with a valid App Check token from one of the allowed
   apps.
4. **A service account for sending**, with the **Firebase Cloud Messaging API Admin**
   role and nothing else. Do not use the broad `firebase-adminsdk` account.
5. **A host that offers:**
   - **A persistent disk.** The queue is a SQLite file.
   - **An HTTPS endpoint.** The app and dashboards refuse plain HTTP.
   - **Exactly one running instance.** Two instances on one SQLite file would
     double-send.

   Scale-to-zero platforms and throwaway filesystems will not work unless the storage
   layer is replaced with a managed database and queue.

## Configuration

All configuration is environment variables. Credentials are never files in the repo or
the image.

| Variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `GOOGLE_CLOUD_PROJECT` | yes | — | Firebase project ID. |
| `VOLTMOSS_APP_CHECK_APP_IDS` | yes | — | Comma-separated Firebase **App IDs** of the iOS and Android apps. Startup fails without it. |
| `VOLTMOSS_FIREBASE_SERVICE_ACCOUNT_JSON` | outside Google Cloud | — | The sending service account's JSON key, as one secret variable. Its `project_id` must equal `GOOGLE_CLOUD_PROJECT`. Leave it unset on Google Cloud with workload identity. |
| `VOLTMOSS_RELAY_DB` | yes (the image sets it) | `/data/push_relay.db` in the image | SQLite file. Must be on the persistent disk. |
| `PORT` | no | `8099` | Listen port. |
| `VOLTMOSS_RELAY_BIND` | no | `0.0.0.0` in the image, `127.0.0.1` otherwise | Listen address. |
| `VOLTMOSS_RELAY_METRICS_SECRET` | no | unset | A long random string that enables `GET /internal/metrics`. Unset disables the route. |
| `VOLTMOSS_TRANSLATIONS_DIR` | no | `copy/` | Where the notification copy lives. Leave it alone. |

## Run it locally

From the repository root:

```bash
python3 -m pip install -r push_relay/requirements.txt

export GOOGLE_CLOUD_PROJECT=your-project-id
export VOLTMOSS_APP_CHECK_APP_IDS=1:111:ios:aaa,1:111:android:bbb
export VOLTMOSS_FIREBASE_SERVICE_ACCOUNT_JSON="$(cat ~/secrets/relay-sender.json)"
export VOLTMOSS_RELAY_DB=/tmp/push_relay.db
python3 -m push_relay.server

curl localhost:8099/healthz        # {"ok":true}
```

`/healthz` says the process is up. It does not check that FCM or APNs are reachable.

## Tests

```bash
python3 -m unittest push_relay.test_relay -v
```

The tests use a fake sender and no credentials. One test, which checks the FCM message
encoding, runs only when `firebase-admin` is installed and is skipped otherwise.

## Notification copy

The words in a push must match what the app shows when it raises the same alert
itself. **The app is the source of the copy.** `copy/` holds the relay's snapshot of the
`alerts` block of the app's translation bundles.

After the app's alert copy changes:

```bash
python3 -m push_relay.sync_copy /path/to/j5_ev_app/assets/translations   # update copy/
git diff push_relay/copy                                                 # review
python3 -m unittest push_relay.test_relay                                # still renders every kind
```

Then commit and redeploy the relay. To check without writing anything (CI, or before a
deploy), add `--check`. It exits 1 when `copy/` is behind the app. `deploy_railway.sh`
runs that check for you.

## Deploy with Docker

The build context is this folder alone, never the repository root:

```bash
docker build -t voltmoss-push-relay push_relay/
docker run -d --name push-relay -p 8099:8099 -v push-relay-data:/data \
  -e GOOGLE_CLOUD_PROJECT=your-project-id \
  -e VOLTMOSS_APP_CHECK_APP_IDS=1:111:ios:aaa,1:111:android:bbb \
  -e VOLTMOSS_FIREBASE_SERVICE_ACCOUNT_JSON="$(cat ~/secrets/relay-sender.json)" \
  voltmoss-push-relay
```

Put it behind an HTTPS reverse proxy. Rate-limit `POST /v1/installations` by source
address at the proxy as well. The relay has its own global caps, but per-source limits
belong at the edge.

## Deploy on Railway

1. **Create a project and service** for the relay. Keep it separate from any dashboard
   service.
2. **Attach a volume** at `/data`. Keep **one replica**: Railway attaches a volume to one
   replica only, and the queue needs exactly one.
3. **Set the variables** from [Configuration](#configuration) on the service. Paste
   `VOLTMOSS_FIREBASE_SERVICE_ACCOUNT_JSON` as a secret.
4. **Set `RAILWAY_DOCKERFILE_PATH=Dockerfile`** as a service variable. Without it,
   Railway may ignore the builder in `railway.toml` and fall back to auto-detection,
   which cannot build this.
5. **Set the health check** in the service settings: path `/healthz`, timeout 60 s.
   The value in `railway.toml` was not applied on a first deploy, so do not rely on it.
6. **Deploy** from the repository root:

   ```bash
   RAILWAY_PROJECT_ID=<project id> \
   VOLTMOSS_APP_TRANSLATIONS=/path/to/j5_ev_app/assets/translations \
     push_relay/deploy_railway.sh
   ```

   The script uploads only this folder. It first checks `copy/` against the app; set
   `VOLTMOSS_SKIP_COPY_CHECK=1` only if the app checkout is not available and its alert
   copy has not changed. `RAILWAY_SERVICE` (default `push-relay`) and
   `RAILWAY_ENVIRONMENT` (default `production`) pick the target.
7. **Check it:**
   - `/healthz` answers 200.
   - `POST /v1/installations` without an App Check token answers 401.
   - `/internal/metrics` answers 401 without the secret.

**Moving an existing service that was deployed from the app repository:** it has
`RAILWAY_DOCKERFILE_PATH=services/push_relay/Dockerfile`. Change it to `Dockerfile`
before the first deploy from here. If you forget, the build fails and Railway keeps
the running deployment, so nothing breaks; fix the variable and deploy again. Nothing
else changes: same variables, same volume, same URL.

## Connect a dashboard

On the dashboard (see `docs/api-map.md` → "Voltmoss push binding"):

| Variable | Set to |
| --- | --- |
| `VOLTMOSS_PUSH_RELAY_URL` | The relay's HTTPS URL. |
| `VOLTMOSS_PUSH_HTTPS=1` | Only when the dashboard's public address is HTTPS. |
| `VOLTMOSS_PUSH_DISPATCHER=1` | Only after the dashboard runs exactly one dispatcher. |

The dashboard must also be password-protected. It advertises the `push_alerts`
capability only when all of these hold, and the app enrols only then.

**Order matters when the contract grows.** A dashboard that sends a new alert kind or
value needs a relay that already accepts it, because the dispatcher drops an event the
relay answers 400 for. Deploy the relay first.

## Connect an app build

The app reads the relay address at build time:

```bash
flutter run --dart-define=VOLTMOSS_PUSH_RELAY_URL=https://your-relay.example
```

An empty value (the default) means the build never registers for push. Debug builds
need an App Check debug token registered for **both** Firebase apps.

## HTTP API

Bodies are JSON, 2048 bytes at most. Installation IDs are 16–64 URL-safe characters.
Sender secrets are 64 hex characters and are never echoed after creation.

| Method and path | Caller and auth | Body → result |
| --- | --- | --- |
| `POST /v1/installations` | App: `X-Firebase-AppCheck` | `{fid, platform: "ios"\|"android", locale: "en"\|"ms"\|"id"\|"zh"}` → `201 {installation_id, sender_secret}` |
| `PUT /v1/installations/{id}` | App: App Check + `Authorization: Bearer <sender_secret>` | `{fid, locale}` → `200 {ok: true}` |
| `DELETE /v1/installations/{id}` | App: App Check + sender secret | → `200 {ok: true}`. Retrying with the same secret stays 200 for seven days. |
| `POST /v1/installations/{id}/events` | Dashboard: sender secret | `{event_id, kind, value, occurred_at}` → `202 {accepted, duplicate}` |
| `PUT /v1/installations/{id}/activity-tokens` | App (native iOS, often a background wake): sender secret, **no** App Check | `{push_to_start?, update?}`, lowercase hex, or `null` to forget → `200 {ok: true}` |
| `POST /v1/installations/{id}/activity` | Dashboard: sender secret | `{event_id, event: "start"\|"update"\|"end", content, occurred_at, alert_minute?}` → `202` |
| `GET /healthz` | Health probe | `{ok: true}` |
| `GET /internal/metrics` | Bearer `VOLTMOSS_RELAY_METRICS_SECRET` | Installation count and event states only. |

The app sends `installation_id` and `sender_secret` to its own dashboard over HTTPS.
That secret authorises events for that one installation only.

**The charging Live Activity (iOS)** is driven through the last two routes:
- **Tokens.** The phone registers its push-to-start and update tokens with
  `activity-tokens`. There is no App Check on that route: the background wake
  that delivers a push-started activity's update token may never reach the app
  itself.
- **Content.** The dashboard posts what the activity should show to `activity`.
  `content` is the app's `ChargeActivityContent`, key for key.
- **Queue.** Each installation keeps one row, and the latest post wins. An
  update arriving before an unsent start stays a start.
- **Delivery:**
  - A start goes out through push-to-start, with the `charge_started` words and
    no sound.
  - Updates go at priority 5.
  - A completed charge clears after 30 minutes.
- **Tokens that fail.** A phone missing the token an event needs is skipped. A
  token APNs refuses is forgotten, but the installation is kept.

**Alert kinds and their `value`:**

| Kind | `value` |
| --- | --- |
| `soc_below`, `charge_complete`, `charge_stopped` | A percentage, 0–100. |
| `volt12_low` | Volts, 0–20. |
| `charge_started` | The car's estimated finish, as minutes past midnight on the phone's clock (0–1439), or `null` when there is no estimate. |
| `panel_open` | What is open, as a bitmask 1–127: door bits 1/2/4/8, then 16 tailgate, 32 sunroof, 64 a window. `null` from older dashboards. |
| `left_unlocked`, `tyre_pressure`, `service_due` | `null`. |

Anything else answers 400. The notification title and body always come from `copy/`;
a caller cannot supply them.

## Limits and delivery

| Limit | Value |
| --- | --- |
| Events per installation | 20 an hour, 100 a day |
| Installations per FID | 16 |
| New installations across the relay | 120 an hour, 1,000 a day |
| Active installations | 10,000 |
| Inactive registration expiry | 90 days |

The counters survive restarts. An event expires 15 minutes after `occurred_at` and gets
at most five send attempts, with backoff. FCM's "unregistered" answer removes the
installation.

**Delivery is at least once, not exactly once.** A crash between FCM accepting a
message and the relay recording it can send it twice. The app de-duplicates by
`event_id` when it shows a notification in the foreground.

## Operations

- **Logs** never contain request paths, headers, bodies, FIDs or event values.
- **Backups** of the volume contain FIDs and hashed sender secrets. Restrict access, and
  keep backups for **seven days at most**: the app's privacy policy promises that a
  deleted registration is gone within a week.
- **Rotating the sending key:**
  1. Create a new key for the same service account.
  2. Replace `VOLTMOSS_FIREBASE_SERVICE_ACCOUNT_JSON`.
  3. Redeploy.
  4. Delete the old key.
- **Never print the service's variables to a terminal.** The service-account JSON spans
  several lines, and `railway variables --kv | cut -d= -f1` once printed its private
  key.

## Live Activity test sender

`live_activity_cli.py` starts, updates and ends the iOS charging Live Activity on a
simulator or phone, using the relay's credential without saving it:

```bash
railway run --service push-relay -- \
  python3 -m push_relay.live_activity_cli start --fid FID --token PUSH_TO_START --soc 22
```

`update` and `end` take the activity's update token. A debug build shows the FID and
both tokens under Settings → Developer → "Live Activity push tokens". `--dry-run`
prints the message, and `--validate` has FCM check it without delivering. It needs
`firebase-admin` installed locally.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| Startup: `App Check app ID allowlist is required` | `VOLTMOSS_APP_CHECK_APP_IDS` is empty. |
| Startup: `Firebase project mismatch` | The key's `project_id` differs from `GOOGLE_CLOUD_PROJECT`. |
| Startup: `missing alert copy for …` | `copy/` lacks a key the relay renders. Re-run `sync_copy.py` against the app. |
| Railway build fails, or tries to detect the language | `RAILWAY_DOCKERFILE_PATH` is not `Dockerfile`. |
| Every registration answers 401 | No App Check token, a debug token not registered for that app, or an App ID missing from the allowlist. |
| The dashboard logs a dropped event (400) | It sends a kind or value this relay does not accept yet. Deploy the relay first. |
