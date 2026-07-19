import asyncio
import logging
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from .models import Base
from beg_k_sebe_bot.bot.config import settings

logger = logging.getLogger(__name__)

engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def create_tables() -> None:
    for attempt in range(10):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                await conn.execute(text(
                    "ALTER TABLE daily_checkins ADD COLUMN IF NOT EXISTS run_km DOUBLE PRECISION"
                ))
            return
        except Exception as e:
            if attempt == 9:
                raise
            wait = 2 ** attempt
            logger.warning("DB not ready (attempt %d): %s. Retrying in %ds...", attempt + 1, e, wait)
            await asyncio.sleep(wait)
