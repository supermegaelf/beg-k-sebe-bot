from dataclasses import dataclass


@dataclass(frozen=True)
class Tariff:
    key: str
    title: str
    price: int
    rank: int


TARIFFS = {
    "basic": Tariff("basic", "Базовый", 3000, 1),
    "middle": Tariff("middle", "Средний", 9000, 2),
    "transform": Tariff("transform", "Трансформационный", 22000, 3),
}

TRANSFORM = "transform"
PROMO_DISCOUNT_PCT = 20


def purchase_price(key: str, promo: bool) -> int:
    price = TARIFFS[key].price
    return price * (100 - PROMO_DISCOUNT_PCT) // 100 if promo else price


def upgrade_price(current: str, target: str) -> int | None:
    if TARIFFS[target].rank <= TARIFFS[current].rank:
        return None
    return TARIFFS[target].price - TARIFFS[current].price


def overpayment(kind: str, tariff: str, amount: int, current: str | None) -> int:
    if current is None:
        return 0
    due = upgrade_price(current, tariff) if kind == "upgrade" else None
    return amount - (due or 0)


def upgrade_targets(current: str) -> list[Tariff]:
    return [t for t in TARIFFS.values() if t.rank > TARIFFS[current].rank]
