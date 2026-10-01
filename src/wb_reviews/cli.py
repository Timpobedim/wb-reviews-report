"""Командная строка.

wb-reviews report 13221580 https://www.wildberries.ru/catalog/146972802/detail.aspx -o отзывы.xlsx
wb-reviews report -f артикулы.txt
wb-reviews demo -o отчёт-демо.xlsx
"""

from __future__ import annotations

import argparse
import io
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from wb_reviews import demo
from wb_reviews.analysis import DEFAULT_THEMES, MSK, Summary, Themes, conclusions, load_themes, summarize
from wb_reviews.client import WbClient
from wb_reviews.pipeline import Collected, JsonCache, collect, parse_product
from wb_reviews.report import PRODUCTS, ReportError, plural, write_report


def _moment(text: str) -> datetime:
    moment = datetime.fromisoformat(text)
    return moment if moment.tzinfo else moment.replace(tzinfo=MSK)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wb-reviews", description="Отзывы конкурентов на Wildberries → отчёт в Excel")
    commands = parser.add_subparsers(dest="command", required=True)
    report = commands.add_parser("report", help="отчёт по артикулам или ссылкам на товары")
    report.add_argument("products", nargs="*", help="артикулы или ссылки на товары WB")
    report.add_argument("-f", "--file", type=Path, help="файл со списком: артикул или ссылка в строке, # — комментарий")
    report.add_argument("-o", "--out", type=Path, default=Path("отзывы.xlsx"), help="куда сохранить отчёт")
    report.add_argument("--cache", type=Path, default=Path(".cache"), help="папка для скачанных ответов WB")
    report.add_argument("--max-age", type=float, default=12, help="часов: более свежий кэш не скачивается заново")
    report.add_argument("--pause", type=float, default=0.3, help="секунд между запросами к WB")
    report.add_argument("--now", type=_moment, help="дата отчёта в ISO-формате — для воспроизводимых отчётов")
    report.add_argument(
        "--themes", type=Path, help="свой словарь тем жалоб (JSON) — стандартный рассчитан на одежду и обувь"
    )
    sample = commands.add_parser("demo", help="отчёт на синтетических данных, без обращения к WB")
    sample.add_argument("-o", "--out", type=Path, default=Path("отчёт-демо.xlsx"), help="куда сохранить отчёт")
    return parser


def _products(args: argparse.Namespace) -> list[int]:
    texts = list(args.products)
    if args.file is not None:
        for line in args.file.read_text("utf-8-sig").splitlines():
            if not line.lstrip().startswith("#"):
                texts.extend(line.split())
    return list(dict.fromkeys(parse_product(text) for text in texts))


def _print(summary: Summary) -> None:
    rating = f"{summary.rating:.1f}".replace(".", ",")
    total = f"{summary.total:,}".replace(",", " ")
    print(f"\n{summary.card.nm} · {summary.card.name} — рейтинг {rating}, отзывов: {total}")
    for line in conclusions(summary):
        print(f"  • {line}")


def _finish(collected: Collected, out: Path, now: datetime, themes: Themes) -> int:
    for note in collected.notes:
        print(note)
    for product, reason in collected.errors:
        print(f"{product}: {reason}", file=sys.stderr)
    if not collected.products:
        print("Отчёт не построен: ни по одному товару не удалось получить отзывы.", file=sys.stderr)
        return 2
    items = [(feedback, summarize(feedback, now, themes)) for feedback in collected.products]
    for _, summary in items:
        _print(summary)
    try:
        write_report(out, items, collected.errors, now, themes)
    except ReportError as exc:
        print(exc, file=sys.stderr)
        return 1
    failed = f", не удалось: {len(collected.errors)}" if collected.errors else ""
    print(f"\nОтчёт: {out} — {len(items)} {plural(len(items), PRODUCTS)}{failed}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="replace")  # консоль Windows без UTF-8 не должна ронять отчёт
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "demo":
        collected = collect(demo.DemoSource(), [s.nm for s in demo.SCENARIOS])
        return _finish(collected, args.out, demo.NOW, DEFAULT_THEMES)
    try:
        products = _products(args)
        themes = load_themes(args.themes) if args.themes else DEFAULT_THEMES
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if not products:
        parser.error("укажите артикулы или ссылки на товары либо файл со списком (-f)")
    client = WbClient(cache_file=args.cache / "baskets.json", pause=args.pause)
    collected = collect(client, products, JsonCache(args.cache, timedelta(hours=args.max_age)))
    print(f"Запросов к WB: {client.requests_made}")
    return _finish(collected, args.out, args.now or datetime.now(UTC), themes)


if __name__ == "__main__":
    sys.exit(main())
