"""От списка артикулов к данным для отчёта: карточки, отзывы, кэш на диске и ошибки по каждому товару."""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from functools import partial
from pathlib import Path
from typing import Any, Protocol

from wb_reviews.client import WbBlockedError, WbError
from wb_reviews.models import Card, ProductFeedback, slim_card, slim_feedbacks

_PRODUCT_URL = re.compile(r"(?:wildberries\.ru|wb\.ru)/catalog/(\d+)")


class Source(Protocol):
    def card(self, nm: int) -> dict[str, Any]: ...

    def feedbacks(self, imt: int) -> dict[str, Any]: ...


def parse_product(text: str) -> int:
    """Артикул или ссылка на товар WB → артикул."""
    text = text.strip()
    if text.isdigit():
        return int(text)
    match = _PRODUCT_URL.search(text)
    if match is None:
        raise ValueError(f"Не похоже на артикул или ссылку на товар WB: «{text}»")
    return int(match.group(1))


class JsonCache:
    """Ответы WB на диске: упавший запуск не теряет скачанное, повторный — не качает заново.

    Всё, что старше `max_age`, скачивается заново.
    """

    def __init__(self, folder: Path, max_age: timedelta, clock: Callable[[], float] = time.time) -> None:
        self.folder = folder
        self.max_age = max_age
        self._clock = clock

    def _path(self, key: str) -> Path:
        return self.folder / f"{key}.json"

    def load(self, key: str) -> dict[str, Any] | None:
        path = self._path(key)
        try:
            age = self._clock() - path.stat().st_mtime
            if age > self.max_age.total_seconds():
                return None
            return dict(json.loads(path.read_text("utf-8")))
        except (OSError, ValueError, TypeError):
            return None  # файла нет или он испорчен — скачаем заново

    def save(self, key: str, data: dict[str, Any]) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self._path(key)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)


@dataclass
class Collected:
    products: list[ProductFeedback] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)  # (артикул, причина)
    notes: list[str] = field(default_factory=list)


def _cached(cache: JsonCache | None, key: str, download: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    data = cache.load(key) if cache is not None else None
    if data is None:
        data = download()
        if cache is not None:
            cache.save(key, data)
    return data


def _card(source: Source, nm: int) -> dict[str, Any]:
    return slim_card(source.card(nm))


def _feedbacks(source: Source, imt: int) -> dict[str, Any]:
    return slim_feedbacks(source.feedbacks(imt))


def collect(source: Source, nms: Sequence[int], cache: JsonCache | None = None) -> Collected:
    """Скачивает всё по списку. Ошибка по одному товару не останавливает остальные, а 403 — останавливает:
    если WB начал блокировать, следующие запросы только продлят блокировку."""
    result = Collected()
    first_nm: dict[int, int] = {}  # карточка → первый артикул из списка
    for index, nm in enumerate(nms):
        try:
            card = Card.from_json(_cached(cache, f"card-{nm}", partial(_card, source, nm)))
            if card.imt in first_nm:
                result.notes.append(
                    f"{nm}: та же карточка, что и {first_nm[card.imt]}, — отзывы общие, считаю один раз"
                )
                continue
            first_nm[card.imt] = nm
            data = _cached(cache, f"feedbacks-{card.imt}", partial(_feedbacks, source, card.imt))
            result.products.append(ProductFeedback.from_json(card, data))
        except WbBlockedError as exc:
            result.errors.append((str(nm), str(exc)))
            skipped = "не запрашивали: WB блокирует запросы, повторите позже"
            result.errors.extend((str(rest), skipped) for rest in nms[index + 1 :])
            break
        except WbError as exc:
            result.errors.append((str(nm), str(exc)))
        except (KeyError, TypeError, ValueError) as exc:
            result.errors.append((str(nm), f"WB изменил формат ответа: {exc!r}"))
    return result
