## RoomRate — Deployment

**Version:** 1.0.0
**Last updated:** 2026-09-13
**Author:** PlanAhead Engineering
**Status:** Round 5.2 — containers, CI, monitoring and backups

Operational companion to `docs/DOCUMENTATION.md`. That file explains what the
system does; this one explains how to run it.

---

### 1. Topology

```text
            load balancer (TLS terminates here)
                    |
        +-----------+-----------+
        |                       |
   frontend (static)        api  (container)
   Angular bundle +         uvicorn, ROOMRATE_PROCESS_ROLE=api
   runtime-config.js        /health  /ready
                                |
                                | durable job rows
                                v
                       Supabase PostgreSQL  <----+
                                ^                |
                                | claim/heartbeat|
                           worker (container)    |
                           python -m api.worker  |
                                |                |
                           python -m scraper ----+
                           (subprocess -> Apify)

   migrate (container, one-shot): alembic upgrade head — runs to completion
   before api and worker are allowed to start.
```

Four runtime pieces, three of them containers from **one image**
(`Dockerfile.api`, differing only in command and `ROOMRATE_PROCESS_ROLE`):

| Piece | Command | Role | Notes |
|-------|---------|------|-------|
| `migrate` | `python -m alembic upgrade head` | — | One-shot; must exit 0 before the rest start |
| `api` | `python -m uvicorn api.main:app` | `api` | Stateless, horizontally scalable, serves `/health` + `/ready` |
| `worker` | `python -m api.worker` | `worker` | **Single instance.** Owns the scheduler and spawns the scraper subprocess |
| frontend | static hosting / nginx | — | Separate image; see `frontend/Dockerfile` |

PostgreSQL is **not** a container here: production uses managed Supabase
Postgres reached through `DATABASE_URL`.

The worker is deliberately one instance. Job claiming is atomic in PostgreSQL
and a stale-job sweeper returns abandoned jobs to `queued`, so a second worker
is safe for correctness — but it doubles Apify spend on the scheduled ticks
and has no capacity justification yet.

---

### 2. Environment variables

`.env.example` is the single source of truth: it documents every variable and
its production meaning. Copy it to `.env` on the host, fill it in, and keep
`.env` out of Git and out of the image (`.dockerignore` excludes it; Compose
injects it at run time via `env_file`).

Production must set at minimum:

```text
APP_ENV=production            # turns on boot validation + HSTS
DATABASE_URL=                 # Supabase pooled connection string
INTERNAL_API_KEY=             # >= 24 chars, not the dev default
SUPABASE_JWT_SECRET=          # or SUPABASE_JWKS_URL / SUPABASE_URL
CORS_ORIGINS=                 # the real frontend origin(s), not localhost
APIFY_TOKEN=
```

`ROOMRATE_PROCESS_ROLE` is the exception: `docker-compose.prod.yml` sets it
per service (`api` / `worker`) and overrides whatever `.env` says, because one
file cannot hold two different roles.

---

### 3. First deploy

```bash
cp .env.example .env          # then fill in the values above
docker compose -f docker-compose.prod.yml up -d --build
```

Compose runs `migrate` first; `api` and `worker` start only after it exits 0
(`depends_on: condition: service_completed_successfully`). **This answers "who
runs `alembic upgrade head` and when": the migrate service, on every `up`,
before any application process starts.** A failed migration leaves the old
containers running rather than exposing new code to an old schema.

Verify:

```bash
docker compose -f docker-compose.prod.yml ps            # migrate = exited (0)
curl -fsS http://localhost:8000/health
curl -fsS http://localhost:8000/ready
docker compose -f docker-compose.prod.yml logs -f worker
python scripts/pre_production_checklist.py --api-base-url https://api.example.com
```

---

### 4. Upgrade

```bash
git pull
ROOMRATE_IMAGE_TAG=$(git rev-parse --short HEAD) \
  docker compose -f docker-compose.prod.yml up -d --build
```

The same ordering applies: the new image is built, `migrate` re-runs (a no-op
when the schema is already at head), then `api` and `worker` are replaced.

Preview a migration before it runs anywhere:

```bash
alembic upgrade head --sql        # prints SQL, applies nothing
```

Roll back by re-deploying the previous tag:

```bash
ROOMRATE_IMAGE_TAG=<previous> docker compose -f docker-compose.prod.yml up -d
```

Migrations are **not** rolled back automatically. Write additive migrations
(add columns/tables, backfill, drop later) so the previous image keeps working
against the new schema; otherwise restore from a backup (§8).

---

### 5. Frontend image

Separate from the three API containers: `frontend/Dockerfile` builds the
Angular bundle with Node 20 and serves it from `nginxinc/nginx-unprivileged`
on **port 8080** (uid 101, no root, no added capabilities). Nothing from the
build stage — sources, `node_modules`, the Node runtime — reaches the final
image.

