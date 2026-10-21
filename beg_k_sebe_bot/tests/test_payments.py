import hashlib
import json
from urllib.parse import parse_qs, unquote, urlparse

from beg_k_sebe_bot.bot.services import robokassa
from beg_k_sebe_bot.bot.services.tariffs import (
    TARIFFS, overpayment, purchase_price, upgrade_price, upgrade_targets,
)


def _md5(s):
    return hashlib.md5(s.encode()).hexdigest()


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
    assert json.loads(unquote(q["Receipt"])) == {
        "items": [{"name": "Тариф Базовый", "quantity": 1, "sum": 3000, "tax": "none"}],
    }
    assert q["SignatureValue"] == _md5(f"shop:{q['OutSum']}:7:{q['Receipt']}:p1")


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


def test_promo_price_is_lower_than_full_price():
    for key, tariff in TARIFFS.items():
        assert purchase_price(key, False) == tariff.price
        assert 0 < purchase_price(key, True) < tariff.price


def test_upgrade_price_tops_up_to_target_price():
    for current, target in [("basic", "middle"), ("basic", "transform"), ("middle", "transform")]:
        assert TARIFFS[current].price + upgrade_price(current, target) == TARIFFS[target].price
    assert upgrade_price("middle", "basic") is None
    assert upgrade_price("transform", "transform") is None


def test_overpayment():
    exact = upgrade_price("basic", "middle")
    assert overpayment("basic", TARIFFS["basic"].price, None) == 0
    assert overpayment("middle", exact, "basic") == 0
    assert overpayment("middle", exact + 500, "basic") == 500
    assert overpayment("middle", TARIFFS["middle"].price, "middle") == TARIFFS["middle"].price
    assert overpayment("basic", TARIFFS["basic"].price, "middle") == TARIFFS["basic"].price


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
