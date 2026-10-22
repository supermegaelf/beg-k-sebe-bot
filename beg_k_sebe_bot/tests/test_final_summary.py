"""Final summary rendered for an arbitrary stream start (used for the archived previous stream).
No DB needed."""
import os
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("BOT_TOKEN", "123456:test")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from beg_k_sebe_bot.bot.services.final_summary import render_final_summary  # noqa: E402

START = date(2026, 9, 21)


def _user(**kw):
    base = dict(
        telegram_id=1,
        goal="Бегать",
        joined_at=datetime(2026, 9, 21, 9, tzinfo=timezone.utc),
        onboarding_completed_at=datetime(2026, 9, 21, 10, tzinfo=timezone.utc),
        point_a_score=4,
        point_a_text="Тяжело начать",
        point_b_score=7,
        point_b_text="Бегаю 3 раза в неделю",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _ci(day, minutes=30, category="run", energy=7):
    return SimpleNamespace(
        date=START + timedelta(days=day - 1), day_number=day, status="answered",
        minutes=minutes, activity_category=category, energy_level=energy, movement_done="yes",
    )


def test_full_summary_uses_given_stream_start():
    text = render_final_summary(_user(), [_ci(d) for d in range(1, 31)], START)
    assert "Твой итог за 30 дней" in text
    assert "Движение: 900 мин бега" in text
    assert "Точка А: 4 — Тяжело начать" in text
    assert "Точка Б: 7 — Бегаю 3 раза в неделю (+3 балла)" in text


def test_low_completion_falls_back_but_keeps_points():
    text = render_final_summary(_user(), [_ci(1)], START)
    assert "недостаточно данных" in text
    assert "Точка Б: 7" in text


def test_user_text_is_html_escaped():
    text = render_final_summary(_user(point_a_text="<b>A & B</b>", point_b_text="x < y"), [], START)
    assert "&lt;b&gt;A &amp; B&lt;/b&gt;" in text
    assert "x &lt; y" in text


def test_header_counts_days_from_join():
    late = _user(
        joined_at=datetime(2026, 10, 1, 9, tzinfo=timezone.utc),
        onboarding_completed_at=datetime(2026, 10, 1, 10, tzinfo=timezone.utc),
    )
    text = render_final_summary(late, [_ci(d) for d in range(11, 31)], START)
    assert "Твой итог за 20 дней" in text
