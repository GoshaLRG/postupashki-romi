"""
Подготовка слоя продаж: строки -> заказы, восстановление прайс-цен, индекс скидки.

Все допущения вынесены в константы и задокументированы в README.
Ни одно поле здесь не выдумывается: всё считается из base.xlsx.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

# --- ДОПУЩЕНИЯ (см. README, раздел "Assumptions") ---------------------------

# Заказ = один покупатель + совпадающий до секунды timestamp.
# Обоснование: доступ к курсу выдаётся в момент оплаты, поэтому одинаковая
# секунда у одного student_id = одна транзакция менеджера.
ORDER_KEY = ["student_id", "ts"]

# Потолок цены одиночного курса. Всё, что выше, в одиночном заказе считается
# пакетной суммой, записанной одной строкой (сетка цен: 6 450-9 950 руб.).
SINGLE_COURSE_PRICE_CAP = 11_000

# Последний календарный день выгрузки обрезан (данные до 06:09), поэтому
# исключается из временных рядов, но остаётся в сыром слое.
DROP_TRUNCATED_LAST_DAY = True

RU2EN = {
    "Номер студента": "student_id",
    "Сумма": "amount",
    "Курс": "course",
    "Время": "ts",
}


def load_raw(path: str | Path) -> pd.DataFrame:
    """Читает base.xlsx и приводит к английским именам колонок."""
    df = pd.read_excel(path)
    if set(RU2EN).issubset(df.columns):
        df = df.rename(columns=RU2EN)
    else:  # запасной вариант, если заголовки поменяются
        df.columns = ["student_id", "amount", "course", "ts"]
    df["ts"] = pd.to_datetime(df["ts"])
    df["date"] = df["ts"].dt.date
    df["hour"] = df["ts"].dt.hour
    df = df.sort_values("ts").reset_index(drop=True)
    df["order_id"] = [
        hashlib.md5(f"{s}|{t}".encode()).hexdigest()[:12]
        for s, t in zip(df["student_id"], df["ts"])
    ]
    df["n_items_in_order"] = df.groupby("order_id")["course"].transform("size")
    return df


def list_prices(lines: pd.DataFrame) -> pd.Series:
    """
    Прайс-цена курса (цена без скидки).

    Берём только одиночные заказы (там amount однозначно = цена курса),
    отбрасываем строки дороже SINGLE_COURSE_PRICE_CAP как пакетные суммы
    и берём 90-й перцентиль: он устойчив к разовым выбросам.
    """
    solo = lines[(lines["n_items_in_order"] == 1)]
    clean = solo[solo["amount"] <= SINGLE_COURSE_PRICE_CAP]
    price = clean.groupby("course")["amount"].quantile(0.90)
    # курсы, которых не было в одиночных заказах
    missing = set(lines["course"]) - set(price.index)
    if missing:
        fallback = lines[lines["course"].isin(missing)].groupby("course")["amount"].max()
        price = pd.concat([price, fallback])
    return price.rename("list_price")


def add_price_ratio(lines: pd.DataFrame, price: pd.Series) -> pd.DataFrame:
    """price_ratio = фактическая цена строки / прайс-цена курса."""
    out = lines.merge(price, left_on="course", right_index=True, how="left")
    out["price_ratio"] = (out["amount"] / out["list_price"]).clip(upper=1.5)
    out["discount"] = (1 - out["price_ratio"]).clip(lower=0)
    return out


def build_orders(lines: pd.DataFrame) -> pd.DataFrame:
    """Свод строк в заказы."""
    orders = (
        lines.groupby("order_id")
        .agg(
            student_id=("student_id", "first"),
            ts=("ts", "first"),
            date=("date", "first"),
            hour=("hour", "first"),
            n_items=("course", "size"),
            revenue=("amount", "sum"),
            courses=("course", lambda s: " + ".join(sorted(s))),
            median_discount=("discount", "median"),
        )
        .sort_values("ts")
        .reset_index()
    )
    orders["is_repeat"] = orders.duplicated("student_id", keep="first")
    return orders


def daily_metrics(orders: pd.DataFrame, lines: pd.DataFrame) -> pd.DataFrame:
    """Дневной ряд: заказы, выручка, покупатели, глубина скидки."""
    d = (
        orders.groupby("date")
        .agg(
            orders=("order_id", "size"),
            revenue=("revenue", "sum"),
            buyers=("student_id", "nunique"),
            avg_check=("revenue", "mean"),
        )
        .reset_index()
    )
    disc = lines.groupby("date")["discount"].median().rename("discount_depth")
    d = d.merge(disc, on="date", how="left")
    d["date"] = pd.to_datetime(d["date"])
    d["dow"] = d["date"].dt.dayofweek

    if DROP_TRUNCATED_LAST_DAY:
        last_day = lines["ts"].max()
        # день считается обрезанным, если последняя покупка в нём раньше 12:00
        if last_day.hour < 12:
            d = d[d["date"] < pd.Timestamp(last_day.date())]

    return d.sort_values("date").reset_index(drop=True)


def prepare(path: str | Path) -> dict[str, pd.DataFrame]:
    """Единая точка входа: возвращает все подготовленные слои."""
    raw = load_raw(path)
    price = list_prices(raw)
    lines = add_price_ratio(raw, price)
    orders = build_orders(lines)
    daily = daily_metrics(orders, lines)
    return {
        "lines": lines,
        "orders": orders,
        "daily": daily,
        "list_price": price.reset_index(),
    }
