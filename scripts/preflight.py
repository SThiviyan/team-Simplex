#!/usr/bin/env python3
"""Pre-flight check for the hackathon template.

Verifies the LOCAL DEV toolchain — what participants need on their laptop:
  - Docker (engine running)
  - Node 20+
  - Python 3.12+
  - git
  - gh (optional — for `gh repo create-from-template`)
  - uv (optional — needed for `make test` / running backend tests on host)

DOES NOT require gcloud or Terraform. Deployments run in GitHub Actions;
participants never touch GCP credentials.

Prints a green checklist or a red error list. Exit 0 if all required tools
pass; exit 1 otherwise.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass


GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
RESET = "\033[0m"


@dataclass
class Check:
    name: str
    required: bool
    ok: bool
    detail: str


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 127, ""


def check_command(name: str, cmd: list[str], required: bool) -> Check:
    if not shutil.which(cmd[0]):
        return Check(name, required, False, "not installed")
    rc, out = _run(cmd)
    if rc != 0:
        return Check(name, required, False, f"command failed: {out[:80]}")
    return Check(name, required, True, out.splitlines()[0] if out else "ok")


def check_docker() -> Check:
    if not shutil.which("docker"):
        return Check("docker", True, False, "not installed")
    rc, out = _run(["docker", "info"])
    if rc != 0:
        return Check("docker", True, False, "engine not running (start Docker Desktop?)")
    rc, out = _run(["docker", "--version"])
    return Check("docker", True, True, out)


def check_node() -> Check:
    if not shutil.which("node"):
        return Check("node>=20", True, False, "not installed")
    rc, out = _run(["node", "--version"])
    m = re.match(r"v(\d+)", out)
    if not m or int(m.group(1)) < 20:
        return Check("node>=20", True, False, f"found {out}, need 20+")
    return Check("node>=20", True, True, out)


def check_python() -> Check:
    rc, out = _run([sys.executable, "--version"])
    m = re.match(r"Python (\d+)\.(\d+)", out)
    if not m or (int(m.group(1)), int(m.group(2))) < (3, 12):
        return Check("python>=3.12", True, False, f"found {out}, need 3.12+")
    return Check("python>=3.12", True, True, out)


def main() -> int:
    checks = [
        check_docker(),
        check_node(),
        check_python(),
        check_command("git", ["git", "--version"], required=True),
        check_command("gh", ["gh", "--version"], required=False),
        check_command("uv", ["uv", "--version"], required=False),
    ]

    print()
    fails: list[Check] = []
    for c in checks:
        if c.ok:
            print(f"  {GREEN}OK{RESET}   {c.name:<14} {DIM}{c.detail}{RESET}")
        elif c.required:
            print(f"  {RED}FAIL{RESET} {c.name:<14} {c.detail}")
            fails.append(c)
        else:
            print(f"  {YELLOW}WARN{RESET} {c.name:<14} {c.detail} {DIM}(optional){RESET}")
    print()

    if fails:
        print(f"{RED}{len(fails)} required check(s) failed.{RESET} See docs/01-setup.md.")
        return 1

    print(f"{GREEN}All required checks passed.{RESET} You're ready to `make dev`.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
