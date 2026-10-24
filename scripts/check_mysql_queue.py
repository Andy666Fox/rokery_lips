"""Exercise queue backfill on an empty, explicitly named isolated MySQL database.

Run with QUEUE_TEST_DATABASE_URL=mysql+aiomysql://.../synch_test_queue_UNIQUE
and uv run --locked python scripts/check_mysql_queue.py. Production databases
and existing schemas are rejected before creating any tables.
"""

import asyncio
import json
import os

from random_telegram_bot.models import Base, Channel, ChannelCheck
from random_telegram_bot.verification_queue import enqueue_untracked
from sqlalchemy import make_url, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


async def verify() -> None:
    url = make_url(os.environ["QUEUE_TEST_DATABASE_URL"])
    if url.get_backend_name() != "mysql" or url.database is None:
        raise ValueError("Use an isolated MySQL test database")
    if not url.database.startswith("synch_test_queue_"):
        raise ValueError("Database name must start with synch_test_queue_")
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            count = await connection.scalar(
                text("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE()")
            )
            if count != 0:
                raise ValueError("The test requires a new empty database")
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine)
        async with sessions.begin() as db:
            db.add_all(
                [
                    Channel(channel_id=1, channelnick="existingchannel", status=True),
                    Channel(channel_id=2, channelnick="legacychannel", status=True),
                    Channel(channel_id=3, channelnick="pendingchannel", status=False),
                ]
            )
            await db.flush()
            db.add(
                ChannelCheck(
                    channel_id=1,
                    state="valid",
                    next_check_at=123456,
                    attempts=5,
                    import_sha256="a" * 64,
                    source_urls="source",
                    attribution="existing receipt",
                )
            )
        assert await enqueue_untracked(engine) == 2
        assert await enqueue_untracked(engine) == 0
        async with sessions() as db:
            channels = list(await db.scalars(select(Channel).order_by(Channel.channel_id)))
            checks = list(await db.scalars(select(ChannelCheck).order_by(ChannelCheck.channel_id)))
            assert [channel.status for channel in channels] == [True, True, False]
            assert checks[0].state == "valid" and checks[0].attempts == 5
            assert checks[0].next_check_at == 123456 and checks[0].attribution == "existing receipt"
            assert len(checks) == 3
            assert all(
                check.state == "pending" and check.next_check_at == 0 for check in checks[1:]
            )
        print(json.dumps({"mysql_queue": "ok", "inserted_missing": 2, "repeat_inserted": 0}))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(verify())
