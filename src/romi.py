"""
ROMI: две принципиально разные оценки.

    ROMI_attr = (R_attr * m - C) / C     кому модель приписала продажу
    ROMI_inc  = ((R_test - R_control) * m - C) / C   что реклама добавила

где C — стоимость размещения, m — contribution margin.

Первая оценка отвечает на вопрос «как распределить заслугу за уже
случившиеся продажи». Вторая — «случились бы эти продажи без рекламы».
Это не уточнение одной метрики другой: они могут расходиться в разы,
и бюджетное решение принимается по второй.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

# ДОПУЩЕНИЕ. Contribution margin онлайн-курса: из выручки вычитается
# эквайринг, поддержка и проверка работ. Точного значения в данных нет,
# 0.85 — консервативная оценка для инфопродукта. Все выводы ниже
# пересчитываются при изменении этого числа.
CONTRIBUTION_MARGIN = 0.85

# Размещение с таким числом оплат считается статистически необеспеченным:
# ROMI по нему считается, но решение по нему принимать нельзя.
MIN_PAYMENTS_FOR_DECISION = 5


def load_costs(db_path: str | Path) -> pd.DataFrame:
    conn = sqlite3.connect(str(db_path))
    ads = pd.read_sql(
        "SELECT placement_id, channel_id, channel_title, campaign_id,"
        " creative_id, cost, publication_time FROM ad_registry",
        conn,
    )
    conn.close()
    return ads


def romi_attr(
    attributed: pd.DataFrame, ads: pd.DataFrame, margin: float = CONTRIBUTION_MARGIN
) -> pd.DataFrame:
    """ROMI по каждой модели атрибуции для каждого размещения."""
    df = attributed.merge(ads, on="placement_id", how="left")
    df["measurable"] = df["cost"].notna() & (df["cost"] > 0)
    df["contribution"] = df["revenue"] * margin
    df["romi"] = np.where(
        df["measurable"], (df["contribution"] - df["cost"]) / df["cost"], np.nan
    )
    df["breakeven_revenue"] = np.where(df["measurable"], df["cost"] / margin, np.nan)
    return df


def compare_models(romi_table: pd.DataFrame) -> pd.DataFrame:
    """Сводка: как меняется ROMI размещения при смене правила атрибуции."""
    wide = romi_table.pivot(index="placement_id", columns="model", values="romi")
    meta = (
        romi_table.groupby("placement_id")
        .agg(
            channel=("channel_title", "first"),
            cost=("cost", "first"),
            payments=("payments", "max"),
        )
    )
    out = meta.join(wide)
    models = [c for c in out.columns if c not in ("channel", "cost", "payments")]
    out["min"] = out[models].min(axis=1)
    out["max"] = out[models].max(axis=1)
    out["verdict"] = np.select(
        [out["min"] > 0, out["max"] < 0],
        ["прибыльно при любой модели", "убыточно при любой модели"],
        default="зависит от модели",
    )
    out["decision_ready"] = out["payments"] >= MIN_PAYMENTS_FOR_DECISION
    return out.sort_values("max", ascending=False)


def romi_incremental(
    revenue_test: float,
    revenue_control: float,
    cost: float,
    margin: float = CONTRIBUTION_MARGIN,
) -> float:
    """ROMI по инкрементальному эффекту. Требует контрольной группы."""
    if not cost:
        return float("nan")
    return ((revenue_test - revenue_control) * margin - cost) / cost


def historical_incrementality(calendar_csv: str | Path, margin: float = CONTRIBUTION_MARGIN):
    """
    Инкрементальная оценка по восстановленным окнам кампаний.

    Числитель есть: прирост выручки над базовой линией из блока 1.
    Знаменателя нет: стоимость исторических размещений не логировалась.
    Поэтому возвращается не ROMI, а порог безубыточности — сколько
    кампания могла стоить, чтобы остаться в плюсе.
    """
    cal = pd.read_csv(calendar_csv)
    cal = cal[cal["incremental_revenue_est"].notna()].copy()
    cal["contribution_est"] = cal["incremental_revenue_est"] * margin
    cal["max_justified_cost"] = cal["contribution_est"]
    cal["romi_inc"] = np.nan  # стоимость неизвестна — считать не из чего
    return cal[
        [
            "date_start",
            "date_end",
            "type",
            "incremental_revenue_est",
            "contribution_est",
            "max_justified_cost",
            "romi_inc",
            "confidence",
        ]
    ]


def allocate_budget(
    comparison: pd.DataFrame, budget: float = 300_000, model: str = "last_touch"
) -> pd.DataFrame:
    """
    Правило 70/20/10, а не угадывание канала.

      70% — размещения, прибыльные при ЛЮБОЙ модели атрибуции и с
            достаточным числом оплат; доля пропорциональна ROMI;
      20% — равными долями в проверку размещений, по которым данных
            не хватает: без этого статистика не появится никогда;
      10% — контрольная группа, которую сознательно не трогаем,
            чтобы измерить инкрементальный эффект.

    Размещения, убыточные при любой модели и уже набравшие достаточно
    оплат, из бюджета исключаются: повторно проверять нечего.
    """
    df = comparison.copy()
    proven = df[(df["verdict"] == "прибыльно при любой модели") & df["decision_ready"]]
    # убыточно при любой модели И данных достаточно -> не тестируем повторно
    rejected = df[(df["verdict"] == "убыточно при любой модели") & df["decision_ready"]]
    untested = df[~df.index.isin(proven.index) & ~df.index.isin(rejected.index)]

    rows = []
    if len(proven):
        weights = proven[model].clip(lower=0)
        weights = weights / weights.sum() if weights.sum() > 0 else None
        for pid, w in zip(proven.index, weights):
            rows.append(
                {
                    "placement_id": pid,
                    "channel": proven.loc[pid, "channel"],
                    "bucket": "проверенные (70%)",
                    "amount": round(budget * 0.70 * w),
                    "reason": f"ROMI {proven.loc[pid, model]:+.2f} при {model}",
                }
            )
    if len(untested):
        share = budget * 0.20 / len(untested)
        for pid in untested.index:
            rows.append(
                {
                    "placement_id": pid,
                    "channel": untested.loc[pid, "channel"],
                    "bucket": "тесты (20%)",
                    "amount": round(share),
                    "reason": "данных недостаточно для решения",
                }
            )
    for pid in rejected.index:
        rows.append(
            {
                "placement_id": pid,
                "channel": rejected.loc[pid, "channel"],
                "bucket": "исключено (0%)",
                "amount": 0,
                "reason": "убыточно при любой модели, данных достаточно",
            }
        )
    rows.append(
        {
            "placement_id": "—",
            "channel": "holdout",
            "bucket": "контроль (10%)",
            "amount": round(budget * 0.10),
            "reason": "не тратим: нужна контрольная группа для ROMI_inc",
        }
    )
    return pd.DataFrame(rows)
