# j5-ev-dashboard engineering notes

## Compatibility on every change

For every server change, check its effect on external clients, including
Voltmoss. Keep one HTTP version:

- `API_VERSION` in `tools/server.py` is the integer contract for
  `/api/summary`. Additive fields do not raise it. Removal, rename, type,
  units, or meaning changes do; record each bump in `docs/api-map.md`.
  Public `/api/status` reports the same `api_version` even when the dashboard
  is locked. Git `release-X.Y.Z` tags identify source releases only; they do
  not create a second HTTP version.

Review `CAPABILITIES` whenever an endpoint or operation changes. Advertise an
ID only when its documented read/write behavior exists in this build. A
capability describes server code, not a car's equipment or CarLinko's
permission. Keep `/api/status` public when locked and preserve its existing
`configured`, `gated`, and `authed` fields. Older clients must be able to
ignore new fields. Update `docs/api-map.md`, status tests, and release notes
with any contract change. Never label a missing or malformed version from an
older deployment as definitely obsolete.

The compatibility implementation belongs on `feat/carlinko-device-endpoints`
until it is merged. The maintainer release procedure is in
`docs/releasing.md`; releases must start from the branch that contains the
intended server code. Run `python3 tools/ci_check.py`,
`python3 tools/status_selftest.py`, `python3 tools/release_selftest.py`,
`python3 tools/push_contract_selftest.py`,
`python3 tools/push_dispatcher_selftest.py`, `python3 tools/replica_selftest.py`, and
`python3 -m unittest push_relay.test_relay` before tagging.

Voltmoss push alerts (`push_alerts`) are opt-in per deployment and off by
default. The dispatcher evaluates committed telemetry, never `GET
/api/summary`, and judges each frame through `alert_summary()`, which shares
its moving, charging, tyre and 12V helpers with `summary()`. A change to how
`summary()` derives any of those must go through those helpers, or the two
will disagree about an alert. `push_dispatcher_selftest.py` pins that parity
on synthetic frames. The contract and deployment notes are in
`docs/api-map.md` → "Voltmoss push binding".

`push_relay/` is the Voltmoss-operated relay those events go to, a separate
service with its own image (build context `push_relay/`, never the repo root);
setup and deployment are in `push_relay/README.md`. It stays on this fork and
out of the upstream PR. Its notification copy in `push_relay/copy/` is a
snapshot of the app's `alerts` translations: re-sync it with
`python3 -m push_relay.sync_copy` whenever the app's alert copy changes. When
the dispatcher starts sending a new kind or value, the relay must accept it
first, so deploy the relay before the dashboard.

The fork's own dashboard runs on Railway (project `tiggo-7-phev`, service
`j5-ev-dashboard`): `production` deploys fork `main`, `staging` deploys fork
`staging`, each with its own `/data` volume. Push to the `fork` remote; `origin`
is upstream. The image's CMD is the dashboard alone, so what keeps the car
logged is each environment's **start command**, `bash railway_start.sh` — a
Railway service setting, not a file in the repo (this service ignores a root
`railway.toml`). Set it per environment with `railway api` →
`environmentPatchCommit`; `serviceInstanceUpdate` reaches every environment that
is not a fork. On 2026-11-02 a rebuild deployed images without it and both
environments stopped logging with no error anywhere, so after any deploy check
the logs for `railway_start:` and `adaptive logging` (production) or
`mirroring` (staging).

Staging is a mirror of production, and only production talks to the car or
sends alerts: CarLinko allows one live session per account. Production serves
token-only `/api/replica/*` routes while `CARLINKO_REPLICA_TOKEN` is set;
staging sets the same token and `CARLINKO_REPLICA_OF`, and `railway_start.sh`
then runs `tools/replica.py` instead of the logger and the dispatcher. It merges
telemetry (production's row wins a same-second clash; staging's older frames
stay) and copies the owner's files — maintenance log, price book, car photo —
when production's copy changes, keeping staging's own where production has
none. `push_alerts.db`, `creds.json` and `token.txt` are never served. These
routes are operations, not a client feature: no capability ID advertises them.
