import logging
from aiogram import Bot

from beg_k_sebe_bot.bot.config import settings

logger = logging.getLogger(__name__)

_MEMBER_STATUSES = {"creator", "administrator", "member", "restricted"}


async def is_group_member(bot: Bot, user_id: int) -> bool:
    if settings.group_chat_id is None:
        return True  # gate disabled while group is not configured
    try:
        member = await bot.get_chat_member(settings.group_chat_id, user_id)
        return member.status in _MEMBER_STATUSES
    except Exception as e:
        logger.warning("Membership check failed for %d: %s — letting through", user_id, e)
        return True
