import hashlib
import json
import tarfile
from pathlib import Path

import pytest

from scripts import release
from scripts.package_release import package


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/absolute",
        ".env",
        ".env.integration",
        ".git/config",
        "esp_software/monitor_secrets.h",
    ],
)
def test_release_rejects_private_and_escaping_paths(tmp_path: Path, name: str) -> None:
    (tmp_path / "release-manifest.json").write_text(
        json.dumps([{"path": name, "sha256": "unused"}])
    )
    with pytest.raises(ValueError):
        release.source_files(tmp_path)


def test_release_verifies_hashes_and_duplicates(tmp_path: Path) -> None:
    source = tmp_path / "compose.yaml"
    source.write_bytes(b"services: {}\n")
    item = {"path": source.name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    manifest = tmp_path / "release-manifest.json"
    manifest.write_text(json.dumps([item]))
    assert release.source_files(tmp_path) == [source.name]
    source.write_text("changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        release.source_files(tmp_path)
    source.write_bytes(b"services: {}\n")
    manifest.write_text(json.dumps([item, item]))
    with pytest.raises(ValueError, match="duplicate"):
        release.source_files(tmp_path)


def test_package_keeps_public_firmware_and_excludes_private_settings(tmp_path: Path) -> None:
    public = {
        "compose.yaml",
        ".env.integration.example",
        "esp_software/monitor_state.h",
        "esp_software/monitor_secrets.example.h",
        "services/app/main.py",
    }
    private = {
        ".env",
        ".local/old.tar.gz",
        "esp_software/secrets.h",
        "esp_software/monitor_secrets.h",
        "services/app/.env",
        "services/app/__pycache__/main.pyc",
    }
    for name in public | private:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
    destination = tmp_path / ".local/release.tar.gz"
    package(tmp_path, destination)
    with tarfile.open(destination) as archive:
        assert set(archive.getnames()) == public | {"release-manifest.json"}
        archive.extractall(tmp_path / "extracted", filter="data")
    assert set(release.source_files(tmp_path / "extracted")) == public
