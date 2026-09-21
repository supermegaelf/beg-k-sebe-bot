import asyncio
import logging
import random
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from beg_k_sebe_bot.bot.config import settings
from beg_k_sebe_bot.bot.database.models import DailyCheckin, SentEvent, User
from beg_k_sebe_bot.bot.texts import messages as msg
from beg_k_sebe_bot.bot.utils.program import today_msk

logger = logging.getLogger(__name__)

# Movement lines are shown in this order, only when the value is above zero.
_CATEGORY_LABELS = [
    ("walk", "🚶 Ходьба"),
    ("run", "🏃 Бег"),
    ("own", "🏃 Своя активность"),
]


async def send_weekly_summary(bot: Bot, session: AsyncSession) -> None:
    if settings.group_chat_id is None:
        logger.warning("GROUP_CHAT_ID not set, skipping weekly summary")
        return

    today = today_msk()
    week_end = today - timedelta(days=1)          # Sunday of the finished week
    week_start = week_end - timedelta(days=6)     # its Monday

    if week_end < settings.start_date:
        logger.info("Finished week %s..%s is before start, skipping", week_start, week_end)
        return

    effective_start = max(week_start, settings.start_date)

    marker_key = f"weekly_summary:{today.isoformat()}"
    if await session.get(SentEvent, marker_key) is not None:
        logger.info("Weekly summary already sent for %s, skipping", today)
        return

    users = (await session.execute(
        select(User).where(User.onboarding_completed_at.is_not(None))
    )).scalars().all()
    if not users:
        return
    user_ids = [u.telegram_id for u in users]

    answered = (await session.execute(
        select(DailyCheckin).where(
            DailyCheckin.date >= effective_start,
            DailyCheckin.date <= week_end,
            DailyCheckin.status == "answered",
            DailyCheckin.user_id.in_(user_ids),
        )
    )).scalars().all()

    minutes_by_category: dict[str, float] = defaultdict(float)
    for c in answered:
        if c.minutes:
            minutes_by_category[c.activity_category] += c.minutes

    expected_total = 0
    for u in users:
        user_start = max(effective_start, u.onboarding_completed_at.date())
        days = (week_end - user_start).days + 1
        if days > 0:
            expected_total += min(days, 7)
    completion_pct = min(round(len(answered) / expected_total * 100), 100) if expected_total > 0 else 0

    movement_lines = ""
    for key, label in _CATEGORY_LABELS:
        total = minutes_by_category.get(key, 0)
        if total > 0:
            movement_lines += f"{label}: {int(total)} мин\n"

    text = msg.WEEKLY_SUMMARY.format(
        movement_lines=movement_lines or msg.WEEKLY_NO_MOVEMENT,
        completion_pct=completion_pct,
        motivation=random.choice(msg.WEEKLY_MOTIVATION_PHRASES),
    )

    sent = False
    for attempt in range(2):
        try:
            await bot.send_message(settings.group_chat_id, text, message_thread_id=settings.info_topic_id)
            sent = True
            logger.info(
                "Weekly summary sent for %s..%s: users=%d answered=%d expected=%d pct=%d%%",
                effective_start, week_end, len(users), len(answered), expected_total, completion_pct,
            )
            break
        except TelegramRetryAfter as e:
            logger.warning("Rate limited sending weekly summary, retrying after %ds", e.retry_after)
            await asyncio.sleep(e.retry_after)
        except Exception as e:
            logger.error("Failed to send weekly summary: %s", e)
            break

    if sent:
        session.add(SentEvent(key=marker_key, sent_at=datetime.now(timezone.utc)))
        await session.commit()
