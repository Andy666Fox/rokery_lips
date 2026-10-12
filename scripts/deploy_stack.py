"""Deploy the three-service stack; keep the current website until candidates pass."""

import argparse
import json
import shutil
import subprocess
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from scripts.bootstrap_monitoring import prepare_agent
from scripts.release import copy_source, source_files, verify
from scripts.stack import configuration

SITE = Path("/opt/synchronisica/repo")
BACKUPS = Path("/var/backups/synchronisica")
NEW_SERVICES = ["bot", "mysql", "beszel-agent", "beszel", "docker-proxy", "monitor-probes"]


def run(command: list[str], cwd: Path) -> str:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"Command failed ({command[0]}): {result.stderr[-1500:]}")
    return result.stdout.strip()


def compose(source: Path, directory: Path) -> list[str]:
    return configuration(directory, source, SITE)


def smoke_app(image: str, cwd: Path) -> None:
    name = "synch-website-candidate"
    run(["docker", "run", "-d", "--name", name, "--network", "none", image], cwd)
    try:
        check = (
            "import urllib.request,json; "
            "base='http://127.0.0.1:8000'; "
            "assert urllib.request.urlopen(base+'/').status==200; "
            "assert json.load(urllib.request.urlopen(base+'/api/health'))['status']=='ok'; "
            "assert json.load(urllib.request.urlopen(base+'/api/credits'))"
        )
        for attempt in range(15):
            try:
                run(["docker", "exec", name, "python", "-c", check], cwd)
                break
            except RuntimeError:
                if attempt == 14:
                    raise
                time.sleep(1)
    finally:
        run(["docker", "rm", "-f", name], cwd)


def deploy(release: Path, domain: str) -> None:
    release = release.resolve()
    if not release.is_relative_to(Path("/opt/synchronisica/releases")):
        raise ValueError("Release must be below /opt/synchronisica/releases")
    files = source_files(release)
    if not (SITE / ".env.integration").is_file():
        raise FileNotFoundError("Upload private .env.integration before deployment")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = BACKUPS / f"stack-{stamp}"
    backup.mkdir(mode=0o700)
    saved: list[str] = []
    for name in files:
        previous = SITE / name
        if previous.is_file():
            target = backup / "site" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(previous, target)
            saved.append(name)
    images = {}
    for service in ("app", "caddy", "bot", "monitor-probes"):
        container = run(
            [
                "docker",
                "ps",
                "-q",
                "--filter",
                "label=com.docker.compose.project=synchronisica-prod",
                "--filter",
                f"label=com.docker.compose.service={service}",
            ],
            SITE,
        )
        if not container:
            continue
        images[service] = run(["docker", "inspect", container, "--format", "{{.Image}}"], SITE)
        run(
            ["docker", "tag", images[service], f"synchronisica-rollback:{stamp.lower()}-{service}"],
            SITE,
        )
    (backup / "state.json").write_text(json.dumps({"saved": saved, "images": images}, indent=2))
    command = compose(release, release)
    print("Validating configuration and building candidates", flush=True)
    run([*command, "config", "--quiet"], release)
    run([*command, "build", "app", "bot", "monitor-probes"], release)
    smoke_app("synchronisica-prod-app", release)
    # New directories do not affect the running site's old bind mounts.
    for name in files:
        if name.startswith("services/") or name == "deploy/mysql.cnf":
            copy_source(release / name, SITE / name)
    staged = compose(release, SITE)
    try:
        print("Starting database and private monitoring hub", flush=True)
        run(
            [
                *staged,
                "up",
                "-d",
                "--wait",
                "--wait-timeout",
                "120",
                "mysql",
                "beszel",
                "docker-proxy",
                "monitor-probes",
            ],
            release,
        )
        run(
            [
                *staged,
                "run",
                "--rm",
                "--no-deps",
                "bot",
                "alembic",
                "-c",
                "services/random_telegram_bot/alembic.ini",
                "upgrade",
                "head",
            ],
            release,
        )
        run([*staged, "up", "-d", "--wait", "bot"], release)
        prepare_agent(SITE / ".env.integration")
        run([*staged, "up", "-d", "beszel-agent"], release)
        print("Publishing verified service configuration", flush=True)
        for name in files:
            copy_source(release / name, SITE / name)
        active = compose(SITE, SITE)
        # Compose passes credentials through the environment, never command-line arguments.
        run(
            [
                *active,
                "run",
                "--rm",
                "--no-deps",
                "caddy",
                "caddy",
                "validate",
                "--config",
                "/etc/caddy/Caddyfile",
                "--adapter",
                "caddyfile",
            ],
            SITE,
        )
        run([*active, "up", "-d", "--wait", "--wait-timeout", "120"], SITE)
        results: list[dict[str, str | int | bool]] = []
        for attempt in range(15):
            try:
                results = verify(domain, SITE)
                for path in ("/api/random/ready", "/api/random/db", "/monitoring/"):
                    with urllib.request.urlopen(f"https://{domain}{path}", timeout=10) as response:
                        assert response.status == 200
                break
            except (OSError, RuntimeError):
                if attempt == 14:
                    raise
                time.sleep(2)
        print(
            json.dumps({"backup": str(backup), "website_checks": results, "stack": "ready"}),
            flush=True,
        )
    except Exception:
        print("Deployment verification failed; restoring previous website", flush=True)
        if "bot" not in images:
            run([*staged, "stop", *NEW_SERVICES], release)
        for name in saved:
            copy_source(backup / "site" / name, SITE / name)
        for service, image in images.items():
            if service != "caddy":
                run(["docker", "tag", image, f"synchronisica-prod-{service}"], SITE)
        old = [
            "docker",
            "compose",
            "-p",
            "synchronisica-prod",
            "--env-file",
            ".env",
            "--env-file",
            ".env.monitor",
            "-f",
            "compose.yaml",
            "-f",
            "compose.prod.yaml",
            "-f",
            "compose.monitor.yaml",
        ]
        if "bot" in images:
            run([*compose(SITE, SITE), "up", "-d", "--wait"], SITE)
        else:
            run([*old, "up", "-d", "--no-deps", "app", "caddy"], SITE)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", type=Path)
    parser.add_argument("--domain", default="dedvnutri.space")
    args = parser.parse_args()
    deploy(args.release, args.domain)


if __name__ == "__main__":
    main()
