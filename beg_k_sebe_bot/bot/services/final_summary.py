from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from beg_k_sebe_bot.bot.config import settings
from beg_k_sebe_bot.bot.database.models import DailyCheckin, MovementFormatChange, User
from beg_k_sebe_bot.bot.services.movement_calc import total_movement
from beg_k_sebe_bot.bot.texts import messages as msg
from beg_k_sebe_bot.bot.utils.pluralize import pluralize


async def build_final_summary(user: User, session: AsyncSession) -> str:
    checkins_result = await session.execute(
        select(DailyCheckin).where(
            DailyCheckin.user_id == user.telegram_id,
            DailyCheckin.status == "answered",
        )
    )
    checkins = checkins_result.scalars().all()

    format_changes_result = await session.execute(
        select(MovementFormatChange).where(MovementFormatChange.user_id == user.telegram_id)
    )
    format_changes = format_changes_result.scalars().all()

    join_day = user.joined_at.date() if user.joined_at else settings.start_date
    lived_days = (settings.final_date - join_day).days + 1
    lived_days = max(1, min(lived_days, settings.final_program_day))

    if len(checkins) / lived_days < 0.4:
        return _build_fallback(user, lived_days)

    return _build_full(user, checkins, format_changes, lived_days)


def _build_fallback(user: User, lived_days: int) -> str:
    days_word = pluralize(lived_days, "день", "дня", "дней")
    text = msg.FINAL_FALLBACK_HEADER
    text += msg.FINAL_WHEEL_HEADER
    text += _wheel_deltas(user)
    return text


def _build_full(
    user: User,
    checkins: list[DailyCheckin],
    format_changes: list[MovementFormatChange],
    lived_days: int,
) -> str:
    days_word = pluralize(lived_days, "день", "дня", "дней")
    text = msg.FINAL_SUMMARY_HEADER.format(days=lived_days, days_word=days_word)

    totals = total_movement(checkins, format_changes, user.movement_format or "walk_22min")
    if totals["min_walk"] > 0:
        text += msg.FINAL_MOVEMENT_LINE.format(value=int(totals["min_walk"]), unit="мин ходьбы")
    if totals["min_run"] > 0:
        text += msg.FINAL_MOVEMENT_LINE.format(value=int(totals["min_run"]), unit="мин бега")
    if totals["km_run"] > 0:
        text += msg.FINAL_MOVEMENT_LINE.format(value=round(totals["km_run"], 1), unit="км бега")

    text += msg.FINAL_WHEEL_HEADER
    text += _wheel_deltas(user)

    energy_phrase = _energy_phrase(checkins)
    if energy_phrase:
        text += energy_phrase

    return text


def _wheel_deltas(user: User) -> str:
    lines = ""
    spheres = [
        ("Деньги", user.wheel_a_money, user.wheel_b_money),
        ("Отношения", user.wheel_a_relationships, user.wheel_b_relationships),
        ("Здоровье", user.wheel_a_health, user.wheel_b_health),
    ]
    for sphere, a, b in spheres:
        if a is None or b is None:
            continue
        delta = b - a
        direction = "+" if delta >= 0 else ""
        delta_word = pluralize(abs(delta), "балл", "балла", "баллов")
        lines += msg.FINAL_WHEEL_DELTA.format(
            sphere=sphere, a=a, b=b,
            direction=direction, delta=delta, delta_word=delta_word,
        )
    return lines


def _energy_phrase(checkins: list[DailyCheckin]) -> str:
    with_movement = [c.energy_level for c in checkins if c.movement_done in ("yes", "partial") and c.energy_level]
    without_movement = [c.energy_level for c in checkins if c.movement_done == "no" and c.energy_level]
    if not with_movement or not without_movement:
        return ""
    avg_with = sum(with_movement) / len(with_movement)
    avg_without = sum(without_movement) / len(without_movement)
    if avg_with > avg_without:
        return msg.FINAL_ENERGY_PHRASE.format(
            with_movement=avg_with, without_movement=avg_without
        )
    return ""
