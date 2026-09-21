"""Single source of truth for participant statistics.

Pure, dependency-free calculations over check-in records so the same numbers
feed "My progress", the final summary and the admin export. Functions take
plain records (anything exposing the used attributes), which keeps them unit
testable without a database.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta

CATEGORY_ORDER = ("walk", "run", "own")
_MOVEMENT_YES = ("yes", "partial")


@dataclass
class UserStats:
    program_day: int
    completed: int
    missed: int
    completion_pct: int
    total_minutes: int
    minutes_by_category: dict[str, int]
    current_streak: int
    energy_series: list[tuple[int, int]] = field(default_factory=list)
    energy_avg: float | None = None


def _program_day(start_date: date, day: date, program_days: int) -> int:
    return max(1, min((day - start_date).days + 1, program_days))


def compute_stats(
    *,
    start_date: date,
    program_days: int,
    onboarding_date: date,
    today: date,
    checkins: list,
) -> UserStats:
    day_last = start_date + timedelta(days=program_days - 1)
    user_start = max(start_date, onboarding_date)

    answered = [c for c in checkins if c.status == "answered"]

    # "Missed" counts only fully-elapsed program days without an answered check-in,
    # so today (still actionable) is never shown as a miss.
    last_full = min(today - timedelta(days=1), day_last)
    expected_past = max(0, (last_full - user_start).days + 1) if last_full >= user_start else 0
    completed_past = sum(1 for c in answered if c.date < today)
    missed = max(0, expected_past - completed_past)

    last_incl = min(today, day_last)
    expected_incl = max(0, (last_incl - user_start).days + 1) if last_incl >= user_start else 0
    completed = len(answered)
    completion_pct = min(round(completed / expected_incl * 100), 100) if expected_incl > 0 else 0

    total_minutes = 0
    minutes_by_category: dict[str, int] = {}
    for c in answered:
        if c.minutes:
            total_minutes += c.minutes
            if c.activity_category:
                minutes_by_category[c.activity_category] = minutes_by_category.get(c.activity_category, 0) + c.minutes

    energy_series = sorted(
        ((c.day_number, c.energy_level) for c in answered if c.energy_level is not None),
        key=lambda t: t[0],
    )
    energy_avg = sum(e for _, e in energy_series) / len(energy_series) if energy_series else None

    return UserStats(
        program_day=_program_day(start_date, today, program_days),
        completed=completed,
        missed=missed,
        completion_pct=completion_pct,
        total_minutes=total_minutes,
        minutes_by_category=minutes_by_category,
        current_streak=current_streak(checkins, today, user_start),
        energy_series=energy_series,
        energy_avg=energy_avg,
    )


def current_streak(checkins: list, today: date, user_start: date) -> int:
    """Consecutive days with an answered check-in ending today (or yesterday if
    today is not done yet). A gap resets it to zero."""
    answered_dates = {c.date for c in checkins if c.status == "answered"}
    cursor = today if today in answered_dates else today - timedelta(days=1)
    streak = 0
    while cursor >= user_start and cursor in answered_dates:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def energy_movement_comparison(checkins: list) -> tuple[float, float] | None:
    """Average energy on days with movement vs without — only when movement days
    scored strictly higher (used for the final summary phrase)."""
    with_mv = [c.energy_level for c in checkins
               if c.status == "answered" and c.movement_done in _MOVEMENT_YES and c.energy_level is not None]
    without_mv = [c.energy_level for c in checkins
                  if c.status == "answered" and c.movement_done == "no" and c.energy_level is not None]
    if not with_mv or not without_mv:
        return None
    avg_with = sum(with_mv) / len(with_mv)
    avg_without = sum(without_mv) / len(without_mv)
    return (avg_with, avg_without) if avg_with > avg_without else None
