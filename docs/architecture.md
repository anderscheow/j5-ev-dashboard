# Architecture and project layout

```
  Car TCU ──(cellular)──> CarLinko cloud ──┐
                                           │  WebSocket (token auth) — telemetry blob
  tools/logger.py  ◀───────────────────────┘  decodes + stores every frame in carlinko.db
       │                                       (auth.py refreshes the token on expiry)
       ▼
  carlinko.db (SQLite)
       │
       ▼
  tools/server.py ── /api/summary, /api/trip, /api/spklu ──▶ web/ PWA (vanilla JS, Leaflet)
  (stdlib http.server)       + Google Places (optional)
```

- **No framework, no build step.** The backend is the Python standard library; the frontend is hand-written HTML/CSS/JS with two vendored libraries (Leaflet, slot-text).
- **Telemetry is a 73-byte blob.** Field offsets were found by driving the car and watching which bytes moved (battery = byte 28, range = bytes 29–30, odometer = bytes 18–20, …). See the [API map](api-map.md).
- **Push alerts** add a dispatcher that reads committed telemetry and sends events to the Voltmoss relay; see the diagram in the [README](../README.md).

## Project layout

- `tools/`: Python backend (`server.py`, `logger.py`, `auth.py`), install and deploy scripts, and reverse-engineering utilities.
- `web/`: the PWA (`index.html` and v2 pages, plus vendored `leaflet.*` and `slot-text.js`).
- `docs/`: these documents, the API map and decompiled signing notes (secrets redacted).
- `push_relay/`: the Voltmoss relay.
- `PRODUCT.md`, `DESIGN.md`: product and visual design notes.

## Contributing

A [compatibility report](https://github.com/GodrezJr2/j5-ev-dashboard/issues/new?template=compatibility.md) (does it work on your car and region?) is the most useful contribution. See [CONTRIBUTING.md](../CONTRIBUTING.md) and [SECURITY.md](../SECURITY.md). Questions go to [Discussions](https://github.com/GodrezJr2/j5-ev-dashboard/discussions).
