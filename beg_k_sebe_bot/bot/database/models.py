from datetime import date, datetime
from sqlalchemy import BigInteger, Boolean, Date, DateTime, Integer, SmallInteger, String, Text, UniqueConstraint, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64))
    goal: Mapped[str | None] = mapped_column(Text)
    movement_format: Mapped[str | None] = mapped_column(String(20))
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    point_a_score: Mapped[int | None] = mapped_column(SmallInteger)
    point_a_text: Mapped[str | None] = mapped_column(Text)

    point_b_score: Mapped[int | None] = mapped_column(SmallInteger)
    point_b_text: Mapped[str | None] = mapped_column(Text)

    final_sent: Mapped[bool] = mapped_column(Boolean, default=False)

    checkins: Mapped[list["DailyCheckin"]] = relationship(back_populates="user")
    format_changes: Mapped[list["MovementFormatChange"]] = relationship(back_populates="user")
    weekly_reflections: Mapped[list["WeeklyReflection"]] = relationship(back_populates="user")


class MovementFormatChange(Base):
    __tablename__ = "movement_format_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"))
    old_format: Mapped[str] = mapped_column(String(20))
    new_format: Mapped[str] = mapped_column(String(20))
    changed_on_day: Mapped[int] = mapped_column(Integer)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(back_populates="format_changes")


class DailyCheckin(Base):
    __tablename__ = "daily_checkins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"))
    day_number: Mapped[int] = mapped_column(Integer)
    date: Mapped[date] = mapped_column(Date)
    movement_done: Mapped[str | None] = mapped_column(String(10))
    minutes: Mapped[int | None] = mapped_column(Integer)
    activity_category: Mapped[str | None] = mapped_column(String(20))
    practice_done: Mapped[str | None] = mapped_column(String(10))
    energy_level: Mapped[int | None] = mapped_column(SmallInteger)
    help_text: Mapped[str | None] = mapped_column(Text)
    hardest_text: Mapped[str | None] = mapped_column(Text)
    shift_text: Mapped[str | None] = mapped_column(Text)
    reminder_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(10), default="pending")

    __table_args__ = (UniqueConstraint("user_id", "day_number"),)

    user: Mapped["User"] = relationship(back_populates="checkins")


class WeeklyReflection(Base):
    __tablename__ = "weekly_reflections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"))
    week_number: Mapped[int] = mapped_column(Integer)
    result_text: Mapped[str | None] = mapped_column(Text)
    helped_text: Mapped[str | None] = mapped_column(Text)
    hardest_text: Mapped[str | None] = mapped_column(Text)
    progress_text: Mapped[str | None] = mapped_column(Text)
    share_text: Mapped[str | None] = mapped_column(Text)
    shared: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(10), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("user_id", "week_number"),)

    user: Mapped["User"] = relationship(back_populates="weekly_reflections")


class SentEvent(Base):
    __tablename__ = "sent_events"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
