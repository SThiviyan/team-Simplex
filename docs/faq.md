# FAQ — Top failure modes

## "My deploy failed with WIF auth error / 403"

Your repo name doesn't match `sinpexgmbh/team-*`. The WIF allowlist is strict about the prefix.

Fix: either rename your repo (GitHub → Settings → Rename), or create a new repo from the template with a name starting with `team-`.

## "Cloud Run returned 'container failed to start, listening on PORT'"

Your `Dockerfile` `CMD` doesn't bind to `$PORT`. Cloud Run injects `PORT=8080` and expects you to listen on it. Hardcoding another port won't work.

The default template's `CMD` is correct: `uvicorn app.main:app --host 0.0.0.0 --port 8080`. If you swap to another framework, mirror this — listen on `0.0.0.0:8080`.

## "I added a secret to secrets.yaml but it's missing at runtime"

Two possible states:

1. You added to `secrets.yaml`, the deploy ran, but the secret in GCP doesn't have a value yet. → Ping `#hackathon-help`; the Sinpex hackathon team populates secret values out-of-band.
2. You added to `secrets.yaml` but didn't trigger a new deploy after merging. → Re-run the workflow.

## "Cold start is slow (~2s)"

Expected. `min-instances=0` keeps cost down at the price of cold-start latency on the first request after idle.

If it bites during a demo, ping `#hackathon-help` — the Sinpex hackathon team will bump your service to `min-instances=1` for the demo hour, then revert.

## "I broke main; what now?"

The deploy is manual by default, so a broken main doesn't auto-deploy. Your last good revision is still live. To verify:

```
curl https://<your-url>/api/health   # should still 200
```

If you accidentally enabled auto-deploy and a bad push went live: the deploy fails (Cloud Run only switches traffic on a successful deploy), so your previous good revision is still serving. Manual rollback via `#hackathon-help` if needed.

## "I want to add Postgres / Redis / Qdrant"

For ephemeral (data resets on each deploy): drop a `compose.cloud.yaml` at the repo root and the workflow switches to multi-container mode. See `docs/04-compose.md` for the contract — especially the **localhost networking** gotcha.

For persistent: ping `#hackathon-help`. The Sinpex hackathon team provisions Cloud SQL / Memorystore via Terraform, adds the connection string as a secret, and the next deploy mounts it.

## "How do I see my Cloud Run logs?"

Ping `#hackathon-help` — the Sinpex hackathon team can `gcloud logging read` for your service. (Direct access requires GCP credentials we deliberately don't hand out.)

## "I renamed my repo — will the deploy still work?"

Yes, **as long as the new name still starts with `team-`**. The WIF allowlist is prefix-based. Renaming `team-isar` → `team-bavarian-quokka` is fine; renaming to `quadratic` is not.

Your Cloud Run service name will track the repo name, so the old service becomes an orphan — the Sinpex hackathon team cleans it up via `scripts/cleanup-orphans.sh`. Orphan services scale to zero so they don't burn money while waiting.

## "My push didn't trigger a deploy"

That's the default. Open the Actions tab → "Deploy to Cloud Run" → "Run workflow" to deploy manually. Or edit `.github/workflows/deploy.yml` to uncomment the `push:` trigger (see `docs/02-deployment.md`).
