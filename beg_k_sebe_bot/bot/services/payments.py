import html
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from beg_k_sebe_bot.bot.config import settings
from beg_k_sebe_bot.bot.database.db import AsyncSessionLocal
from beg_k_sebe_bot.bot.database.models import Access, Payment, User
from beg_k_sebe_bot.bot.services import robokassa
from beg_k_sebe_bot.bot.services.chat_access import chat_buttons
from beg_k_sebe_bot.bot.services.tariffs import TARIFFS, TRANSFORM
from beg_k_sebe_bot.bot.texts import messages as msg

logger = logging.getLogger(__name__)

PAYMENT_TTL = timedelta(minutes=30)
_KIND_LABELS = {"purchase": "покупка", "upgrade": "повышение"}


def format_date(d) -> str:
    return d.strftime("%d.%m.%Y")


async def transform_seats_taken(session: AsyncSession) -> int:
    paid = await session.scalar(select(func.count()).select_from(Access).where(Access.tariff == TRANSFORM))
    reserved = await session.scalar(
        select(func.count()).select_from(Payment).where(
            Payment.tariff == TRANSFORM,
            Payment.status == "pending",
            Payment.created_at > datetime.now(timezone.utc) - PAYMENT_TTL,
        )
    )
    return paid + reserved


async def transform_available(session: AsyncSession) -> bool:
    return await transform_seats_taken(session) < settings.transform_seats


async def create_payment(
    session: AsyncSession, user_id: int, tariff: str, kind: str, amount: int, promo_code: str | None,
) -> tuple[Payment, str]:
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
        expiration=datetime.now(ZoneInfo(settings.timezone)) + PAYMENT_TTL,
    )
    return payment, url


async def grant_access(session: AsyncSession, user_id: int, tariff: str, source: str) -> Access:
    access = await session.get(Access, user_id)
    if access is None:
        access = Access(
            user_id=user_id, tariff=tariff, access_until=settings.final_date,
            source=source, granted_at=datetime.now(timezone.utc),
        )
        session.add(access)
    elif TARIFFS[tariff].rank > TARIFFS[access.tariff].rank or source == "manual":
        access.tariff = tariff
    await session.commit()
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


async def _notify_admins(bot: Bot, payment: Payment, user: User, seats_taken: int) -> None:
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
    for admin_id in settings.admin_id_list:
        try:
            await bot.send_message(admin_id, text)
        except Exception as e:
            logger.error("Failed to notify admin %d about payment: %s", admin_id, e)


async def complete_payment(bot: Bot, inv_id: int, out_sum: Decimal) -> bool:
    async with AsyncSessionLocal() as session:
        payment = await session.get(Payment, inv_id)
        if payment is None:
            logger.error("Payment %d not found", inv_id)
            return False
        if Decimal(payment.amount) != out_sum:
            logger.error("Payment %d amount mismatch: expected %s, got %s", inv_id, payment.amount, out_sum)
            return False

        result = await session.execute(
            update(Payment)
            .where(Payment.inv_id == inv_id, Payment.status != "paid")
            .values(status="paid", paid_at=datetime.now(timezone.utc))
        )
        await session.commit()
        if result.rowcount == 0:
            return True

        access = await grant_access(session, payment.user_id, payment.tariff, "payment")
        user = await session.get(User, payment.user_id)
        seats_taken = await session.scalar(
            select(func.count()).select_from(Access).where(Access.tariff == TRANSFORM)
        )

    logger.info("Payment %d paid: user=%d tariff=%s amount=%d", inv_id, payment.user_id, payment.tariff, payment.amount)
    await notify_access_granted(bot, user, access)
    await _notify_admins(bot, payment, user, seats_taken)
    return True


async def fail_payment(bot: Bot, inv_id: int) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            update(Payment)
            .where(Payment.inv_id == inv_id, Payment.status == "pending")
            .values(status="failed")
            .returning(Payment.user_id)
        )
        user_id = result.scalar_one_or_none()
        await session.commit()
    if user_id is None:
        return
    try:
        await bot.send_message(user_id, msg.PAYMENT_FAILED)
    except Exception as e:
        logger.error("Failed to notify %d about failed payment: %s", user_id, e)


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
