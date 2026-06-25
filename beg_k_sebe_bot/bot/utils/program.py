from datetime import date, datetime
from zoneinfo import ZoneInfo
from beg_k_sebe_bot.bot.config import settings


def today_msk() -> date:
    return datetime.now(ZoneInfo(settings.timezone)).date()


def current_program_day() -> int:
    delta = (today_msk() - settings.start_date).days + 1
    return max(1, delta)
