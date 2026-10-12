"""Package the collector and its existing token without changing the live server."""

import argparse
import io
import re
import secrets
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "services/monitoring/src/server_monitor/collector.py",
    "services/monitoring/src/server_monitor/install.py",
    "services/monitoring/src/server_monitor/synch-monitor.service",
)


def existing_token(environment: Path, header: Path) -> str:
    """Reuse either credential; refuse to silently rotate conflicting or invalid tokens."""
    tokens: set[str] = set()
    for path, pattern in (
        (environment, r"WATCH_MONITOR_TOKEN=([a-f0-9]{64})\s*"),
        (header, r'MONITOR_TOKEN\s*(?:\[\])?\s*=\s*"([a-f0-9]{64})"'),
    ):
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8")
        match = (
            re.fullmatch(pattern, content) if path == environment else re.search(pattern, content)
        )
        if match is None:
            raise ValueError(f"Invalid token in {path}; resolve it explicitly before packaging")
        tokens.add(match[1])
    if len(tokens) > 1:
        raise ValueError(".env.monitor and firmware tokens differ; refusing to rotate credentials")
    return tokens.pop() if tokens else secrets.token_hex(32)


def prepare(root: Path, domain: str) -> Path:
    """Create a private release archive and missing credentials, preserving existing headers."""
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", domain):
        raise ValueError("Domain must be a hostname without a scheme, port, or path")
    for relative in FILES:
        if not (root / relative).is_file():
            raise FileNotFoundError(root / relative)
    environment = root / ".env.monitor"
    header = root / "esp_software/monitor_secrets.h"
    token = existing_token(environment, header)
    secret = f"WATCH_MONITOR_TOKEN={token}\n"
    if not environment.exists():
        with environment.open("x", encoding="utf-8") as target:
            target.write(secret)
        environment.chmod(0o600)
    if not header.exists():
        header.parent.mkdir(parents=True, exist_ok=True)
        with header.open("x", encoding="utf-8") as target:
            target.write(
                "#pragma once\n\n"
                f'constexpr char HEALTH_URL[] = "https://{domain}/";\n'
                f'constexpr char MONITOR_URL[] = "https://{domain}/monitor/status";\n'
                f'constexpr char MONITOR_TOKEN[] = "{token}";\n'
            )
        header.chmod(0o600)
    output = root / ".local/monitor-deploy.tar"
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w") as archive:
        for relative in FILES:
            archive.add(root / relative, arcname=relative)
        info = tarfile.TarInfo(".env.monitor")
        info.size = len(secret.encode())
        info.mode = 0o600
        archive.addfile(info, io.BytesIO(secret.encode()))
    output.chmod(0o600)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", default="dedvnutri.space")
    args = parser.parse_args()
    print(f"Private deployment archive: {prepare(ROOT, args.domain)}")


if __name__ == "__main__":
    main()
