"""Large catalogues and status changes must remain usable with a warm cache."""

import asyncio

from random_telegram_bot.catalogue import Catalogue
from random_telegram_bot.models import Base, Channel
from sqlalchemy import insert, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def test_large_catalogue_and_retired_channels() -> None:
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite://")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
                await connection.execute(
                    insert(Channel),
                    [{"channelnick": f"channel{i}", "status": True} for i in range(2501)]
                    + [
                        {"channelnick": "bad", "status": True},
                        {"channelnick": "inactive", "status": False},
                    ],
                )
            catalogue = Catalogue(sessions)
            for _ in range(10):
                result = await catalogue.choose()
                assert result is not None and result[1].startswith("channel")
            async with sessions.begin() as db:
                await db.execute(update(Channel).values(status=False))
            assert await catalogue.choose() is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())
