"""
Восстановление маркетингового календаря из слоя продаж.

Журнала рекламных размещений нет. Но след кампании остаётся в данных:
  1) всплеск числа заказов над сезонным baseline,
  2) провал средней цены (скидка / промокод),
  3) появление курса, которого раньше не продавали (запуск).

Модуль помечает такие окна и классифицирует их. Все результаты — ОЦЕНКА
(knowledge_level = "estimated"), а не факт: это прямо указано в выгрузке.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# --- Пороги (единственные "ручки" модели) ------------------------------------
MIN_UPLIFT = 2.0             # заказов минимум в 2 раза выше baseline
MIN_UPLIFT_ABS = 5           # и минимум на 5 заказов в абсолюте
DISCOUNT_THRESHOLD = 0.20    # медианная цена дня на 20%+ ниже прайса = акция
LAUNCH_MIN_SALES_7D = 3      # запуск засчитывается, если за 7 дней >= 3 продажи
LEFT_CENSOR_DAYS = 2         # первые дни выгрузки: "первая продажа" != запуск
FLASH_MIN_LINES = 3          # минимум строк в часе для детекта флеш-распродажи
FLASH_EXTRA_DISCOUNT = 0.30  # насколько час должен быть дешевле своего дня
TRAILING_WINDOW = 14         # длина окна для уровня baseline, дней
BRIDGE_GAP_DAYS = 1          # склеивать окна, разделённые одним спокойным днём


def robust_z(x: pd.Series) -> pd.Series:
    """z-score на медиане и MAD: не ломается от самих всплесков."""
    med = x.median()
    mad = np.median(np.abs(x - med))
    scale = 1.4826 * mad if mad > 0 else x.std(ddof=0)
    if not scale or np.isnan(scale):
        return pd.Series(np.zeros(len(x)), index=x.index)
    return (x - med) / scale


def quiet_days(d: pd.DataFrame, col: str = "orders") -> pd.Series:
    """Маска "спокойных" дней — без явных всплесков (черновой проход)."""
    return robust_z(d[col]) < 2.5


def dow_profile(d: pd.DataFrame, calendar: pd.DataFrame | None = None,
                col: str = "orders") -> pd.DataFrame:
    """
    Профиль дня недели ВНЕ рекламных окон — диагностика, а не часть модели.

    Считается на днях, не попавших ни в одно детектированное окно (плюс день
    после окна — хвост кампании). Проверка показывает: вне кампаний дня недели
    практически нет, выходные "высокие" только потому, что на них ставили
    кампании. Поэтому baseline строится без сезонной поправки — иначе всплеск
    растворился бы в "нормальной субботе".
    """
    mask = pd.Series(True, index=d.index)
    if calendar is not None and len(calendar):
        blocked: set = set()
        for _, e in calendar.iterrows():
            rng = pd.date_range(e["date_start"], pd.Timestamp(e["date_end"]) + pd.Timedelta(days=1))
            blocked |= set(rng)
        mask = ~d["date"].isin(blocked)
    q = d[mask]
    prof = q.groupby("dow")[col].agg(["median", "size"])
    prof["factor"] = (prof["median"] / q[col].median()).round(2)
    prof.index = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"][: len(prof)]
    return prof


def add_baseline(d: pd.DataFrame, col: str) -> pd.DataFrame:
    """
    baseline = медиана значения за предыдущие TRAILING_WINDOW дней.

    Медиана по длинному окну устойчива: 2-3 дня всплеска внутри окна её
    почти не двигают, поэтому кампания не "съедает" собственный baseline.
    """
    d = d.copy()
    level = d[col].shift(1).rolling(TRAILING_WINDOW, min_periods=5).median()
    level = level.fillna(d[col].expanding(min_periods=1).median().shift(1))
    level = level.fillna(d[col].median())
    d[f"{col}_baseline"] = level
    d[f"{col}_ratio"] = d[col] / d[f"{col}_baseline"]
    d[f"{col}_z"] = robust_z(d[f"{col}_ratio"])
    d[f"{col}_uplift_abs"] = d[col] - d[f"{col}_baseline"]
    return d


def detect_launches(lines: pd.DataFrame) -> pd.DataFrame:
    """
    Дата запуска курса = дата первой продажи.

    Курсы, впервые проданные в первые LEFT_CENSOR_DAYS дней выгрузки,
    запусками НЕ считаются: история обрезана слева, эти курсы почти наверняка
    продавались и раньше. Это left censoring, а не находка.
    """
    data_start = lines["ts"].min().normalize()
    cutoff = data_start + pd.Timedelta(days=LEFT_CENSOR_DAYS)
    first = lines.groupby("course")["ts"].min().rename("first_sale").reset_index()
    rows = []
    for _, r in first.iterrows():
        if r["first_sale"] < cutoff:
            continue
        window = lines[
            (lines["course"] == r["course"])
            & (lines["ts"] < r["first_sale"] + pd.Timedelta(days=7))
        ]
        if len(window) >= LAUNCH_MIN_SALES_7D:
            rows.append(
                {
                    "course": r["course"],
                    "launch_date": pd.Timestamp(r["first_sale"].date()),
                    "sales_first_7d": len(window),
                }
            )
    cols = ["course", "launch_date", "sales_first_7d"]
    out = pd.DataFrame(rows, columns=cols)
    if len(out):
        out = out.sort_values("launch_date").reset_index(drop=True)
    return out


def detect_flash_sales(lines: pd.DataFrame) -> pd.DataFrame:
    """Внутридневные окна с резким провалом цены (флеш-распродажа)."""
    h = (
        lines.groupby(["date", "hour"])
        .agg(lines_cnt=("amount", "size"), discount=("discount", "median"))
        .reset_index()
    )
    day = lines.groupby("date")["discount"].median().rename("day_discount")
    h = h.merge(day, on="date")
    flash = h[
        (h["lines_cnt"] >= FLASH_MIN_LINES)
        & (h["discount"] - h["day_discount"] >= FLASH_EXTRA_DISCOUNT)
    ].copy()
    flash["extra_discount"] = (flash["discount"] - flash["day_discount"]).round(3)
    flash["discount"] = flash["discount"].round(3)
    return flash.sort_values(["date", "hour"]).reset_index(drop=True)


def _windows(flags: list[bool], bridge: int = 0) -> list[tuple[int, int]]:
    """Подряд идущие True -> интервалы; окна через <= bridge дней склеиваются."""
    out, start = [], None
    for i, v in enumerate(flags):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(flags) - 1))

    merged: list[tuple[int, int]] = []
    for w in out:
        if merged and w[0] - merged[-1][1] - 1 <= bridge:
            merged[-1] = (merged[-1][0], w[1])
        else:
            merged.append(w)
    return merged


def build_calendar(daily: pd.DataFrame, lines: pd.DataFrame):
    """Главная функция: обогащённый дневной ряд + календарь кампаний + запуски."""
    d = add_baseline(daily, "orders")
    d = add_baseline(d, "revenue")
    d["discount_z"] = robust_z(d["discount_depth"])

    d["spike"] = (d["orders_ratio"] >= MIN_UPLIFT) & (
        d["orders_uplift_abs"] >= MIN_UPLIFT_ABS
    )
    d["deep_discount"] = d["discount_depth"] >= DISCOUNT_THRESHOLD

    launches = detect_launches(lines)
    events = []

    for lo, hi in _windows((d["spike"] | d["deep_discount"]).tolist(), BRIDGE_GAP_DAYS):
        w = d.iloc[lo : hi + 1]
        start, end = w["date"].iloc[0], w["date"].iloc[-1]
        launched = (
            launches[(launches["launch_date"] >= start) & (launches["launch_date"] <= end)]
            if len(launches)
            else launches
        )

        has_spike = bool(w["spike"].any())
        has_disc = bool(w["deep_discount"].any())

        if has_disc and has_spike:
            etype, evidence = "sale", "всплеск заказов + провал цены"
        elif has_spike and len(launched):
            etype, evidence = "product_launch", "всплеск заказов + новый курс в продаже"
        elif has_spike:
            etype, evidence = "traffic_spike", "всплеск заказов без скидки"
        else:
            etype, evidence = "targeted_promo", "скидка без роста объёма"

        if len(launched) and etype != "product_launch":
            evidence += f"; запуск: {', '.join(launched['course'])}"

        ratio_max = float(w["orders_ratio"].max())
        confidence = "high" if ratio_max >= 3 else "medium" if ratio_max >= 2 else "low"

        events.append(
            {
                "date_start": start.date(),
                "date_end": end.date(),
                "days": len(w),
                "type": etype,
                "orders": int(w["orders"].sum()),
                "orders_baseline": round(float(w["orders_baseline"].sum()), 1),
                "uplift_orders": round(float(w["orders_uplift_abs"].sum()), 1),
                "revenue": round(float(w["revenue"].sum())),
                "revenue_baseline": round(float(w["revenue_baseline"].sum())),
                "incremental_revenue_est": round(float(w["revenue_uplift_abs"].sum())),
                "discount_depth": round(float(w["discount_depth"].max()), 3),
                "max_uplift_ratio": round(ratio_max, 2),
                "evidence": evidence,
                "confidence": confidence,
                "knowledge_level": "estimated",
                "launched_courses": ", ".join(launched["course"]) if len(launched) else "",
            }
        )

    cal = pd.DataFrame(events)

    covered: set = set()
    for e in events:
        covered |= set(pd.date_range(e["date_start"], e["date_end"]))
    if len(launches):
        extra = launches[~launches["launch_date"].isin(covered)]
        for date, grp in extra.groupby("launch_date"):
            cal = pd.concat(
                [
                    cal,
                    pd.DataFrame(
                        [
                            {
                                "date_start": date.date(),
                                "date_end": date.date(),
                                "days": 1,
                                "type": "product_launch",
                                "orders": np.nan,
                                "orders_baseline": np.nan,
                                "uplift_orders": np.nan,
                                "revenue": np.nan,
                                "revenue_baseline": np.nan,
                                "incremental_revenue_est": np.nan,
                                "discount_depth": np.nan,
                                "z_orders_max": np.nan,
                                "evidence": "первая продажа курса без всплеска объёма",
                                "confidence": "medium",
                                "knowledge_level": "estimated",
                                "launched_courses": ", ".join(grp["course"]),
                            }
                        ]
                    ),
                ],
                ignore_index=True,
            )

    return d, cal.sort_values("date_start").reset_index(drop=True), launches
