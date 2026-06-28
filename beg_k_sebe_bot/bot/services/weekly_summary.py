import asyncio
import logging
import random
from collections import defaultdict
from datetime import timedelta
from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from beg_k_sebe_bot.bot.config import settings
from beg_k_sebe_bot.bot.database.models import DailyCheckin, MovementFormatChange, User
from beg_k_sebe_bot.bot.services.movement_calc import total_movement
from beg_k_sebe_bot.bot.texts import messages as msg
from beg_k_sebe_bot.bot.utils.program import today_msk

logger = logging.getLogger(__name__)


async def send_weekly_summary(bot: Bot, session: AsyncSession) -> None:
    if settings.group_chat_id is None:
        logger.warning("GROUP_CHAT_ID not set, skipping weekly summary")
        return

    today = today_msk()
    week_start = today - timedelta(days=6)
    effective_start = max(week_start, settings.start_date)

    users_result = await session.execute(
        select(User).where(User.onboarding_completed_at.is_not(None))
    )
    users = users_result.scalars().all()
    if not users:
        return

    user_ids = [u.telegram_id for u in users]

    checkins_result = await session.execute(
        select(DailyCheckin).where(
            DailyCheckin.date >= effective_start,
            DailyCheckin.date <= today,
            DailyCheckin.status == "answered",
            DailyCheckin.user_id.in_(user_ids),
        )
    )
    answered_checkins = checkins_result.scalars().all()

    format_changes_result = await session.execute(
        select(MovementFormatChange).where(MovementFormatChange.user_id.in_(user_ids))
    )
    all_format_changes = format_changes_result.scalars().all()

    checkins_by_user: dict[int, list] = defaultdict(list)
    for c in answered_checkins:
        checkins_by_user[c.user_id].append(c)

    format_changes_by_user: dict[int, list] = defaultdict(list)
    for fc in all_format_changes:
        format_changes_by_user[fc.user_id].append(fc)

    total_min_walk = 0.0
    total_min_run = 0.0
    total_km_run = 0.0

    for user in users:
        user_checkins = checkins_by_user[user.telegram_id]
        if not user_checkins:
            continue
        totals = total_movement(user_checkins, format_changes_by_user[user.telegram_id], user.movement_format or "walk_22min")
        total_min_walk += totals["min_walk"]
        total_min_run += totals["min_run"]
        total_km_run += totals["km_run"]

    expected_total = sum(
        min(
            (today - max(effective_start, u.onboarding_completed_at.date())).days + 1,
            7,
        )
        for u in users
    )
    completion_pct = min(round(len(answered_checkins) / expected_total * 100), 100) if expected_total > 0 else 0

    movement_lines = ""
    if total_min_walk > 0:
        movement_lines += f"🚶 Ходьба: {int(total_min_walk)} мин\n"
    if total_min_run > 0:
        movement_lines += f"🏃 Бег: {int(total_min_run)} мин\n"
    if total_km_run > 0:
        movement_lines += f"🏃 Бег (дистанция): {total_km_run:.1f} км\n"

    text = msg.WEEKLY_SUMMARY.format(
        movement_lines=movement_lines or "Данных о движении пока нет.\n",
        completion_pct=completion_pct,
        motivation=random.choice(msg.WEEKLY_MOTIVATION_PHRASES),
    )

    try:
        await bot.send_message(settings.group_chat_id, text)
        logger.info(
            "Weekly summary sent: users=%d answered=%d expected=%d pct=%d%%",
            len(users), len(answered_checkins), expected_total, completion_pct,
        )
    except TelegramRetryAfter as e:
        logger.warning("Rate limited sending weekly summary, retrying after %ds", e.retry_after)
        await asyncio.sleep(e.retry_after)
        await bot.send_message(settings.group_chat_id, text)
    except Exception as e:
        logger.error("Failed to send weekly summary: %s", e)
