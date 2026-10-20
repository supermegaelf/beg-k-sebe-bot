import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from beg_k_sebe_bot.bot.config import settings
from beg_k_sebe_bot.bot.database.db import AsyncSessionLocal
from beg_k_sebe_bot.bot.database.models import Access, Payment, User
from beg_k_sebe_bot.bot.services import robokassa
from beg_k_sebe_bot.bot.services.chat_access import chat_buttons
from beg_k_sebe_bot.bot.services.tariffs import TARIFFS, TRANSFORM, overpayment
from beg_k_sebe_bot.bot.texts import messages as msg

logger = logging.getLogger(__name__)

PAYMENT_TTL = timedelta(minutes=30)
_KIND_LABELS = {"purchase": "покупка", "upgrade": "повышение"}
_notify_tasks: set[asyncio.Task] = set()


def format_date(d) -> str:
    return d.strftime("%d.%m.%Y")


def _open_payment_filter():
    return (Payment.status == "pending", Payment.created_at > datetime.now(timezone.utc) - PAYMENT_TTL)


async def transform_seats_taken(session: AsyncSession, user_id: int) -> int:
    transform_users = select(Access.user_id).where(Access.tariff == TRANSFORM)
    paid = await session.scalar(select(func.count()).select_from(transform_users.subquery()))
    reserved = await session.scalar(
        select(func.count(func.distinct(Payment.user_id))).where(
            Payment.tariff == TRANSFORM,
            *_open_payment_filter(),
            Payment.user_id.not_in(transform_users),
            Payment.user_id != user_id,
        )
    )
    return paid + reserved


async def transform_available(session: AsyncSession, user_id: int) -> bool:
    return await transform_seats_taken(session, user_id) < settings.transform_seats


async def get_or_create_payment(
    session: AsyncSession, user_id: int, tariff: str, kind: str, amount: int, promo_code: str | None,
) -> tuple[Payment, str]:
    payment = await session.scalar(
        select(Payment)
        .where(
            Payment.user_id == user_id, Payment.tariff == tariff, Payment.kind == kind,
            Payment.amount == amount, *_open_payment_filter(),
        )
        .order_by(Payment.inv_id.desc())
        .limit(1)
    )
    if payment is None:
        payment = Payment(
            user_id=user_id, tariff=tariff, kind=kind, amount=amount, promo_code=promo_code,
            status="pending", created_at=datetime.now(timezone.utc),
        )
        session.add(payment)
        await session.commit()

    title = TARIFFS[tariff].title
    url = robokassa.payment_url(
        login=settings.robokassa_login,
        password1=settings.robokassa_password1,
        inv_id=payment.inv_id,
        amount=amount,
        description=f"Бег к себе - тариф {title}",
        receipt_item=f"Участие в потоке Бег к себе, тариф {title}",
        is_test=settings.robokassa_is_test,
        expiration=payment.created_at.astimezone(ZoneInfo(settings.timezone)) + PAYMENT_TTL,
    )
    return payment, url


def grant_access(session: AsyncSession, access: Access | None, user_id: int, tariff: str, source: str) -> Access:
    if access is None:
        access = Access(
            user_id=user_id, tariff=tariff, access_until=settings.final_date - timedelta(days=1),
            source=source, granted_at=datetime.now(timezone.utc),
        )
        session.add(access)
    elif TARIFFS[tariff].rank > TARIFFS[access.tariff].rank or source == "manual":
        access.tariff = tariff
    return access


