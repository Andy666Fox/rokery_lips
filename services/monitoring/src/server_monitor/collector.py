"""Publish host metrics atomically for Caddy; no listening socket or runtime dependencies."""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

LOG = logging.getLogger(__name__)


def cpu_counters(text: str) -> tuple[int, int]:
    """Read total and idle ticks, excluding guest ticks already counted as user time."""
    lines = text.splitlines()
    fields = lines[0].split() if lines else []
    if len(fields) < 9 or fields[0] != "cpu":
        raise ValueError("/proc/stat is missing the aggregate CPU counters")
    ticks = [int(value) for value in fields[1:9]]
    if min(ticks) < 0:
        raise ValueError("CPU counters must be non-negative")
    return sum(ticks), ticks[3] + ticks[4]


def cpu_percent(previous: tuple[int, int], current: tuple[int, int]) -> float | None:
    """Return interval CPU usage; a reset or empty interval has no valid measurement."""
    total = current[0] - previous[0]
    idle = current[1] - previous[1]
    if total <= 0 or idle < 0 or idle > total:
        return None
    return round(100 * (total - idle) / total, 1)


def memory_bytes(text: str) -> tuple[int, int]:
    """Return used and available RAM; reclaimable cache counts as available."""
    fields = {line.split(":", 1)[0]: line.split(":", 1)[1].split() for line in text.splitlines()}
    total = int(fields["MemTotal"][0]) * 1024
    available = int(fields["MemAvailable"][0]) * 1024
    if total <= 0 or not 0 <= available <= total:
        raise ValueError("Invalid MemTotal/MemAvailable in /proc/meminfo")
    return total - available, available


def disk_busy_ms(text: str) -> int:
    """Read field 10 (milliseconds doing I/O) from a sysfs block stat file."""
    fields = text.split()
    if len(fields) < 11:
        raise ValueError("Block device stat must contain at least 11 counters")
    value = int(fields[9])
    if value < 0:
        raise ValueError("Disk busy time must be non-negative")
    return value


def disk_percent(previous: int, current: int, elapsed: float) -> float | None:
    """Return time busy, capped at 100%; this is not a disk bandwidth percentage."""
    if current < previous or elapsed <= 0:
        return None
    return round(min(100.0, (current - previous) / (elapsed * 10)), 1)


def resolve_disk(mount: Path, device: str | None) -> Path:
    """Find the mount's device, using the parent disk when the mount is a partition."""
    if device is not None:
        if Path(device).name != device:
            raise ValueError("--device expects a block name such as sda, not a path")
        block = Path("/sys/class/block") / device
    else:
        number = mount.stat().st_dev
        block = Path(f"/sys/dev/block/{os.major(number)}:{os.minor(number)}").resolve()
        if (block / "partition").exists():
            block = block.parent
    stat = block / "stat"
    if not stat.is_file():
        raise ValueError(f"Cannot find disk counters at {stat}; specify --device from lsblk")
    return stat


@dataclass(frozen=True)
class Sample:
    """One host observation with both UTC freshness and monotonic interval timing."""

    monotonic: float
    generated_at: int
    cpu: tuple[int, int]
    disk_busy: int | None
    ram_used: int
    ram_free: int
    disk_free: int
    disk_total: int


def sample(mount: Path, disk_stat: Path) -> Sample:
    """Collect host-wide counters; unavailable I/O counters remain explicitly unknown."""
    cpu = cpu_counters(Path("/proc/stat").read_text())
    used, free = memory_bytes(Path("/proc/meminfo").read_text())
    space = shutil.disk_usage(mount)
    try:
        busy = disk_busy_ms(disk_stat.read_text())
    except (OSError, ValueError) as error:
        LOG.warning("Cannot read disk I/O at %s: %s", disk_stat, error)
        busy = None
    return Sample(
        time.monotonic(), int(time.time()), cpu, busy, used, free, space.free, space.total
    )


def payload(previous: Sample, current: Sample) -> dict[str, int | float | None]:
    """Build the versioned numeric contract consumed by the display."""
    io = None
    if previous.disk_busy is not None and current.disk_busy is not None:
        io = disk_percent(
            previous.disk_busy, current.disk_busy, current.monotonic - previous.monotonic
        )
    return {
        "schema_version": 1,
        "generated_at": current.generated_at,
        "cpu_percent": cpu_percent(previous.cpu, current.cpu),
        "ram_used_bytes": current.ram_used,
        "ram_free_bytes": current.ram_free,
        "disk_free_bytes": current.disk_free,
        "disk_total_bytes": current.disk_total,
        "disk_util_percent": io,
    }


def publish(path: Path, data: dict[str, int | float | None]) -> None:
    """Replace the complete snapshot; readers can never see partially written JSON."""
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    """Run on Ubuntu under systemd; failed samples do not refresh stale data."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mount", type=Path, default=Path("/"))
    parser.add_argument(
        "--device", help="Optional block name from lsblk; default detects mount device"
    )
    parser.add_argument("--output", type=Path, default=Path("/var/lib/synch-monitor/status.json"))
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args()
    if not math.isfinite(args.interval) or args.interval < 0.2:
        parser.error("--interval must be finite and at least 0.2 seconds")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    disk_stat = resolve_disk(args.mount, args.device)
    LOG.info("Monitoring mount %s and disk %s", args.mount, disk_stat.parent.name)
    previous = None
    while True:
        started = time.monotonic()
        try:
            current = sample(args.mount, disk_stat)
            if previous is not None:
                publish(args.output, payload(previous, current))
            previous = current
        except (OSError, ValueError, KeyError, IndexError) as error:
            LOG.exception("Cannot publish host metrics: %s", error)
            previous = None
        time.sleep(max(0, args.interval - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
