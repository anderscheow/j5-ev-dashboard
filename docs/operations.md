# Hosting, updating and logs

For the Docker update steps and push alerts, see the [README](../README.md). This page covers hosting choices and the other deployment paths.

## Where to run it

The host must be on **24/7**. A phone alone won't do: iOS can't run a background server and Android (Termux) gets killed by battery management. Run the dashboard elsewhere and use your phone as the browser (add it to the home screen as a PWA).

| Host | Cost | Notes |
| --- | --- | --- |
| Spare PC, old laptop or **Raspberry Pi** at home | free | best privacy; reach it over [Tailscale](https://tailscale.com) |
| Small **VPS** (Hetzner, Contabo, DigitalOcean…) | ~$4/mo | easiest always-on; use Tailscale or a `dashboard_password` |
| **Fly.io / Railway / Render** | varies | deploy the Docker image and set a `dashboard_password` |
| **Oracle Cloud Free / Google e2-micro** | free | always-on free VM |

On a private, home or Tailscale host the dashboard can stay open. The moment its URL is reachable from the internet, serve it over HTTPS and set a `dashboard_password` (login page → Advanced).

### One service, both processes

`docker compose` runs the dashboard and the logger as two services sharing `./data`. A host that attaches a volume to a single service (Railway) can't, so `tools/railway_start.sh` starts the logger in the background and the dashboard in the foreground from one container:

- Set the service start command to `bash railway_start.sh`.
- Put the contents of `creds.json` in a `CREDS_JSON` service variable; the script writes it into the volume on every boot.
- It is deliberately **not** the image default. CarLinko allows one live session per account, so a plain `docker run` that quietly started a logger would knock your own app offline.

If you use this, check the logs after every deploy for `railway_start:` and `adaptive logging`; without the start command, nothing logs the car and no error appears.

## Updating

Clients that ask for a newer dashboard are reading the public `/api/status` response, which reports the same `api_version` as `/api/summary` plus supported `capabilities` (see the [status contract](api-map.md#public-status-and-capabilities--apistatus)). After updating, reopen the client or use its retry action. Maintainers: see the [release flow](releasing.md).

- **Docker:** see the README update section.
- **systemd** (the layout `tools/install.sh` creates, running from a git checkout): push your commits, then from your own machine run `./tools/deploy.sh user@host` (or set `CARLINKO_HOST`; an ssh alias works). It refuses unpushed commits or dirty tracked files, fast-forwards the checkout on the box, then runs `tools/apply.sh`, which restarts only the units whose code changed, checks `/api/status`, and fetches the changed pages back from the running server to compare them with the repo. Override the layout with `CARLINKO_DIR` (default `~/j5-ev-dashboard`), `CARLINKO_PORT`, `CARLINKO_BRANCH` or `CARLINKO_SERVICES`. You can also run `tools/apply.sh` on the box itself. The maintainer pipeline is in [ci-cd.md](ci-cd.md).
- **Hosted Docker (Railway, Render, Fly.io):** point the host at the release you want and trigger a fresh build. Keep the data volume and environment attached. Branch-tracking hosts may redeploy on push, so verify `/api/status` rather than assuming the container restarted.

## Backend logs

The dashboard writes timestamped `INFO`, `WARNING` and `ERROR` lines to stderr: each HTTP response with its method, path and outcome, application failures returned with HTTP 200 as warnings, and caught errors with exception type and source location. Query strings, cookies, request bodies, passwords and tokens are omitted. Set `CARLINKO_LOG_LEVEL=WARNING` to show only warnings and errors (default `INFO`). The telemetry logger runs separately and writes to its own service log.

On Railway the same lines go to stdout as one JSON object each (`level`, `message`, `logger`), because Railway marks every stderr line as an error. This switches on when `RAILWAY_ENVIRONMENT_NAME` is set; `CARLINKO_LOG_FORMAT=json` or `text` forces a format anywhere.

| Deployment | Live logs |
| --- | --- |
| Docker Compose | `docker compose logs -f web logger` |
| systemd | `journalctl -fu carlinko-web -u carlinko-logger` |
| Railway | the service's **Logs** tab |
| `start.sh` or `tools/run.py` | the terminal where it runs |
