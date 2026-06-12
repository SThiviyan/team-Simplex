# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Sinpex Hackathon 2026 federated-search template: FastAPI backend + Vite/React/Tailwind frontend, deployed as a single container to Cloud Run. The template is intentionally minimal — the stub providers and orchestrator are meant to be replaced, not preserved.

## Commands

```bash
make dev      # local dev via docker compose: frontend http://localhost:5173 (hot reload), backend http://localhost:8080
make test     # backend tests (runs `uv run pytest` in backend/; requires uv on host)
make build    # build the production Docker image locally
make check    # verify toolchain (docker/node/python/git)
make clean    # docker compose down -v + remove .venv/node_modules/dist
```

Run a single backend test: `cd backend && uv run pytest tests/test_smoke.py::test_name`

Lint backend: `cd backend && uv run ruff check .` (line-length 100, py312). Pytest runs with `asyncio_mode = "auto"` — async tests need no decorator.

Frontend (inside `frontend/`): `npm run dev`, `npm run build` (runs `tsc --noEmit` first, so type errors fail the build). When running the frontend outside compose, set `VITE_PROXY_TARGET=http://localhost:8080` — the vite proxy defaults to the compose service name `http://backend:8080`.

Deploy: GitHub Actions tab → "Deploy to Cloud Run" → Run workflow. Manual-trigger only by default (push to `main` does NOT deploy). Watch with `gh run watch`.

## Architecture

Request flow: browser → FastAPI (`backend/app/main.py`) → `FederatedSearch` orchestrator (`backend/app/search/orchestrator.py`) → all `SearchProvider`s in parallel (`asyncio.gather`, provider failures are non-fatal) → results merged and sorted by score.

- `backend/app/search/base.py` — `SearchProvider` ABC and `SearchResult` pydantic model (`score` must be 0.0–1.0). New providers implement `async def search(query, limit) -> list[SearchResult]`.
- Providers are wired in the FastAPI `lifespan` in `main.py` (stored on `app.state.search`). To add a provider: implement it under `backend/app/search/providers/`, then add it to the list in `lifespan`.
- `frontend/src/api.ts` mirrors the `SearchResult`/response shape in TypeScript — keep the two in sync if the API shape changes.
- Config via pydantic-settings in `backend/app/config.py` (reads `.env`).

Two distinct runtime layouts — don't confuse them:
- **Local dev** (`compose.yaml`): two containers, vite dev server proxies `/api/*` to the backend.
- **Production** (`Dockerfile`): multi-stage build bundles `frontend/dist` into the backend image; FastAPI serves the static frontend at `/`. One container, port 8080.

## Deploy contract (don't break these)

- The root `Dockerfile` must build and listen on `$PORT` (Cloud Run sets `PORT=8080`). Sanity check: `docker build -t x . && docker run -p 8080:8080 x` must respond on `/api/health`.
- One Cloud Run service per repo. Don't split into multiple services.
- Secrets (API keys etc.) go through `secrets.yaml` (`env:` + `secret:` entries), not hardcoded env vars. The hackathon staff populates values; a manual re-deploy is needed before they appear.
- Multi-container (Redis sidecar, workers): add `compose.cloud.yaml` at the repo root and the workflow switches to compose mode. Rules: exactly one ingress service with `ports: ["8080"]` and `build:`; sidecars use `image:` only; max 10 services; `volumes`/`depends_on`/`environment` are ignored; secrets mount on the ingress only; all sidecar storage is ephemeral. **Inter-container traffic uses `localhost:<port>`, never compose service names** — Cloud Run containers share localhost and have no service-name DNS (see `docs/04-compose.md`).
