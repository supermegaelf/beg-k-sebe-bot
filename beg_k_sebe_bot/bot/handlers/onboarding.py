from contextlib import suppress
from datetime import datetime, timezone
from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from beg_k_sebe_bot.bot.database.models import Access, User, MovementFormatChange
from beg_k_sebe_bot.bot.handlers.daily_checkin import checkin_reply_keyboard
from beg_k_sebe_bot.bot.handlers.final import start_previous_final
from beg_k_sebe_bot.bot.handlers.payment import show_tariffs
from beg_k_sebe_bot.bot.services import archive_final
from beg_k_sebe_bot.bot.texts import messages as msg
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
        [InlineKeyboardButton(text="🧘 22+ минут медитации", callback_data="fmt:own")],
    ])


async def begin_onboarding(user_id: int, username: str | None, chat: Message, state: FSMContext, session: AsyncSession) -> None:
    archived = await archive_final.pending_user(session, user_id)
    if archived is not None:
        # Previous-stream participants close point B first; onboarding resumes after the summary.
        await chat.answer(msg.PREV_FINAL_BEFORE_ONBOARDING)
        await start_previous_final(user_id, chat.bot, state, archived)
        return

    existing = await session.get(User, user_id)
    if not existing:
        session.add(User(telegram_id=user_id, username=username, joined_at=datetime.now(timezone.utc)))
        await session.commit()
    elif existing.joined_at is None:
        existing.joined_at = datetime.now(timezone.utc)
        await session.commit()

    await chat.answer(msg.GREETING)
    await chat.answer(msg.GOAL_QUESTION)
    await state.set_state(OnboardingStates.waiting_goal)


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext, session: AsyncSession) -> None:
    existing = await session.get(User, message.from_user.id)
    access = await session.get(Access, message.from_user.id)

    if access is None:
        await show_tariffs(message, state, session, message.from_user.id, message.from_user.username)
        return

    if existing and existing.onboarding_completed_at:
        await state.clear()
        await message.answer(msg.RESTART, reply_markup=checkin_reply_keyboard())
        return

    await begin_onboarding(message.from_user.id, message.from_user.username, message, state, session)


@router.callback_query(F.data == "onb:start")
async def handle_onboarding_start(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await callback.answer()
    user = await session.get(User, callback.from_user.id)
    if await session.get(Access, callback.from_user.id) is None or (user and user.onboarding_completed_at):
        return
    rows = [row for row in callback.message.reply_markup.inline_keyboard if row[0].callback_data != "onb:start"]
    with suppress(TelegramBadRequest):
        await callback.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await begin_onboarding(callback.from_user.id, callback.from_user.username, callback.message, state, session)


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
