# 04 — Compose-mode deployment

Most teams will ship a single container — the default `Dockerfile` mode. If your team needs sidecar services (a Redis cache, a worker, a queue, a vector DB), drop a `compose.cloud.yaml` at the repo root and the deploy workflow shifts into **compose mode**.

Under the hood: one Cloud Run service per repo, multiple containers per instance. They share the same instance lifecycle and **share localhost** for networking.

## Contract

Write `compose.cloud.yaml` at the repo root:

```yaml
services:
  app:                          # ingress — exactly one service
    build: .                    # we build the root Dockerfile
    ports: ["8080"]             # must expose 8080

  redis:                        # sidecar — pre-built image
    image: redis:7-alpine

  worker:                       # another sidecar
    image: ghcr.io/your-org/worker:v1
```

The rules the deploy script enforces:

1. **Exactly one service exposes port 8080.** That's the ingress. Anything else is a sidecar.
2. **The ingress must declare `build:`** (any value — it's ignored; we always build the root `Dockerfile`).
3. **Sidecars must declare `image:`** (no `build:`). Use a public registry (Docker Hub, ghcr.io) or a tag in our Artifact Registry.
4. **Maximum 10 services** (Cloud Run instance container limit).

Anything not in that list — `volumes`, `depends_on`, `networks`, `profiles`, `environment` — is **silently ignored**.

## The localhost gotcha

This is the single most important behavioral difference from local `docker compose`.

```python
# WRONG (works locally via compose service-name DNS, breaks on Cloud Run):
redis_url = "redis://redis:6379"

# RIGHT (works on Cloud Run, also works locally with `network_mode: host`
# or via 127.0.0.1):
redis_url = "redis://localhost:6379"
```

On Cloud Run multi-container, the containers in an instance share `localhost`. There is no service-name DNS. Bind to `localhost:<port>` for inter-container traffic.

Your local `compose.yaml` (the one `make dev` uses) can still use service-name DNS; that file is unrelated to deployment.

## What you give up vs single-container mode

| Thing | Status |
|---|---|
| Per-container env vars | ✗ — secrets in `secrets.yaml` mount only on the ingress |
| Volumes | ✗ — Cloud Run has no shared FS between containers |
| Persistent data | ✗ — all sidecar storage is ephemeral; restart wipes it |
| Inter-container startup order (`depends_on`) | ✗ — all start together; design retries into your ingress |
| External traffic on sidecar ports | ✗ — only ingress is externally reachable |
| Multiple ingress containers | ✗ — exactly one per service |

If you need persistent data, **don't** rely on a sidecar; ping `#hackathon-help` and the Sinpex hackathon team will provision a Cloud SQL instance + connection string via Terraform.

## How secrets work in compose mode

Same as single mode: declare in `secrets.yaml`, the Sinpex hackathon team populates the value, the deploy mounts as env vars **on the ingress container only**. Sidecars don't see them — by design (most public images don't need your secrets, and exposing keys to a 3rd-party image surface is unnecessary).

If a sidecar genuinely needs a credential, ping `#hackathon-help` about extending the deploy script to mount it onto a specific named container.

## Resource sizing

The deploy script sets safe defaults:

| Container | CPU | Memory |
|---|---|---|
| ingress | 1 | 1 GiB |
| each sidecar | 0.5 | 256 MiB |

That fits a typical app + cache + worker inside one Cloud Run instance comfortably. If you're hitting OOM on a sidecar, that's a sign to bring the data layer out of process — ping `#hackathon-help` about Memorystore (Redis) or Cloud SQL (Postgres).

## Testing locally

`docker compose -f compose.cloud.yaml up` will run all the services. To match Cloud Run's localhost-only networking, either:

- Use `network_mode: host` on Linux, or
- Add `extra_hosts: ["redis:127.0.0.1"]` to fake out DNS during dev, or
- Just always reference services as `localhost:<port>` in your code, and rely on compose's default network resolving `localhost` correctly when all services are bound to host.

The simplest discipline: **write your code as if you're on Cloud Run**. `localhost:<port>` everywhere.

## Example: counter app with Redis sidecar

`compose.cloud.yaml`:

```yaml
services:
  app:
    build: .
    ports: ["8080"]
  redis:
    image: redis:7-alpine
```

`backend/app/main.py` snippet:

```python
import redis.asyncio as redis_async
r = redis_async.from_url("redis://localhost:6379")

@app.get("/api/count")
async def count():
    n = await r.incr("counter")
    return {"counter": n}
```

`pyproject.toml`: add `redis>=5.0` to dependencies.

Deploy: Actions tab → "Deploy to Cloud Run" → "Run workflow". Visit `<url>/api/count` repeatedly; the counter increments. Restart the service — counter resets (ephemeral).

## Failure modes

| Symptom | Cause |
|---|---|
| Deploy step prints `no service exposes port 8080` | Your `compose.cloud.yaml` doesn't have a service with `ports: ["8080"]`. |
| Deploy step prints `multiple services expose port 8080` | More than one service has `ports: ["8080"]`. Pick exactly one ingress. |
| Deploy step prints `sidecar X must use image:, not build:` | A sidecar has `build:`. Only the ingress is built from source; sidecars must use a pre-built image. |
| Revision fails to start, `image pull error` on a sidecar | The sidecar's `image:` is private or doesn't exist. Use a public image or mirror it into our Artifact Registry. |
| App can't reach the sidecar | You're using the service-name (`redis`) instead of `localhost`. Re-read the localhost gotcha above. |
