"""Configure the pinned Beszel API over loopback before publishing its login page."""

import argparse
import json
import time
import urllib.request
from pathlib import Path
from typing import Any, cast


def read_environment(path: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in path.read_text().splitlines()
        if line and not line.startswith("#")
    )


def write_environment(path: Path, values: dict[str, str]) -> None:
    temporary = path.with_suffix(path.suffix + ".new")
    temporary.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
    temporary.chmod(0o600)
    temporary.replace(path)


class Beszel:
    def __init__(self, base_url: str = "http://127.0.0.1:8090") -> None:
        self.base_url = base_url
        self.token = ""

    def call(
        self, path: str, payload: dict[str, Any] | None = None, *, method: str | None = None
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = self.token
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers=headers,
            method=method,
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read()
            return cast(dict[str, Any], json.loads(body)) if body else {}

    def login(self, email: str, password: str) -> None:
        result = self.call(
            "/api/collections/users/auth-with-password", {"identity": email, "password": password}
        )
        self.token = str(result["token"])


def prepare_agent(environment: Path) -> None:
    values = read_environment(environment)
    client = Beszel()
    client.login(values["MONITORING_EMAIL"], values["MONITORING_PASSWORD"])
    values["BESZEL_KEY"] = str(client.call("/api/beszel/info")["key"])
    values["BESZEL_TOKEN"] = str(
        client.call("/api/beszel/universal-token?enable=1&permanent=1")["token"]
    )
    write_environment(environment, values)
    print("Beszel account verified; private agent credentials saved")


def configure_monitors(environment: Path, domain: str) -> None:
    values = read_environment(environment)
    client = Beszel()
    client.login(values["MONITORING_EMAIL"], values["MONITORING_PASSWORD"])
    systems: list[dict[str, Any]] = []
    for _ in range(30):
        systems = [
            record
            for record in client.call("/api/collections/systems/records")["items"]
            if record.get("status") == "up"
        ]
        if len(systems) > 1:
            raise RuntimeError("Select a monitoring system explicitly when multiple agents are up")
        if systems:
            break
        time.sleep(1)
    else:
        raise RuntimeError("Beszel agent has not registered a healthy system")
    system_id = str(systems[0]["id"])
    existing = client.call("/api/collections/network_monitors/records?perPage=100")["items"]
    targets = {str(record["target"]): record for record in existing}
    for target in (
        f"https://{domain}/",
        f"https://{domain}/api/health",
        f"https://{domain}/api/random/ready",
        f"https://{domain}/api/random/db",
        "http://127.0.0.1:8091/health/esp",
    ):
        if target not in targets:
            client.call(
                "/api/collections/network_monitors/records",
                {
                    "system": system_id,
                    "target": target,
                    "protocol": "http",
                    "interval": 30,
                    "enabled": True,
                },
            )
        elif targets[target]["system"] != system_id:
            client.call(
                "/api/collections/network_monitors/records/" + str(targets[target]["id"]),
                {"system": system_id},
                method="PATCH",
            )
    # Preserve user-chosen thresholds on repeat runs.
    for name, value, minutes in (
        ("CPU", 90, 5),
        ("Memory", 90, 5),
        ("Disk", 85, 5),
        ("Status", 0, 1),
        ("ContainerHealth", 0, 1),
        ("NetworkMonitorLoss", 5, 0),
    ):
        client.call(
            "/api/beszel/user-alerts",
            {
                "name": name,
                "value": value,
                "min": minutes,
                "systems": [system_id],
                "overwrite": False,
            },
        )
    print(
        f"Beszel: healthy system registered; {len(targets)} previous checks; "
        "five target checks configured"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", type=Path, default=Path(".env.integration"))
    parser.add_argument("--domain", default="dedvnutri.space")
    parser.add_argument("--checks", action="store_true")
    args = parser.parse_args()
    if args.checks:
        configure_monitors(args.environment, args.domain)
    else:
        prepare_agent(args.environment)


if __name__ == "__main__":
    main()
