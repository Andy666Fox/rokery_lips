"""Back up production MySQL and optionally verify restoration in an isolated database."""

import argparse
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def execute(command: list[str], *, data: bytes | None = None) -> bytes:
    result = subprocess.run(command, input=data, capture_output=True, check=False, timeout=120)
    if result.returncode:
        raise RuntimeError(f"MySQL operation failed: {result.stderr.decode()[-1000:]}")
    return result.stdout


def mysql(container: str, sql: str, database: str = "random_bot") -> bytes:
    return execute(
        [
            "docker",
            "exec",
            "-i",
            container,
            "sh",
            "-c",
            'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot -N "$@"',
            "sh",
            database,
        ],
        data=sql.encode(),
    )


def backup(container: str, directory: Path, *, restore_check: bool = False) -> dict[str, object]:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output = directory / f"random-bot-{stamp}.sql"
    dump = execute(
        [
            "docker",
            "exec",
            container,
            "sh",
            "-c",
            'MYSQL_PWD="$MYSQL_PASSWORD" exec mysqldump -u"$MYSQL_USER" '
            "--single-transaction --no-tablespaces --set-gtid-purged=OFF "
            '--skip-comments "$MYSQL_DATABASE"',
        ]
    )
    with output.open("xb") as destination:
        output.chmod(0o600)
        destination.write(dump)
    result: dict[str, object] = {"backup": str(output), "bytes": len(dump)}
    if not restore_check:
        return result

    temporary = "synch_restore_" + stamp.lower()
    assert re.fullmatch(r"synch_restore_[a-z0-9]+", temporary)
    mysql(container, f"CREATE DATABASE `{temporary}`;")
    try:
        execute(
            [
                "docker",
                "exec",
                "-i",
                container,
                "sh",
                "-c",
                'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot "$@"',
                "sh",
                temporary,
            ],
            data=dump,
        )
        tables = (
            mysql(
                container,
                "SELECT TABLE_NAME FROM information_schema.tables "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME;",
                temporary,
            )
            .decode()
            .splitlines()
        )
        if not tables or any(not re.fullmatch(r"[a-zA-Z0-9_]+", name) for name in tables):
            raise RuntimeError("Restored database has no valid application tables")
        # Compare with the immutable dump, rather than a live DB receiving new messages.
        restored_dump = execute(
            [
                "docker",
                "exec",
                container,
                "sh",
                "-c",
                'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqldump -uroot '
                "--single-transaction --no-tablespaces --set-gtid-purged=OFF "
                '--skip-comments "$@"',
                "sh",
                temporary,
            ]
        )
        if restored_dump != dump:
            raise RuntimeError("Restored schema or data differs from the backup")
        counts = {
            table: int(mysql(container, f"SELECT COUNT(*) FROM `{table}`;", temporary))
            for table in tables
        }
        revision = mysql(container, "SELECT version_num FROM alembic_version;", temporary)
        if not revision.strip():
            raise RuntimeError("Restored database has no migration revision")
        result.update(
            {
                "restore": "ok",
                "dump_match": True,
                "tables": counts,
                "revision": revision.decode().strip(),
            }
        )
    finally:
        # This name is created exclusively by this invocation; production is never dropped.
        mysql(container, f"DROP DATABASE `{temporary}`;")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", default="synchronisica-prod-mysql-1")
    parser.add_argument("--directory", type=Path, default=Path("/var/backups/synchronisica/mysql"))
    parser.add_argument("--restore-check", action="store_true")
    args = parser.parse_args()
    print(json.dumps(backup(args.container, args.directory, restore_check=args.restore_check)))


if __name__ == "__main__":
    main()
