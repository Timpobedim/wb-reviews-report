from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from wb_reviews import demo
from wb_reviews.analysis import compile_themes, conclusions, is_negative_tag, monthly, summarize, themes_of
from wb_reviews.models import Card, ProductFeedback, Review, slim_feedbacks

NOW = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)
CARD = Card(nm=1, imt=2, name="Товар", subject="Предмет", vendor_code="A-1")


def review(
    days_ago: float,
    rating: int = 5,
    *,
    text: str = "",
    cons: str = "",
    tags: tuple[str, ...] = (),
    fit: str = "",
    answer_hours: float | None = None,
    excluded: bool = False,
) -> Review:
    created = NOW - timedelta(days=days_ago)
    answered = created + timedelta(hours=answer_hours) if answer_hours is not None else None
    return Review(
        id=f"{days_ago}-{rating}-{text}", nm=1, created=created, rating=rating, text=text, pros="", cons=cons,
        tags=tags, size_fit=fit, answered_at=answered, excluded=excluded,
    )  # fmt: skip


def product(reviews: list[Review], total: int | None = None) -> ProductFeedback:
    count = total if total is not None else len(reviews)
    return ProductFeedback(card=CARD, rating=4.6, total=count, distribution={}, reviews=tuple(reviews))


def period(start_days: float, count: int, negative: int) -> list[Review]:
    """`count` отзывов через каждые полдня начиная со `start_days` дней назад; первые `negative` — на 1★."""
    return [review(start_days + index / 2, 1 if index < negative else 5) for index in range(count)]


@pytest.mark.parametrize(
    ("recent_negative", "before_negative", "trend"),
    [(5, 4, "негатив растёт"), (1, 16, "негатив снижается"), (2, 4, "без изменений")],
)
def test_trend(recent_negative: int, before_negative: int, trend: str) -> None:
    summary = summarize(product(period(1, 20, recent_negative) + period(31, 40, before_negative)), NOW)
    assert summary.trend == trend


def test_trend_needs_enough_reviews() -> None:
    summary = summarize(product(period(1, 5, 3) + period(31, 40, 0)), NOW)
    assert summary.trend == "мало отзывов"
    assert conclusions(summary)[0].startswith("Отзывов мало")


def test_shares_ignore_reviews_excluded_by_wb() -> None:
    reviews = [*period(1, 20, 5), *period(31, 40, 4), *[review(2, 1, excluded=True)] * 3, review(200, 1)]
    summary = summarize(product(reviews), NOW)
    assert summary.negative_recent == pytest.approx(0.25)
    assert summary.negative_before == pytest.approx(0.1)
    assert (summary.recent_count, summary.before_count, summary.analyzed) == (20, 40, 61)


def test_seller_answers() -> None:
    reviews = [
        review(1, 1, answer_hours=2), review(2, 2), review(3, 3, answer_hours=10),  # на жалобы: 2 ответа из 3
        review(4, 5, answer_hours=30), review(5, 5), review(6, 4),
    ]  # fmt: skip
    summary = summarize(product(reviews), NOW)
    assert summary.response_rate == pytest.approx(3 / 6)
    assert summary.negative_response_rate == pytest.approx(2 / 3)
    assert summary.response_hours == pytest.approx(10)


def test_size_fit_shares_and_conclusion() -> None:
    reviews = [review(1, fit="smaller")] * 3 + [review(1, fit="bigger")] + [review(1, fit="ok")] * 6 + [review(1)] * 5
    summary = summarize(product(reviews), NOW)
    assert summary.fit_marks == 10
    assert summary.smaller == pytest.approx(0.3)
    assert summary.bigger == pytest.approx(0.1)
    lines = conclusions(summary)
    assert "Маломерит: так отметили 30% из 10 покупателей, указавших посадку." in lines
    assert "Большемерит: так отметили 10% из 10 покупателей, указавших посадку." in lines


def test_complaint_themes_come_from_text_cons_and_negative_tags() -> None:
    reviews = [
        review(1, 2, tags=("маломерит",)),
        review(1, 1, text="Пришёл не тот цвет"),
        review(1, 3, tags=("ткань не как на фото", "качество")),
        review(1, 2, text="Всё плохо", cons="Порвался шов"),
        review(1, 1, text="Просто не понравилось"),  # текст есть, тема не распознана
        review(1, 1, tags=("качество",)),  # ни текста, ни негативных тегов — причину не узнать
        review(1, 5, text="Маломерит, но взяла на размер больше — отлично"),  # 5★ — не жалоба
    ]
    summary = summarize(product(reviews), NOW)
    assert (summary.complaints, summary.worded, summary.silent, summary.unclassified) == (6, 5, 1, 1)
    assert summary.themes == {
        "Размер и посадка": 1,
        "Не тот товар": 1,
        "Не как на фото или в описании": 1,
        "Качество и брак": 1,
    }
    assert conclusions(summary)[1] == "Чаще всего жалуются: «Размер и посадка» — 1 из 5 жалоб с текстом."


