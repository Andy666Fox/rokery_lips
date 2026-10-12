"""One Compose entry point keeps local and production service sets consistent."""

import argparse
import subprocess
from pathlib import Path


def configuration(
    directory: Path,
    source: Path | None = None,
    environment: Path | None = None,
    *,
    dev: bool = False,
) -> list[str]:
    source = source or directory
    environment = environment or directory
    files = ["compose.yaml", "compose.dev.yaml" if dev else "compose.prod.yaml"]
    environments = [".env", ".env.integration"]
    if not dev:
        files += ["compose.monitor.yaml", "compose.observability.yaml"]
        environments += [".env.monitor"]
    return [
        "docker",
        "compose",
        "-p",
        "synchronisica-dev" if dev else "synchronisica-prod",
        "--project-directory",
        str(directory),
        *[arg for name in environments for arg in ("--env-file", str(environment / name))],
        *[arg for name in files for arg in ("-f", str(source / name))],
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev", action="store_true")
    args, command = parser.parse_known_args()
    if not command:
        parser.error("Pass a Compose command, such as ps or up -d --wait")
    root = Path(__file__).resolve().parents[1]
    raise SystemExit(
        subprocess.run([*configuration(root, dev=args.dev), *command], cwd=root).returncode
    )


if __name__ == "__main__":
    main()
