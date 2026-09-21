import asyncio
import logging
from datetime import datetime, time, timedelta, timezone
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
from beg_k_sebe_bot.bot.database.models import DailyCheckin, SentEvent, User
from beg_k_sebe_bot.bot.handlers.daily_checkin import checkin_reply_keyboard
from beg_k_sebe_bot.bot.utils.program import today_msk
from beg_k_sebe_bot.bot.handlers.final import send_final
from beg_k_sebe_bot.bot.handlers.weekly_reflection import send_reflection_prompts
from beg_k_sebe_bot.bot.services.weekly_summary import send_weekly_summary
from beg_k_sebe_bot.bot.services.admin_summary import send_admin_summary
from beg_k_sebe_bot.bot.texts import messages as msg

logger = logging.getLogger(__name__)

# Telegram allows ~30 outgoing messages/sec per bot; 20/sec gives comfortable headroom.
_MSG_INTERVAL = 1 / 20


async def _send_reminders(bot: Bot) -> None:
    today = today_msk()
    if today < settings.start_date or today >= settings.final_date:
        return

    marker_key = f"checkin_reminders:{today.isoformat()}"
    is_day_30 = today == settings.start_date + timedelta(days=29)

    async with AsyncSessionLocal() as session:
        if await session.get(SentEvent, marker_key) is not None:
            logger.info("Reminders already sent for %s, skipping", today)
            return

        users = (await session.execute(
            select(User).where(User.onboarding_completed_at.is_not(None))
        )).scalars().all()

        today_checkins = (await session.execute(
            select(DailyCheckin).where(DailyCheckin.date == today)
        )).scalars().all()
        status_by_user = {c.user_id: c.status for c in today_checkins}

    reminded = warned = 0
    for user in users:
        status = status_by_user.get(user.telegram_id)
        if status != "answered":
            text = msg.CHECKIN_REMINDER_RESUME if status == "pending" else msg.CHECKIN_REMINDER
            try:
                await bot.send_message(user.telegram_id, text, reply_markup=checkin_reply_keyboard())
                reminded += 1
            except Exception as e:
                logger.error("Failed to send reminder to %d: %s", user.telegram_id, e)

        if is_day_30:
            try:
                await bot.send_message(user.telegram_id, msg.DAY_30_WARNING)
                warned += 1
            except Exception as e:
                logger.error("Failed to send day30 warning to %d: %s", user.telegram_id, e)

        await asyncio.sleep(_MSG_INTERVAL)

    async with AsyncSessionLocal() as session:
        session.add(SentEvent(key=marker_key, sent_at=datetime.now(timezone.utc)))
        await session.commit()

    logger.info("Reminders done: reminded=%d warned=%d total=%d", reminded, warned, len(users))


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


async def _send_reflection_prompts(bot: Bot) -> None:
    async with AsyncSessionLocal() as session:
        await send_reflection_prompts(bot, session)


async def _send_admin_summary(bot: Bot) -> None:
    async with AsyncSessionLocal() as session:
        await send_admin_summary(bot, session)


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
        _send_reminders,
        trigger=CronTrigger(hour=settings.checkin_hour, minute=0, timezone=tz),
        args=[bot],
        id="checkin_reminders",
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
    scheduler.add_job(
        _send_reflection_prompts,
        trigger=CronTrigger(day_of_week=settings.weekly_reflection_dow, hour=settings.weekly_reflection_hour, minute=0, timezone=tz),
        args=[bot],
        id="weekly_reflection",
    )
    scheduler.add_job(
        _send_admin_summary,
        trigger=CronTrigger(day_of_week=settings.admin_summary_dow, hour=settings.admin_summary_hour, minute=0, timezone=tz),
        args=[bot],
        id="admin_summary",
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
    logger.info("Startup checkin catch-up: past reminder hour, ensuring today's reminders")
    await _send_reminders(bot)


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
