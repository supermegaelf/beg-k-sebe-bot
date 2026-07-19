import asyncio
import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.base import BaseStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy import select, update

from beg_k_sebe_bot.bot.config import settings
from beg_k_sebe_bot.bot.database.db import AsyncSessionLocal
from beg_k_sebe_bot.bot.database.models import DailyCheckin, User
from beg_k_sebe_bot.bot.handlers.daily_checkin import send_checkin
from beg_k_sebe_bot.bot.utils.program import today_msk
from beg_k_sebe_bot.bot.handlers.final import send_final
from beg_k_sebe_bot.bot.services.weekly_summary import send_weekly_summary
from beg_k_sebe_bot.bot.texts import messages as msg

logger = logging.getLogger(__name__)

# Telegram allows ~30 outgoing messages/sec per bot; 20/sec gives comfortable headroom.
_MSG_INTERVAL = 1 / 20


async def _send_daily_checkins(bot: Bot, storage: BaseStorage) -> None:
    today = today_msk()
    if today < settings.start_date or today >= settings.final_date:
        return

    is_day_30 = today == settings.start_date + timedelta(days=29)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.onboarding_completed_at.is_not(None))
        )
        users = result.scalars().all()

    sent = skipped = errors = 0
    for user in users:
        key = StorageKey(bot_id=bot.id, chat_id=user.telegram_id, user_id=user.telegram_id)
        state = FSMContext(storage=storage, key=key)
        current = await state.get_state()
        if current is not None:
            logger.warning("Skipping checkin for %d (@%s): FSM state=%s", user.telegram_id, user.username, current)
            skipped += 1
            continue

        async with AsyncSessionLocal() as session:
            existing = await session.execute(
                select(DailyCheckin).where(
                    DailyCheckin.user_id == user.telegram_id,
                    DailyCheckin.date == today,
                )
            )
            if existing.scalar_one_or_none() is not None:
                logger.info("Checkin already exists for %d on %s, skipping", user.telegram_id, today)
                skipped += 1
                continue

            try:
                await send_checkin(user.telegram_id, bot, session, state, today)
                sent += 1
            except Exception as e:
                logger.error("Failed to send checkin to %d: %s", user.telegram_id, e)
                errors += 1

        if is_day_30:
            try:
                await bot.send_message(user.telegram_id, msg.DAY_30_WARNING)
            except Exception as e:
                logger.error("Failed to send day30 warning to %d: %s", user.telegram_id, e)

        await asyncio.sleep(_MSG_INTERVAL)

    logger.info("Daily checkin done: sent=%d skipped=%d errors=%d total=%d", sent, skipped, errors, len(users))


async def _mark_missed(bot: Bot, storage: BaseStorage) -> None:
    today = today_msk()
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            update(DailyCheckin)
            .where(DailyCheckin.date <= today, DailyCheckin.status == "pending")
            .values(status="missed")
            .returning(DailyCheckin.user_id)
        )
        missed_user_ids = [row[0] for row in result]
        await session.commit()
        logger.info("Marked %d checkins as missed for %s", len(missed_user_ids), today)

    for user_id in missed_user_ids:
        key = StorageKey(bot_id=bot.id, chat_id=user_id, user_id=user_id)
        try:
            await FSMContext(storage=storage, key=key).clear()
        except Exception as e:
            logger.error("Failed to clear FSM state for %d: %s", user_id, e)


async def _send_weekly_summary(bot: Bot) -> None:
    async with AsyncSessionLocal() as session:
        await send_weekly_summary(bot, session)


async def _trigger_final(bot: Bot, storage: BaseStorage) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(
                User.onboarding_completed_at.is_not(None),
                User.final_sent == False,  # noqa: E712
            )
        )
        users = result.scalars().all()
        for user in users:
            key = StorageKey(bot_id=bot.id, chat_id=user.telegram_id, user_id=user.telegram_id)
            state = FSMContext(storage=storage, key=key)
            try:
                await send_final(user.telegram_id, bot, state, session)
            except Exception as e:
                logger.error("Failed to send final to %d: %s", user.telegram_id, e)


def build_scheduler(bot: Bot, storage: BaseStorage) -> AsyncIOScheduler:
    tz = settings.timezone
    scheduler = AsyncIOScheduler()

    scheduler.add_job(
        _send_daily_checkins,
        trigger=CronTrigger(hour=settings.checkin_hour, minute=0, timezone=tz),
        args=[bot, storage],
        id="daily_checkin",
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        _mark_missed,
        trigger=CronTrigger(hour=23, minute=59, timezone=tz),
        args=[bot, storage],
        id="end_of_day_mark",
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        _send_weekly_summary,
        trigger=CronTrigger(day_of_week=settings.weekly_summary_dow, hour=settings.weekly_summary_hour, minute=0, timezone=tz),
        args=[bot],
        id="weekly_summary",
    )

    tz_obj = ZoneInfo(settings.timezone)
    final_dt = datetime.combine(settings.final_date, time(9, 0), tzinfo=tz_obj)
    if final_dt > datetime.now(tz=tz_obj):
        scheduler.add_job(
            _trigger_final,
            trigger=DateTrigger(run_date=final_dt),
            args=[bot, storage],
            id="final_trigger",
        )
    else:
        logger.warning("Final date %s is in the past, scheduling immediate final check on startup", settings.final_date)

    return scheduler


async def run_missed_final_if_needed(bot: Bot, storage: BaseStorage) -> None:
    if today_msk() >= settings.final_date:
        logger.info("Final date reached, checking for unsent finals...")
        await _trigger_final(bot, storage)


async def run_missed_checkin_if_needed(bot: Bot, storage: BaseStorage) -> None:
    now = datetime.now(ZoneInfo(settings.timezone))
    today = now.date()
    if today < settings.start_date or today >= settings.final_date:
        return
    if now.hour < settings.checkin_hour:
        return
    logger.info("Startup checkin catch-up: past checkin hour, ensuring today's checkins")
    await _send_daily_checkins(bot, storage)


_DOW_TO_WEEKDAY = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


async def run_missed_summary_if_needed(bot: Bot) -> None:
    now = datetime.now(ZoneInfo(settings.timezone))
    target_weekday = _DOW_TO_WEEKDAY.get(settings.weekly_summary_dow.lower())
    if target_weekday is None or now.date().weekday() != target_weekday:
        return
    if now.hour < settings.weekly_summary_hour:
        return
    logger.info("Startup summary catch-up: summary day past summary hour, ensuring weekly summary")
    await _send_weekly_summary(bot)