```bash
docker build -t roomrate-frontend:$(git rev-parse --short HEAD) frontend/
```

**The image has no build arguments, and that is the point.** Angular reads no
environment variables: `index.html` loads `/runtime-config.js`, which sets
`window.__ROOMRATE_CONFIG__`, and `src/environments/environment.ts` reads that
object at startup. So one image tag is promoted from staging to production
unchanged, and only the container's environment differs:

```text
ROOMRATE_API_BASE_URL=https://api.example.gr
ROOMRATE_SUPABASE_URL=https://<project>.supabase.co
ROOMRATE_SUPABASE_ANON_KEY=
ROOMRATE_MAPBOX_TOKEN=
ROOMRATE_CSP_API_ORIGINS=https://api.example.gr wss://api.example.gr
```

`frontend/nginx.conf` (installed as an envsubst template) turns the first four
into the `/runtime-config.js` response, which overrides the empty placeholder
file baked into the bundle. All four are browser-readable by design —
publishable identifiers only, never `INTERNAL_API_KEY` or `DATABASE_URL`.

`ROOMRATE_CSP_API_ORIGINS` is the one that is easy to get wrong. The
Content-Security-Policy has to name the API origin explicitly, and the alerts
socket reuses the same base URL with `http` swapped for `ws` — so **always**
list both schemes, space-separated, even when the API is reverse-proxied onto
the same origin as the frontend. `'self'` covers the `https` origin, but
whether it also implicitly covers the matching `wss:` origin is CSP Level 3
same-origin-upgrade behaviour, not a browser support matrix worth betting on
— the explicit `wss://` origin costs one token and removes the question.
Getting it wrong shows up as blocked requests in the browser console, not as
a container failure.

Also served by that config: SPA fallback to `index.html`, gzip, immutable
caching for the content-hashed bundles with `no-cache` on `index.html` and
`no-store` on `/runtime-config.js`, and the usual security headers
(`X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options`,
`Permissions-Policy`, `Cross-Origin-Opener-Policy`). HSTS is deliberately
**not** set here — it belongs to whatever terminates TLS (§1).

The container `HEALTHCHECK` fetches `/`. Point the load balancer there too;
there is no separate readiness concept for a static bundle.

**Note:** `angular.json` sets `"inlineCritical": false`, so the build never
emits Angular's critical-CSS inlining trick
(`<link media="print" onload="this.media='all'">`) that used to force the CSP
to carry a `'unsafe-hashes'` hash token and nginx to carry a `sub_filter` to
strip the handler before the HTML was served. Both are gone from
`frontend/nginx.conf` now; re-enabling `inlineCritical` would silently bring
back the need for both, so check the built `index.html` for an inline
`onload` before doing that.

---

### 6. Health and readiness

| Endpoint | Auth | Touches DB | Use for |
|----------|------|-----------|---------|
| `GET /health` | none | no | Liveness / restart decisions |
| `GET /ready` | none | yes | Load-balancer traffic admission |

`/health` is deliberately database-free so a database incident does not make
an orchestrator restart otherwise-healthy API processes. `/ready` checks
PostgreSQL (and, in local `all` mode, the in-process scheduler) and returns
**503** when the process should not receive traffic.

Point the load balancer at `/ready`, and container/platform restarts at
`/health` — which is exactly what the image's `HEALTHCHECK` does.

The worker serves no HTTP, so its Compose healthcheck is disabled. Monitor it
by process liveness plus **queue age** (the oldest `queued` job's
`next_attempt_at`); a rising queue age is the signal that the worker is dead
or wedged, and no HTTP probe can tell you that.

---

### 7. Logs and monitoring

**Structured logs.** Set `LOG_FORMAT=json` in production: one JSON object per
line with timestamp/level/logger/message, the structured extras the code
already attaches (`job_id`, `account_id`, `config_id`, …) and the per-request
`X-Request-ID`. Containers log to stdout, so any aggregator that reads
`docker logs` works unchanged.

**Request correlation.** Every response carries `X-Request-ID` (echoed from
the request when the client sends one). Quote it in incident reports — it ties
a user-visible failure to its log lines.

**Sentry (optional).** Set `SENTRY_DSN` and the API reports unhandled
exceptions tagged with `APP_ENV`. Leave it blank and `sentry_sdk` is never
even imported. `send_default_pii=False` is hard-coded: request bodies,
headers, cookies and user identifiers are never sent. `SENTRY_TRACES_SAMPLE_RATE`
defaults to `0` (errors only); raise it to 0.05–0.2 when you need latency
traces and accept the quota cost.

**Still missing** (see `docs/DOCUMENTATION.md` §13): worker/queue metrics —
oldest-ready-job age, retry rate, terminal-failure rate, provider latency.
Until those exist, queue age has to be checked by query or by eye.

