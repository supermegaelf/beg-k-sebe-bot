from datetime import date, datetime, timezone
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from beg_k_sebe_bot.bot.config import settings
from beg_k_sebe_bot.bot.database.models import MovementFormatChange, User

router = Router()

FORMAT_LABELS = {
    "walk_22min": "🚶 22 мин быстрой ходьбы",
    "run_22min": "🏃 22 мин бега",
    "run_5km": "🏃 5 км бега",
}


def _format_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"chfmt:{key}")]
        for key, label in FORMAT_LABELS.items()
    ])


def _current_program_day() -> int:
    delta = (date.today() - settings.start_date).days + 1
    return max(1, delta)


@router.message(Command("change_format"))
async def cmd_change_format(message: Message, session: AsyncSession) -> None:
    user = await session.get(User, message.from_user.id)
    if not user or not user.onboarding_completed_at:
        return

    today = date.today()
    if today < settings.start_date or today > settings.final_date:
        return

    current_label = FORMAT_LABELS.get(user.movement_format, user.movement_format)
    await message.answer(
        f"Текущий формат: {current_label}\n\nВыбери новый формат:",
        reply_markup=_format_keyboard(),
    )


@router.callback_query(F.data.startswith("chfmt:"))
async def handle_format_change(callback: CallbackQuery, session: AsyncSession) -> None:
    new_format = callback.data.split(":")[1]
    user = await session.get(User, callback.from_user.id)
    if not user:
        await callback.answer()
        return

    change = MovementFormatChange(
        user_id=callback.from_user.id,
        old_format=user.movement_format,
        new_format=new_format,
        changed_on_day=_current_program_day(),
        changed_at=datetime.now(timezone.utc),
    )
    session.add(change)
    user.movement_format = new_format
    await session.commit()

    label = FORMAT_LABELS[new_format]
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(f"Формат обновлён: {label}")
    await callback.answer()
