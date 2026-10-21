import asyncio
import hashlib
import os
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
if not TEST_DATABASE_URL:
    pytest.skip("TEST_DATABASE_URL is not set", allow_module_level=True)

os.environ.update(
    DATABASE_URL=TEST_DATABASE_URL,
    BOT_TOKEN="123456:test",
    ROBOKASSA_PASSWORD1="p1",
    ROBOKASSA_PASSWORD2="p2",
)

import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import select

from beg_k_sebe_bot.bot.database.db import AsyncSessionLocal, engine
from beg_k_sebe_bot.bot.database.models import Access, Base, Payment, User
from beg_k_sebe_bot.bot.services.tariffs import purchase_price, upgrade_price
from beg_k_sebe_bot.bot.web import build_app

pytestmark = pytest.mark.asyncio

USER_ID = 1001


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append(chat_id)

    async def create_chat_invite_link(self, chat_id, **kwargs):
        return SimpleNamespace(invite_link="https://t.me/+test")

    def messages_to(self, chat_id):
        return self.sent.count(chat_id)


@pytest_asyncio.fixture
async def bot():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield FakeBot()
    await engine.dispose()


@pytest_asyncio.fixture
async def client(bot):
    async with TestClient(TestServer(build_app(bot))) as c:
        yield c


async def _create_payment(tariff, amount, kind="purchase", access=None):
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as session:
        session.add(User(telegram_id=USER_ID))
        await session.flush()
        if access:
            session.add(Access(user_id=USER_ID, tariff=access, access_until=date(2026, 11, 20),
                               source="payment", granted_at=now))
        payment = Payment(user_id=USER_ID, tariff=tariff, kind=kind, amount=amount,
                          status="pending", created_at=now)
        session.add(payment)
        await session.commit()
        return payment.inv_id


async def _payment_status(inv_id):
    async with AsyncSessionLocal() as session:
        return await session.scalar(select(Payment.status).where(Payment.inv_id == inv_id))


async def _access_tariff():
    async with AsyncSessionLocal() as session:
        return await session.scalar(select(Access.tariff).where(Access.user_id == USER_ID))


def _signed(inv_id, amount, password, signature=None):
    out_sum = f"{amount:.6f}"
    sig = signature or hashlib.md5(f"{out_sum}:{inv_id}:{password}".encode()).hexdigest().upper()
    return {"OutSum": out_sum, "InvId": str(inv_id), "SignatureValue": sig}


async def _settle():
    await asyncio.sleep(0.1)


async def test_result_marks_payment_paid_and_grants_access(client, bot):
    amount = purchase_price("basic", False)
    inv_id = await _create_payment("basic", amount)

    resp = await client.post("/robokassa/result", data=_signed(inv_id, amount, "p2"))

    assert resp.status == 200
    assert await resp.text() == f"OK{inv_id}"
    assert await _payment_status(inv_id) == "paid"
    assert await _access_tariff() == "basic"
    await _settle()
    assert bot.messages_to(USER_ID) == 1


async def test_result_repeated_callback_is_handled_once(client, bot):
    amount = purchase_price("basic", False)
    inv_id = await _create_payment("basic", amount)

    first = await client.post("/robokassa/result", data=_signed(inv_id, amount, "p2"))
    second = await client.post("/robokassa/result", data=_signed(inv_id, amount, "p2"))

    assert first.status == second.status == 200
    assert await second.text() == f"OK{inv_id}"
    await _settle()
    assert bot.messages_to(USER_ID) == 1


async def test_result_upgrade_raises_tariff(client):
    amount = upgrade_price("basic", "middle")
    inv_id = await _create_payment("middle", amount, kind="upgrade", access="basic")

    resp = await client.post("/robokassa/result", data=_signed(inv_id, amount, "p2"))

    assert resp.status == 200
    assert await _access_tariff() == "middle"


async def test_result_rejects_bad_signature(client):
    amount = purchase_price("basic", False)
    inv_id = await _create_payment("basic", amount)

    resp = await client.post("/robokassa/result", data=_signed(inv_id, amount, "p1"))

    assert resp.status == 400
    assert await _payment_status(inv_id) == "pending"
    assert await _access_tariff() is None


async def test_result_rejects_amount_mismatch(client):
    amount = purchase_price("basic", False)
    inv_id = await _create_payment("basic", amount)

    resp = await client.post("/robokassa/result", data=_signed(inv_id, amount - 1, "p2"))

    assert resp.status == 400
    assert await _payment_status(inv_id) == "pending"
    assert await _access_tariff() is None


async def test_result_rejects_unknown_invoice(client):
    resp = await client.post("/robokassa/result", data=_signed(999, 3000, "p2"))

    assert resp.status == 400


async def test_success_notifies_user_while_payment_pending(client, bot):
    amount = purchase_price("basic", False)
    inv_id = await _create_payment("basic", amount)

    resp = await client.get("/robokassa/success", params=_signed(inv_id, amount, "p1"), allow_redirects=False)

    assert resp.status == 302
    assert bot.messages_to(USER_ID) == 1


async def test_success_ignores_bad_signature(client, bot):
    amount = purchase_price("basic", False)
    inv_id = await _create_payment("basic", amount)

    resp = await client.get("/robokassa/success", params=_signed(inv_id, amount, "p2"), allow_redirects=False)

    assert resp.status == 302
    assert bot.messages_to(USER_ID) == 0


async def test_fail_notifies_only_pending_payment(client, bot):
    amount = purchase_price("basic", False)
    inv_id = await _create_payment("basic", amount)

    await client.get("/robokassa/fail", params={"InvId": str(inv_id)}, allow_redirects=False)
    assert bot.messages_to(USER_ID) == 1

    await client.post("/robokassa/result", data=_signed(inv_id, amount, "p2"))
    await _settle()
    sent_after_paid = bot.messages_to(USER_ID)
    resp = await client.get("/robokassa/fail", params={"InvId": str(inv_id)}, allow_redirects=False)

    assert resp.status == 302
    assert bot.messages_to(USER_ID) == sent_after_paid
