"""Синтетические товары для демонстрации и тестов.

Формат — как у настоящих ответов WB, с теми же полями, включая имя и идентификатор покупателя: так тесты
проверяют, что они не попадают ни в кэш, ни в отчёт. Ни одного реального отзыва или покупателя.
"""

from __future__ import annotations

import random
import string
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from wb_reviews.client import ProductNotFoundError

NOW = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)  # 12:00 по Москве
SIZE = "Размер и посадка"
VARIANTS = (0, 50, 90)  # другие цвета той же карточки: свои артикулы, но отзывы общие
ANSWERS = {
    "complaint": (
        "Здравствуйте! Жаль, что товар не подошёл. Оформите возврат в личном кабинете, а мы проверим партию.",
        "Здравствуйте! Спасибо, что сообщили, — передали замечание на производство.",
    ),
    "praise": ("Спасибо за отзыв! Рады, что понравилось.", "Благодарим за покупку и тёплые слова!"),
}


@dataclass(frozen=True)
class Scenario:
    nm: int
    imt: int
    name: str
    subject: str
    root: str
    vendor_code: str
    days: int  # за сколько дней есть отзывы
    per_day: float
    hidden: int  # столько старых отзывов WB учитывает в рейтинге, но уже не отдаёт
    negative_before: float  # доля 1–2★ старше 30 дней
    negative_recent: float  # доля 1–2★ за последние 30 дней
    complaints: dict[str, tuple[str, ...]]  # тема → тексты жалоб
    dominant: tuple[str | None, str | None]  # тема, которая преобладает в жалобах: раньше, сейчас
    praise: tuple[str, ...]
    praise_tags: tuple[str, ...]
    answer_rate: float  # доля ответов на 4–5★
    answer_rate_complaints: float  # доля ответов на 1–3★
    answer_hours: float
    fit_marked: float  # доля покупателей, отмечающих посадку; 0 — у товара нет размера
    seed: int


SCENARIOS = (
    Scenario(
        nm=101000001, imt=90000001, name="Платье миди из вискозы с поясом", subject="Платья", root="Женщинам",
        vendor_code="DRS-2041", days=200, per_day=5, hidden=400, negative_before=0.06, negative_recent=0.22,
        complaints={
            SIZE: ("Сильно маломерит, пришлось вернуть", "Размер не соответствует сетке, узко в плечах",
                   "Маломерит на размер, в груди не застегнуть", "Не подошёл размер, хотя всегда ношу этот"),
            "Качество и брак": ("После первой стирки катышки", "Разошёлся шов на поясе", "Ткань тонкая, торчат нитки"),
            "Не как на фото или в описании": ("Цвет не как на фото, заметно темнее",
                                              "Ткань не соответствует описанию, это не вискоза"),
        },
        dominant=(None, SIZE),  # новая партия села в размер
        praise=("Отличное платье, сидит идеально", "Ткань приятная, цвет как на фото", "Хорошее качество за свою цену",
                "Очень красивое, взяла второе в другом цвете"),
        praise_tags=("хорошо сидит", "качество", "цвет как на фото"),
        answer_rate=0.35, answer_rate_complaints=0.25, answer_hours=30, fit_marked=0.3, seed=1,
    ),
    Scenario(
        nm=101000002, imt=90000002, name="Кроссовки беговые с амортизацией", subject="Кроссовки", root="Обувь",
        vendor_code="RUN-77", days=180, per_day=3.5, hidden=0, negative_before=0.05, negative_recent=0.05,
        complaints={
            "Качество и брак": ("Через месяц отклеилась подошва", "На носке появилась дырка через две недели"),
            "Доставка и упаковка": ("Коробка пришла помятая", "Доставка заняла две недели"),
            SIZE: ("Большемерят на полразмера", "Узкая колодка, жмёт в пальцах"),
        },
        dominant=(None, None),
        praise=("Лёгкие и удобные, бегаю каждый день", "Отличная амортизация", "Размер в размер, рекомендую"),
        praise_tags=("удобные", "качество", "соответствует размеру"),
        answer_rate=0.9, answer_rate_complaints=1.0, answer_hours=5, fit_marked=0.35, seed=2,
    ),
    Scenario(
        nm=101000003, imt=90000003, name="Термокружка 450 мл с крышкой-поилкой", subject="Термокружки", root="Дом",
        vendor_code="MUG-450", days=240, per_day=3.4, hidden=0, negative_before=0.24, negative_recent=0.07,
        complaints={
            "Качество и брак": ("Протекает крышка, всё в сумке мокрое", "Не держит тепло, через час чай холодный",
                                "Треснула крышка через неделю"),
            "Запах": ("Сильный запах пластика, не выветривается",),
            "Не как на фото или в описании": ("Объём меньше, чем в описании, — не соответствует описанию",),
        },
        dominant=("Качество и брак", None),  # продавец сменил крышку — жалоб на протечки стало меньше
        praise=("Держит тепло весь день", "Удобная поилка, не проливается", "Отличная кружка, беру с собой в поездки"),
        praise_tags=("держит тепло", "качество"),
        answer_rate=0.5, answer_rate_complaints=0.8, answer_hours=14, fit_marked=0, seed=3,
    ),
)  # fmt: skip


