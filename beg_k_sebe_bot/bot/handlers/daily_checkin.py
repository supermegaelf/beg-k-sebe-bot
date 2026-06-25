from datetime import datetime, timezone
from aiogram import Router, Bot, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from beg_k_sebe_bot.bot.database.models import DailyCheckin
from beg_k_sebe_bot.bot.texts import messages as msg
from beg_k_sebe_bot.bot.utils.program import current_program_day, today_msk

router = Router()


class CheckinStates(StatesGroup):
    waiting_movement = State()
    waiting_practice = State()
    waiting_energy = State()
    waiting_shift = State()


def _yes_partial_no_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Да", callback_data="ci:yes"),
        InlineKeyboardButton(text="Частично", callback_data="ci:partial"),
        InlineKeyboardButton(text="Нет", callback_data="ci:no"),
    ]])


async def send_checkin(user_id: int, bot: Bot, session: AsyncSession, state: FSMContext) -> None:
    day = current_program_day()

    checkin = DailyCheckin(
        user_id=user_id,
        day_number=day,
        date=today_msk(),
        status="pending",
    )
    session.add(checkin)
    await session.commit()

    await bot.send_message(user_id, msg.CHECKIN_Q1, reply_markup=_yes_partial_no_keyboard())
    await state.set_state(CheckinStates.waiting_movement)


async def _get_pending_checkin(user_id: int, session: AsyncSession) -> DailyCheckin | None:
    result = await session.execute(
        select(DailyCheckin).where(
            DailyCheckin.user_id == user_id,
            DailyCheckin.status == "pending",
        ).order_by(DailyCheckin.day_number.desc()).limit(1)
    )
    return result.scalar_one_or_none()


@router.callback_query(CheckinStates.waiting_movement, F.data.startswith("ci:"))
async def handle_movement(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    checkin = await _get_pending_checkin(callback.from_user.id, session)
    if checkin is None:
        await callback.answer()
        return

    checkin.movement_done = callback.data.split(":")[1]
    await session.commit()

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(msg.CHECKIN_Q2, reply_markup=_yes_partial_no_keyboard())
    await state.set_state(CheckinStates.waiting_practice)
    await callback.answer()


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