@pytest.mark.parametrize(
    ("text", "tags", "theme"),
    [
        ("Резинка очень тугая", (), "Неудобно носить"),
        ("", ("неудобно носить",), "Неудобно носить"),
        ("После пары стирок растянулись и стали прозрачными", (), "Качество и брак"),
        ("Это не хлопок, а синтетика", (), "Не как на фото или в описании"),
        ("В наборе все разного размера", (), "Размер и посадка"),
    ],
)
def test_clothing_complaints_seen_on_live_wb(text: str, tags: tuple[str, ...], theme: str) -> None:
    """Формулировки жалоб, которые словарь не узнавал на живой карточке (пересказ, не цитаты)."""
    assert themes_of(review(1, 2, text=text, tags=tags)) == {theme}


def test_conclusions_agree_numbers_with_words() -> None:
    summary = summarize(product([review(1, 2, text="Маломерит") for _ in range(21)]), NOW)
    assert "Чаще всего жалуются: «Размер и посадка» — 21 из 21 жалобы с текстом." in conclusions(summary)
    truncated = summarize(product([review(1, 5)], total=94_940), NOW)
    assert "WB отдал не все отзывы (всего 94 940) — в анализе отзывы с 09.09.2026." in conclusions(truncated)


def test_own_theme_dictionary_is_normalized_and_validated() -> None:
    themes = compile_themes({" Швы ": ["  Жёсткий   ШОВ "]})
    assert themes == {"Швы": ("жесткий шов",)}
    assert themes_of(review(1, 2, text="Жесткий шов натирает"), themes) == {"Швы"}
    with pytest.raises(ValueError, match="нет ни одной основы"):
        compile_themes({"Пустая": ["  "]})


def test_complaints_without_text_are_reported_as_such() -> None:
    summary = summarize(product([review(1, 1), review(2, 2, tags=("качество",))]), NOW)
    assert (summary.silent, summary.top_theme) == (2, None)
    assert "Отзывов на 1–3: 2, но все без текста — причины не видны." in conclusions(summary)


@pytest.mark.parametrize(
    ("tag", "negative"),
    [
        ("не по размеру", True),
        ("плохое качество", True),
        ("ткань не как на фото", True),
        ("маломерит", True),
        ("качество", False),
        ("хорошо сидит", False),
        ("соответствует размеру", False),
    ],
)
def test_negative_tags(tag: str, negative: bool) -> None:
    assert is_negative_tag(tag) is negative


@pytest.mark.parametrize("scenario", demo.SCENARIOS, ids=lambda s: s.vendor_code)
def test_demo_texts_fall_into_their_own_themes(scenario: demo.Scenario) -> None:
    for theme, texts in scenario.complaints.items():
        for text in texts:
            assert themes_of(review(1, 2, text=text)) == {theme}, text
    for text in scenario.praise:
        assert themes_of(review(1, 2, text=text)) == set(), text


def test_demo_scenarios_tell_their_stories() -> None:
    source = demo.DemoSource()
    summaries = {
        s.vendor_code: summarize(
            ProductFeedback.from_json(Card.from_json(source.card(s.nm)), slim_feedbacks(source.feedbacks(s.imt))),
            demo.NOW,
        )
        for s in demo.SCENARIOS
    }
    dress, shoes, mug = summaries["DRS-2041"], summaries["RUN-77"], summaries["MUG-450"]
    assert (dress.trend, dress.top_theme, dress.truncated) == ("негатив растёт", "Размер и посадка", True)
    assert any(line.startswith("Маломерит") for line in conclusions(dress))
    assert any(line.startswith("Продавец отвечает только") for line in conclusions(dress))
    assert (shoes.trend, shoes.negative_response_rate) == ("без изменений", 1.0)
    assert any(line.startswith("Продавец отвечает почти на все") for line in conclusions(shoes))
    assert (mug.trend, mug.top_theme, mug.fit_marks) == ("негатив снижается", "Качество и брак", 0)


def test_monthly_marks_months_that_wb_did_not_return() -> None:
    # 10.09.2026: отзывы 1–7 дней назад — сентябрь, 40 дней назад — 1 августа
    reviews = [review(days, 1 if days < 3 else 5) for days in (1, 2, 4, 5, 6, 7)] + [review(40)] * 3
    months = monthly(product(reviews, total=100), NOW, months=3)
    assert [month.label for month in months] == ["июл 2026", "авг 2026", "сен 2026"]
    assert months[0].count is None  # WB отдал отзывы только с августа — про июль ничего не известно
    assert (months[1].count, months[1].negative) == (3, None)  # меньше 5 отзывов — долю не считаем
    assert months[2].count == 6
    assert months[2].negative == pytest.approx(2 / 6)
    assert monthly(product(reviews), NOW, months=3)[0].count == 0  # WB отдал все отзывы — в июле их просто не было
