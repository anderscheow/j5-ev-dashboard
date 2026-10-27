# Unreleased

- Added Malaysia maintenance timetables and nearby service centre lookups through
  `GET /api/maintenance-guide` and `GET /api/service-centres`.
- Added the `maintenance_guide` and `service_centres` capability IDs to public
  `/api/status`. Demo mode advertises these two implemented reads.
- `/api/summary` remains at `api_version: 1`; its payload contract is unchanged.
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
