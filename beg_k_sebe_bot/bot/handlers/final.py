from datetime import datetime, timezone
from aiogram import Router, Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from beg_k_sebe_bot.bot.database.models import User
from beg_k_sebe_bot.bot.texts import messages as msg

router = Router()


class FinalStates(StatesGroup):
    waiting_wheel_b_money = State()
    waiting_wheel_b_relationships = State()
    waiting_wheel_b_health = State()


async def send_final(user_id: int, bot: Bot, state: FSMContext, session: AsyncSession) -> None:
    current = await state.get_state()
    if current is not None:
        return

    await bot.send_message(user_id, msg.WHEEL_B_INTRO)
    await bot.send_message(user_id, msg.WHEEL_B_MONEY)
    await state.set_state(FinalStates.waiting_wheel_b_money)


@router.message(FinalStates.waiting_wheel_b_money)
async def handle_wheel_b_money(message: Message, state: FSMContext, session: AsyncSession) -> None:
    value = _parse_wheel_value(message.text)
    if value is None:
        await message.answer(msg.WHEEL_INVALID)
        return
    user = await session.get(User, message.from_user.id)
    user.wheel_b_money = value
    await session.commit()
    await message.answer(msg.WHEEL_B_RELATIONSHIPS)
    await state.set_state(FinalStates.waiting_wheel_b_relationships)


@router.message(FinalStates.waiting_wheel_b_relationships)
async def handle_wheel_b_relationships(message: Message, state: FSMContext, session: AsyncSession) -> None:
    value = _parse_wheel_value(message.text)
    if value is None:
        await message.answer(msg.WHEEL_INVALID)
        return
    user = await session.get(User, message.from_user.id)
    user.wheel_b_relationships = value
    await session.commit()
    await message.answer(msg.WHEEL_B_HEALTH)
    await state.set_state(FinalStates.waiting_wheel_b_health)


@router.message(FinalStates.waiting_wheel_b_health)
async def handle_wheel_b_health(message: Message, state: FSMContext, session: AsyncSession) -> None:
    value = _parse_wheel_value(message.text)
    if value is None:
        await message.answer(msg.WHEEL_INVALID)
        return

    user = await session.get(User, message.from_user.id)
    user.wheel_b_health = value
    user.final_sent = True
    await session.commit()
    await state.clear()

    from beg_k_sebe_bot.bot.services.final_summary import build_final_summary
    summary = await build_final_summary(user, session)
    await message.answer(summary)


def _parse_wheel_value(text: str) -> int | None:
    try:
        v = int(text.strip())
        return v if 1 <= v <= 10 else None
    except ValueError:
        return None
