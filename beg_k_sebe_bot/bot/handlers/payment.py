from contextlib import suppress
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from beg_k_sebe_bot.bot.config import settings
from beg_k_sebe_bot.bot.database.models import Access, Consent, User
from beg_k_sebe_bot.bot.services import payments
from beg_k_sebe_bot.bot.services.chat_access import chat_buttons
from beg_k_sebe_bot.bot.services.tariffs import (
    TARIFFS, TRANSFORM, purchase_price, upgrade_price, upgrade_targets,
)
from beg_k_sebe_bot.bot.texts import messages as msg
from beg_k_sebe_bot.bot.utils.program import today_msk

router = Router()


class PaymentStates(StatesGroup):
    waiting_promo = State()


def _price_label(key: str, promo: bool) -> str:
    price = purchase_price(key, promo)
    if promo:
        return msg.TARIFF_PRICE_PROMO.format(full=TARIFFS[key].price, price=price)
    return msg.TARIFF_PRICE.format(price=price)


async def _promo(state: FSMContext) -> str | None:
    return (await state.get_data()).get("promo")


async def show_tariffs(message: Message, state: FSMContext, session: AsyncSession, user_id: int, username: str | None) -> None:
    await state.set_state(None)
    if today_msk() > settings.registration_deadline:
        await message.answer(msg.REGISTRATION_CLOSED)
        return

    if await session.get(User, user_id) is None:
        session.add(User(telegram_id=user_id, username=username, joined_at=datetime.now(timezone.utc)))
        await session.commit()

    promo = await _promo(state) is not None
    available = await payments.transform_available(session, user_id)

    text = msg.TARIFFS.format(
        basic=_price_label("basic", promo),
        middle=_price_label("middle", promo),
        transform=_price_label(TRANSFORM, promo),
        transform_seats=settings.transform_seats,
    )
    if promo:
        text += msg.TARIFFS_PROMO_APPLIED
    if not available:
        text += msg.TRANSFORM_SOLD_OUT

    rows = [
        [InlineKeyboardButton(
            text=msg.TARIFF_BUTTON.format(title=t.title, price=purchase_price(t.key, promo)),
            callback_data=f"buy:{t.key}",
        )]
        for t in TARIFFS.values()
        if t.key != TRANSFORM or available
    ]
    if not promo and settings.promo_code:
        rows.append([InlineKeyboardButton(text=msg.PROMO_BUTTON, callback_data="promo:enter")])
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data == "promo:enter")
async def promo_enter(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PaymentStates.waiting_promo)
    await callback.message.answer(msg.PROMO_ASK, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=msg.PROMO_SKIP_BUTTON, callback_data="promo:skip"),
    ]]))
    await callback.answer()


@router.callback_query(F.data == "promo:skip")
async def promo_skip(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    with suppress(TelegramBadRequest):
        await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()
    await show_tariffs(callback.message, state, session, callback.from_user.id, callback.from_user.username)


@router.message(PaymentStates.waiting_promo, ~F.text.startswith("/"))
async def promo_entered(message: Message, state: FSMContext, session: AsyncSession) -> None:
    code = (message.text or "").strip()
    if not settings.promo_code or code.lower() != settings.promo_code.lower():
        await message.answer(msg.PROMO_INVALID)
        return
    await state.update_data(promo=settings.promo_code)
    await show_tariffs(message, state, session, message.from_user.id, message.from_user.username)


def _consent_keyboard(action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=msg.CONSENT_BUTTON, callback_data=f"consent:{action}"),
    ]])


async def _send_payment_link(
    message: Message, session: AsyncSession, user_id: int, tariff: str, kind: str, amount: int, promo: str | None,
) -> None:
    _, url = await payments.get_or_create_payment(session, user_id, tariff, kind, amount, promo)
    await message.answer(
        msg.PAYMENT_LINK.format(tariff=TARIFFS[tariff].title, price=amount),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=msg.PAYMENT_PAY_BUTTON.format(price=amount), url=url)],
        ]),
    )


async def _proceed(callback: CallbackQuery, state: FSMContext, session: AsyncSession, action: str) -> None:
    kind, tariff = action.split(":")
    user_id = callback.from_user.id
    if tariff not in TARIFFS:
        return

    access = await session.get(Access, user_id)
    if kind == "buy":
        if access is not None:
            await callback.message.answer(msg.ALREADY_HAS_TARIFF.format(tariff=TARIFFS[access.tariff].title))
            return
        if today_msk() > settings.registration_deadline:
            await callback.message.answer(msg.REGISTRATION_CLOSED)
            return
        promo = await _promo(state)
        amount = purchase_price(tariff, promo is not None)
        payment_kind = "purchase"
    else:
        if access is None:
            await callback.message.answer(msg.MY_TARIFF_NONE)
            return
        if today_msk() >= settings.final_date:
            await callback.message.answer(msg.UPGRADE_CLOSED)
            return
        amount = upgrade_price(access.tariff, tariff)
        if amount is None:
            return
        promo = None
        payment_kind = "upgrade"

    if tariff == TRANSFORM and not await payments.transform_available(session, user_id):
        await callback.message.answer(msg.TRANSFORM_SOLD_OUT.strip())
        return

    if await session.get(Consent, user_id) is None:
        await callback.message.answer(
            msg.CONSENT.format(offer_url=settings.offer_url, consent_url=settings.consent_url),
            reply_markup=_consent_keyboard(action),
            disable_web_page_preview=True,
        )
        return

    await _send_payment_link(callback.message, session, user_id, tariff, payment_kind, amount, promo)


@router.callback_query(F.data.startswith("buy:"))
async def handle_buy(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await callback.answer()
    await _proceed(callback, state, session, callback.data)


@router.callback_query(F.data.startswith("upgrade:"))
async def handle_upgrade(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await callback.answer()
    await _proceed(callback, state, session, callback.data)


@router.callback_query(F.data.startswith("consent:"))
async def handle_consent(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await callback.answer()
    if await session.get(Consent, callback.from_user.id) is None:
        session.add(Consent(user_id=callback.from_user.id, accepted_at=datetime.now(timezone.utc)))
        await session.commit()
    with suppress(TelegramBadRequest):
        await callback.message.edit_reply_markup(reply_markup=None)
    await _proceed(callback, state, session, callback.data.removeprefix("consent:"))


@router.message(Command("tariff"))
async def cmd_tariff(message: Message, session: AsyncSession) -> None:
    access = await session.get(Access, message.from_user.id)
    if access is None:
        await message.answer(msg.MY_TARIFF_NONE)
        return

    text = msg.MY_TARIFF.format(tariff=TARIFFS[access.tariff].title, until=payments.format_date(access.access_until))
    rows = await chat_buttons(message.bot, access.tariff)
    if today_msk() < settings.final_date:
        transform_open = await payments.transform_available(session, message.from_user.id)
        targets = [t for t in upgrade_targets(access.tariff) if t.key != TRANSFORM or transform_open]
        if targets:
            text += msg.MY_TARIFF_UPGRADE
            rows += [
                [InlineKeyboardButton(
                    text=msg.UPGRADE_BUTTON.format(title=t.title, price=upgrade_price(access.tariff, t.key)),
                    callback_data=f"upgrade:{t.key}",
                )]
                for t in targets
            ]
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
