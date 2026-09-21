"""Unit tests for the stats core. Run: python -m beg_k_sebe_bot.tests.test_stats
or with pytest. No DB or aiogram needed."""
from datetime import date
from types import SimpleNamespace

from beg_k_sebe_bot.bot.services.stats import (
    compute_stats,
    current_streak,
    energy_movement_comparison,
)

START = date(2026, 9, 21)
PROGRAM_DAYS = 30


def _ci(day, *, status="answered", minutes=None, category=None, energy=None, movement="yes"):
    """Build a fake check-in for the given program day (1-based)."""
    return SimpleNamespace(
        date=date(2026, 9, 20 + day),
        day_number=day,
        status=status,
        minutes=minutes,
        activity_category=category,
        energy_level=energy,
        movement_done=movement,
    )


def test_completed_missed_pct():
    checkins = [_ci(1), _ci(2), _ci(3)]  # days 4 missed, 5 = today (in progress)
    s = compute_stats(start_date=START, program_days=PROGRAM_DAYS,
                      onboarding_date=START, today=date(2026, 9, 25), checkins=checkins)
    assert s.program_day == 5
    assert s.completed == 3
    assert s.missed == 1          # only day 4 (fully elapsed, not done); today not counted as miss
    assert s.completion_pct == 60  # 3 of 5


def test_minutes_by_category_and_skip():
    checkins = [
        _ci(1, minutes=22, category="walk"),
        _ci(2, minutes=30, category="run"),
        _ci(3, minutes=45, category="own"),
        _ci(4, minutes=0, category="walk"),   # skipped -> not counted
    ]
    s = compute_stats(start_date=START, program_days=PROGRAM_DAYS,
                      onboarding_date=START, today=date(2026, 9, 25), checkins=checkins)
    assert s.total_minutes == 97
    assert s.minutes_by_category == {"walk": 22, "run": 30, "own": 45}


def test_streak_including_today():
    checkins = [_ci(1), _ci(2), _ci(3)]
    assert current_streak(checkins, date(2026, 9, 23), START) == 3


def test_streak_ending_yesterday():
    checkins = [_ci(1), _ci(2), _ci(3)]  # today (day 4) not done yet
    assert current_streak(checkins, date(2026, 9, 24), START) == 3


def test_streak_broken_by_gap():
    checkins = [_ci(1), _ci(2), _ci(4)]  # gap on day 3
    assert current_streak(checkins, date(2026, 9, 24), START) == 1


def test_late_registration_ignores_pre_reg_days():
    # Registered on day 4; days 1-3 must not count as missed.
    checkins = [_ci(4), _ci(5)]
    s = compute_stats(start_date=START, program_days=PROGRAM_DAYS,
                      onboarding_date=date(2026, 9, 24), today=date(2026, 9, 27), checkins=checkins)
    assert s.completed == 2
    assert s.missed == 1          # only day 6; days 1-3 (before reg) excluded
    assert s.completion_pct == 50  # 2 of 4 (days 4-7)


def test_format_change_splits_minutes_by_category():
    # Walked days 1-2, then switched to running on day 3 — each check-in carries its own
    # category snapshot, so minutes must split correctly across formats.
    checkins = [
        _ci(1, minutes=22, category="walk"),
        _ci(2, minutes=30, category="walk"),
        _ci(3, minutes=25, category="run"),
        _ci(4, minutes=40, category="run"),
    ]
    s = compute_stats(start_date=START, program_days=PROGRAM_DAYS,
                      onboarding_date=START, today=date(2026, 9, 25), checkins=checkins)
    assert s.minutes_by_category == {"walk": 52, "run": 65}
    assert s.total_minutes == 117


def test_completion_pct_capped_at_100_late_registration():
    # Registered day 5, answered every day since — must be 100%, not over.
    checkins = [_ci(5), _ci(6), _ci(7)]
    s = compute_stats(start_date=START, program_days=PROGRAM_DAYS,
                      onboarding_date=date(2026, 9, 25), today=date(2026, 9, 27), checkins=checkins)
    assert s.completion_pct == 100
    assert s.missed == 0


def test_program_day_capped_after_end():
    s = compute_stats(start_date=START, program_days=PROGRAM_DAYS,
                      onboarding_date=START, today=date(2026, 11, 1), checkins=[_ci(1)])
    assert s.program_day == 30


def test_energy_comparison():
    checkins = [
        _ci(1, energy=7, movement="yes"),
        _ci(2, energy=8, movement="partial"),
        _ci(3, energy=5, movement="no"),
    ]
    assert energy_movement_comparison(checkins) == (7.5, 5.0)


def test_energy_comparison_none_when_not_higher():
    checkins = [_ci(1, energy=4, movement="yes"), _ci(2, energy=6, movement="no")]
    assert energy_movement_comparison(checkins) is None


def test_energy_comparison_none_without_both_groups():
    checkins = [_ci(1, energy=7, movement="yes"), _ci(2, energy=8, movement="yes")]
    assert energy_movement_comparison(checkins) is None


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    _run_all()
