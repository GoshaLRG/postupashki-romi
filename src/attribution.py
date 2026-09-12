"""
Движок атрибуции: распределение выручки между рекламными касаниями.

Одна покупка может иметь несколько касаний, поэтому вопрос «какой канал
принёс деньги» не имеет единственного ответа — ответ зависит от правила
распределения. Модуль считает пять правил сразу, чтобы было видно,
насколько сильно от них зависит вывод.

Окно атрибуции: 14 дней. Обоснование на данных кейса — всплески продаж
затухают за 3-4 дня, повторные покупки редки (3,5% заказов), поэтому
14 дней покрывают цикл принятия решения с запасом. Касания старше окна
не участвуют.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

ATTRIBUTION_WINDOW_DAYS = 14
TIME_DECAY_HALFLIFE_DAYS = 7
POSITION_FIRST = 0.40
POSITION_LAST = 0.40

MODELS = ["first_touch", "last_touch", "linear", "time_decay", "position_based"]


def load_events(db_path: str | Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Оплаты и рекламные касания из базы трекинга."""
    conn = sqlite3.connect(str(db_path))
    payments = pd.read_sql(
        "SELECT payment_id, user_hash, ts, amount FROM payment", conn
    )
    touches = pd.read_sql(
        "SELECT touch_id, user_hash, placement_id, campaign_id, ts FROM touch"
        " WHERE placement_id IS NOT NULL",
        conn,
    )
    conn.close()
    for df in (payments, touches):
        df["ts"] = pd.to_datetime(df["ts"])
    return payments, touches


def _weights(model: str, deltas_days: np.ndarray) -> np.ndarray:
    """
    Веса касаний внутри одной покупки.

    deltas_days — сколько дней прошло от касания до оплаты,
    отсортировано по возрастанию времени касания (первое касание — [0]).
    """
    n = len(deltas_days)
    if n == 1:
        return np.array([1.0])

    if model == "first_touch":
        w = np.zeros(n)
        w[0] = 1.0
    elif model == "last_touch":
        w = np.zeros(n)
        w[-1] = 1.0
    elif model == "linear":
        w = np.full(n, 1 / n)
    elif model == "time_decay":
        # чем ближе касание к оплате, тем больше вес
        w = np.power(0.5, deltas_days / TIME_DECAY_HALFLIFE_DAYS)
        w = w / w.sum()
    elif model == "position_based":
        if n == 2:
            w = np.array([0.5, 0.5])
        else:
            middle = (1 - POSITION_FIRST - POSITION_LAST) / (n - 2)
            w = np.full(n, middle)
            w[0], w[-1] = POSITION_FIRST, POSITION_LAST
    else:
        raise ValueError(f"неизвестная модель: {model}")
    return w


def attribute(
    payments: pd.DataFrame, touches: pd.DataFrame, window_days: int = ATTRIBUTION_WINDOW_DAYS
) -> tuple[pd.DataFrame, dict]:
    """
    Возвращает длинную таблицу (model, placement_id, revenue, payments)
    и сводку по неатрибутированной выручке.
    """
    window = pd.Timedelta(days=window_days)
    by_user = {u: g.sort_values("ts") for u, g in touches.groupby("user_hash")}

    rows: list[dict] = []
    unattributed_revenue = 0.0
    unattributed_count = 0
    multitouch_payments = 0

    for _, p in payments.iterrows():
        g = by_user.get(p["user_hash"])
        if g is None:
            unattributed_revenue += p["amount"]
            unattributed_count += 1
            continue

        path = g[(g["ts"] <= p["ts"]) & (g["ts"] >= p["ts"] - window)]
        if path.empty:
            # касания есть, но вне окна атрибуции — считаем органикой
            unattributed_revenue += p["amount"]
            unattributed_count += 1
            continue

        if len(path) > 1:
            multitouch_payments += 1

        deltas = (p["ts"] - path["ts"]).dt.total_seconds().to_numpy() / 86400
        for model in MODELS:
            w = _weights(model, deltas)
            for placement, weight in zip(path["placement_id"], w):
                rows.append(
                    {
                        "model": model,
                        "placement_id": placement,
                        "revenue": p["amount"] * weight,
                        "payments": weight,
                    }
                )

    attributed = (
        pd.DataFrame(rows)
        .groupby(["model", "placement_id"], as_index=False)
        .agg(revenue=("revenue", "sum"), payments=("payments", "sum"))
    )

    total = float(payments["amount"].sum())
    summary = {
        "payments_total": len(payments),
        "revenue_total": total,
        "unattributed_payments": unattributed_count,
        "unattributed_revenue": unattributed_revenue,
        "unattributed_share": unattributed_revenue / total if total else 0.0,
        "multitouch_payments": multitouch_payments,
        "window_days": window_days,
    }
    return attributed, summary


def sensitivity(attributed: pd.DataFrame) -> pd.DataFrame:
    """
    Насколько вывод зависит от выбора модели.

    Если разброс выручки по одному размещению между моделями велик,
    решение нельзя принимать по атрибуции — нужна инкрементальная оценка.
    """
    wide = attributed.pivot(index="placement_id", columns="model", values="revenue").fillna(0)
    wide["min"] = wide[MODELS].min(axis=1)
    wide["max"] = wide[MODELS].max(axis=1)
    wide["spread_pct"] = np.where(
        wide["max"] > 0, (wide["max"] - wide["min"]) / wide["max"] * 100, 0
    )
    return wide.sort_values("max", ascending=False)