async def notify_access_granted(bot: Bot, user: User, access: Access) -> None:
    text = msg.PAYMENT_SUCCESS.format(tariff=TARIFFS[access.tariff].title, until=format_date(access.access_until))
    rows = await chat_buttons(bot, access.tariff)
    if user.onboarding_completed_at is None:
        text += msg.PAYMENT_SUCCESS_ONBOARDING
        rows.append([InlineKeyboardButton(text=msg.ONBOARDING_START_BUTTON, callback_data="onb:start")])
    try:
        await bot.send_message(user.telegram_id, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    except Exception as e:
        logger.error("Failed to notify %d about access: %s", user.telegram_id, e)


async def _notify_admins(bot: Bot, payment: Payment, user: User, seats_taken: int, overpaid: int) -> None:
    text = msg.ADMIN_PAYMENT.format(
        amount=payment.amount,
        tariff=TARIFFS[payment.tariff].title,
        kind=_KIND_LABELS[payment.kind],
        user=html.escape(f"@{user.username}" if user.username else "без юзернейма"),
        user_id=user.telegram_id,
        inv_id=payment.inv_id,
    )
    if payment.promo_code:
        text += msg.ADMIN_PAYMENT_PROMO.format(promo=html.escape(payment.promo_code))
    if payment.tariff == TRANSFORM and seats_taken > settings.transform_seats:
        text += msg.ADMIN_PAYMENT_OVER_LIMIT.format(taken=seats_taken, limit=settings.transform_seats)
    if overpaid > 0:
        text += msg.ADMIN_PAYMENT_OVERPAID.format(overpaid=overpaid)
    for admin_id in settings.admin_id_list:
        try:
            await bot.send_message(admin_id, text)
        except Exception as e:
            logger.error("Failed to notify admin %d about payment: %s", admin_id, e)


async def complete_payment(bot: Bot, inv_id: int, out_sum: Decimal) -> bool:
    async with AsyncSessionLocal() as session:
        payment = await session.scalar(select(Payment).where(Payment.inv_id == inv_id).with_for_update())
        if payment is None:
            logger.error("Payment %d not found", inv_id)
            return False
        if Decimal(payment.amount) != out_sum:
            logger.error("Payment %d amount mismatch: expected %s, got %s", inv_id, payment.amount, out_sum)
            return False
        if payment.status == "paid":
            return True

        access = await session.get(Access, payment.user_id)
        overpaid = overpayment(payment.kind, payment.tariff, payment.amount, access.tariff if access else None)
        payment.status = "paid"
        payment.paid_at = datetime.now(timezone.utc)
        access = grant_access(session, access, payment.user_id, payment.tariff, "payment")
        await session.commit()

        user = await session.get(User, payment.user_id)
        seats_taken = await session.scalar(
            select(func.count()).select_from(Access).where(Access.tariff == TRANSFORM)
        )

    logger.info("Payment %d paid: user=%d tariff=%s amount=%d", inv_id, payment.user_id, payment.tariff, payment.amount)
    task = asyncio.create_task(_notify_paid(bot, payment, user, access, seats_taken, overpaid))
    _notify_tasks.add(task)
    task.add_done_callback(_notify_tasks.discard)
    return True


async def _notify_paid(bot: Bot, payment: Payment, user: User, access: Access, seats_taken: int, overpaid: int) -> None:
    await notify_access_granted(bot, user, access)
    await _notify_admins(bot, payment, user, seats_taken, overpaid)


async def _notify_if_pending(bot: Bot, inv_id: int, text: str) -> None:
    async with AsyncSessionLocal() as session:
        user_id = await session.scalar(
            select(Payment.user_id).where(Payment.inv_id == inv_id, Payment.status == "pending")
        )
    if user_id is None:
        return
    try:
        await bot.send_message(user_id, text)
    except Exception as e:
        logger.error("Failed to notify %d about payment %d: %s", user_id, inv_id, e)


async def notify_payment_failed(bot: Bot, inv_id: int) -> None:
    await _notify_if_pending(bot, inv_id, msg.PAYMENT_FAILED)


async def notify_payment_pending(bot: Bot, inv_id: int) -> None:
    await _notify_if_pending(bot, inv_id, msg.PAYMENT_RECEIVED)


async def check_payment(bot: Bot, payment: Payment) -> bool:
    if settings.robokassa_is_test:
        return False
    try:
        state = await robokassa.fetch_op_state(settings.robokassa_login, payment.inv_id, settings.robokassa_password2)
    except Exception as e:
        logger.error("OpStateExt failed for %d: %s", payment.inv_id, e)
        return False
    if state != robokassa.STATE_PAID:
        return False
    return await complete_payment(bot, payment.inv_id, Decimal(payment.amount))


async def check_pending_payments(bot: Bot) -> None:
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as session:
        pending = (await session.execute(
            select(Payment).where(
                Payment.status != "paid",
                Payment.created_at > now - timedelta(hours=2),
                Payment.created_at < now - timedelta(minutes=2),
            )
        )).scalars().all()
    for payment in pending:
        await check_payment(bot, payment)
