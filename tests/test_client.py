from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import requests
from conftest import FakeWb

from wb_reviews import demo
from wb_reviews.client import (
    BASKET_HINTS,
    TIMEOUT,
    ProductNotFoundError,
    WbBlockedError,
    WbClient,
    WbError,
    hinted_basket,
)

DRESS = demo.SCENARIOS[0]


def make_client(**kwargs: Any) -> tuple[WbClient, list[float]]:
    sleeps: list[float] = []
    return WbClient(pause=0, sleep=sleeps.append, **kwargs), sleeps


def test_hint_table_is_ordered_and_matches_live_observations() -> None:
    assert [last for last, _ in BASKET_HINTS] == sorted(last for last, _ in BASKET_HINTS)
    assert [basket for _, basket in BASKET_HINTS] == sorted(basket for _, basket in BASKET_HINTS)
    assert (hinted_basket(132), hinted_basket(1469), hinted_basket(2153)) == (
        1,
        10,
        14,
    )  # проверено на живом WB
    assert hinted_basket(99_999) is None


def test_card_from_hinted_host_takes_one_request(wb: FakeWb) -> None:
    wb.add(DRESS)
    client, _ = make_client()
    assert client.card(DRESS.nm)["imt_id"] == DRESS.imt
    assert len(wb.urls) == 1


def test_unknown_volume_is_found_by_probing_and_remembered(wb: FakeWb, tmp_path: Path) -> None:
    nm = 250_100_123  # том 2501: подсказка — basket-16, а карточка лежит на basket-18
    wb.cards[nm] = (18, {**demo.card_json(DRESS), "nm_id": nm})
    cache = tmp_path / "baskets.json"
    first, _ = make_client(cache_file=cache)
    assert first.card(nm)["nm_id"] == nm
    assert wb.urls[0].startswith("https://basket-16.")
    assert wb.urls[-1].startswith("https://basket-18.")
    assert first.requests_made == 18  # 16, затем 1…15, 17, 18
    assert json.loads(cache.read_text("utf-8")) == {"2501": 18}

    second, _ = make_client(cache_file=cache)
    second.card(nm)
    assert second.requests_made == 1  # следующий запуск сразу идёт на нужный хост


def test_missing_product_checks_every_host_once(wb: FakeWb) -> None:
    client, _ = make_client()
    with pytest.raises(ProductNotFoundError, match="не найден"):
        client.card(123_456_789)
    assert len(wb.urls) == 40
    assert len(set(wb.urls)) == 40


def test_every_request_has_timeout_and_browser_headers(wb: FakeWb) -> None:
    wb.add(DRESS)
    client, _ = make_client()
    client.feedbacks(DRESS.imt)
    request = wb.mock.calls[0].request
    assert request.req_kwargs["timeout"] == TIMEOUT
    assert request.headers["User-Agent"].startswith("Mozilla/5.0")


def test_pause_between_requests(wb: FakeWb) -> None:
    wb.add(DRESS)
    now = [100.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    client = WbClient(pause=0.5, sleep=sleep, clock=lambda: now[0])
    client.card(DRESS.nm)
    now[0] += 0.2  # разбор ответа тоже занимает время — его вычитаем из паузы
    client.feedbacks(DRESS.imt)
    assert sleeps == [pytest.approx(0.3)]


def test_rate_limit_waits_as_asked(wb: FakeWb) -> None:
    wb.add(DRESS)
    wb.failures["feedbacks1"] = [(429, {"Retry-After": "7"})]
    client, sleeps = make_client()
    assert client.feedbacks(DRESS.imt)["feedbackCount"] > 0
    assert sleeps == [7.0]
    assert all("feedbacks1" in url for url in wb.urls)


def test_server_errors_are_retried_then_second_host_is_used(wb: FakeWb) -> None:
    wb.add(DRESS)
    wb.failures["feedbacks1"] = [503] * 4
    client, sleeps = make_client()
    assert client.feedbacks(DRESS.imt)["feedbacks"]
    assert [url.split("/")[2] for url in wb.urls] == ["feedbacks1.wb.ru"] * 4 + ["feedbacks2.wb.ru"]
    assert sleeps == [1.0, 2.0, 4.0]


def test_blocked_request_is_not_retried(wb: FakeWb) -> None:
    wb.add(DRESS)
    wb.failures["feedbacks1"] = [403]
    client, sleeps = make_client()
    with pytest.raises(WbBlockedError, match="403"):
        client.feedbacks(DRESS.imt)
    assert len(wb.urls) == 1
    assert sleeps == []


def test_network_failure_gives_clear_error(wb: FakeWb) -> None:
    for host in ("feedbacks1", "feedbacks2"):
        wb.failures[host] = [requests.ConnectionError("нет сети")] * 4
    client, _ = make_client()
    with pytest.raises(WbError, match="Нет ответа от WB"):
        client.feedbacks(DRESS.imt)
    assert len(wb.urls) == 8
