from datetime import date, timedelta
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    bot_token: str
    database_url: str
    group_chat_id: int | None = None
    postgres_user: str = ""
    postgres_password: str = ""
    postgres_db: str = ""

    start_date: date = date(2026, 6, 25)
    registration_deadline: date = date(2026, 6, 28)
    checkin_hour: int = 8
    reminder_hour: int = 13
    weekly_summary_dow: str = "sun"
    weekly_summary_hour: int = 19
    final_program_day: int = 31
    timezone: str = "Europe/Moscow"

    @property
    def final_date(self) -> date:
        return self.start_date + timedelta(days=self.final_program_day - 1)

    model_config = {"env_file": ".env", "env_ignore_empty": True}


settings = Settings()
