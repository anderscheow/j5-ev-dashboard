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
`python3 tools/push_contract_selftest.py`, and
`python3 tools/push_dispatcher_selftest.py` before tagging.

Voltmoss push alerts (`push_alerts`) are opt-in per deployment and off by
default. The dispatcher evaluates committed telemetry, never `GET
/api/summary`, and judges each frame through `alert_summary()`, which shares
its moving, charging, tyre and 12V helpers with `summary()`. A change to how
`summary()` derives any of those must go through those helpers, or the two
will disagree about an alert. `push_dispatcher_selftest.py` pins that parity
on synthetic frames. The contract and deployment notes are in
`docs/api-map.md` → "Voltmoss push binding".
