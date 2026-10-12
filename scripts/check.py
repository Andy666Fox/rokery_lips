"""Run repeatable checks; network and browser checks are explicit options."""

import argparse
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*command: str) -> None:
    print("+ " + " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", action="store_true", help="Query dependency advisories")
    parser.add_argument("--browser", action="store_true", help="Test a running site in Chrome")
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--docker", action="store_true", help="Validate Compose and build the app")
    args = parser.parse_args()
    run("uv", "lock", "--check")
    for command in (
        ("ruff", "check", "."),
        ("ruff", "format", "--check", "."),
        ("pytest", "-q"),
    ):
        run("uv", "run", "--locked", *command)
    run("uv", "run", "--locked", "--group", "browser", "ty", "check")
    run("node", "--check", "services/website/html/app.js")
    if args.audit:
        run("uv", "run", "--locked", "pip-audit", "--progress-spinner", "off")
    if args.docker:
        for name in (
            "MYSQL_PASSWORD",
            "MYSQL_ROOT_PASSWORD",
            "WEBHOOK_SECRET",
            "MONITORING_EMAIL",
            "MONITORING_PASSWORD",
            "WATCH_MONITOR_TOKEN",
        ):
            os.environ.setdefault(name, "validation-placeholder-not-a-secret")
        for overlay in ("compose.dev.yaml", "compose.prod.yaml"):
            run("docker", "compose", "-f", "compose.yaml", "-f", overlay, "config", "--quiet")
        run("docker", "compose", "-f", "compose.yaml", "-f", "compose.dev.yaml", "build", "app")
        configuration = json.loads(
            subprocess.check_output(
                [
                    "docker",
                    "compose",
                    "-f",
                    "compose.yaml",
                    "-f",
                    "compose.dev.yaml",
                    "config",
                    "--format",
                    "json",
                ],
                cwd=ROOT,
                text=True,
            )
        )
        caddy_image = configuration["services"]["caddy"]["image"]
        assert isinstance(caddy_image, str)
        subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                "compose.yaml",
                "-f",
                "compose.prod.yaml",
                "-f",
                "compose.monitor.yaml",
                "-f",
                "compose.observability.yaml",
                "config",
                "--quiet",
            ],
            cwd=ROOT,
            check=True,
            env=os.environ | {"WATCH_MONITOR_TOKEN": "audit-placeholder-not-a-secret"},
        )
        for filename in ("Caddyfile.dev", "Caddyfile.prod", "Caddyfile.monitor"):
            run(
                "docker",
                "run",
                "--rm",
                "--mount",
                f"type=bind,source={ROOT / 'deploy/caddy'},target=/etc/caddy,readonly",
                "--env",
                "WATCH_MONITOR_TOKEN=audit-placeholder-not-a-secret",
                "--env",
                "SITE_DOMAIN=example.com",
                caddy_image,
                "caddy",
                "validate",
                "--config",
                f"/etc/caddy/{filename}",
                "--adapter",
                "caddyfile",
            )
    if args.browser:
        run(
            "uv",
            "run",
            "--locked",
            "--group",
            "browser",
            "python",
            "scripts/check_browser.py",
            "--url",
            args.url,
        )


if __name__ == "__main__":
    main()
