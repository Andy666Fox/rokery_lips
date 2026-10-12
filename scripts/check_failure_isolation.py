"""Verify SQL outage behavior in an isolated bot container without Telegram credentials."""

import argparse
import subprocess
import time


def run(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, timeout=40)
    if result.returncode:
        raise RuntimeError(f"Validation command failed: {result.stderr[-1000:]}")
    return result.stdout.strip()


def check(image: str) -> None:
    name = "synch-bot-failure-check"
    run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--network",
            "none",
            "--memory",
            "256m",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--env",
            "DATABASE_URL=mysql+aiomysql://test:test@127.0.0.1:1/test",
            "--env",
            "BOT_TOKEN=",
            "--env",
            "WEBHOOK_SECRET=" + "s" * 40,
            image,
        ]
    )
    source = """
import urllib.request, urllib.error
for path, expected in [('/health/live',200),('/health/db',503),('/health/ready',503),
                       ('/api/random/channel',503)]:
    try:
        with urllib.request.urlopen('http://127.0.0.1:8001'+path,timeout=5) as response:
            code=response.status
    except urllib.error.HTTPError as error:
        code=error.code
    assert code==expected,(path,code,expected)
print('Isolated SQL outage: live=200; SQL, readiness and selection=503')
"""
    try:
        for attempt in range(15):
            try:
                print(run(["docker", "exec", name, "python", "-c", source]))
                break
            except RuntimeError:
                if attempt == 14:
                    raise
                time.sleep(1)
    finally:
        run(["docker", "rm", "-f", name])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="synchronisica-prod-bot")
    args = parser.parse_args()
    check(args.image)


if __name__ == "__main__":
    main()
