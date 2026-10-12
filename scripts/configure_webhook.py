"""Register a verified HTTPS webhook, preserving Telegram's pending updates."""

import argparse
import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, cast

from scripts.bootstrap_monitoring import read_environment


def telegram(token: str, method: str, values: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=urllib.parse.urlencode(values).encode(),
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        result = cast(dict[str, Any], json.load(response))
    if not result.get("ok"):
        raise RuntimeError(f"Telegram method {method} failed")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", type=Path, default=Path(".env.integration"))
    parser.add_argument("--domain", default="dedvnutri.space")
    args = parser.parse_args()
    values = read_environment(args.environment)
    token = values["BOT_TOKEN"]
    me = telegram(token, "getMe", {})["result"]
    url = f"https://{args.domain}/telegram/webhook"
    telegram(
        token,
        "setWebhook",
        {
            "url": url,
            "secret_token": values["WEBHOOK_SECRET"],
            "allowed_updates": json.dumps(["message", "callback_query"]),
            "max_connections": "20",
            "drop_pending_updates": "false",
        },
    )
    telegram(
        token,
        "setMyCommands",
        {
            "commands": json.dumps(
                [
                    {"command": "random", "description": "Случайный Telegram-канал"},
                    {"command": "suggest", "description": "Предложить канал"},
                    {"command": "extra", "description": "Моя статистика"},
                    {"command": "help", "description": "Справка"},
                ]
            )
        },
    )
    info = telegram(token, "getWebhookInfo", {})["result"]
    if info["url"] != url:
        raise RuntimeError("Telegram did not retain the expected webhook URL")
    print(
        json.dumps(
            {
                "bot": me["username"],
                "webhook": info["url"],
                "pending_updates": info["pending_update_count"],
            }
        )
    )


if __name__ == "__main__":
    main()
