import logging

from aiogram import Router
from aiogram.types import ChatJoinRequest
from sqlalchemy.ext.asyncio import AsyncSession

from beg_k_sebe_bot.bot.config import settings
from beg_k_sebe_bot.bot.database.models import Access
from beg_k_sebe_bot.bot.services.chat_access import tariff_allows_chat
from beg_k_sebe_bot.bot.texts import messages as msg

logger = logging.getLogger(__name__)
router = Router()


@router.chat_join_request()
async def handle_join_request(request: ChatJoinRequest, session: AsyncSession) -> None:
    if request.chat.id not in (settings.group_chat_id, settings.private_chat_id):
        return

    access = await session.get(Access, request.from_user.id)
    if access is not None and tariff_allows_chat(access.tariff, request.chat.id):
        await request.approve()
        return

    await request.decline()
    text = msg.JOIN_DECLINED if access is None else msg.JOIN_DECLINED_PRIVATE
    try:
        await request.bot.send_message(request.user_chat_id, text)
    except Exception as e:
        logger.warning("Failed to explain declined join to %d: %s", request.from_user.id, e)
