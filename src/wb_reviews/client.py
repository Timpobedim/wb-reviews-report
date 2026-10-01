"""Клиент публичных JSON-адресов Wildberries: карточка товара и отзывы.

Карточка лежит в статическом хранилище `basket-NN.wbbasket.ru`; номер хоста зависит от «тома»
артикула (артикул // 100 000). Таблица томов у WB растёт, поэтому таблица здесь — только подсказка:
если хост не угадан, клиент перебирает остальные и запоминает найденный в файле кэша.
Отзывы отдают `feedbacks1` и `feedbacks2.wb.ru` — если первый не отвечает, спрашиваем второй.

У каждого запроса таймаут, между запросами пауза, 429 и 5xx
повторяются ограниченное число раз, а 403 объясняется, а не превращается в «нет данных».
"""

from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import requests

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)
TIMEOUT = (5, 20)  # соединение, чтение
RETRY_STATUSES = {429, 500, 502, 503, 504}
BASKETS = 40
# (последний том, номер хоста) — по наблюдениям сентября 2026 года; для новых томов — перебор
BASKET_HINTS = (
    (143, 1), (287, 2), (431, 3), (719, 4), (1007, 5), (1061, 6), (1115, 7), (1169, 8), (1313, 9),
    (1601, 10), (1655, 11), (1919, 12), (2045, 13), (2189, 14), (2405, 15), (2621, 16), (2837, 17),
)  # fmt: skip
FEEDBACK_HOSTS = ("https://feedbacks1.wb.ru", "https://feedbacks2.wb.ru")

Sleep = Callable[[float], None]


class WbError(Exception):
    pass


class WbBlockedError(WbError):
    """WB закрыл адрес от запросов из скриптов (403) — повторять бесполезно."""


class ProductNotFoundError(WbError):
    pass


def hinted_basket(vol: int) -> int | None:
    for last, basket in BASKET_HINTS:
        if vol <= last:
            return basket
    return None


def _retry_after(response: requests.Response) -> float | None:
    try:
        return float(response.headers["Retry-After"])
    except (KeyError, ValueError):
        return None


class WbClient:
    def __init__(
        self,
        *,
        cache_file: Path | None = None,
        pause: float = 0.3,
        retries: int = 3,
        backoff: float = 1.0,
        session: requests.Session | None = None,
        sleep: Sleep = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        self.pause = pause
        self.retries = retries
        self.backoff = backoff
        self.cache_file = cache_file
        self.requests_made = 0
        self._sleep = sleep
        self._clock = clock
        self._last = -math.inf
        self._baskets = self._load_baskets()

    # --- кэш хостов --------------------------------------------------------------------------------

    def _load_baskets(self) -> dict[int, int]:
        if self.cache_file is None or not self.cache_file.exists():
            return {}
        return {int(vol): int(basket) for vol, basket in json.loads(self.cache_file.read_text("utf-8")).items()}

    def _remember(self, vol: int, basket: int) -> None:
        if self._baskets.get(vol) == basket:
            return
        self._baskets[vol] = basket
        if self.cache_file is not None:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.cache_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(self._baskets, indent=1), encoding="utf-8")
            os.replace(temporary, self.cache_file)

    # --- запросы ----------------------------------------------------------------------------------

    def _get(self, url: str) -> requests.Response:
        for attempt in range(self.retries + 1):
            wait = self._last + self.pause - self._clock()
            if wait > 0:
                self._sleep(wait)  # вежливая пауза: не чаще одного запроса в `pause` секунд
            self._last = self._clock()
            self.requests_made += 1
            try:
                response = self.session.get(url, timeout=TIMEOUT)
            except requests.RequestException as exc:
                if attempt == self.retries:
                    raise WbError(f"Нет ответа от WB: {url} — {exc}") from exc
                self._sleep(self.backoff * 2**attempt)
                continue
            if response.status_code in RETRY_STATUSES and attempt < self.retries:
                self._sleep(_retry_after(response) or self.backoff * 2**attempt)
                continue
            return response
        raise AssertionError("недостижимо")  # pragma: no cover

    def _json(self, url: str) -> Any:
        """JSON ответа; None — если адреса нет (404)."""
        response = self._get(url)
        if response.status_code == 403:
            raise WbBlockedError(f"WB отклонил запрос (403): {url} — адрес закрыт от запросов из скриптов")
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise WbError(f"WB ответил {response.status_code}: {url}")
        try:
            return response.json()
        except ValueError as exc:
            raise WbError(f"WB вернул не JSON: {url}") from exc

    def _candidates(self, vol: int) -> list[int]:
        first = [basket for basket in (self._baskets.get(vol), hinted_basket(vol)) if basket]
        return list(dict.fromkeys([*first, *range(1, BASKETS + 1)]))

    def card(self, nm: int) -> dict[str, Any]:
        """Карточка товара из статического хранилища: imt_id, название, предмет, артикул продавца."""
        vol, part = nm // 100_000, nm // 1_000
        for basket in self._candidates(vol):
            data = self._json(f"https://basket-{basket:02d}.wbbasket.ru/vol{vol}/part{part}/{nm}/info/ru/card.json")
            if data is not None:
                self._remember(vol, basket)
                return dict(data)
        raise ProductNotFoundError(f"Товар {nm} не найден ни на одном хосте basket-01…basket-{BASKETS:02d}")

    def feedbacks(self, imt: int) -> dict[str, Any]:
        """Отзывы карточки: последние до 1000 штук и распределение оценок за всё время."""
        last_error: WbError | None = None
        for host in FEEDBACK_HOSTS:
            try:
                data = self._json(f"{host}/feedbacks/v1/{imt}")
            except WbBlockedError:
                raise
            except WbError as exc:
                last_error = exc
                continue
            if data is not None:
                return dict(data)
        raise last_error or ProductNotFoundError(f"У карточки {imt} нет отзывов на WB")
