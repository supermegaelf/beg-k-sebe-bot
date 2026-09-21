from datetime import timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from beg_k_sebe_bot.bot.config import settings
from beg_k_sebe_bot.bot.database.models import DailyCheckin, User
from beg_k_sebe_bot.bot.services import stats
from beg_k_sebe_bot.bot.texts import messages as msg
from beg_k_sebe_bot.bot.utils.pluralize import pluralize

_PROGRAM_DAYS = settings.final_program_day - 1  # check-in days (1..30)
_FULL_SUMMARY_THRESHOLD_PCT = 40


async def build_final_summary(user: User, session: AsyncSession) -> str:
    answered = (await session.execute(
        select(DailyCheckin).where(
            DailyCheckin.user_id == user.telegram_id,
            DailyCheckin.status == "answered",
        )
    )).scalars().all()

    onboarding_date = user.onboarding_completed_at.date() if user.onboarding_completed_at else settings.start_date
    day_last = settings.start_date + timedelta(days=_PROGRAM_DAYS - 1)

    s = stats.compute_stats(
        start_date=settings.start_date,
        program_days=_PROGRAM_DAYS,
        onboarding_date=onboarding_date,
        today=day_last,
        checkins=answered,
    )

    if s.completion_pct < _FULL_SUMMARY_THRESHOLD_PCT:
        return msg.FINAL_FALLBACK_HEADER + _goal_block(user)

    join_day = user.joined_at.date() if user.joined_at else settings.start_date
    header_days = max(1, min((settings.final_date - join_day).days + 1, settings.final_program_day))
    text = msg.FINAL_SUMMARY_HEADER.format(
        days=header_days,
        days_word=pluralize(header_days, "день", "дня", "дней"),
    )

    mins = s.minutes_by_category
    if mins.get("walk"):
        text += msg.FINAL_MOVE_WALK.format(n=mins["walk"])
    if mins.get("run"):
        text += msg.FINAL_MOVE_RUN.format(n=mins["run"])
    if mins.get("own"):
        text += msg.FINAL_MOVE_OWN.format(n=mins["own"])

    text += _goal_block(user)

    comparison = stats.energy_movement_comparison(answered)
    if comparison:
        text += msg.FINAL_ENERGY_PHRASE.format(with_movement=comparison[0], without_movement=comparison[1])

    return text


def _goal_block(user: User) -> str:
    text = msg.FINAL_GOAL_HEADER
    text += msg.FINAL_GOAL_A.format(
        score=user.point_a_score if user.point_a_score is not None else "—",
        text=user.point_a_text or "",
    )
    if user.point_a_score is not None and user.point_b_score is not None:
        delta = user.point_b_score - user.point_a_score
        direction = "+" if delta >= 0 else ""
        text += msg.FINAL_GOAL_B.format(
            score=user.point_b_score,
            text=user.point_b_text or "",
            direction=direction,
            delta=delta,
            delta_word=pluralize(abs(delta), "балл", "балла", "баллов"),
        )
    return text
