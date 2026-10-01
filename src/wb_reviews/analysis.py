"""Что происходит с товаром по отзывам: тренд негатива, ответы продавца, посадка, темы жалоб.

Негатив — оценки 1–2: по их доле считается тренд. Жалобы — отзывы на 1–3: в тройках тоже чаще всего
пишут, что не так. Отзывы, которые WB исключил из рейтинга, в расчёт не идут.
"""

from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from wb_reviews.models import Card, ProductFeedback, Review

MSK = timezone(timedelta(hours=3), "МСК")
RECENT = timedelta(days=30)
BEFORE = timedelta(days=90)  # с чем сравниваем: 90 дней перед последними 30
TREND_STEP = 0.05  # на сколько должна измениться доля негатива, чтобы считать это трендом
MIN_REVIEWS = 10  # меньше отзывов в периоде — о тренде не судим
MIN_FIT_MARKS = 10  # меньше отметок о посадке — о размере не судим
MIN_MONTH = 5  # меньше отзывов за месяц — долю негатива за месяц не показываем
MONTHS = ("янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")

# Темы жалоб по умолчанию — для одежды и обуви. Основы слов без морфологии: «маломерит», «маломерка»
# и «маломерят» ловит «маломер». Для другой категории свой словарь передаётся через --themes.
THEMES: dict[str, tuple[str, ...]] = {
    "Размер и посадка": ("маломер", "большемер", "не по размеру", "размер не", "плохо сидит", "не подош", "жмёт",
                         "не соответствует размер", "не соответствуют размер", "разных размер", "разного размер"),
    "Неудобно носить": ("неудоб", "не удоб", "давит", "давят", "впива", "натир", "туг", "груб", "жёстк", "колет",
                        "колюч", "раздраж"),
    "Качество и брак": ("брак", "порвал", "располз", "катыш", "торчат нит", "шов", "швы", "дыр", "слома",
                        "плохое качество", "ужасное качество", "качество ужас", "качество ткани ужас", "некачеств",
                        "треснул", "отклеил", "протека", "не держит", "растян", "вытяг", "ослаб", "повылаз",
                        "прозрачн", "линя", "крася", "красит"),
    "Не как на фото или в описании": ("не как на фото", "не соответствует опис", "не соответствует фото",
                                      "не соответствует заявл", "отличается от", "цвет другой", "обман",
                                      "не хлопок", "не х/б", "синтетик"),
    "Доставка и упаковка": ("упаков", "грязн", "мятый", "помят", "поврежд", "курьер", "пвз", "доставк"),
    "Запах": ("запах", "воня", "пахнет"),
    "Не тот товар": ("не тот", "другой товар", "пересорт", "прислали не", "пришёл не"),
    "Цена": ("дорог", "переплат", "не стоит своих денег", "цена завыш"),
}  # fmt: skip
NO_THEME = "Тема не распознана"
SILENT = "Без текста"
_NEGATIVE_TAG = re.compile(r"(?:^|\s)не\s|плох|маломер|большемер|неудоб|брак")


Themes = dict[str, tuple[str, ...]]


