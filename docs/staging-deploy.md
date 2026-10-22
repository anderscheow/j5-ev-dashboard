# Railway staging dashboard

The `staging` branch of `anderscheow/j5-ev-dashboard` is the source for a
separate Railway project and service named `tiggo-7-phev-staging`. A merge
into that branch pushes a new commit, which Railway deploys from its GitHub
source. `main` and the existing production deployment are independent.

The staging service uses the repository's `Dockerfile`. `server.py` listens on
Railway's `PORT` and Docker checks the public `/api/status` endpoint. The
service configuration lives in `.railway/railway.ts` using Railway's current
Infrastructure as Code format. The `staging-check` GitHub workflow checks
source, builds the image, and exercises the maintenance API on a non-default
port. Enable **Wait for CI** on the Railway service so a failing workflow
prevents a deployment.

Staging runs with `CARLINKO_DEMO=1`. It serves realistic sample telemetry and
the full Malaysian maintenance catalogue without vehicle credentials or a
logger. It has no production database or volume. No real vehicle control
should be configured there. Service-centre results still use the published
directory when a location is supplied.

To review infrastructure changes, run `railway config plan` from a checkout
linked to the staging Railway project. Apply them with `railway config apply`.
The Railway service must stay connected to the fork's `staging` branch and
have automatic deployments and Wait for CI enabled. Generate a Railway domain
for the service if a public preview is needed.
