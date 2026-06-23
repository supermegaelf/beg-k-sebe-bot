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
    joined_at: Mapped[datetime | None] = mapped_column(DateTime)
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(DateTime)

    wheel_a_money: Mapped[int | None] = mapped_column(SmallInteger)
    wheel_a_relationships: Mapped[int | None] = mapped_column(SmallInteger)
    wheel_a_health: Mapped[int | None] = mapped_column(SmallInteger)

    wheel_b_money: Mapped[int | None] = mapped_column(SmallInteger)
    wheel_b_relationships: Mapped[int | None] = mapped_column(SmallInteger)
    wheel_b_health: Mapped[int | None] = mapped_column(SmallInteger)

    final_sent: Mapped[bool] = mapped_column(Boolean, default=False)

    checkins: Mapped[list["DailyCheckin"]] = relationship(back_populates="user")
    format_changes: Mapped[list["MovementFormatChange"]] = relationship(back_populates="user")


class MovementFormatChange(Base):
    __tablename__ = "movement_format_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"))
    old_format: Mapped[str] = mapped_column(String(20))
    new_format: Mapped[str] = mapped_column(String(20))
    changed_on_day: Mapped[int] = mapped_column(Integer)
    changed_at: Mapped[datetime] = mapped_column(DateTime)

    user: Mapped["User"] = relationship(back_populates="format_changes")


class DailyCheckin(Base):
    __tablename__ = "daily_checkins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.telegram_id"))
    day_number: Mapped[int] = mapped_column(Integer)
    date: Mapped[date] = mapped_column(Date)
    movement_done: Mapped[str | None] = mapped_column(String(10))
    practice_done: Mapped[str | None] = mapped_column(String(10))
    energy_level: Mapped[int | None] = mapped_column(SmallInteger)
    shift_text: Mapped[str | None] = mapped_column(Text)
    reminder_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(10), default="pending")

    __table_args__ = (UniqueConstraint("user_id", "day_number"),)

    user: Mapped["User"] = relationship(back_populates="checkins")
