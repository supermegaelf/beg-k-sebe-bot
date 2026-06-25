import logging
import random
from datetime import timedelta
from aiogram import Bot
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

    week_start = today_msk() - timedelta(days=6)
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
            DailyCheckin.date <= today_msk(),
            DailyCheckin.status == "answered",
            DailyCheckin.user_id.in_(user_ids),
        )
    )
    answered_checkins = checkins_result.scalars().all()

    format_changes_result = await session.execute(
        select(MovementFormatChange).where(MovementFormatChange.user_id.in_(user_ids))
    )
    all_format_changes = format_changes_result.scalars().all()

    total_min_walk = 0.0
    total_min_run = 0.0
    total_km_run = 0.0

    for user in users:
        user_checkins = [c for c in answered_checkins if c.user_id == user.telegram_id]
        if not user_checkins:
            continue
        user_format_changes = [fc for fc in all_format_changes if fc.user_id == user.telegram_id]
        totals = total_movement(user_checkins, user_format_changes, user.movement_format or "walk_22min")
        total_min_walk += totals["min_walk"]
        total_min_run += totals["min_run"]
        total_km_run += totals["km_run"]

    expected_total = sum(
        min((today_msk() - max(effective_start, u.joined_at.date() if u.joined_at else effective_start)).days + 1, 7)
        for u in users
    )
    completion_pct = round(len(answered_checkins) / expected_total * 100) if expected_total > 0 else 0

    movement_lines = ""
    if total_min_walk > 0:
        movement_lines += f"🚶 Ходьба: {int(total_min_walk)} мин\n"
    if total_min_run > 0:
        movement_lines += f"🏃 Бег: {int(total_min_run)} мин\n"
    if total_km_run > 0:
        movement_lines += f"🏃 Бег: {total_km_run:.1f} км\n"

    text = msg.WEEKLY_SUMMARY.format(
        movement_lines=movement_lines or "Данных о движении пока нет.\n",
        completion_pct=completion_pct,
        motivation=random.choice(msg.WEEKLY_MOTIVATION_PHRASES),
    )

    await bot.send_message(settings.group_chat_id, text)
