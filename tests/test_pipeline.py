from __future__ import annotations

import os
import time
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import FakeWb

from wb_reviews import demo
from wb_reviews.client import WbClient
from wb_reviews.pipeline import JsonCache, collect, parse_product

DRESS, SHOES, MUG = demo.SCENARIOS


def make_client(tmp_path: Path) -> WbClient:
    return WbClient(pause=0, sleep=lambda _: None, cache_file=tmp_path / "baskets.json")


@pytest.mark.parametrize(
    ("text", "nm"),
    [
        ("13221580", 13221580),
        (" 146972802 ", 146972802),
        ("https://www.wildberries.ru/catalog/13221580/detail.aspx?targetUrl=GP", 13221580),
        ("https://wb.ru/catalog/146972802/detail.aspx", 146972802),
    ],
)
def test_parse_product(text: str, nm: int) -> None:
    assert parse_product(text) == nm


def test_parse_product_rejects_other_text() -> None:
    with pytest.raises(ValueError, match="Не похоже"):
        parse_product("https://www.ozon.ru/product/123")


def test_second_run_is_served_from_cache(wb: FakeWb, tmp_path: Path) -> None:
    wb.add(DRESS)
    cache = JsonCache(tmp_path, timedelta(hours=12))
    first = collect(make_client(tmp_path), [DRESS.nm], cache)
    assert len(wb.urls) == 2
    second = collect(make_client(tmp_path), [DRESS.nm], cache)
    assert len(wb.urls) == 2  # ни одного нового запроса
    assert first.products == second.products


def test_week_old_cache_is_downloaded_again(wb: FakeWb, tmp_path: Path) -> None:
    """Недельный кэш не считается свежим."""
    wb.add(DRESS)
    cache = JsonCache(tmp_path, timedelta(hours=12))
    collect(make_client(tmp_path), [DRESS.nm], cache)
    week_ago = time.time() - 7 * 86_400
    for path in tmp_path.glob("*-*.json"):
        os.utime(path, (week_ago, week_ago))
    collect(make_client(tmp_path), [DRESS.nm], cache)
    assert len(wb.urls) == 4


def test_cache_keeps_no_personal_data(wb: FakeWb, tmp_path: Path) -> None:
    wb.add(DRESS)
    assert "Покупатель" in str(wb.feedbacks[DRESS.imt])  # в ответе «WB» имена есть…
    collect(make_client(tmp_path), [DRESS.nm], JsonCache(tmp_path, timedelta(hours=1)))
    for path in tmp_path.glob("*.json"):
        text = path.read_text("utf-8")
        for secret in ("Покупатель", "globalUserId", "wbUserId"):
            assert secret not in text  # …а в кэше нет


def test_variants_of_one_card_are_counted_once(wb: FakeWb, tmp_path: Path) -> None:
    wb.add(DRESS)
    variant = DRESS.nm + 50
    wb.cards[variant] = (wb.cards[DRESS.nm][0], {**demo.card_json(DRESS), "nm_id": variant})
    result = collect(make_client(tmp_path), [DRESS.nm, variant])
    assert len(result.products) == 1
    assert result.notes == [f"{variant}: та же карточка, что и {DRESS.nm}, — отзывы общие, считаю один раз"]
    assert sum("feedbacks" in url for url in wb.urls) == 1


def test_missing_product_does_not_stop_the_others(wb: FakeWb, tmp_path: Path) -> None:
    wb.add(SHOES)
    result = collect(make_client(tmp_path), [123_456_789, SHOES.nm])
    assert [product.card.nm for product in result.products] == [SHOES.nm]
    assert result.errors[0][0] == "123456789"
    assert "не найден" in result.errors[0][1]


def test_blocked_wb_stops_the_run(wb: FakeWb, tmp_path: Path) -> None:
    for scenario in demo.SCENARIOS:
        wb.add(scenario)
    wb.failures["feedbacks1"] = [403]
    result = collect(make_client(tmp_path), [DRESS.nm, SHOES.nm, MUG.nm])
    assert result.products == []
    assert [nm for nm, _ in result.errors] == [str(DRESS.nm), str(SHOES.nm), str(MUG.nm)]
    assert "403" in result.errors[0][1]
    assert "не запрашивали" in result.errors[1][1]
    assert len(wb.urls) == 2  # карточка и один запрос отзывов — дальше WB не трогаем


def test_changed_format_is_reported_per_product(wb: FakeWb, tmp_path: Path) -> None:
    wb.add(DRESS)
    wb.add(SHOES)
    basket, card = wb.cards[DRESS.nm]
    wb.cards[DRESS.nm] = (basket, {key: value for key, value in card.items() if key != "imt_id"})
    result = collect(make_client(tmp_path), [DRESS.nm, SHOES.nm])
    assert "WB изменил формат ответа" in result.errors[0][1]
    assert [product.card.nm for product in result.products] == [SHOES.nm]


def test_broken_reviews_are_skipped_not_fatal(wb: FakeWb, tmp_path: Path) -> None:
    wb.add(DRESS)
    items = wb.feedbacks[DRESS.imt]["feedbacks"]
    items[0]["createdDate"] = ""
    items[1]["productValuation"] = None
    (product,) = collect(make_client(tmp_path), [DRESS.nm]).products
    assert product.broken == 2
    assert len(product.reviews) == len(items) - 2
