import logging
from decimal import Decimal, InvalidOperation

from aiogram import Bot
from aiohttp import web

from beg_k_sebe_bot.bot.config import settings
from beg_k_sebe_bot.bot.services import robokassa
from beg_k_sebe_bot.bot.services.payments import complete_payment, notify_payment_failed

logger = logging.getLogger(__name__)

_BOT_KEY = web.AppKey("bot", Bot)


async def _params(request: web.Request) -> dict[str, str]:
    params = dict(request.query)
    if request.method == "POST":
        params.update(await request.post())
    return params


async def handle_result(request: web.Request) -> web.Response:
    params = await _params(request)
    out_sum, inv_id, signature = params.get("OutSum"), params.get("InvId"), params.get("SignatureValue")
    if not out_sum or not inv_id or not signature or not inv_id.isdigit():
        return web.Response(status=400, text="bad request")
    if not robokassa.verify_result(out_sum, inv_id, signature, settings.robokassa_password2):
        logger.warning("ResultURL: bad signature for InvId=%s", inv_id)
        return web.Response(status=400, text="bad sign")
    try:
        amount = Decimal(out_sum)
    except InvalidOperation:
        return web.Response(status=400, text="bad sum")

    if not await complete_payment(request.app[_BOT_KEY], int(inv_id), amount):
        return web.Response(status=400, text="payment rejected")
    return web.Response(text=f"OK{inv_id}")


async def handle_success(_: web.Request) -> web.Response:
    raise web.HTTPFound(settings.success_page_url or "https://t.me")


async def handle_fail(request: web.Request) -> web.Response:
    inv_id = (await _params(request)).get("InvId", "")
    if inv_id.isdigit():
        await notify_payment_failed(request.app[_BOT_KEY], int(inv_id))
    raise web.HTTPFound(settings.fail_page_url or "https://t.me")


def build_app(bot: Bot) -> web.Application:
    app = web.Application()
    app[_BOT_KEY] = bot
    app.router.add_route("*", "/robokassa/result", handle_result)
    app.router.add_route("*", "/robokassa/success", handle_success)
    app.router.add_route("*", "/robokassa/fail", handle_fail)
    return app
