from datetime import date
from beg_k_sebe_bot.bot.config import settings


def current_program_day() -> int:
    delta = (date.today() - settings.start_date).days + 1
    return max(1, delta)
