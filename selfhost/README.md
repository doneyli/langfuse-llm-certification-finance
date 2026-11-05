# Self-host Langfuse (local evaluation)

A curated [Langfuse v4](https://langfuse.com) Docker Compose stack for running
this deployment gate against a self-hosted Langfuse instance instead of
[Langfuse Cloud](https://cloud.langfuse.com).

**Versions.** Langfuse web + worker are pinned to the `:4` major line.
Langfuse v4 needs ClickHouse **25.12 or newer** (the compose pins upstream's
26.3), Postgres 15+ and Redis 7+. This repo's Python SDK v5 needs Langfuse
**4.59.0 or newer**. Langfuse v3 receives security patches only until the end
of January 2027 — a fresh `up` needs no migration; an existing v3 volume set is
migrated by the v4 worker on first boot (back it up first, see the
[v3 → v4 upgrade guide](https://langfuse.com/self-hosting/upgrade/upgrade-guides/upgrade-v3-to-v4)).

This is meant for local evaluation and demos — not production. For a hardened
production deployment (Kubernetes, managed databases, external object storage,
TLS, SSO), follow the [official self-hosting guide](https://langfuse.com/self-hosting).

## Prerequisites

- Docker 24+ and Docker Compose v2
- ~8 GB free RAM recommended (ClickHouse + Postgres + the two Langfuse
  containers each want 1–2 GB); 4 GB will boot but expect slowness and swap
- Free ports on the host: `3000` (Langfuse UI), `3030` (worker), `5432`
  (Postgres), `6379` (Redis), `8123` / `9000` (ClickHouse), `9090` / `9091`
  (MinIO API / console). Of these, only `3000` and `9090` are bound on all
  interfaces — the rest are localhost-only (see [Network exposure](#network-exposure)).

## Start the stack

From the repo root:

```bash
docker compose -f selfhost/docker-compose.yml up -d
```

First boot takes a couple of minutes (ClickHouse migrations run on the worker).
Watch progress with:

```bash
docker compose -f selfhost/docker-compose.yml logs -f langfuse-web langfuse-worker
```

## Create a project and get API keys

1. Open <http://localhost:3000>.
2. Sign up (the first account becomes the org owner).
3. Create an **Organization**, then a **Project**.
4. **Settings → API Keys → Create new API keys**. Copy the public + secret keys.
5. Populate the project `.env` (one level up):

   ```bash
   # ../.env
   LANGFUSE_PUBLIC_KEY=pk-lf-...
   LANGFUSE_SECRET_KEY=sk-lf-...
   LANGFUSE_BASE_URL=http://localhost:3000
   ```

Then jump back to the main [README Quick Start](../README.md#quick-start) and
continue from step 2 (Load Sample Dataset).

## Stop / reset

```bash
# Stop containers but keep data
docker compose -f selfhost/docker-compose.yml down

# Stop and wipe all data (Postgres, ClickHouse, MinIO, Redis volumes)
docker compose -f selfhost/docker-compose.yml down -v
```

ClickHouse query/server logs accumulate in the `langfuse_clickhouse_logs`
volume and are rarely needed for a local demo. On a long-running stack,
periodically wipe with `docker volume rm langfuse_clickhouse_logs` (after a
plain `down`) or just use `down -v` to wipe everything.

## Optional: code evaluators

Langfuse-managed **code evaluators** (deterministic scorers that run inside
Langfuse) need a dispatcher on the worker. The compose ships two commented
lines in the worker environment:

```yaml
# LANGFUSE_CODE_EVAL_DISPATCHER: insecure-local
# QUEUE_CONSUMER_CODE_EVAL_EXECUTION_QUEUE_IS_ENABLED: "true"
```

`insecure-local` runs **TypeScript/JavaScript** code evaluators only, in the
worker process with no sandbox — fine for a laptop, not for shared hosts.
Python code evaluators need the `aws-lambda` dispatcher. Without a dispatcher,
`monitor_production.py` remains the way to run the deterministic checks.

## Security notes

### Credentials

The compose file ships with placeholder credentials marked `# CHANGEME`
(Postgres password, ClickHouse password, Redis auth, MinIO secret, NextAuth
secret, encryption key, salt). They are fine for a laptop demo but **must** be
replaced before exposing the stack to anything beyond localhost.

To generate the cryptographic values:

```bash
openssl rand -hex 32     # ENCRYPTION_KEY
openssl rand -base64 32  # NEXTAUTH_SECRET, SALT
```

Override them by exporting the corresponding env vars before `docker compose
up`, or by writing a `selfhost/.env` file (Docker Compose loads it
automatically).

### Network exposure

The compose binds:

- `0.0.0.0:3000` — Langfuse web UI
- `0.0.0.0:9090` — MinIO S3 API (browser presigned URLs hit this directly)
- `127.0.0.1:*` for everything else: Postgres (5432), ClickHouse (8123, 9000),
  Redis (6379), langfuse-worker (3030), MinIO console (9091)

If the host is reachable from other machines, restrict inbound traffic on the
host firewall to ports `3000` and `9090` only — or change the `ports:` entries
to `127.0.0.1:3000:3000` / `127.0.0.1:9090:9000` if you only need local access.

## Where this file came from

`docker-compose.yml` is vendored from Langfuse's official v4 compose at
<https://github.com/langfuse/langfuse/blob/main/docker-compose.yml>, shipped
here so the deployment gate has a known-good local stack.

To re-sync when Langfuse publishes a new version:

```bash
curl -fsSL https://raw.githubusercontent.com/langfuse/langfuse/main/docker-compose.yml \
  -o selfhost/docker-compose.yml
# Then re-apply the local header comment and the commented code-evaluator
# lines, and (for major bumps) check the `:4` tag on langfuse-web and
# langfuse-worker and the ClickHouse minimum.
```

Track upstream releases at <https://github.com/langfuse/langfuse/releases>.
