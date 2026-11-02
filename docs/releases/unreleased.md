# Unreleased

- Added Malaysia maintenance timetables and nearby service centre lookups through
  `GET /api/maintenance-guide` and `GET /api/service-centres`.
- Added the `maintenance_guide` and `service_centres` capability IDs to public
  `/api/status`. Demo mode advertises these two implemented reads.
- `/api/summary` moves to `api_version: 2`: `history[].kwh` and `.eff` are net of regen; the
  drops-only (head-unit) figures are now `kwh_hu` / `eff_hu`. `energy.week_consumption` (and
  `energy.consumption` when the car reports none) is net of regen too. `/api/history` changed the same way.
  See the version table in `docs/api-map.md`.
- `/api/controls` keeps `seatHeat` / `seatVent` beside the new `seats`, for clients that read them.
- Voltmoss can open the maintenance guide from the Vehicle tab. Older clients
  can ignore the new capability IDs and routes.
- Maintenance lookup recognizes Chery Super Hybrid (CSH) as PHEV and selects
  the specific Tiggo 7 or Tiggo 8 PHEV schedule.
- Added the maintenance log behind the new `maintenance_records` capability:
  `GET /api/maintenance/records`, `PUT`/`DELETE /api/maintenance/records/{km}`,
  `PUT /api/maintenance/preferences`, and `POST`/`GET /api/maintenance/receipts`.
  Service records, receipt images and the guide's market and model choice are
  kept in `maintenance.db` in the data directory, so a reinstalled or second
  client sees the same log. Demo mode keeps a scratch copy per process.
- Added the price book behind the new `price_book` capability: dated charging
  prices (all, AC or DC) and fuel prices, and per-session overrides (a price per
  kWh, a receipt's total with its metered kWh, or free). `GET`/`PUT /api/prices`,
  `GET /api/charges?month=`, `PUT`/`DELETE /api/charges/{id}/price` and
  `POST /api/charges/prices`; stored in `prices.db` in the data directory.
  Every session, `charging.month_cost`, `lifetime.cost`, `/api/history` cost and
  trip-planner stop is priced from it, falling back to `creds.json`. Sessions now
  carry `id`, `type`, `price` and `price_source`; the summary adds
  `insights.price_basis`, `lifetime.petrol_cost` and `fuel_price`. No
  `api_version` bump: with no price book every cost is unchanged.
- `insights.rp_per_km` (and `month_cost_est`) now uses what charging actually
  cost over the last 90 days when at least 20 kWh went in, and `lifetime.saved`
  is the petrol those km would have cost less the actual charging spend. Both
  move on existing installs even with no price book; see `docs/api-map.md`.
  Demo mode does not offer the price book.
- The summary and `/api/history` now carry the meter's kWh beside the pack's wherever charging is
  totalled: `charging.session_kwh_billed`, `week_kwh_billed`, `month_kwh_billed`, and
  `chg_kwh_billed` / `charges[].kwh_billed` per day and for the month. Costs were already priced on
  it; the web dashboard now shows it first. Additive, no `api_version` bump.
