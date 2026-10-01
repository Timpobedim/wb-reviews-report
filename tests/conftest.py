from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

import pytest
import requests
import responses

from wb_reviews import demo
from wb_reviews.client import hinted_basket

BASKET = re.compile(r"https://basket-(\d{2})\.wbbasket\.ru/vol(\d+)/part(\d+)/(\d+)/info/ru/card\.json")
FEEDBACKS = re.compile(r"https://(feedbacks[12])\.wb\.ru/feedbacks/v1/(\d+)")
Failure = int | tuple[int, dict[str, str]] | Exception
Reply = tuple[int, dict[str, str], str] | Exception


class FakeWb:
    """Публичные адреса WB в памяти: карточки лежат на «своих» basket-хостах, отзывы — на feedbacks1/2."""

    def __init__(self, mock: responses.RequestsMock) -> None:
        self.mock = mock
        self.cards: dict[int, tuple[int, dict[str, Any]]] = {}  # артикул → (номер basket-хоста, card.json)
        self.feedbacks: dict[int, dict[str, Any]] = {}
        self.failures: dict[str, list[Failure]] = {"feedbacks1": [], "feedbacks2": []}
        mock.add_callback(responses.GET, BASKET, callback=self._card)
        mock.add_callback(responses.GET, FEEDBACKS, callback=self._feedbacks)

    def add(self, scenario: demo.Scenario, basket: int | None = None) -> None:
        host = basket or hinted_basket(scenario.nm // 100_000) or 1
        self.cards[scenario.nm] = (host, demo.card_json(scenario))
        self.feedbacks[scenario.imt] = demo.feedbacks_json(scenario)

    @property
    def urls(self) -> list[str]:
        return [str(call.request.url) for call in self.mock.calls]

    def _card(self, request: requests.PreparedRequest) -> Reply:
        match = BASKET.match(str(request.url))
        assert match is not None
        basket, vol, part, nm = (int(group) for group in match.groups())
        found = self.cards.get(nm)
        if found is None or found[0] != basket or (vol, part) != (nm // 100_000, nm // 1_000):
            return 404, {}, "Not Found"
        return 200, {"Content-Type": "application/json"}, json.dumps(found[1], ensure_ascii=False)

    def _feedbacks(self, request: requests.PreparedRequest) -> Reply:
        match = FEEDBACKS.match(str(request.url))
        assert match is not None
        host, imt = match.group(1), int(match.group(2))
        if self.failures[host]:
            failure = self.failures[host].pop(0)
            if isinstance(failure, Exception):
                return failure
            status, headers = failure if isinstance(failure, tuple) else (failure, {})
            return status, headers, ""
        data = self.feedbacks.get(imt)
        if data is None:
            return 404, {}, ""
        return 200, {"Content-Type": "application/json"}, json.dumps(data, ensure_ascii=False)


@pytest.fixture
def wb() -> Iterator[FakeWb]:
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        yield FakeWb(mock)
