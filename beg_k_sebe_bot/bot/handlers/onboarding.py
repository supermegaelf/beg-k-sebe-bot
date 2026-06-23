from datetime import datetime, timezone
from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from beg_k_sebe_bot.bot.config import settings
from beg_k_sebe_bot.bot.database.models import User, MovementFormatChange
from beg_k_sebe_bot.bot.texts import messages as msg

router = Router()


class OnboardingStates(StatesGroup):
    waiting_goal = State()
    waiting_format = State()
    waiting_wheel_a_money = State()
    waiting_wheel_a_relationships = State()
    waiting_wheel_a_health = State()


def _format_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚶 22 мин быстрой ходьбы", callback_data="fmt:walk_22min")],
        [InlineKeyboardButton(text="🏃 22 мин бега", callback_data="fmt:run_22min")],
        [InlineKeyboardButton(text="🏃 5 км бега", callback_data="fmt:run_5km")],
    ])


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext, session: AsyncSession) -> None:
    today = datetime.now(timezone.utc).date()

    existing = await session.get(User, message.from_user.id)
    if existing and existing.onboarding_completed_at:
        await message.answer(msg.ALREADY_REGISTERED)
        return

    if today > settings.registration_deadline and not existing:
        await message.answer(msg.REGISTRATION_CLOSED)
        return

    if not existing:
        user = User(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            joined_at=datetime.now(timezone.utc),
        )
        session.add(user)
        await session.commit()

    await message.answer(msg.GREETING)
    await message.answer(msg.GOAL_QUESTION)
    await state.set_state(OnboardingStates.waiting_goal)


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

    change = MovementFormatChange(
        user_id=callback.from_user.id,
        old_format=format_key,
        new_format=format_key,
        changed_on_day=0,
        changed_at=datetime.now(timezone.utc),
    )
    session.add(change)
    await session.commit()

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(msg.WHEEL_A_INTRO)
    await callback.message.answer(msg.WHEEL_A_MONEY)
    await state.set_state(OnboardingStates.waiting_wheel_a_money)
    await callback.answer()


@router.message(OnboardingStates.waiting_wheel_a_money)
async def handle_wheel_a_money(message: Message, state: FSMContext, session: AsyncSession) -> None:
    value = _parse_wheel_value(message.text)
    if value is None:
        await message.answer(msg.WHEEL_INVALID)
        return
    user = await session.get(User, message.from_user.id)
    user.wheel_a_money = value
    await session.commit()
    await message.answer(msg.WHEEL_A_RELATIONSHIPS)
    await state.set_state(OnboardingStates.waiting_wheel_a_relationships)


@router.message(OnboardingStates.waiting_wheel_a_relationships)
async def handle_wheel_a_relationships(message: Message, state: FSMContext, session: AsyncSession) -> None:
    value = _parse_wheel_value(message.text)
    if value is None:
        await message.answer(msg.WHEEL_INVALID)
        return
    user = await session.get(User, message.from_user.id)
    user.wheel_a_relationships = value
    await session.commit()
    await message.answer(msg.WHEEL_A_HEALTH)
    await state.set_state(OnboardingStates.waiting_wheel_a_health)


@router.message(OnboardingStates.waiting_wheel_a_health)
async def handle_wheel_a_health(message: Message, state: FSMContext, session: AsyncSession) -> None:
    value = _parse_wheel_value(message.text)
    if value is None:
        await message.answer(msg.WHEEL_INVALID)
        return
    user = await session.get(User, message.from_user.id)
    user.wheel_a_health = value
    user.onboarding_completed_at = datetime.now(timezone.utc)
    await session.commit()
    await state.clear()
    await message.answer(msg.ONBOARDING_COMPLETE)


def _parse_wheel_value(text: str) -> int | None:
    try:
        v = int(text.strip())
        return v if 1 <= v <= 10 else None
    except ValueError:
        return None
