"""Build a secret-free source archive and SHA256 manifest for server deployment."""

import hashlib
import io
import json
import tarfile
from pathlib import Path

from scripts.release import is_private_path

ROOT = Path(__file__).resolve().parents[1]


def package(root: Path, destination: Path) -> int:
    files = []
    candidates = list(root.iterdir())
    for name in ("services", "deploy", "scripts", "tests", "docs", "esp_software"):
        directory = root / name
        if directory.is_dir():
            candidates.extend(directory.rglob("*"))
    for path in candidates:
        relative = path.relative_to(root)
        if not path.is_file() or path.is_symlink():
            continue
        if is_private_path(relative):
            continue
        files.append(path)
    manifest = [
        {
            "path": p.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        }
        for p in sorted(files)
    ]
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(destination, "w:gz") as archive:
        for path in files:
            archive.add(path, arcname=path.relative_to(root).as_posix())
        payload = json.dumps(manifest, indent=2).encode()
        info = tarfile.TarInfo("release-manifest.json")
        info.size = len(payload)
        info.mode = 0o644
        archive.addfile(info, io.BytesIO(payload))
    return len(files)


if __name__ == "__main__":
    output = ROOT / ".local/integration-release.tar.gz"
    print(f"Packaged {package(ROOT, output)} files: {output}")
