"""Point B and the final summary for the previous stream, read from the archive_* tables
written by archive_stream.py. Active only while PREVIOUS_STREAM_START is set."""
import html
from types import SimpleNamespace

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from beg_k_sebe_bot.bot.config import settings
from beg_k_sebe_bot.bot.services.final_summary import render_final_summary
from beg_k_sebe_bot.bot.texts import messages as msg

_USER_COLUMNS = (
    "telegram_id, username, goal, movement_format, joined_at, onboarding_completed_at, "
    "point_a_score, point_a_text, point_b_score, point_b_text, final_sent"
)


async def load_user(session: AsyncSession, user_id: int) -> SimpleNamespace | None:
    if settings.previous_stream_start is None:
        return None
    row = (await session.execute(
        text(
            f"SELECT {_USER_COLUMNS} FROM archive_users "
            "WHERE telegram_id = :id AND stream_start = :s AND onboarding_completed_at IS NOT NULL"
        ),
        {"id": user_id, "s": settings.previous_stream_start},
    )).mappings().first()
    return SimpleNamespace(**row) if row else None


async def pending_user(session: AsyncSession, user_id: int) -> SimpleNamespace | None:
    """The archived user if they took part in the previous stream and haven't finished point B yet."""
    user = await load_user(session, user_id)
    return user if user is not None and user.point_b_text is None else None


async def pending_user_ids(session: AsyncSession) -> list[int]:
    if settings.previous_stream_start is None:
        return []
    return list((await session.execute(
        text(
            "SELECT telegram_id FROM archive_users WHERE stream_start = :s "
            "AND onboarding_completed_at IS NOT NULL AND point_b_text IS NULL ORDER BY telegram_id"
        ),
        {"s": settings.previous_stream_start},
    )).scalars().all())


async def save_score(session: AsyncSession, user_id: int, score: int) -> None:
    await session.execute(
        text("UPDATE archive_users SET point_b_score = :v WHERE telegram_id = :id AND stream_start = :s"),
        {"v": score, "id": user_id, "s": settings.previous_stream_start},
    )
    await session.commit()


async def save_text(session: AsyncSession, user_id: int, value: str) -> None:
    await session.execute(
        text(
            "UPDATE archive_users SET point_b_text = :v, final_sent = true "
            "WHERE telegram_id = :id AND stream_start = :s"
        ),
        {"v": value, "id": user_id, "s": settings.previous_stream_start},
    )
    await session.commit()


def intro_text(user: SimpleNamespace) -> str:
    return msg.PREV_FINAL_INTRO.format(
        goal=html.escape(user.goal or "—"),
        score=user.point_a_score if user.point_a_score is not None else "—",
        text=html.escape(user.point_a_text or ""),
    )


async def build_summary(session: AsyncSession, user: SimpleNamespace) -> str:
    rows = (await session.execute(
        text(
            "SELECT date, day_number, status, minutes, activity_category, energy_level, movement_done "
            "FROM archive_daily_checkins WHERE user_id = :id AND stream_start = :s AND status = 'answered'"
        ),
        {"id": user.telegram_id, "s": settings.previous_stream_start},
    )).mappings().all()
    answered = [SimpleNamespace(**r) for r in rows]
    return render_final_summary(user, answered, settings.previous_stream_start)
