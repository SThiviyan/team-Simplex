# syntax=docker/dockerfile:1.7

# ---- Stage 1: build the frontend ----
FROM node:20-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package*.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: install the backend with uv ----
FROM python:3.12-slim AS backend

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

RUN pip install --no-cache-dir uv==0.7.5

WORKDIR /app/backend

# Install dependencies first for layer caching.
COPY backend/pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --no-install-project

# Copy the rest of the source and install the project itself.
COPY backend/ ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev

# Bundle the built frontend so FastAPI can serve it at /.
COPY --from=frontend-build /frontend/dist /app/frontend/dist

ENV PATH="/app/backend/.venv/bin:${PATH}"

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
