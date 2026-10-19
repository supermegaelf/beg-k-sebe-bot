import hashlib
import json
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import quote, urlencode

PAYMENT_URL = "https://auth.robokassa.ru/Merchant/Index.aspx"
OP_STATE_URL = "https://auth.robokassa.ru/Merchant/WebService/Service.asmx/OpStateExt"

STATE_PAID = 100


def _md5(value: str) -> str:
    return hashlib.md5(value.encode("utf-8")).hexdigest()


def format_sum(amount: int) -> str:
    return f"{amount:.2f}"


def build_receipt(item_name: str, amount: int) -> str:
    receipt = {"items": [{"name": item_name, "quantity": 1, "sum": amount, "tax": "none"}]}
    return quote(json.dumps(receipt, ensure_ascii=False, separators=(",", ":")), safe="")


def payment_signature(login: str, out_sum: str, inv_id: int, receipt: str, password1: str) -> str:
    return _md5(f"{login}:{out_sum}:{inv_id}:{receipt}:{password1}")


def payment_url(
    *,
    login: str,
    password1: str,
    inv_id: int,
    amount: int,
    description: str,
    receipt_item: str,
    is_test: bool,
    expiration: datetime | None = None,
) -> str:
    out_sum = format_sum(amount)
    receipt = build_receipt(receipt_item, amount)
    params = {
        "MerchantLogin": login,
        "OutSum": out_sum,
        "InvId": inv_id,
        "Description": description,
        "Receipt": receipt,
        "Culture": "ru",
        "SignatureValue": payment_signature(login, out_sum, inv_id, receipt, password1),
    }
    if expiration is not None:
        params["ExpirationDate"] = expiration.strftime("%Y-%m-%dT%H:%M")
    if is_test:
        params["IsTest"] = "1"
    return f"{PAYMENT_URL}?{urlencode(params)}"


def result_signature(out_sum: str, inv_id: str, password2: str) -> str:
    return _md5(f"{out_sum}:{inv_id}:{password2}")


def verify_result(out_sum: str, inv_id: str, signature: str, password2: str) -> bool:
    return result_signature(out_sum, inv_id, password2) == signature.lower()


def parse_op_state(xml_text: str) -> tuple[int | None, int | None]:
    root = ET.fromstring(xml_text)
    result_code = state_code = None
    for parent in root:
        tag = parent.tag.rsplit("}", 1)[-1]
        if tag not in ("Result", "State"):
            continue
        for child in parent:
            if child.tag.rsplit("}", 1)[-1] == "Code" and child.text:
                if tag == "Result":
                    result_code = int(child.text)
                else:
                    state_code = int(child.text)
    return result_code, state_code


async def fetch_op_state(login: str, inv_id: int, password2: str) -> int | None:
    import aiohttp

    params = {
        "MerchantLogin": login,
        "InvoiceID": inv_id,
        "Signature": _md5(f"{login}:{inv_id}:{password2}"),
    }
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as http:
        async with http.get(OP_STATE_URL, params=params) as resp:
            text = await resp.text()
    result_code, state_code = parse_op_state(text)
    return state_code if result_code == 0 else None
