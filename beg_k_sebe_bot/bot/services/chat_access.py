import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardButton

from beg_k_sebe_bot.bot.config import settings
from beg_k_sebe_bot.bot.services.tariffs import TRANSFORM
from beg_k_sebe_bot.bot.texts import messages as msg

logger = logging.getLogger(__name__)

_join_links: dict[int, str] = {}


def chats_for_tariff(tariff: str) -> list[int]:
    chats = [settings.group_chat_id]
    if tariff == TRANSFORM:
        chats.append(settings.private_chat_id)
    return [c for c in chats if c is not None]


def tariff_allows_chat(tariff: str, chat_id: int) -> bool:
    return chat_id in chats_for_tariff(tariff)


async def _join_link(bot: Bot, chat_id: int) -> str | None:
    if chat_id not in _join_links:
        try:
            link = await bot.create_chat_invite_link(chat_id, name="Вход после оплаты", creates_join_request=True)
        except Exception as e:
            logger.error("Failed to create join link for chat %d: %s", chat_id, e)
            return None
        _join_links[chat_id] = link.invite_link
    return _join_links[chat_id]


async def chat_buttons(bot: Bot, tariff: str) -> list[list[InlineKeyboardButton]]:
    rows = []
    for chat_id in chats_for_tariff(tariff):
        link = await _join_link(bot, chat_id)
        if link is None:
            continue
        text = msg.CHAT_PRIVATE_BUTTON if chat_id == settings.private_chat_id else msg.CHAT_MAIN_BUTTON
        rows.append([InlineKeyboardButton(text=text, url=link)])
    return rows


async def remove_from_chats(bot: Bot, user_id: int) -> None:
    for chat_id in (settings.group_chat_id, settings.private_chat_id):
        if chat_id is None:
            continue
        try:
            await bot.ban_chat_member(chat_id, user_id)
            await bot.unban_chat_member(chat_id, user_id, only_if_banned=True)
        except Exception as e:
            logger.error("Failed to remove %d from chat %d: %s", user_id, chat_id, e)
