"""
Запуск: python run_analysis.py

Делает три вещи:
  1. Чистит слой продаж (строки -> заказы) и фиксирует допущения.
  2. Восстанавливает маркетинговый календарь из самих продаж.
  3. Сохраняет выгрузки и графики в output/.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src import charts, detect_events, prep

ROOT = Path(__file__).parent
RAW = ROOT / "data" / "raw" / "base.xlsx"
OUT = ROOT / "output"


def main() -> None:
    OUT.mkdir(exist_ok=True)
    layers = prep.prepare(RAW)
    lines, orders, daily = layers["lines"], layers["orders"], layers["daily"]

    daily_enriched, calendar, launches = detect_events.build_calendar(daily, lines)
    flash = detect_events.detect_flash_sales(lines)

    lines.to_csv(OUT / "lines_clean.csv", index=False)
    orders.to_csv(OUT / "orders.csv", index=False)
    daily_enriched.to_csv(OUT / "daily_metrics.csv", index=False)
    calendar.to_csv(OUT / "campaign_calendar.csv", index=False)
    launches.to_csv(OUT / "product_launches.csv", index=False)
    flash.to_csv(OUT / "flash_sale_windows.csv", index=False)
    layers["list_price"].to_csv(OUT / "list_prices.csv", index=False)

    charts.make_all(daily_enriched, calendar, lines, OUT / "charts")

    # --- краткая сводка в консоль (цифры для слайдов) ---
    print("=" * 78)
    print("СЛОЙ ПРОДАЖ")
    print("=" * 78)
    print(f"строк:              {len(lines)}")
    print(f"заказов:            {len(orders)}")
    print(f"покупателей:        {orders['student_id'].nunique()}")
    print(f"курсов:             {lines['course'].nunique()}")
    print(f"период:             {lines['ts'].min():%d.%m.%Y} - {lines['ts'].max():%d.%m.%Y}")
    print(f"выручка (по строкам): {lines['amount'].sum():,.0f} руб.".replace(",", " "))
    repeat = orders["is_repeat"].sum()
    print(
        f"повторные заказы:   {repeat} "
        f"({repeat / len(orders):.1%} заказов, "
        f"{orders.groupby('student_id').size().gt(1).sum()} покупателей)"
    )
    print(f"средний чек заказа: {orders['revenue'].mean():,.0f} руб.".replace(",", " "))
    print(f"заказов из 2+ курсов: {(orders['n_items'] > 1).sum()}")

    print()
    print("=" * 78)
    print("ВОССТАНОВЛЕННЫЙ МАРКЕТИНГОВЫЙ КАЛЕНДАРЬ (оценка, не факт)")
    print("=" * 78)
    cols = [
        "date_start",
        "date_end",
        "type",
        "orders",
        "orders_baseline",
        "uplift_orders",
        "incremental_revenue_est",
        "discount_depth",
        "max_uplift_ratio",
        "confidence",
    ]
    with pd.option_context("display.width", 200, "display.max_columns", 50):
        print(calendar[cols].to_string(index=False))

    print()
    print("Профиль дня недели на спокойных днях (диагностика сезонности):")
    print(detect_events.dow_profile(daily_enriched, calendar).to_string())

    print()
    print("Запуски продуктов (по первой продаже):")
    with pd.option_context("display.width", 200):
        print(launches.to_string(index=False))

    if len(flash):
        print()
        print("Внутридневные флеш-окна (цена резко ниже дневной медианы):")
        with pd.option_context("display.width", 200):
            print(
                flash[["date", "hour", "lines_cnt", "discount", "extra_discount"]].to_string(
                    index=False
                )
            )

    print()
    print(f"Выгрузки и графики сохранены в {OUT}")


if __name__ == "__main__":
    main()
