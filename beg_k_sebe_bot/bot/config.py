from datetime import date, timedelta
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    bot_token: str
    database_url: str
    group_chat_id: int | None = None
    private_chat_id: int | None = None
    info_topic_id: int | None = None
    postgres_user: str = ""
    postgres_password: str = ""
    postgres_db: str = ""

    start_date: date = date(2026, 10, 21)
    registration_deadline: date = date(2026, 10, 25)
    kick_unpaid_date: date = date(2026, 10, 26)
    checkin_hour: int = 20
    weekly_summary_dow: str = "mon"
    weekly_summary_hour: int = 9
    weekly_reflection_dow: str = "sun"
    weekly_reflection_hour: int = 19
    admin_summary_dow: str = "mon"
    admin_summary_hour: int = 10
    admin_ids: str = ""
    final_program_day: int = 31
    timezone: str = "Europe/Moscow"

    robokassa_login: str = ""
    robokassa_password1: str = ""
    robokassa_password2: str = ""
    robokassa_is_test: bool = True
    web_port: int = 8080
    success_page_url: str = ""
    fail_page_url: str = ""
    offer_url: str = ""
    consent_url: str = ""
    promo_code: str = ""
    transform_seats: int = 12

    @property
    def admin_id_list(self) -> list[int]:
        return [int(x) for x in self.admin_ids.replace(" ", "").split(",") if x]

    @property
    def final_date(self) -> date:
        return self.start_date + timedelta(days=self.final_program_day - 1)

    model_config = {"env_file": ".env", "env_ignore_empty": True}


settings = Settings()
