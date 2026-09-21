import asyncio
import logging
from datetime import date, datetime, timezone
from aiogram import Router, Bot, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
)
from contextlib import suppress
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from beg_k_sebe_bot.bot.config import settings
from beg_k_sebe_bot.bot.database.models import SentEvent, User, WeeklyReflection
from beg_k_sebe_bot.bot.texts import messages as msg
from beg_k_sebe_bot.bot.utils.program import today_msk

logger = logging.getLogger(__name__)
router = Router()

_MSG_INTERVAL = 1 / 20


class ReflectionStates(StatesGroup):
    waiting_result = State()
    waiting_helped = State()
    waiting_hardest = State()
    waiting_progress = State()
    waiting_share = State()


def _week_number(today: date) -> int:
    return (today - settings.start_date).days // 7 + 1


def _start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=msg.REFLECTION_START_BUTTON, callback_data="reflect:start"),
    ]])


def _share_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Да", callback_data="reflect:share_yes"),
        InlineKeyboardButton(text="Нет", callback_data="reflect:share_no"),
    ]])


async def _get_reflection(user_id: int, week: int, session: AsyncSession) -> WeeklyReflection | None:
    return (await session.execute(
        select(WeeklyReflection).where(
            WeeklyReflection.user_id == user_id,
            WeeklyReflection.week_number == week,
        )
    )).scalar_one_or_none()


async def send_reflection_prompts(bot: Bot, session: AsyncSession) -> None:
    today = today_msk()
    if today < settings.start_date or today >= settings.final_date:
        return
    week = _week_number(today)
    marker_key = f"reflection_prompt:{week}"
    if await session.get(SentEvent, marker_key) is not None:
        logger.info("Reflection prompt already sent for week %d, skipping", week)
        return

    users = (await session.execute(
        select(User).where(User.onboarding_completed_at.is_not(None))
    )).scalars().all()

    sent = 0
    for user in users:
        try:
            await bot.send_message(user.telegram_id, msg.REFLECTION_PROMPT, reply_markup=_start_keyboard())
            sent += 1
        except Exception as e:
            logger.error("Failed to send reflection prompt to %d: %s", user.telegram_id, e)
        await asyncio.sleep(_MSG_INTERVAL)

    session.add(SentEvent(key=marker_key, sent_at=datetime.now(timezone.utc)))
    await session.commit()
    logger.info("Reflection prompts sent for week %d: %d", week, sent)


@router.callback_query(F.data == "reflect:start")
async def start_reflection(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    week = _week_number(today_msk())
    reflection = await _get_reflection(callback.from_user.id, week, session)

    if reflection is not None and reflection.status == "answered":
        with suppress(TelegramBadRequest):
            await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(msg.REFLECTION_ALREADY_DONE)
        await callback.answer()
        return

    if reflection is None:
        session.add(WeeklyReflection(
            user_id=callback.from_user.id,
            week_number=week,
            status="pending",
            created_at=datetime.now(timezone.utc),
        ))
        await session.commit()

    with suppress(TelegramBadRequest):
        await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(msg.REFLECTION_Q_RESULT)
    await state.set_state(ReflectionStates.waiting_result)
    await callback.answer()


async def _save(message: Message, session: AsyncSession, field: str) -> WeeklyReflection | None:
    week = _week_number(today_msk())
    reflection = await _get_reflection(message.from_user.id, week, session)
    if reflection is None:
        return None
    setattr(reflection, field, message.text)
    await session.commit()
    return reflection


@router.message(ReflectionStates.waiting_result)
async def handle_result(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if await _save(message, session, "result_text") is None:
        await state.clear()
        return
    await message.answer(msg.REFLECTION_Q_HELPED)
    await state.set_state(ReflectionStates.waiting_helped)


@router.message(ReflectionStates.waiting_helped)
async def handle_helped(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if await _save(message, session, "helped_text") is None:
        await state.clear()
        return
    await message.answer(msg.REFLECTION_Q_HARDEST)
    await state.set_state(ReflectionStates.waiting_hardest)


@router.message(ReflectionStates.waiting_hardest)
async def handle_hardest(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if await _save(message, session, "hardest_text") is None:
        await state.clear()
        return
    await message.answer(msg.REFLECTION_Q_PROGRESS)
    await state.set_state(ReflectionStates.waiting_progress)


@router.message(ReflectionStates.waiting_progress)
async def handle_progress(message: Message, state: FSMContext, session: AsyncSession) -> None:
    if await _save(message, session, "progress_text") is None:
        await state.clear()
        return
    await message.answer(msg.REFLECTION_Q_SHARE)
    await state.set_state(ReflectionStates.waiting_share)


@router.message(ReflectionStates.waiting_share)
async def handle_share(message: Message, state: FSMContext, session: AsyncSession) -> None:
    reflection = await _save(message, session, "share_text")
    if reflection is None:
        await state.clear()
        return
    reflection.status = "answered"
    await session.commit()
    await state.clear()
    await message.answer(msg.REFLECTION_SHARE_ASK, reply_markup=_share_keyboard())


@router.callback_query(F.data == "reflect:share_yes")
async def share_yes(callback: CallbackQuery, session: AsyncSession) -> None:
    with suppress(TelegramBadRequest):
        await callback.message.edit_reply_markup(reply_markup=None)
    week = _week_number(today_msk())
    reflection = await _get_reflection(callback.from_user.id, week, session)

    if reflection is None or not reflection.share_text:
        await callback.message.answer(msg.REFLECTION_SHARE_EMPTY)
        await callback.answer()
        return

    if settings.group_chat_id is not None:
        try:
            await callback.bot.send_message(
                settings.group_chat_id,
                msg.REFLECTION_SHARE_POST.format(text=reflection.share_text),
            )
            reflection.shared = True
            await session.commit()
        except Exception as e:
            logger.error("Failed to publish reflection for %d: %s", callback.from_user.id, e)

    await callback.message.answer(msg.REFLECTION_SHARED_OK)
    await callback.answer()


@router.callback_query(F.data == "reflect:share_no")
async def share_no(callback: CallbackQuery) -> None:
    with suppress(TelegramBadRequest):
        await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(msg.REFLECTION_SHARE_SKIP)
    await callback.answer()