def _id(rng: random.Random, length: int = 20) -> str:
    return "".join(rng.choices(string.ascii_letters + string.digits, k=length))


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _complaint(s: Scenario, rng: random.Random, dominant: str | None) -> tuple[str, str, list[str], str]:
    """Текст, недостатки, теги и отметка о посадке для отзыва на 1–3★."""
    theme = dominant if dominant is not None and rng.random() < 0.7 else rng.choice(list(s.complaints))
    text = rng.choice(s.complaints[theme])
    cons = rng.choice(s.complaints[theme]) if rng.random() < 0.3 else ""
    fit = ""
    if theme == SIZE:
        bigger = "большемер" in text.lower()
        tags = ["большемерит" if bigger else "маломерит", "не по размеру"][: rng.randint(0, 2)]
        if s.fit_marked and rng.random() < 0.8:
            fit = "bigger" if bigger else "smaller"
    elif theme == "Качество и брак":
        tags = ["плохое качество"][: rng.randint(0, 1)]
    elif theme == "Не как на фото или в описании":
        tags = ["не как на фото"][: rng.randint(0, 1)]
    else:
        tags = []
    if theme != SIZE and s.fit_marked and rng.random() < s.fit_marked:
        fit = "ok"
    return text, cons, tags, fit


def _review(
    s: Scenario, rng: random.Random, moment: datetime, rating: int, dominant: str | None, now: datetime
) -> dict[str, Any]:
    if rating <= 3:
        text, cons, tags, fit = _complaint(s, rng, dominant)
        pros = ""
    else:
        text, cons = rng.choice(s.praise), ""
        tags = rng.sample(s.praise_tags, k=rng.randint(0, 2))
        pros = rng.choice(("Качество", "Цена", "Внешний вид", "Удобство"))
        fit = rng.choices(("ok", "smaller", "bigger"), weights=(94, 3, 3))[0] if rng.random() < s.fit_marked else ""
    answer = None
    if rng.random() < (s.answer_rate_complaints if rating <= 3 else s.answer_rate):
        answered = moment + timedelta(hours=s.answer_hours * rng.uniform(0.3, 1.7))
        if answered < now:
            answer = {
                "text": rng.choice(ANSWERS["complaint" if rating <= 3 else "praise"]),
                "state": "wbRu", "editable": False, "createDate": _iso(answered), "lastUpdate": _iso(answered),
                "supplierId": 900_000 + s.seed, "employeeId": 0, "metadata": None, "rejectReason": None,
            }  # fmt: skip
    excluded = rng.random() < 0.01
    return {
        "id": _id(rng),
        "globalUserId": _id(rng, 32),
        "wbUserId": rng.randint(10_000_000, 99_999_999),
        "wbUserDetails": {"name": "Покупатель", "country": "ru", "hasPhoto": False},
        "nmId": s.nm + rng.choice(VARIANTS),
        "text": text, "pros": pros, "cons": cons, "productValuation": rating,
        "createdDate": _iso(moment), "updatedDate": _iso(moment), "answer": answer, "bables": tags or None,
        "matchingSize": fit, "matchingPhoto": "", "matchingDescription": "",
        "excludedFromRating": {"isExcluded": excluded, "reasons": ["Отзыв не о товаре"] if excluded else []},
        "size": "", "color": "", "statusId": 16, "rank": 0, "reasons": [], "video": None,
        "votes": {"pluses": rng.randint(0, 6), "minuses": rng.randint(0, 2)},
    }  # fmt: skip


