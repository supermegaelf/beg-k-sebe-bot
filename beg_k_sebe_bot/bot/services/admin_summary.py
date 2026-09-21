import csv
import io
import logging
from collections import defaultdict
from datetime import datetime, timezone
from aiogram import Bot
from aiogram.types import BufferedInputFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from beg_k_sebe_bot.bot.config import settings
from beg_k_sebe_bot.bot.database.models import DailyCheckin, SentEvent, User, WeeklyReflection
from beg_k_sebe_bot.bot.services import stats
from beg_k_sebe_bot.bot.utils.program import today_msk

logger = logging.getLogger(__name__)

_PROGRAM_DAYS = settings.final_program_day - 1

# Plain format names for the export (no emoji, one word).
_FORMAT_NAME = {"walk_22min": "ходьба", "run_22min": "бег", "own": "другое"}

_COLUMNS = [
    "telegram_id", "username", "onboarding_date", "format",
    "completed", "missed", "completion_pct",
    "minutes_walk", "minutes_run", "minutes_own", "total_minutes",
    "avg_energy", "current_streak",
    "reflection_week", "reflection_result", "reflection_helped",
    "reflection_hardest", "reflection_progress", "reflection_share",
]


def _build_csv(users, checkins_by_user, reflection_by_user, today) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")  # ; opens straight into columns in RU Excel
    writer.writerow(_COLUMNS)

    for user in users:
        checkins = checkins_by_user.get(user.telegram_id, [])
        onboarding_date = user.onboarding_completed_at.date() if user.onboarding_completed_at else settings.start_date
        s = stats.compute_stats(
            start_date=settings.start_date,
            program_days=_PROGRAM_DAYS,
            onboarding_date=onboarding_date,
            today=today,
            checkins=checkins,
        )
        mins = s.minutes_by_category
        r = reflection_by_user.get(user.telegram_id)
        writer.writerow([
            user.telegram_id,
            user.username or "",
            onboarding_date.isoformat(),
            _FORMAT_NAME.get(user.movement_format, user.movement_format or ""),
            s.completed, s.missed, s.completion_pct,
            mins.get("walk", 0), mins.get("run", 0), mins.get("own", 0), s.total_minutes,
            f"{s.energy_avg:.1f}" if s.energy_avg is not None else "",
            s.current_streak,
            r.week_number if r else "",
            (r.result_text if r else "") or "",
            (r.helped_text if r else "") or "",
            (r.hardest_text if r else "") or "",
            (r.progress_text if r else "") or "",
            (r.share_text if r else "") or "",
        ])

    return buffer.getvalue().encode("utf-8-sig")


async def send_admin_summary(bot: Bot, session: AsyncSession) -> None:
    admin_ids = settings.admin_id_list
    if not admin_ids:
        logger.warning("ADMIN_IDS not set, skipping admin summary")
        return

    today = today_msk()
    marker_key = f"admin_summary:{today.isoformat()}"
    if await session.get(SentEvent, marker_key) is not None:
        logger.info("Admin summary already sent for %s, skipping", today)
        return

    users = (await session.execute(
        select(User).where(User.onboarding_completed_at.is_not(None))
    )).scalars().all()
    if not users:
        return
    user_ids = [u.telegram_id for u in users]

    checkins = (await session.execute(
        select(DailyCheckin).where(DailyCheckin.user_id.in_(user_ids))
    )).scalars().all()
    checkins_by_user: dict[int, list] = defaultdict(list)
    for c in checkins:
        checkins_by_user[c.user_id].append(c)

    reflections = (await session.execute(
        select(WeeklyReflection).where(
            WeeklyReflection.user_id.in_(user_ids),
            WeeklyReflection.status == "answered",
        )
    )).scalars().all()
    reflection_by_user: dict[int, WeeklyReflection] = {}
    for r in reflections:  # keep the latest answered week per user
        cur = reflection_by_user.get(r.user_id)
        if cur is None or r.week_number > cur.week_number:
            reflection_by_user[r.user_id] = r

    data = _build_csv(users, checkins_by_user, reflection_by_user, today)
    filename = f"beg_k_sebe_{today.isoformat()}.csv"

    sent = False
    for admin_id in admin_ids:
        try:
            await bot.send_document(
                admin_id,
                BufferedInputFile(data, filename=filename),
                caption=f"Сводка по участникам на {today.isoformat()} ({len(users)} чел.)",
            )
            sent = True
        except Exception as e:
            logger.error("Failed to send admin summary to %d: %s", admin_id, e)

    if sent:
        session.add(SentEvent(key=marker_key, sent_at=datetime.now(timezone.utc)))
        await session.commit()
    logger.info("Admin summary sent to %d admins for %d users", len(admin_ids), len(users))
