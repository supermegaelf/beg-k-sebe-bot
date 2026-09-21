import io
import logging
from collections import defaultdict
from datetime import datetime, timezone
from aiogram import Bot
from aiogram.types import BufferedInputFile
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
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

# (заголовок, ширина колонки). Порядок совпадает с _row() ниже.
_COLUMNS = [
    ("Telegram ID", 14),
    ("Юзернейм", 18),
    ("Дата регистрации", 16),
    ("Формат", 12),
    ("Выполнено", 11),
    ("Пропущено", 11),
    ("% вып.", 8),
    ("Ходьба, мин", 12),
    ("Бег, мин", 10),
    ("Другое, мин", 12),
    ("Всего, мин", 11),
    ("Сред. сост.", 11),
    ("Серия, дн.", 10),
    ("Неделя рефл.", 12),
    ("Рефлексия: результат", 34),
    ("Рефлексия: что помогало", 34),
    ("Рефлексия: сложнее всего", 34),
    ("Рефлексия: ближе к цели", 34),
    ("Рефлексия: чем поделиться", 34),
]


def _row(user, checkins_by_user, reflection_by_user, today) -> list:
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
    return [
        str(user.telegram_id),
        user.username or "",
        onboarding_date.isoformat(),
        _FORMAT_NAME.get(user.movement_format, user.movement_format or ""),
        s.completed, s.missed, s.completion_pct,
        mins.get("walk", 0), mins.get("run", 0), mins.get("own", 0), s.total_minutes,
        round(s.energy_avg, 1) if s.energy_avg is not None else "",
        s.current_streak,
        r.week_number if r else "",
        (r.result_text if r else "") or "",
        (r.helped_text if r else "") or "",
        (r.hardest_text if r else "") or "",
        (r.progress_text if r else "") or "",
        (r.share_text if r else "") or "",
    ]


def _build_xlsx(users, checkins_by_user, reflection_by_user, today) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Участники"

    header_fill = PatternFill("solid", fgColor="4472C4")
    header_font = Font(bold=True, color="FFFFFF")
    thin = Side(style="thin", color="D9D9D9")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    for col, (title, width) in enumerate(_COLUMNS, start=1):
        cell = ws.cell(row=1, column=col, value=title)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = border
        ws.column_dimensions[get_column_letter(col)].width = width

    for i, user in enumerate(users, start=2):
        for col, value in enumerate(_row(user, checkins_by_user, reflection_by_user, today), start=1):
            cell = ws.cell(row=i, column=col, value=value)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.border = border

    ws.row_dimensions[1].height = 30
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(_COLUMNS))}{len(users) + 1}"

    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


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

    data = _build_xlsx(users, checkins_by_user, reflection_by_user, today)
    filename = f"beg_k_sebe_{today.isoformat()}.xlsx"

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
