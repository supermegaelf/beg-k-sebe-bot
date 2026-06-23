from beg_k_sebe_bot.bot.database.models import DailyCheckin, MovementFormatChange

FORMAT_VALUES: dict[str, dict] = {
    "walk_22min": {"unit_walk": 22, "unit_run_min": 0, "unit_run_km": 0},
    "run_22min":  {"unit_walk": 0,  "unit_run_min": 22, "unit_run_km": 0},
    "run_5km":    {"unit_walk": 0,  "unit_run_min": 0,  "unit_run_km": 5.0},
}

MULTIPLIERS = {"yes": 1.0, "partial": 0.5, "no": 0.0}


def format_for_day(
    day_number: int,
    format_changes: list[MovementFormatChange],
    initial_format: str,
) -> str:
    active = initial_format
    for change in sorted(format_changes, key=lambda c: (c.changed_on_day, c.changed_at)):
        if change.changed_on_day <= day_number:
            active = change.new_format
    return active


def total_movement(
    checkins: list[DailyCheckin],
    format_changes: list[MovementFormatChange],
    initial_format: str,
) -> dict[str, float]:
    result = {"min_walk": 0.0, "min_run": 0.0, "km_run": 0.0}
    for checkin in checkins:
        if checkin.movement_done not in MULTIPLIERS:
            continue
        fmt = format_for_day(checkin.day_number, format_changes, initial_format)
        values = FORMAT_VALUES.get(fmt, FORMAT_VALUES["walk_22min"])
        multiplier = MULTIPLIERS[checkin.movement_done]
        result["min_walk"] += values["unit_walk"] * multiplier
        result["min_run"] += values["unit_run_min"] * multiplier
        result["km_run"] += values["unit_run_km"] * multiplier
    return result
