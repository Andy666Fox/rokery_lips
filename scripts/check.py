"""Run the current repository checks through its locked uv environment."""

import argparse
import os
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--mysql-queue", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    commands = [
        ["uv", "lock", "--check"],
        ["uv", "run", "--locked", "ruff", "check", "."],
        ["uv", "run", "--locked", "ruff", "format", "--check", "."],
        ["uv", "run", "--locked", "--group", "browser", "ty", "check"],
        ["uv", "run", "--locked", "pytest", "-q"],
    ]
    for source in sorted((root / "services").rglob("*")):
        if source.suffix in {".js", ".mjs"} and not {"node_modules", "vendor"}.intersection(
            source.parts
        ):
            commands.append(["node", "--check", str(source)])
    if args.mysql_queue:
        commands.append(["uv", "run", "--locked", "python", "scripts/check_mysql_queue.py"])
    if args.audit:
        output = root / ".local" / "audit-requirements.txt"
        output.parent.mkdir(exist_ok=True)
        commands += [
            [
                "uv",
                "export",
                "--locked",
                "--no-dev",
                "--no-emit-workspace",
                "--no-hashes",
                "--output-file",
                str(output),
            ],
            ["uv", "run", "--locked", "pip-audit", "--disable-pip", "--no-deps", "-r", str(output)],
            [
                "npm.cmd" if os.name == "nt" else "npm",
                "--prefix",
                str(root / "services" / "website"),
                "audit",
                "--audit-level=moderate",
            ],
        ]
    for command in commands:
        print("Running " + " ".join(command[:6]), flush=True)
        subprocess.run(command, cwd=root, check=True)


if __name__ == "__main__":
    main()