---

### 8. Backups and restore

**Primary: Supabase managed backups.** Supabase takes automated backups of the
project's database; daily backups and point-in-time recovery depend on the
plan, so check the project's Database → Backups page and confirm what the
current plan actually retains before relying on it. Restores are performed
from the Supabase dashboard.

**Secondary: portable dumps.** Provider-managed backups cannot be restored
anywhere but the provider, so keep an independent copy. Run it from a host
that has the repository checked out with its Python requirements installed
(`pip install -r requirements.txt`) and the `pg_dump` binary on `PATH` — the
`roomrate-backend` image itself is **not** a valid place to run this from: it
ships neither `pg_dump` nor a `.env` file (Compose injects environment into
the container at run time via `env_file:`; there is no `.env` inside the
image to read, and nothing to `exec` a backup into). The Compose host itself,
with `.env` sourced or the individual `PG*` variables exported directly, is
the usual choice:

```bash
python scripts/backup_db.py                      # -> output/backups/roomrate_<db>_<utc>.dump
python scripts/backup_db.py --output-dir /srv/backups
python scripts/backup_db.py --dry-run            # prints the command, runs nothing
```

`pg_dump` (Debian/Ubuntu package: `postgresql-client`) is what the script
actually shells out to, and it checks the binary is on `PATH` before doing
anything else. It uses custom format with `--no-owner --no-privileges`, so a
production dump restores into a database whose roles differ. Credentials are
passed to `pg_dump` through `PG*` environment variables, never on the command
line — nothing in `ps` output or in the script's own logging exposes the
password.

**Restore:**

```bash
pg_restore --no-owner --no-privileges --clean --if-exists \
  -d "$DATABASE_URL" output/backups/roomrate_postgres_20260913T090000Z.dump
```

Keep at least 7 daily plus 4 weekly dumps and **test a restore quarterly** —
an untested backup is a hypothesis, not a backup.

**Retention already handled in code:** per-job scraper CSVs under
`output/scrapes/` are pruned daily by `SCRAPE_CSV_RETENTION_DAYS` (structured
results live in PostgreSQL); `scout_cache` rows are auto-deleted after a week;
notifications and pricing audits are bounded by `NOTIFICATION_RETENTION_DAYS`
and `PRICE_RECOMMENDATION_AUDIT_RETENTION_DAYS`.

---

### 9. Security posture already enforced in code

These are implemented, not aspirational — see `.env.example` for the knobs.

- **Boot validation.** With `APP_ENV=production` the process refuses to start
  when `DATABASE_URL` is empty, `ROOMRATE_PROCESS_ROLE=all`, the API role
  still uses the public dev `INTERNAL_API_KEY` (or one under 24 characters),
  no Supabase token-verification source is configured, retry limits are
  invalid, or the worker has scheduling disabled. All violations are reported
  in one startup error.
- **HSTS and security headers** on every response; HSTS is enabled by
  `APP_ENV=production`.
- **Rate limiting** (`RATE_LIMIT_PER_MINUTE`, default 30) on the expensive
  mutation POSTs, keyed by account id and falling back to client IP. It is
  counted **per process**, so it is approximate behind multiple API replicas —
  size it accordingly, or move it to the edge.
- **Quotas** per account: concurrent and daily scrape jobs, daily price
  recommendations — the spend controls, distinct from the rate limit.
- **Token verification is local** (JWKS or HS256), with no per-request
  round-trip to Supabase.
- **No secrets in the image or the browser.** `.env` is excluded from the
  build context; the browser only ever receives `frontend/public/runtime-config.js`
  values (`apiBaseUrl`, `supabaseUrl`, `supabaseAnonKey`, `mapboxToken`).

---

### 10. Not yet — decisions still owed

- **Billing / subscriptions.** Deferred: it needs a pricing model first (per
  property? per tracked competitor? per scrape?). No code, no provider, no
  schema. Apify spend per account is already bounded by the quota settings,
  which is the input a pricing model will need.
- **GDPR legal review.** The frontend scaffolds the policy pages; the actual
  texts, the data-processing agreement with Supabase/Apify/Anthropic, and the
  retention defaults need legal sign-off. Deletion and export flows are not
  implemented.
- **Hosting provider.** The stack is plain Docker Compose, so it runs on a
  single VM today and ports to ECS/Fly/Render/Kubernetes without code changes.
  Nobody has chosen one, and the choice decides TLS termination, secret
  storage, log aggregation and how the single worker is kept singular.
- **CI deploys nothing.** `.github/workflows/roomrate-ci.yml` (at the repository root, one level above room_project2, because GitHub only reads workflows from the root) tests and builds only;
  there is no automated deployment, image registry push, or staging
  environment. Deploys are the manual runbooks above.