def _normalized(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").split())


def compile_themes(raw: Mapping[str, Sequence[str]]) -> Themes:
    """Словарь тем → нормализованные основы. Пустая тема — ошибка, а не молчаливый пропуск."""
    themes: Themes = {}
    for theme, stems in raw.items():
        normalized = tuple(_normalized(stem) for stem in stems if isinstance(stem, str) and _normalized(stem))
        if not str(theme).strip() or not normalized:
            raise ValueError(f"В теме «{theme}» нет ни одной основы слова")
        themes[str(theme).strip()] = normalized
    if not themes:
        raise ValueError("Словарь тем пуст")
    return themes


DEFAULT_THEMES = compile_themes(THEMES)


def load_themes(path: Path) -> Themes:
    """Свой словарь для другой категории: JSON вида {"Тема": ["основа", ...]}. Заменяет стандартный."""
    try:
        raw = json.loads(path.read_text("utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Не удалось прочитать словарь тем {path}: {exc}") from exc
    if not isinstance(raw, dict) or not all(isinstance(stems, list) for stems in raw.values()):
        raise ValueError(f'Словарь тем {path}: нужен JSON-объект вида {{"Тема": ["основа", ...]}}')
    return compile_themes(raw)


def is_negative_tag(tag: str) -> bool:
    """«не по размеру», «плохое качество», «ткань не как на фото» — да; «качество», «хорошо сидит» — нет."""
    return _NEGATIVE_TAG.search(_normalized(tag)) is not None


def themes_of(review: Review, themes: Themes = DEFAULT_THEMES) -> set[str]:
    """Темы отзыва по тексту, минусам и негативным тегам WB. Отзыв может попасть в несколько тем."""
    tags = " ".join(tag for tag in review.tags if is_negative_tag(tag))
    text = _normalized(" ".join((review.text, review.cons, tags)))
    return {theme for theme, stems in themes.items() if any(stem in text for stem in stems)}


def is_worded(review: Review) -> bool:
    """Есть ли, по чему судить о причине: текст, минусы или негативный тег WB. Две трети оценок — без текста."""
    return bool(review.text.strip() or review.cons.strip() or any(is_negative_tag(tag) for tag in review.tags))


@dataclass(frozen=True)
class Summary:
    card: Card
    rating: float
    total: int
    analyzed: int
    truncated: bool  # WB отдал не все отзывы, а только последние
    broken: int
    since: datetime | None  # самый старый из проанализированных отзывов
    negative_recent: float | None  # доля 1–2★ за последние 30 дней
    negative_before: float | None  # доля 1–2★ за 90 дней до них
    recent_count: int
    before_count: int
    response_rate: float | None
    negative_response_rate: float | None  # ответы на 1–3★
    response_hours: float | None  # медиана времени ответа продавца
    fit_marks: int  # сколько покупателей отметили посадку
    smaller: float | None  # доля «маломерит» среди отметивших посадку
    bigger: float | None
    complaints: int  # отзывов на 1–3★
    silent: int  # из них без текста и негативных тегов — о причине судить не по чему
    unclassified: int  # с текстом, но ни одна тема не нашлась
    themes: dict[str, int]  # тема → сколько жалоб её упоминают, по убыванию

    @property
    def worded(self) -> int:
        """Жалобы с текстом — от них считаются доли тем."""
        return self.complaints - self.silent

    @property
    def trend(self) -> str:
        if (
            self.negative_recent is None
            or self.negative_before is None
            or min(self.recent_count, self.before_count) < MIN_REVIEWS
        ):
            return "мало отзывов"
        change = self.negative_recent - self.negative_before
        if change >= TREND_STEP:
            return "негатив растёт"
        if change <= -TREND_STEP:
            return "негатив снижается"
        return "без изменений"

    @property
    def top_theme(self) -> str | None:
        return next(iter(self.themes), None)


def _share(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def summarize(feedback: ProductFeedback, now: datetime, themes: Themes = DEFAULT_THEMES) -> Summary:
    reviews = [review for review in feedback.reviews if not review.excluded]
    recent = [review for review in reviews if review.created >= now - RECENT]
    before = [review for review in reviews if now - RECENT - BEFORE <= review.created < now - RECENT]
    complaints = [review for review in reviews if review.rating <= 3]
    hours = [
        (review.answered_at - review.created).total_seconds() / 3600
        for review in reviews
        if review.answered_at is not None
    ]
    fits = Counter(review.size_fit for review in reviews if review.size_fit)
    fit_marks = sum(fits.values())
    found = [themes_of(review, themes) for review in complaints if is_worded(review)]
    mentions = Counter(theme for review_themes in found for theme in review_themes)
    return Summary(
        card=feedback.card,
        rating=feedback.rating,
        total=feedback.total,
        analyzed=len(reviews),
        truncated=len(feedback.reviews) + feedback.broken < feedback.total,
        broken=feedback.broken,
        since=min((review.created for review in reviews), default=None),
        negative_recent=_share(sum(review.rating <= 2 for review in recent), len(recent)),
        negative_before=_share(sum(review.rating <= 2 for review in before), len(before)),
        recent_count=len(recent),
        before_count=len(before),
        response_rate=_share(len(hours), len(reviews)),
        negative_response_rate=_share(sum(review.answered for review in complaints), len(complaints)),
        response_hours=statistics.median(hours) if hours else None,
        fit_marks=fit_marks,
        smaller=_share(fits["smaller"], fit_marks),
        bigger=_share(fits["bigger"], fit_marks),
        complaints=len(complaints),
        silent=len(complaints) - len(found),
        unclassified=sum(not review_themes for review_themes in found),
        themes=dict(mentions.most_common()),
    )


def _pct(value: float) -> str:
    return f"{value:.0%}"


def _num(value: int) -> str:
    return f"{value:,}".replace(",", " ")  # 94 940


def _of(count: int, one: str, many: str) -> str:
    """После «из»: «из 21 жалобы», но «из 5 жалоб»."""
    return one if count % 10 == 1 and count % 100 != 11 else many


def conclusions(summary: Summary) -> list[str]:
    """Выводы человеческим языком — то, что продавец прочитает в первую очередь."""
    s = summary
    lines = []
    if s.negative_recent is not None and s.negative_before is not None and s.trend != "мало отзывов":
        if s.trend == "без изменений":
            lines.append(f"Доля оценок 1–2 стабильна: {_pct(s.negative_recent)} за 30 дней.")
        else:
            verb = "Негатив растёт" if s.trend == "негатив растёт" else "Негатив снижается"
            lines.append(
                f"{verb}: {_pct(s.negative_recent)} оценок 1–2 за 30 дней "
                f"против {_pct(s.negative_before)} за 90 дней до этого."
            )
    else:
        lines.append(
            f"Отзывов мало, чтобы судить о тренде: {s.recent_count} за 30 дней и {s.before_count} за 90 дней до них."
        )
    if s.top_theme is not None:
        complaints = _of(s.worded, "жалобы", "жалоб")
        lines.append(
            f"Чаще всего жалуются: «{s.top_theme}» — {s.themes[s.top_theme]} из {s.worded} {complaints} с текстом."
        )
    elif s.complaints and not s.worded:
        lines.append(f"Отзывов на 1–3: {s.complaints}, но все без текста — причины не видны.")
    if s.fit_marks >= MIN_FIT_MARKS:
        for share, label in ((s.smaller, "Маломерит"), (s.bigger, "Большемерит")):
            if share is not None and share >= 0.1:
                buyers = _of(s.fit_marks, "покупателя, указавшего", "покупателей, указавших")
                lines.append(f"{label}: так отметили {_pct(share)} из {s.fit_marks} {buyers} посадку.")
    if s.negative_response_rate is not None and s.complaints >= 5:
        if s.negative_response_rate < 0.5:
            lines.append(f"Продавец отвечает только на {_pct(s.negative_response_rate)} отзывов на 1–3.")
        elif s.negative_response_rate >= 0.9 and s.response_hours is not None:
            lines.append(
                f"Продавец отвечает почти на все отзывы на 1–3 ({_pct(s.negative_response_rate)}), "
                f"медиана ответа — {s.response_hours:.0f} ч."
            )
    if s.truncated and s.since is not None:
        since = s.since.astimezone(MSK)
        lines.append(f"WB отдал не все отзывы (всего {_num(s.total)}) — в анализе отзывы с {since:%d.%m.%Y}.")
    if s.broken:
        lines.append(f"Пропущено отзывов без даты или оценки: {s.broken}.")
    return lines


@dataclass(frozen=True)
class Month:
    label: str  # «сен 2026»
    count: int | None  # None — WB не отдал отзывы за этот месяц (окно в 1000 отзывов)
    negative: float | None  # доля 1–2★; None — отзывов меньше MIN_MONTH


def monthly(feedback: ProductFeedback, now: datetime, months: int = 12) -> list[Month]:
    """Негатив по календарным месяцам (московское время), последние `months` месяцев."""
    local = now.astimezone(MSK)
    keys = [
        ((local.year * 12 + local.month - 1 - back) // 12, (local.month - 1 - back) % 12 + 1) for back in range(months)
    ]
    keys.reverse()
    by_month: dict[tuple[int, int], list[Review]] = {key: [] for key in keys}
    reviews = [review for review in feedback.reviews if not review.excluded]
    for review in reviews:
        moment = review.created.astimezone(MSK)
        by_month.get((moment.year, moment.month), []).append(review)
    first = min((review.created for review in reviews), default=None)
    first_key = (first.astimezone(MSK).year, first.astimezone(MSK).month) if first else None
    truncated = len(feedback.reviews) + feedback.broken < feedback.total
    result = []
    for key in keys:
        label = f"{MONTHS[key[1] - 1]} {key[0]}"
        if truncated and first_key is not None and key < first_key:
            result.append(Month(label, None, None))
            continue
        items = by_month[key]
        negative = sum(review.rating <= 2 for review in items) / len(items) if len(items) >= MIN_MONTH else None
        result.append(Month(label, len(items), negative))
    return result
