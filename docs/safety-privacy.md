# Legal, privacy and multi-user

## Legal and ethics: read this first

This is a **personal interoperability / reverse-engineering** project for accessing **your own vehicle and your own account**, provided for educational and personal use.

- **Use it only with an account and a car you own.** Do not access anyone else's data.
- It talks to a **private, undocumented vendor API**. There is no warranty, it can break whenever the vendor changes its backend, and it is **not affiliated with, endorsed by, or supported by** Jaecoo, Chery or CarLinko.
- **No personal data is shipped.** Your account, token, VIN, plate, vehicle id and device serial live only in `creds.json` and `token.txt` (both gitignored). The request-signing key is an app-wide constant, the same string in every CarLinko install, so it is bundled; it is not a secret tied to you.
- **Do not run this as a public, multi-user service.** That means storing other people's credentials (which can unlock their cars) and almost certainly breaches the vendor's terms.
- **Controls are beta and real.** Only *Stop charging* is a confirmed opcode; the rest are best-effort decodes, and every tap actuates a real car. Control is cloud only: the car must be awake and have cellular signal, or the command fails (`50043`). See [control notes](control-opcodes.md).

If you don't accept the above, don't use this.

## Privacy and security

Everything runs on your own host; there is no backend operated by the maintainers. The full picture is in [SECURITY.md](../SECURITY.md). In short:

- Your CarLinko email and password are stored locally (`creds.json`, gitignored) and used only to sign in to CarLinko's own cloud (`*.hzhjcl.com`) over TLS, the same place the app talks to.
- Other outbound calls: Google Maps (only if you add a key), OpenStreetMap / OSRM for the trip planner, and the Voltmoss relay (only if you enable push alerts).
- Keep the dashboard private (LAN or Tailscale). If it faces the internet, use HTTPS and set a `dashboard_password` (login page → Advanced).
- Use a second CarLinko account for the dashboard; see [Installation](installation.md#use-a-second-account-recommended).
- Found a vulnerability? Report it privately as described in [SECURITY.md](../SECURITY.md); don't open a public issue.

## Going multi-user

Each instance is **single-tenant by design**. To let other owners use it, have **each owner run their own instance** with their own `creds.json`, not one service holding everyone's credentials. Per-car differences are already handled through `battery_kwh`, `wltp_kwh_100`, `tariff` and the vehicle details in `creds.json`; see [Configuration](configuration.md).
