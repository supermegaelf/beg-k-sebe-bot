from contextlib import suppress
from datetime import datetime, timezone
from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from beg_k_sebe_bot.bot.config import settings
from beg_k_sebe_bot.bot.database.models import User, MovementFormatChange
from beg_k_sebe_bot.bot.handlers.daily_checkin import checkin_reply_keyboard
from beg_k_sebe_bot.bot.texts import messages as msg
from beg_k_sebe_bot.bot.utils.access import is_group_member
from beg_k_sebe_bot.bot.utils.program import today_msk
from beg_k_sebe_bot.bot.utils.validators import parse_score

router = Router()


class OnboardingStates(StatesGroup):
    waiting_goal = State()
    waiting_format = State()
    waiting_point_a_score = State()
    waiting_point_a_text = State()


def _format_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚶 22+ минут быстрой ходьбы", callback_data="fmt:walk_22min")],
        [InlineKeyboardButton(text="🏃 22+ минут бега", callback_data="fmt:run_22min")],
        [InlineKeyboardButton(text="🏃 22+ минут другого действия", callback_data="fmt:own")],
    ])


def _check_access_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Проверить доступ", callback_data="chat:check"),
    ]])


async def _begin_onboarding(user_id: int, username: str | None, chat: Message, state: FSMContext, session: AsyncSession) -> None:
    existing = await session.get(User, user_id)
    if not existing:
        session.add(User(telegram_id=user_id, username=username, joined_at=datetime.now(timezone.utc)))
        await session.commit()

    await chat.answer(msg.GREETING)
    await chat.answer(msg.GOAL_QUESTION)
    await state.set_state(OnboardingStates.waiting_goal)


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext, session: AsyncSession) -> None:
    today = today_msk()

    existing = await session.get(User, message.from_user.id)
    if existing and existing.onboarding_completed_at:
        await state.clear()
        await message.answer(msg.RESTART, reply_markup=checkin_reply_keyboard())
        return

    if today > settings.registration_deadline and not existing:
        await message.answer(msg.REGISTRATION_CLOSED)
        return

    if not await is_group_member(message.bot, message.from_user.id):
        await message.answer(
            msg.CHAT_GATE.format(invite_link=settings.chat_invite_link),
            reply_markup=_check_access_keyboard(),
            disable_web_page_preview=True,
        )
        return

    await _begin_onboarding(message.from_user.id, message.from_user.username, message, state, session)


@router.callback_query(F.data == "chat:check")
async def handle_chat_check(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    today = today_msk()
    existing = await session.get(User, callback.from_user.id)

    if existing and existing.onboarding_completed_at:
        await callback.answer()
        return

    if today > settings.registration_deadline and not existing:
        await callback.message.answer(msg.REGISTRATION_CLOSED)
        await callback.answer()
        return

    if not await is_group_member(callback.bot, callback.from_user.id):
        await callback.answer()
        await callback.message.answer(msg.CHAT_GATE_STILL_OUT)
        return

    with suppress(TelegramBadRequest):
        await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()
    await _begin_onboarding(callback.from_user.id, callback.from_user.username, callback.message, state, session)


@router.message(OnboardingStates.waiting_goal)
async def handle_goal(message: Message, state: FSMContext, session: AsyncSession) -> None:
    user = await session.get(User, message.from_user.id)
    user.goal = message.text
    await session.commit()

    await message.answer(msg.FORMAT_QUESTION, reply_markup=_format_keyboard())
    await state.set_state(OnboardingStates.waiting_format)


@router.callback_query(OnboardingStates.waiting_format, F.data.startswith("fmt:"))
async def handle_format(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    format_key = callback.data.split(":")[1]
    user = await session.get(User, callback.from_user.id)
    user.movement_format = format_key

    session.add(MovementFormatChange(
        user_id=callback.from_user.id,
        old_format=format_key,
        new_format=format_key,
        changed_on_day=0,
        changed_at=datetime.now(timezone.utc),
    ))
    await session.commit()

    with suppress(TelegramBadRequest):
        await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(msg.POINT_A_INTRO)
    await state.set_state(OnboardingStates.waiting_point_a_text)
    await callback.answer()


@router.message(OnboardingStates.waiting_point_a_text)
async def handle_point_a_text(message: Message, state: FSMContext, session: AsyncSession) -> None:
    user = await session.get(User, message.from_user.id)
    user.point_a_text = message.text
    await session.commit()
    await message.answer(msg.POINT_A_SCORE)
    await state.set_state(OnboardingStates.waiting_point_a_score)


@router.message(OnboardingStates.waiting_point_a_score)
async def handle_point_a_score(message: Message, state: FSMContext, session: AsyncSession) -> None:
    value = parse_score(message.text)
    if value is None:
        await message.answer(msg.SCORE_INVALID)
        return
    user = await session.get(User, message.from_user.id)
    user.point_a_score = value
    user.onboarding_completed_at = datetime.now(timezone.utc)
    await session.commit()
    await state.clear()
    await message.answer(
        msg.ONBOARDING_COMPLETE,
        reply_markup=checkin_reply_keyboard(),
    )
    await message.answer(msg.POINT_A_THANKS)
