.PHONY: dev test build check rollback claude clean

# Default: dev-mode docker compose.
dev:
	docker compose up

# Run backend tests. Requires `uv` on host (brew install uv / pip install uv).
test:
	cd backend && uv run pytest

# Build the production image locally for smoke-testing.
build:
	docker build -t hack-app .

# Pre-flight: docker + node + python. NOT gcloud — participants don't need it.
check:
	python3 scripts/preflight.py

# [STAFF ONLY] Roll back Cloud Run to the previous revision in one shot.
# Requires gcloud locally + project access. Participants: ping #hackathon-help.
rollback:
	@SERVICE=$$(basename `git config --get remote.origin.url` .git); \
	REGION=europe-west3; \
	PREV=$$(gcloud run revisions list --service=$$SERVICE --region=$$REGION \
	  --format='value(metadata.name)' --limit=2 | tail -1); \
	echo "Rolling $$SERVICE back to $$PREV..."; \
	gcloud run services update-traffic $$SERVICE \
	  --region=$$REGION --to-revisions=$$PREV=100

claude:
	claude

clean:
	docker compose down -v
	rm -rf backend/.venv frontend/node_modules frontend/dist
