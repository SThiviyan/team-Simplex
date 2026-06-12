#!/usr/bin/env python3
"""Translate compose.cloud.yaml + secrets.yaml into a Knative Service manifest
for `gcloud run services replace`.

Contract enforced here:
  - compose.cloud.yaml has a `services:` dict with at least one entry.
  - Exactly one service exposes port 8080 in its `ports:` list. That's the
    ingress.
  - The ingress service declares `build:` (the value is ignored — the workflow
    always builds the root Dockerfile).
  - All other services declare `image:` (used as-is, must be a public image
    or an Artifact Registry reference the runtime SA can pull).
  - `environment:`, `volumes:`, `depends_on:`, `networks:`, `profiles:` are
    silently ignored. Env vars come from secrets.yaml.

Usage:
  compose-to-knative.py \\
    --compose compose.cloud.yaml \\
    --secrets secrets.yaml \\
    --service team-foo \\
    --ingress-image europe-west3-docker.pkg.dev/.../team-foo:sha \\
    --runtime-sa hackathon-runtime@sinpex-cloud-aligator.iam.gserviceaccount.com \\
    > /tmp/service.yaml
"""

from __future__ import annotations

import argparse
import sys

import yaml


INGRESS_PORT = 8080
DEFAULT_INGRESS_CPU = "1"
DEFAULT_INGRESS_MEM = "1Gi"
DEFAULT_SIDECAR_CPU = "500m"
DEFAULT_SIDECAR_MEM = "256Mi"
MAX_CONTAINERS = 10  # Cloud Run limit


def die(msg: str) -> None:
    print(f"compose-to-knative: ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def parse_ports(ports) -> list[int]:
    """Extract integer ports from a compose `ports:` value."""
    if not ports:
        return []
    out: list[int] = []
    for p in ports:
        if isinstance(p, int):
            out.append(p)
        elif isinstance(p, str):
            # Forms: "8080", "8080:8080", "127.0.0.1:8080:8080", "8080/tcp"
            last = p.split(":")[-1].split("/")[0]
            try:
                out.append(int(last))
            except ValueError:
                pass
        elif isinstance(p, dict):
            # Long form: {target: 8080, published: 8080}
            target = p.get("target")
            if isinstance(target, int):
                out.append(target)
    return out


def find_ingress(services: dict) -> str:
    candidates = [
        name for name, spec in services.items()
        if INGRESS_PORT in parse_ports((spec or {}).get("ports", []))
    ]
    if not candidates:
        die(
            f"no service exposes port {INGRESS_PORT}. "
            f"Exactly one service must declare `ports: [\"{INGRESS_PORT}\"]`."
        )
    if len(candidates) > 1:
        die(
            f"multiple services expose port {INGRESS_PORT}: {candidates}. "
            f"Exactly one ingress allowed."
        )
    return candidates[0]


def secrets_env_block(secrets_path: str) -> list[dict]:
    try:
        with open(secrets_path) as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return []
    out: list[dict] = []
    for entry in (data.get("secrets") or []):
        env_name = (entry.get("env") or "").strip()
        secret = (entry.get("secret") or "").strip()
        if not env_name or not secret:
            continue
        out.append({
            "name": env_name,
            "valueFrom": {"secretKeyRef": {"name": secret, "key": "latest"}},
        })
    return out


def container_for(name: str, spec: dict, *, is_ingress: bool,
                  ingress_image: str, secrets_env: list[dict]) -> dict:
    spec = spec or {}
    container: dict = {"name": name}
    if is_ingress:
        if "build" not in spec:
            die(f"ingress `{name}` must declare `build:` (value ignored — "
                f"the workflow builds the root Dockerfile).")
        if "image" in spec:
            die(f"ingress `{name}` must use `build:`, not `image:`.")
        container["image"] = ingress_image
        container["ports"] = [{"containerPort": INGRESS_PORT}]
        container["resources"] = {
            "limits": {"cpu": DEFAULT_INGRESS_CPU, "memory": DEFAULT_INGRESS_MEM},
        }
        if secrets_env:
            container["env"] = secrets_env
    else:
        if "build" in spec:
            die(f"sidecar `{name}` must use `image:`, not `build:`. "
                f"Only the ingress is built; sidecars must use pre-built images.")
        if "image" not in spec:
            die(f"sidecar `{name}` must declare `image:`.")
        container["image"] = spec["image"]
        container["resources"] = {
            "limits": {"cpu": DEFAULT_SIDECAR_CPU, "memory": DEFAULT_SIDECAR_MEM},
        }
    return container


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--compose", required=True)
    ap.add_argument("--secrets", required=True)
    ap.add_argument("--service", required=True)
    ap.add_argument("--ingress-image", required=True)
    ap.add_argument("--runtime-sa", required=True)
    ap.add_argument("--max-instances", default="3")
    ap.add_argument("--min-instances", default="0")
    args = ap.parse_args()

    with open(args.compose) as f:
        compose = yaml.safe_load(f) or {}

    services = compose.get("services") or {}
    if not services:
        die("compose.cloud.yaml has no `services:` declared.")
    if len(services) > MAX_CONTAINERS:
        die(f"{len(services)} services declared; Cloud Run caps at {MAX_CONTAINERS} per instance.")

    ingress_name = find_ingress(services)
    secrets_env = secrets_env_block(args.secrets)

    containers: list[dict] = [container_for(
        ingress_name, services[ingress_name],
        is_ingress=True, ingress_image=args.ingress_image, secrets_env=secrets_env,
    )]
    for name, spec in services.items():
        if name == ingress_name:
            continue
        containers.append(container_for(
            name, spec, is_ingress=False,
            ingress_image=args.ingress_image, secrets_env=secrets_env,
        ))

    manifest = {
        "apiVersion": "serving.knative.dev/v1",
        "kind": "Service",
        "metadata": {
            "name": args.service,
            "annotations": {
                "run.googleapis.com/ingress": "all",
                "run.googleapis.com/launch-stage": "GA",
            },
        },
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "run.googleapis.com/execution-environment": "gen2",
                        "autoscaling.knative.dev/minScale": args.min_instances,
                        "autoscaling.knative.dev/maxScale": args.max_instances,
                    },
                },
                "spec": {
                    "serviceAccountName": args.runtime_sa,
                    "containerConcurrency": 80,
                    "timeoutSeconds": 60,
                    "containers": containers,
                },
            },
            "traffic": [{"percent": 100, "latestRevision": True}],
        },
    }

    yaml.safe_dump(manifest, sys.stdout, sort_keys=False)


if __name__ == "__main__":
    main()