def reviews_json(s: Scenario, now: datetime = NOW) -> list[dict[str, Any]]:
    """Отзывы, новые сверху. Доля 1–2★ в каждом периоде — ровно заданная, а не «примерно»."""
    rng = random.Random(s.seed)
    count = round(s.days * s.per_day)
    moments = sorted((now - timedelta(seconds=rng.uniform(600, s.days * 86_400)) for _ in range(count)), reverse=True)
    border = now - timedelta(days=30)
    items = []
    for period, share, dominant in (
        ([m for m in moments if m >= border], s.negative_recent, s.dominant[1]),
        ([m for m in moments if m < border], s.negative_before, s.dominant[0]),
    ):
        order = list(range(len(period)))
        rng.shuffle(order)
        negative = set(order[: round(share * len(period))])
        neutral = set(order[len(negative) : len(negative) + round(0.08 * len(period))])
        for index, moment in enumerate(period):
            if index in negative:
                rating = rng.choice((1, 1, 2))
            elif index in neutral:
                rating = 3
            else:
                rating = rng.choice((4, 5, 5, 5, 5))
            items.append(_review(s, rng, moment, rating, dominant, now))
    return items


def feedbacks_json(s: Scenario, now: datetime = NOW) -> dict[str, Any]:
    items = reviews_json(s, now)
    distribution = Counter(int(item["productValuation"]) for item in items)
    rng = random.Random(-s.seed)
    for _ in range(s.hidden):  # старые отзывы: в рейтинге учтены, но WB их уже не отдаёт
        distribution[rng.choices((1, 2, 3, 4, 5), weights=(3, 2, 8, 17, 70))[0]] += 1
    total = sum(distribution.values())
    points = sum(stars * count for stars, count in distribution.items())
    return {
        "feedbackCount": total,
        "feedbackCountWithText": total,
        "valuation": f"{points / total:.1f}",
        "valuationSum": points,
        "valuationDistribution": {str(stars): distribution[stars] for stars in range(1, 6)},
        "valuationDistributionPercent": {str(stars): round(100 * distribution[stars] / total) for stars in range(1, 6)},
        "feedbacks": items,
    }


def card_json(s: Scenario) -> dict[str, Any]:
    return {
        "imt_id": s.imt, "nm_id": s.nm, "imt_name": s.name, "subj_name": s.subject, "subj_root_name": s.root,
        "vendor_code": s.vendor_code, "description": f"{s.name}. Демонстрационная карточка.", "options": [],
    }  # fmt: skip


class DemoSource:
    """Источник для `wb-reviews demo`: ответы в формате WB, но без сети."""

    def __init__(self, now: datetime = NOW) -> None:
        self._cards = {s.nm: card_json(s) for s in SCENARIOS}
        self._feedbacks = {s.imt: feedbacks_json(s, now) for s in SCENARIOS}

    def card(self, nm: int) -> dict[str, Any]:
        if nm not in self._cards:
            raise ProductNotFoundError(f"В демо нет товара {nm}")
        return self._cards[nm]

    def feedbacks(self, imt: int) -> dict[str, Any]:
        return self._feedbacks[imt]
