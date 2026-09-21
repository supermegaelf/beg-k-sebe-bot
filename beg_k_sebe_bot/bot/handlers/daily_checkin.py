import logging
from contextlib import suppress
from datetime import date, datetime, timezone
from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from beg_k_sebe_bot.bot.config import settings
from beg_k_sebe_bot.bot.database.models import DailyCheckin, User
from beg_k_sebe_bot.bot.services.stats import current_streak
from beg_k_sebe_bot.bot.texts import messages as msg
from beg_k_sebe_bot.bot.utils.access import is_group_member
from beg_k_sebe_bot.bot.utils.program import today_msk
from beg_k_sebe_bot.bot.utils.validators import parse_score

logger = logging.getLogger(__name__)
router = Router()

_FORMAT_TO_CATEGORY = {"walk_22min": "walk", "run_22min": "run", "own": "own"}
_MAX_MINUTES = 1440


class CheckinStates(StatesGroup):
    waiting_movement = State()
    waiting_minutes = State()
    waiting_practice = State()
    waiting_energy = State()
    waiting_help = State()
    waiting_hardest = State()
    waiting_shift = State()


def checkin_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=msg.CHECKIN_START_BUTTON)],
            [KeyboardButton(text=msg.MY_PROGRESS_BUTTON)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def _ypn_keyboard(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Да", callback_data=f"{prefix}:yes"),
        InlineKeyboardButton(text="Частично", callback_data=f"{prefix}:partial"),
        InlineKeyboardButton(text="Нет", callback_data=f"{prefix}:no"),
    ]])


def _skip_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Пропустить", callback_data="min:skip"),
    ]])


async def _get_today_checkin(user_id: int, session: AsyncSession, today: date) -> DailyCheckin | None:
    result = await session.execute(
        select(DailyCheckin).where(
            DailyCheckin.user_id == user_id,
            DailyCheckin.date == today,
        )
    )
    return result.scalar_one_or_none()


def _resume_state(checkin: DailyCheckin) -> State:
    if checkin.movement_done is None:
        return CheckinStates.waiting_movement
    if checkin.minutes is None:
        return CheckinStates.waiting_minutes
    if checkin.practice_done is None:
        return CheckinStates.waiting_practice
    if checkin.energy_level is None:
        return CheckinStates.waiting_energy
    if checkin.help_text is None:
        return CheckinStates.waiting_help
    if checkin.hardest_text is None:
        return CheckinStates.waiting_hardest
    return CheckinStates.waiting_shift


async def _ask(message: Message, state: FSMContext, target: State) -> None:
    await state.set_state(target)
    if target == CheckinStates.waiting_movement:
        await message.answer(msg.CHECKIN_Q1, reply_markup=_ypn_keyboard("mv"))
    elif target == CheckinStates.waiting_minutes:
        await message.answer(msg.CHECKIN_MINUTES, reply_markup=_skip_keyboard())
    elif target == CheckinStates.waiting_practice:
        await message.answer(msg.CHECKIN_Q2, reply_markup=_ypn_keyboard("pr"))
    elif target == CheckinStates.waiting_energy:
        await message.answer(msg.CHECKIN_ENERGY)
    elif target == CheckinStates.waiting_help:
        await message.answer(msg.CHECKIN_HELP)
    elif target == CheckinStates.waiting_hardest:
        await message.answer(msg.CHECKIN_HARDEST)
    elif target == CheckinStates.waiting_shift:
        await message.answer(msg.CHECKIN_SHIFT)


@router.message(F.text == msg.CHECKIN_START_BUTTON)
async def start_checkin(message: Message, state: FSMContext, session: AsyncSession) -> None:
    today = today_msk()
    if today < settings.start_date or today >= settings.final_date:
        await message.answer(msg.CHECKIN_UNAVAILABLE)
        return

    if not await is_group_member(message.bot, message.from_user.id):
        await message.answer(msg.CHAT_GATE_CHECKIN.format(invite_link=settings.chat_invite_link))
        return

    checkin = await _get_today_checkin(message.from_user.id, session, today)

    if checkin is not None and checkin.status != "pending":
        await message.answer(msg.CHECKIN_ALREADY_DONE)
        return

    if checkin is None:
        user = await session.get(User, message.from_user.id)
        checkin = DailyCheckin(
            user_id=message.from_user.id,
            day_number=(today - settings.start_date).days + 1,
            date=today,
            status="pending",
            activity_category=_FORMAT_TO_CATEGORY.get(user.movement_format if user else None),
        )
        session.add(checkin)
        await session.commit()
        await _ask(message, state, CheckinStates.waiting_movement)
        return

    # Pending check-in exists — resume where the dialog left off, never a second one.
    await _ask(message, state, _resume_state(checkin))


