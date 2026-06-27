import asyncio
import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import StateFilter
from aiogram.fsm.state import default_state
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import BotCommand, CallbackQuery, Message

from beg_k_sebe_bot.bot.config import settings
from beg_k_sebe_bot.bot.database.db import AsyncSessionLocal, create_tables
from beg_k_sebe_bot.bot.handlers import onboarding, daily_checkin, change_format, final
from beg_k_sebe_bot.bot.middleware import DbSessionMiddleware
from beg_k_sebe_bot.bot.services.scheduler import build_scheduler, run_missed_final_if_needed

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_ignore_router = Router()


@_ignore_router.message(StateFilter(default_state))
async def ignore_unhandled(_: Message) -> None:
    pass


@_ignore_router.callback_query()
async def ignore_callback(callback: CallbackQuery) -> None:
    await callback.answer()


async def main() -> None:
    await create_tables()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    storage = RedisStorage.from_url("redis://redis:6379/0")
    dp = Dispatcher(storage=storage)

    dp.update.middleware(DbSessionMiddleware(AsyncSessionLocal))

    dp.include_router(onboarding.router)
    dp.include_router(daily_checkin.router)
    dp.include_router(change_format.router)
    dp.include_router(final.router)
    dp.include_router(_ignore_router)

    scheduler = build_scheduler(bot, storage)
    scheduler.start()
    logger.info("Scheduler started with %d jobs", len(scheduler.get_jobs()))

    await run_missed_final_if_needed(bot, storage)

    await bot.set_my_commands([
        BotCommand(command="start", description="Перезапустить"),
        BotCommand(command="change_format", description="Сменить формат движения"),
    ])

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.shutdown()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
