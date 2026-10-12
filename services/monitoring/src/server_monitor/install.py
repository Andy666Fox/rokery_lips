"""Install the metrics collector on Ubuntu. Requires root; does not change the website."""

import os
import pwd
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> None:
    if os.geteuid() != 0:
        sys.exit("Run this installer with sudo on the Ubuntu host.")
    source = Path(__file__).resolve().parent
    destination = Path("/opt/synch-monitor")
    unit = Path("/etc/systemd/system/synch-monitor.service")
    try:
        pwd.getpwnam("synch-monitor")
    except KeyError:
        subprocess.run(
            [
                "useradd",
                "--system",
                "--user-group",
                "--no-create-home",
                "--shell",
                "/usr/sbin/nologin",
                "synch-monitor",
            ],
            check=True,
        )
    destination.mkdir(mode=0o755, parents=True, exist_ok=True)
    # The administrator may use umask 077; the service user still needs traversal.
    destination.chmod(0o755)
    for original, target in (
        (source / "collector.py", destination / "collector.py"),
        (source / "synch-monitor.service", unit),
    ):
        if target.exists():
            shutil.copy2(target, target.with_suffix(target.suffix + ".previous"))
        temporary = target.with_suffix(target.suffix + ".new")
        shutil.copyfile(original, temporary)
        temporary.chmod(0o644)
        temporary.replace(target)
    subprocess.run(["systemd-analyze", "verify", str(unit)], check=True)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "synch-monitor"], check=True)
    subprocess.run(["systemctl", "restart", "synch-monitor"], check=True)
    subprocess.run(["systemctl", "is-active", "synch-monitor"], check=True)
    print("Collector installed. Metrics: /var/lib/synch-monitor/status.json")


if __name__ == "__main__":
    main()
