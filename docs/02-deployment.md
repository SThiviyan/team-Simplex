# 02 — Deployment

## How to deploy

1. Go to the **Actions** tab in your repo.
2. Click **"Deploy to Cloud Run"** → **"Run workflow"** → **Run workflow**.
3. Watch the run. The summary block at the end shows your live URL.

End-to-end, <5 minutes on a warm cache.

## Manual trigger is the default

Push to `main` whenever you like — it won't deploy until you hit "Run workflow". This means broken WIP commits don't accidentally go live.

When your team wants push-to-deploy, uncomment two lines in `.github/workflows/deploy.yml`:

```yaml
on:
  workflow_dispatch:
  push:                  # ← uncomment to auto-deploy on main
    branches: [main]
```

## The deploy contract

> Your repo has a `Dockerfile` at the root. When built and run, it listens on `$PORT` (Cloud Run sets `PORT=8080`).

That's it. If `docker build . && docker run -p 8080:8080 <image>` works locally, it will deploy.

For multi-service setups (Redis, Postgres, workers), see [`docs/04-compose.md`](04-compose.md).

## Requesting secrets

The template ships with an empty `secrets.yaml`. Add any env vars your service needs:

```yaml
secrets:
  - env: OPENAI_API_KEY
    secret: team-<yourname>-openai-key
```

Open a PR and ping `#hackathon-help`. The Sinpex hackathon team will create the secret and add your value. Once they confirm it's populated, **re-trigger the deploy manually** — the env var won't appear until the next deploy.

## Watching a deploy

```bash
gh run watch    # stream the live log
gh run view     # pick from recent runs
```

Or watch in the Actions tab. On failure, the run summary prints the most likely cause.

For runtime logs (after deploy), ping `#hackathon-help` — participants don't have GCP console access by design.

## Rolling back

If a bad deploy goes live, ping `#hackathon-help` — the team rolls back to the previous revision in <30s.
