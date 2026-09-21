from aiogram import Router, F
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from beg_k_sebe_bot.bot.config import settings
from beg_k_sebe_bot.bot.database.models import DailyCheckin, User
from beg_k_sebe_bot.bot.services import stats
from beg_k_sebe_bot.bot.texts import messages as msg
from beg_k_sebe_bot.bot.utils.program import today_msk
from beg_k_sebe_bot.bot.utils.pluralize import pluralize

router = Router()

_PROGRAM_DAYS = settings.final_program_day - 1  # check-in days (1..30)


@router.message(F.text == msg.MY_PROGRESS_BUTTON)
async def show_progress(message: Message, session: AsyncSession) -> None:
    user = await session.get(User, message.from_user.id)
    if user is None or user.onboarding_completed_at is None:
        return

    checkins = (await session.execute(
        select(DailyCheckin).where(DailyCheckin.user_id == message.from_user.id)
    )).scalars().all()

    s = stats.compute_stats(
        start_date=settings.start_date,
        program_days=_PROGRAM_DAYS,
        onboarding_date=user.onboarding_completed_at.date(),
        today=today_msk(),
        checkins=checkins,
    )

    text = msg.PROGRESS_HEADER.format(day=s.program_day, total_days=_PROGRAM_DAYS)
    if user.goal:
        text += msg.PROGRESS_GOAL.format(goal=user.goal)

    text += msg.PROGRESS_CHECKINS.format(
        completed=s.completed,
        missed=s.missed,
        pct=s.completion_pct,
        streak=s.current_streak,
        streak_word=pluralize(s.current_streak, "день", "дня", "дней"),
    )

    if s.total_minutes > 0:
        text += msg.PROGRESS_MOVEMENT_HEADER
        for key in stats.CATEGORY_ORDER:
            value = s.minutes_by_category.get(key)
            if value:
                text += f"{msg.CATEGORY_LABELS[key]}: {value} мин\n"
        text += msg.PROGRESS_MOVEMENT_TOTAL.format(total=s.total_minutes)
    else:
        text += msg.PROGRESS_NO_MOVEMENT

    if s.energy_series:
        series = " → ".join(str(e) for _, e in s.energy_series[-10:])
        text += msg.PROGRESS_ENERGY.format(series=series, avg=f"{s.energy_avg:.1f}")

    await message.answer(text)
