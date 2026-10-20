import hashlib
import json
from urllib.parse import parse_qs, unquote, urlparse

from beg_k_sebe_bot.bot.services import robokassa
from beg_k_sebe_bot.bot.services.tariffs import overpayment, purchase_price, upgrade_price, upgrade_targets


def _md5(s):
    return hashlib.md5(s.encode()).hexdigest()


def test_format_sum():
    assert robokassa.format_sum(3000) == "3000.00"


def test_receipt_is_urlencoded_minified_json():
    receipt = robokassa.build_receipt("Тариф Базовый", 2400)
    assert "%" in receipt and " " not in receipt
    assert json.loads(unquote(receipt)) == {"items": [{"name": "Тариф Базовый", "quantity": 1, "sum": 2400, "tax": "none"}]}


def test_docs_receipt_example_signature():
    receipt = "%7B%22items%22%3A%5B%7B%22name%22%3A%22product%22%2C%22quantity%22%3A1%2C%22sum%22%3A8.96%2C%22tax%22%3A%22none%22%7D%5D%7D"
    assert robokassa.payment_signature("demo", "8.96", 5, receipt, "pass1") == _md5(f"demo:8.96:5:{receipt}:pass1")


def test_payment_url_params_and_signature():
    url = robokassa.payment_url(
        login="shop", password1="p1", inv_id=7, amount=3000,
        description="Бег к себе - тариф Базовый", receipt_item="Тариф Базовый", is_test=True,
    )
    parsed = urlparse(url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == robokassa.PAYMENT_URL
    q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert q["MerchantLogin"] == "shop"
    assert q["OutSum"] == "3000.00"
    assert q["InvId"] == "7"
    assert q["IsTest"] == "1"
    assert q["Receipt"] == robokassa.build_receipt("Тариф Базовый", 3000)
    assert q["SignatureValue"] == _md5(f"shop:3000.00:7:{q['Receipt']}:p1")


def test_payment_url_without_test_flag():
    url = robokassa.payment_url(
        login="shop", password1="p1", inv_id=1, amount=100,
        description="x", receipt_item="x", is_test=False,
    )
    assert "IsTest" not in parse_qs(urlparse(url).query)


def test_verify_signature_uses_received_sum_and_password():
    sig = _md5("3000.000000:7:p2").upper()
    assert robokassa.verify_signature("3000.000000", "7", sig, "p2")
    assert not robokassa.verify_signature("3000.000000", "7", sig, "p1")
    assert not robokassa.verify_signature("2400.000000", "7", sig, "p2")


def test_parse_op_state():
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<OperationStateResponse xmlns="http://merchant.roboxchange.com/WebService/">'
        "<Result><Code>0</Code></Result>"
        "<State><Code>100</Code><RequestDate>2026-10-20T10:00:00+03:00</RequestDate></State>"
        "</OperationStateResponse>"
    )
    assert robokassa.parse_op_state(xml) == (0, 100)


def test_parse_op_state_not_found():
    xml = '<OperationStateResponse xmlns="http://merchant.roboxchange.com/WebService/"><Result><Code>3</Code></Result></OperationStateResponse>'
    assert robokassa.parse_op_state(xml) == (3, None)


def test_purchase_price_with_promo():
    assert purchase_price("basic", False) == 3000
    assert purchase_price("basic", True) == 2400
    assert purchase_price("middle", True) == 5600
    assert purchase_price("transform", True) == 17600


def test_upgrade_price_is_full_price_difference():
    assert upgrade_price("basic", "middle") == 4000
    assert upgrade_price("basic", "transform") == 19000
    assert upgrade_price("middle", "transform") == 15000
    assert upgrade_price("middle", "basic") is None
    assert upgrade_price("transform", "transform") is None


def test_overpayment():
    assert overpayment("purchase", "basic", 3000, None) == 0
    assert overpayment("upgrade", "middle", 4000, "basic") == 0
    assert overpayment("purchase", "middle", 7000, "basic") == 7000
    assert overpayment("upgrade", "transform", 19000, "middle") == 4000
    assert overpayment("upgrade", "middle", 4000, "middle") == 4000


def test_upgrade_targets():
    assert [t.key for t in upgrade_targets("basic")] == ["middle", "transform"]
    assert upgrade_targets("transform") == []


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} passed")


if __name__ == "__main__":
    _run_all()
