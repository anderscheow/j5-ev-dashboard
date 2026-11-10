# Installation options

The README covers the recommended Docker setup. This page covers the prerequisites and every other way to run the dashboard. All options need a host that stays on 24/7, because the logger polls continuously to build trends and charge history. See [Operations](operations.md) for where to host it.

## Prerequisites

- A CarLinko account with your car on it.
- Docker Compose, **or** Python 3.10+ with `pip install requests websocket-client`.
- Optional: a Google Maps API key for the trip planner and charger map.

No app capture, MITM or decompiling is needed: you just log in with your account.

## Use a second account (recommended)

CarLinko allows **one active session per account**, so logging the dashboard in can sign you out of the official app. Give the dashboard its own account:

1. Create a second CarLinko account (different email).
2. In your main account, go to **Me → Authorisation → +** and authorise the second email to your car.
3. Log the dashboard into the second account and keep the app on the main one.

The in-app Authorisation screen describes Bluetooth sharing. Confirm the second account can also reach the car over the **cloud**: run `python setup.py` with it, and if auto-detect finds the vehicle, you're set. If not, use one account and accept the occasional re-login.

## Docker (recommended)

```bash
docker compose up -d --build     # then open http://localhost:8088
```

The first visit shows a login page; enter your CarLinko email and password and it auto-detects your car (vehicle id, device SN, VIN, plate, model). Prefer the terminal? `docker compose run --rm web python setup.py` does the same interactively. Credentials, token and database persist in `./data`.

- **macOS / Windows:** install [Docker Desktop](https://www.docker.com/products/docker-desktop/), open it, then run the command from the cloned repository.
- **Linux:** `sudo apt install docker.io docker-compose-plugin` (or your distro's equivalent), then the same command.

## Windows without Docker

1. On GitHub choose **Code → Download ZIP** and extract it.
2. Double-click **`start.bat`**. The first run installs Python if you lack it (double-click again afterwards) and two small libraries.
3. Your browser opens the login page; enter your CarLinko email and password.

Keep the window open: it runs the dashboard and the logger. On another OS, `python tools/run.py` does the same.

## Linux, one command (always on)

On a home server, mini PC or Raspberry Pi, this sets up the virtualenv, dependencies, login and both systemd services, so it survives reboots:

```bash
git clone https://github.com/anderscheow/j5-ev-dashboard.git
cd j5-ev-dashboard
./tools/install.sh
```

It asks only your CarLinko login and your country/currency. Everything is scoped to the current user and folder.

| Flag | Effect |
| --- | --- |
| `--tailscale` | private access from your phone anywhere, nothing exposed publicly |
| `--port 9000` | use a different port |
| `--no-service` | set up only; don't install systemd units |

Why Tailscale? The dashboard reads your car's data, so don't port-forward it. Tailscale puts your host and phone on a private network (free for personal use). The reference unit files are [`tools/carlinko-logger.service`](../tools/carlinko-logger.service) and [`tools/carlinko-web.service`](../tools/carlinko-web.service), but `install.sh` generates correct ones for your paths.

## Python (manual)

```bash
pip install requests websocket-client
cd tools
python setup.py                # interactive config, login and car auto-detect
python logger.py --adaptive    # record telemetry (fast when awake, slow when parked)
python server.py 8088          # dashboard at http://<host>:8088
```

To skip the helper, `cp creds.example.json tools/creds.json && chmod 600 tools/creds.json` and fill it in by hand; see the [configuration reference](configuration.md). Never commit `creds.json` or `token.txt`.

## Railway or another single-container host

Set `bash railway_start.sh` as the service start command and attach a persistent `/data` volume. Details are in [Operations](operations.md#one-service-both-processes).

## Let an AI coding agent do it

Paste this into an agent (Claude Code, Cursor, …) running on the machine that will host the dashboard:

```text
Set up the open-source project https://github.com/anderscheow/j5-ev-dashboard on this machine.
Clone it, then bring it up with Docker (docker compose up -d --build). It serves a login page
on http://localhost:8088; tell me the URL when it's running. I'll enter my CarLinko email and
password there myself; do not ask me for them. If Docker isn't available, fall back to the
manual Python steps in docs/installation.md. If the host is reachable from the internet,
remind me to set a dashboard password in the login page's Advanced section.
```