@router.callback_query(CheckinStates.waiting_movement, F.data.startswith("mv:"))
async def handle_movement(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    checkin = await _get_today_checkin(callback.from_user.id, session, today_msk())
    if checkin is None or checkin.status != "pending":
        await state.clear()
        await callback.answer()
        return
    checkin.movement_done = callback.data.split(":")[1]
    await session.commit()
    with suppress(TelegramBadRequest):
        await callback.message.edit_reply_markup(reply_markup=None)
    await _ask(callback.message, state, CheckinStates.waiting_minutes)
    await callback.answer()


@router.callback_query(CheckinStates.waiting_minutes, F.data == "min:skip")
async def handle_minutes_skip(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    checkin = await _get_today_checkin(callback.from_user.id, session, today_msk())
    if checkin is None or checkin.status != "pending":
        await state.clear()
        await callback.answer()
        return
    checkin.minutes = 0
    await session.commit()
    with suppress(TelegramBadRequest):
        await callback.message.edit_reply_markup(reply_markup=None)
    await _ask(callback.message, state, CheckinStates.waiting_practice)
    await callback.answer()


@router.message(CheckinStates.waiting_minutes)
async def handle_minutes(message: Message, state: FSMContext, session: AsyncSession) -> None:
    try:
        minutes = int(message.text.strip())
        if not 0 <= minutes <= _MAX_MINUTES:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer(msg.CHECKIN_MINUTES_INVALID)
        return
    checkin = await _get_today_checkin(message.from_user.id, session, today_msk())
    if checkin is None or checkin.status != "pending":
        await state.clear()
        return
    checkin.minutes = minutes
    await session.commit()
    await _ask(message, state, CheckinStates.waiting_practice)


@router.callback_query(CheckinStates.waiting_practice, F.data.startswith("pr:"))
async def handle_practice(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    checkin = await _get_today_checkin(callback.from_user.id, session, today_msk())
    if checkin is None or checkin.status != "pending":
        await state.clear()
        await callback.answer()
        return
    checkin.practice_done = callback.data.split(":")[1]
    await session.commit()
    with suppress(TelegramBadRequest):
        await callback.message.edit_reply_markup(reply_markup=None)
    await _ask(callback.message, state, CheckinStates.waiting_energy)
    await callback.answer()


@router.message(CheckinStates.waiting_energy)
async def handle_energy(message: Message, state: FSMContext, session: AsyncSession) -> None:
    value = parse_score(message.text)
    if value is None:
        await message.answer(msg.SCORE_INVALID)
        return
    checkin = await _get_today_checkin(message.from_user.id, session, today_msk())
    if checkin is None or checkin.status != "pending":
        await state.clear()
        return
    checkin.energy_level = value
    await session.commit()
    await _ask(message, state, CheckinStates.waiting_help)


@router.message(CheckinStates.waiting_help)
async def handle_help(message: Message, state: FSMContext, session: AsyncSession) -> None:
    checkin = await _get_today_checkin(message.from_user.id, session, today_msk())
    if checkin is None or checkin.status != "pending":
        await state.clear()
        return
    checkin.help_text = message.text
    await session.commit()
    await _ask(message, state, CheckinStates.waiting_hardest)


@router.message(CheckinStates.waiting_hardest)
async def handle_hardest(message: Message, state: FSMContext, session: AsyncSession) -> None:
    checkin = await _get_today_checkin(message.from_user.id, session, today_msk())
    if checkin is None or checkin.status != "pending":
        await state.clear()
        return
    checkin.hardest_text = message.text
    await session.commit()
    await _ask(message, state, CheckinStates.waiting_shift)


@router.message(CheckinStates.waiting_shift)
async def handle_shift(message: Message, state: FSMContext, session: AsyncSession) -> None:
    checkin = await _get_today_checkin(message.from_user.id, session, today_msk())
    if checkin is None or checkin.status != "pending":
        await state.clear()
        return
    checkin.shift_text = message.text
    checkin.status = "answered"
    checkin.answered_at = datetime.now(timezone.utc)
    await session.commit()
    await state.clear()
    await _maybe_streak_message(message, session)


async def _maybe_streak_message(message: Message, session: AsyncSession) -> None:
    user = await session.get(User, message.from_user.id)
    if user is None or user.onboarding_completed_at is None:
        return
    checkins = (await session.execute(
        select(DailyCheckin).where(DailyCheckin.user_id == message.from_user.id)
    )).scalars().all()
    user_start = max(settings.start_date, user.onboarding_completed_at.date())
    if current_streak(checkins, today_msk(), user_start) == 3:
        await message.answer(msg.STREAK_SUPPORT)
