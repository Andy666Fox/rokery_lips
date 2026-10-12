import json
import tarfile
from pathlib import Path

import pytest

from scripts.prepare_monitor import FILES, existing_token, prepare
from server_monitor.collector import (
    Sample,
    cpu_counters,
    cpu_percent,
    disk_busy_ms,
    disk_percent,
    memory_bytes,
    payload,
    publish,
)


@pytest.mark.parametrize("text", ["", "\n", "cpu 1 2", "cpu -1 0 0 0 0 0 0 0"])
def test_cpu_rejects_invalid_counters(text: str) -> None:
    with pytest.raises(ValueError):
        cpu_counters(text)


def test_metrics_intervals_and_unknown_values() -> None:
    assert cpu_counters("cpu 10 0 10 70 10 0 0 0 500 500") == (100, 80)
    assert cpu_percent((100, 80), (200, 140)) == 40
    assert cpu_percent((200, 140), (100, 80)) is None
    assert cpu_percent((100, 80), (100, 80)) is None
    assert memory_bytes("MemTotal: 1024 kB\nMemAvailable: 256 kB\n") == (768 * 1024, 256 * 1024)
    assert disk_busy_ms("0 0 0 0 0 0 0 0 0 123 0") == 123
    assert disk_percent(100, 600, 2) == 25
    assert disk_percent(0, 5000, 1) == 100
    assert disk_percent(100, 99, 1) is None
    old = Sample(10, 1000, (100, 80), None, 700, 300, 1000, 2000)
    new = Sample(12, 1002, (200, 140), 100, 700, 300, 1000, 2000)
    result = payload(old, new)
    assert result["cpu_percent"] == 40
    assert result["disk_util_percent"] is None
    assert result["generated_at"] == 1002
    assert result["schema_version"] == 1


def test_publish_preserves_last_snapshot_on_invalid_sample(tmp_path: Path) -> None:
    output = tmp_path / "status.json"
    publish(output, {"schema_version": 1, "cpu_percent": None})
    with pytest.raises(ValueError):
        publish(output, {"cpu_percent": float("nan")})
    assert json.loads(output.read_text()) == {"schema_version": 1, "cpu_percent": None}
    publish(output, {"cpu_percent": 42})
    assert json.loads(output.read_text()) == {"cpu_percent": 42}
    assert not output.with_suffix(".tmp").exists()


def test_package_reuses_token_and_excludes_unlisted_files(tmp_path: Path) -> None:
    for relative in FILES:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")
    header = tmp_path / "esp_software/monitor_secrets.h"
    header.parent.mkdir()
    original = f'constexpr char MONITOR_TOKEN[] = "{"a" * 64}";\n'
    header.write_text(original, encoding="utf-8")
    (header.parent / "secrets.h").write_text("private wifi password", encoding="utf-8")
    archive_path = prepare(tmp_path, "example.com")
    with tarfile.open(archive_path) as archive:
        assert set(archive.getnames()) == set(FILES) | {".env.monitor"}
        secret = archive.extractfile(".env.monitor")
        assert secret is not None
        assert secret.read() == f"WATCH_MONITOR_TOKEN={'a' * 64}\n".encode()
        assert archive.getmember(".env.monitor").mode == 0o600
    prepare(tmp_path, "example.com")
    assert header.read_text() == original


def test_package_refuses_token_conflict(tmp_path: Path) -> None:
    environment = tmp_path / ".env.monitor"
    header = tmp_path / "monitor_secrets.h"
    environment.write_text(f"WATCH_MONITOR_TOKEN={'a' * 64}\n")
    header.write_text(f'constexpr char MONITOR_TOKEN[] = "{"b" * 64}";')
    with pytest.raises(ValueError, match="differ"):
        existing_token(environment, header)
