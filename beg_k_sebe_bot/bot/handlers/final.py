from aiogram import Router, Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from beg_k_sebe_bot.bot.database.models import User
from beg_k_sebe_bot.bot.texts import messages as msg
from beg_k_sebe_bot.bot.utils.validators import parse_score

router = Router()


class FinalStates(StatesGroup):
    waiting_point_b_score = State()
    waiting_point_b_text = State()


async def send_final(user_id: int, bot: Bot, state: FSMContext, session: AsyncSession) -> None:
    current = await state.get_state()
    if current is not None:
        return

    user = await session.get(User, user_id)
    if user is None:
        return
    user.final_sent = True  # mark up front so a restart won't re-send the intro
    await session.commit()

    await bot.send_message(user_id, msg.FINAL_INTRO)
    await bot.send_message(user_id, msg.FINAL_POINT_B_SCORE)
    await state.set_state(FinalStates.waiting_point_b_score)


@router.message(FinalStates.waiting_point_b_score)
async def handle_point_b_score(message: Message, state: FSMContext, session: AsyncSession) -> None:
    value = parse_score(message.text)
    if value is None:
        await message.answer(msg.SCORE_INVALID)
        return
    user = await session.get(User, message.from_user.id)
    if user is None:
        await state.clear()
        return
    user.point_b_score = value
    await session.commit()
    await message.answer(msg.FINAL_POINT_B_TEXT)
    await state.set_state(FinalStates.waiting_point_b_text)


@router.message(FinalStates.waiting_point_b_text)
async def handle_point_b_text(message: Message, state: FSMContext, session: AsyncSession) -> None:
    user = await session.get(User, message.from_user.id)
    if user is None:
        await state.clear()
        return
    user.point_b_text = message.text
    await session.commit()
    await state.clear()

    from beg_k_sebe_bot.bot.services.final_summary import build_final_summary
    summary = await build_final_summary(user, session)
    await message.answer(summary)
