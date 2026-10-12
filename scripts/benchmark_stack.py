"""Measure HTTPS API latency using harmless webhook events that produce no messages."""

import argparse
import concurrent.futures
import http.client
import json
import math
import ssl
import threading
import time
from pathlib import Path

from scripts.bootstrap_monitoring import read_environment

SYNTHETIC_CHAT = -9876543210000


def summary(values: list[float]) -> dict[str, int | float]:
    values = sorted(values)
    return {
        "requests": len(values),
        "p50_ms": round(values[math.ceil(len(values) * 0.50) - 1], 2),
        "p95_ms": round(values[math.ceil(len(values) * 0.95) - 1], 2),
        "p99_ms": round(values[math.ceil(len(values) * 0.99) - 1], 2),
        "max_ms": round(values[-1], 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", default="dedvnutri.space")
    parser.add_argument("--environment", type=Path, default=Path(".env.integration"))
    parser.add_argument("--output", type=Path, default=Path(".local/performance.json"))
    args = parser.parse_args()
    settings = read_environment(args.environment)
    update_ids = [-time.time_ns() // 1000 - i for i in range(64)]
    context = ssl.create_default_context()
    local = threading.local()
    connections: list[http.client.HTTPSConnection] = []

    def admit(update_id: int) -> float:
        if not hasattr(local, "connection"):
            local.connection = http.client.HTTPSConnection(args.domain, timeout=10, context=context)
            connections.append(local.connection)
        body = json.dumps(
            {
                "update_id": update_id,
                "message": {
                    "message_id": 1,
                    "date": int(time.time()),
                    "chat": {"id": SYNTHETIC_CHAT, "type": "private"},
                },
            }
        )
        started = time.perf_counter()
        local.connection.request(
            "POST",
            "/telegram/webhook",
            body,
            {
                "Content-Type": "application/json",
                "X-Telegram-Bot-Api-Secret-Token": settings["WEBHOOK_SECRET"],
            },
        )
        response = local.connection.getresponse()
        data = response.read()
        assert response.status == 200 and json.loads(data)["ok"]
        return (time.perf_counter() - started) * 1000

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            webhook = list(executor.map(admit, update_ids))
        public = []
        connection = http.client.HTTPSConnection(args.domain, timeout=10, context=context)
        connections.append(connection)
        for _ in range(15):
            started = time.perf_counter()
            connection.request("GET", "/api/random/channel")
            response = connection.getresponse()
            data = response.read()
            assert response.status == 200 and json.loads(data)["channel"]
            public.append((time.perf_counter() - started) * 1000)
        report = {
            "webhook_https_admission": summary(webhook),
            "public_https_selection": summary(public),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))
    finally:
        for connection in connections:
            connection.close()


if __name__ == "__main__":
    main()
