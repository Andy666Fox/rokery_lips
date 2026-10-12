"""Translate snapshot freshness into HTTP status for Beszel network monitors."""

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import override


def snapshot_is_fresh(path: Path, now: float) -> bool:
    try:
        data = json.loads(path.read_text())
        return bool(data["schema_version"] == 1 and -5 <= now - data["generated_at"] <= 20)
    except (OSError, ValueError, KeyError, TypeError):
        return False


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        valid = self.path == "/health/esp" and snapshot_is_fresh(
            Path("/monitor/status.json"), time.time()
        )
        self.send_response(200 if valid else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}' if valid else b'{"status":"unavailable"}')

    @override
    def log_message(self, format: str, *args: object) -> None:
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8091), Handler).serve_forever()
