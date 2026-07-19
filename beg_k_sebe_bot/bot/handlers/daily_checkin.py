import asyncio
import logging
from datetime import date, datetime, timezone
from aiogram import Router, Bot, F
from aiogram.exceptions import TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from beg_k_sebe_bot.bot.config import settings
from beg_k_sebe_bot.bot.database.models import DailyCheckin, User
from beg_k_sebe_bot.bot.services.movement_calc import KM_INPUT_FORMATS
from beg_k_sebe_bot.bot.texts import messages as msg

logger = logging.getLogger(__name__)
router = Router()


class CheckinStates(StatesGroup):
    waiting_movement = State()
    waiting_run_km = State()
    waiting_practice = State()
    waiting_energy = State()
    waiting_shift = State()


def _yes_partial_no_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Да", callback_data="ci:yes"),
        InlineKeyboardButton(text="Частично", callback_data="ci:partial"),
        InlineKeyboardButton(text="Нет", callback_data="ci:no"),
    ]])


def _skip_km_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Пропустить", callback_data="runkm:skip"),
    ]])


async def send_checkin(user_id: int, bot: Bot, session: AsyncSession, state: FSMContext, today: date) -> None:
    day = (today - settings.start_date).days + 1

    checkin = DailyCheckin(
        user_id=user_id,
        day_number=day,
        date=today,
        status="pending",
    )
    session.add(checkin)

    for attempt in range(3):
        try:
            await bot.send_message(user_id, msg.CHECKIN_Q1, reply_markup=_yes_partial_no_keyboard())
            break
        except TelegramRetryAfter as e:
            if attempt == 2:
                raise
            logger.warning("Rate limited sending checkin to %d, retrying after %ds", user_id, e.retry_after)
            await asyncio.sleep(e.retry_after)

    await session.commit()
    await state.set_state(CheckinStates.waiting_movement)


async def _get_pending_checkin(user_id: int, session: AsyncSession) -> DailyCheckin | None:
    result = await session.execute(
        select(DailyCheckin).where(
            DailyCheckin.user_id == user_id,
            DailyCheckin.status == "pending",
        ).order_by(DailyCheckin.day_number.desc()).limit(1)
    )
    return result.scalar_one_or_none()


async def _ask_practice(message: Message, state: FSMContext) -> None:
    await message.answer(msg.CHECKIN_Q2, reply_markup=_yes_partial_no_keyboard())
    await state.set_state(CheckinStates.waiting_practice)


@router.callback_query(CheckinStates.waiting_movement, F.data.startswith("ci:"))
async def handle_movement(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    checkin = await _get_pending_checkin(callback.from_user.id, session)
    if checkin is None:
        await callback.answer()
        return

    value = callback.data.split(":")[1]
    checkin.movement_done = value
    await session.commit()

    await callback.message.edit_reply_markup(reply_markup=None)

    user = await session.get(User, callback.from_user.id)
    if value in ("yes", "partial") and user and user.movement_format in KM_INPUT_FORMATS:
        await callback.message.answer(msg.CHECKIN_RUN_KM, reply_markup=_skip_km_keyboard())
        await state.set_state(CheckinStates.waiting_run_km)
    else:
        await _ask_practice(callback.message, state)
    await callback.answer()


@router.callback_query(CheckinStates.waiting_run_km, F.data == "runkm:skip")
async def handle_run_km_skip(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_reply_markup(reply_markup=None)
    await _ask_practice(callback.message, state)
    await callback.answer()


@router.message(CheckinStates.waiting_run_km)
async def handle_run_km(message: Message, state: FSMContext, session: AsyncSession) -> None:
    try:
        run_km = float(message.text.strip().replace(",", "."))
        if not 0 < run_km <= 200:
            raise ValueError
    except (ValueError, AttributeError):
        await message.answer(msg.CHECKIN_RUN_KM_INVALID)
        return

    checkin = await _get_pending_checkin(message.from_user.id, session)
    if checkin is None:
        return

    checkin.run_km = run_km
    await session.commit()
    await _ask_practice(message, state)


@router.callback_query(CheckinStates.waiting_practice, F.data.startswith("ci:"))
async def handle_practice(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    checkin = await _get_pending_checkin(callback.from_user.id, session)
    if checkin is None:
        await callback.answer()
        return

    checkin.practice_done = callback.data.split(":")[1]
    await session.commit()

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(msg.CHECKIN_Q3)
    await state.set_state(CheckinStates.waiting_energy)
    await callback.answer()


@router.message(CheckinStates.waiting_energy)
async def handle_energy(message: Message, state: FSMContext, session: AsyncSession) -> None:
    try:
        value = int(message.text.strip())
        if not 1 <= value <= 10:
            raise ValueError
    except ValueError:
        await message.answer(msg.WHEEL_INVALID)
        return

    checkin = await _get_pending_checkin(message.from_user.id, session)
    if checkin is None:
        return

    checkin.energy_level = value
    await session.commit()

    await message.answer(msg.CHECKIN_Q4)
    await state.set_state(CheckinStates.waiting_shift)


@router.message(CheckinStates.waiting_shift)
async def handle_shift(message: Message, state: FSMContext, session: AsyncSession) -> None:
    checkin = await _get_pending_checkin(message.from_user.id, session)
    if checkin is None:
        return

    checkin.shift_text = message.text
    checkin.status = "answered"
    checkin.answered_at = datetime.now(timezone.utc)
    await session.commit()
    await state.clear()
