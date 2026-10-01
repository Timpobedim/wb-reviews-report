"""Карточка и отзывы в удобном виде.

Имена и идентификаторы покупателей для анализа не нужны, поэтому `slim_feedbacks` оставляет от ответа WB
только нужные поля — ещё до того, как он попадёт в кэш на диске.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

CARD_FIELDS = ("nm_id", "imt_id", "imt_name", "subj_name", "vendor_code")
REVIEW_FIELDS = (
    "id", "nmId", "createdDate", "productValuation", "text", "pros", "cons", "bables", "matchingSize",
    "excludedFromRating",
)  # fmt: skip
_CONTACTS = re.compile(
    r"[\w.+-]+@[\w-]+\.[\w.-]+"  # почта
    r"|(?:\+7|\b8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}\b"  # телефон
    r"|https?://\S+|www\.\S+"  # ссылки
)


def mask_contacts(text: str) -> str:
    """Почта, телефоны и ссылки из текста отзыва не попадают в отчёт."""
    return _CONTACTS.sub("[скрыто]", text)


def slim_card(data: dict[str, Any]) -> dict[str, Any]:
    return {field: data.get(field) for field in CARD_FIELDS}


def slim_feedbacks(data: dict[str, Any]) -> dict[str, Any]:
    """Только то, что нужно для анализа: без имён, фото профиля и идентификаторов покупателей."""
    reviews = []
    for item in data.get("feedbacks") or []:
        slim = {field: item.get(field) for field in REVIEW_FIELDS}
        answer = item.get("answer") or {}
        slim["answer"] = {"text": answer.get("text"), "createDate": answer.get("createDate")} if answer else None
        reviews.append(slim)
    return {
        "feedbackCount": data.get("feedbackCount"),
        "valuation": data.get("valuation"),
        "valuationDistribution": data.get("valuationDistribution"),
        "feedbacks": reviews,
    }


def parse_time(value: object) -> datetime | None:
    """«2026-09-09T06:18:03Z» → datetime в UTC; пустое и «0001-01-01…» (так WB пишет «нет даты») → None."""
    text = str(value or "")
    if not text or text.startswith("0001-"):
        return None
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


@dataclass(frozen=True)
class Card:
    nm: int  # артикул WB
    imt: int  # карточка, объединяющая цвета и размеры: отзывы общие для всей карточки
    name: str
    subject: str
    vendor_code: str

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Card:
        return cls(
            nm=int(data["nm_id"]),
            imt=int(data["imt_id"]),
            name=str(data.get("imt_name") or ""),
            subject=str(data.get("subj_name") or ""),
            vendor_code=str(data.get("vendor_code") or ""),
        )


@dataclass(frozen=True)
class Review:
    id: str
    nm: int  # вариант (цвет), к которому оставлен отзыв
    created: datetime
    rating: int
    text: str
    pros: str
    cons: str
    tags: tuple[str, ...]  # теги WB: «качество», «не по размеру», «маломерит»…
    size_fit: str  # "ok", "smaller", "bigger" или "" — покупатель не отметил
    answered_at: datetime | None
    excluded: bool  # WB исключил отзыв из рейтинга (например, «не о товаре»)

    @property
    def answered(self) -> bool:
        return self.answered_at is not None

    @classmethod
    def from_json(cls, item: dict[str, Any]) -> Review:
        created = parse_time(item.get("createdDate"))
        if created is None:
            raise ValueError(f"У отзыва {item.get('id')} нет даты")
        answer = item.get("answer") or {}
        return cls(
            id=str(item["id"]),
            nm=int(item.get("nmId") or 0),
            created=created,
            rating=int(item["productValuation"]),
            text=str(item.get("text") or ""),
            pros=str(item.get("pros") or ""),
            cons=str(item.get("cons") or ""),
            tags=tuple(str(tag) for tag in item.get("bables") or []),
            size_fit=str(item.get("matchingSize") or ""),
            answered_at=parse_time(answer.get("createDate")) if answer.get("text") else None,
            excluded=bool((item.get("excludedFromRating") or {}).get("isExcluded")),
        )


@dataclass(frozen=True)
class ProductFeedback:
    card: Card
    rating: float  # рейтинг карточки по данным WB
    total: int  # отзывов за всё время
    distribution: dict[int, int]  # оценка → сколько отзывов за всё время
    reviews: tuple[Review, ...]  # последние отзывы: WB отдаёт не больше 1000
    broken: int = 0  # отзывы без даты или оценки — пропущены, а не роняют весь товар

    @classmethod
    def from_json(cls, card: Card, data: dict[str, Any]) -> ProductFeedback:
        reviews, broken = [], 0
        for item in data.get("feedbacks") or []:
            try:
                reviews.append(Review.from_json(item))
            except (KeyError, TypeError, ValueError):
                broken += 1
        return cls(
            card=card,
            rating=float(data.get("valuation") or 0),
            total=int(data.get("feedbackCount") or 0),
            distribution={int(k): int(v) for k, v in (data.get("valuationDistribution") or {}).items()},
            reviews=tuple(reviews),
            broken=broken,
        )
