# 01 — Setup

## Onboarding (Day 0 — pick someone in your team to do this)

One person per team creates the repo; everyone else joins as a collaborator.

1. From this repo page, **click "Use this template" → "Create a new repository"**.
2. **Owner:** `sinpexgmbh`. **Repository name:** starts with `team-` (e.g. `team-isar`, `team-bavarian-quokka` — pick anything as long as the prefix is `team-`).
3. **Visibility:** Private.
4. **Click "Create repository"**.
5. In your new repo: **Settings → Collaborators and teams → Add people** → invite your 3 teammates with `Write` access.
6. Clone locally:
   ```bash
   gh repo clone sinpexgmbh/team-<yourname>
   cd team-<yourname>
   ```
7. Carry on with the toolchain checks below.

**You can rename your repo later** — as long as the new name still starts with `team-`, deploys keep working. Your old Cloud Run service keeps running until the Sinpex hackathon team removes it.

**You do NOT need:** a GCP account, gcloud, terraform, or any service-account JSON files. Authentication happens server-side via Workload Identity Federation.

---

## What you need on your laptop

| Tool | Version | Purpose |
|---|---|---|
| Docker Desktop | latest | Runs `make dev` |
| Node | ≥ 20 | Frontend tooling (vite, tsc) |
| Python | ≥ 3.12 | Backend (FastAPI) |
| git | any | Push your code |
| `gh` | any | Optional — convenient for cloning |
| `uv` | any | Optional — only needed to run `make test` on host |
| Claude Code | latest | Recommended pair-programmer |

You do **NOT** need:
- `gcloud` (deploys run server-side in GitHub Actions)
- `terraform` (the Sinpex hackathon team runs IaC)
- A GCP account or project access

Install links:
- Docker Desktop — https://www.docker.com/products/docker-desktop/
- Node 20 — https://nodejs.org/ or via `nvm install 20`
- Python 3.12 — https://www.python.org/downloads/ or via `pyenv install 3.12`
- gh — https://cli.github.com/
- uv — https://docs.astral.sh/uv/getting-started/installation/ or `brew install uv` / `pip install uv`
- Claude Code — https://docs.claude.com/en/docs/claude-code/setup

## Verify

```bash
make check
```

You should see green `OK` for docker / node / python / git. Missing tools print `FAIL` with what to do.

## First run

```bash
make dev
```

After a minute or two:
- Frontend: http://localhost:5173 (with hot reload)
- Backend health: http://localhost:8080/api/health
- Backend search: http://localhost:8080/api/search?q=hello

First `make dev` is slow (~2 min) because it installs Python + Node deps inside the containers. Subsequent runs are fast (named volumes cache `.venv` and `node_modules`).

## Run backend tests

```bash
make test           # equivalent to: cd backend && uv run pytest
```

Requires `uv` on host (`brew install uv` or `pip install uv`).

## Trouble?

See `docs/faq.md` for the top failure modes.
