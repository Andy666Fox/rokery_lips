"""Validate, copy and verify public source releases for the current stack."""

import hashlib
import http.client
import json
import shutil
import time
from pathlib import Path, PurePath, PurePosixPath

PUBLIC_DOTFILES = {
    ".dockerignore",
    ".env.example",
    ".env.integration.example",
    ".python-version",
    ".gitignore",
    ".gitattributes",
}


def is_private_path(path: PurePath) -> bool:
    """Keep credentials and generated Python files outside source releases."""
    return any(
        part in {"secrets.h", "monitor_secrets.h", "__pycache__"}
        or (part.startswith(".") and part not in PUBLIC_DOTFILES)
        for part in path.parts
    ) or path.suffix in {".pyc", ".pyo", ".key", ".pem"}


def source_files(release: Path) -> list[str]:
    """Validate the upload manifest before any live file is changed."""
    manifest = json.loads((release / "release-manifest.json").read_text())
    names: list[str] = []
    for item in manifest:
        name = item["path"]
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or name in names:
            raise ValueError(f"Unsafe or duplicate release path: {name}")
        if is_private_path(path):
            raise ValueError(f"Private path in release: {name}")
        source = release / name
        if source.is_symlink() or not source.resolve().is_relative_to(release):
            raise ValueError(f"Release source escapes its directory: {name}")
        if hashlib.sha256(source.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError(f"Release hash mismatch: {name}")
        names.append(name)
    if not names:
        raise ValueError("Release manifest is empty")
    return names


def copy_source(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.parent.chmod(0o755)
    temporary = destination.with_name(destination.name + ".deploy-new")
    shutil.copyfile(source, temporary)
    temporary.chmod(0o644)
    temporary.replace(destination)


def verify(domain: str, site: Path) -> list[dict[str, str | int | bool]]:
    """Check real HTTPS and snapshot freshness without exposing the token."""
    token = (site / ".env.monitor").read_text().strip().split("=", 1)[1]
    results: list[dict[str, str | int | bool]] = []
    for path, authorized, expected in (
        ("/", False, 200),
        ("/api/health", False, 200),
        ("/api/credits", False, 200),
        ("/monitor/status", False, 401),
        ("/monitor/status", True, 200),
    ):
        connection = http.client.HTTPSConnection(domain, timeout=10)
        try:
            connection.request(
                "GET", path, headers={"Authorization": f"Bearer {token}"} if authorized else {}
            )
            response = connection.getresponse()
            body = response.read()
            if response.status != expected:
                raise RuntimeError(f"{path}: expected {expected}, got {response.status}")
            if path == "/monitor/status":
                if response.getheader("Cache-Control") != "no-store":
                    raise RuntimeError("Metrics caching is enabled")
                if authorized:
                    data = json.loads(body)
                    if (
                        data["schema_version"] != 1
                        or not -5 <= time.time() - data["generated_at"] <= 20
                    ):
                        raise RuntimeError("Metrics are stale or have the wrong schema")
            results.append({"path": path, "authorized": authorized, "status": response.status})
        finally:
            connection.close()
    return results
