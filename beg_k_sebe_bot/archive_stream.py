import argparse
import asyncio
from datetime import date

from redis.asyncio import Redis
from sqlalchemy import text

from beg_k_sebe_bot.bot.database.db import create_tables, engine

_TABLES = ("users", "daily_checkins", "weekly_reflections", "movement_format_changes")


async def archive(old_start: date) -> None:
    await create_tables()
    async with engine.begin() as conn:
        for table in _TABLES:
            await conn.execute(text(f"CREATE TABLE IF NOT EXISTS archive_{table} (LIKE {table})"))
            await conn.execute(text(f"ALTER TABLE archive_{table} ADD COLUMN IF NOT EXISTS stream_start date"))
            exists = await conn.scalar(
                text(f"SELECT EXISTS (SELECT 1 FROM archive_{table} WHERE stream_start = :s)"), {"s": old_start}
            )
            if exists:
                raise SystemExit(f"Stream {old_start} is already archived in archive_{table}")
            await conn.execute(text(f"INSERT INTO archive_{table} SELECT *, :s FROM {table}"), {"s": old_start})

        await conn.execute(text("DELETE FROM daily_checkins"))
        await conn.execute(text("DELETE FROM weekly_reflections"))
        await conn.execute(text("DELETE FROM movement_format_changes"))
        await conn.execute(text(
            "UPDATE users SET goal = NULL, movement_format = NULL, joined_at = NULL, "
            "onboarding_completed_at = NULL, point_a_score = NULL, point_a_text = NULL, "
            "point_b_score = NULL, point_b_text = NULL, final_sent = false"
        ))
        await conn.execute(text("DELETE FROM sent_events WHERE key LIKE 'reflection_prompt:%'"))

    redis = Redis.from_url("redis://redis:6379/0")
    keys = [k async for k in redis.scan_iter("fsm:*")]
    if keys:
        await redis.delete(*keys)
    await redis.aclose()
    print(f"Stream {old_start} archived, {len(keys)} FSM keys cleared")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-start", type=date.fromisoformat, required=True)
    asyncio.run(archive(parser.parse_args().old_start))
