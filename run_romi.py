"""
Блок 4: атрибуция и ROMI.

Запуск: python3 run_romi.py

Читает события трекинга (tracking/tracking.db) и календарь кампаний
блока 1 (output/campaign_calendar.csv), пишет выгрузки в output/.

Важно: атрибуция считается на СИНТЕТИЧЕСКИХ событиях трекинга —
реальных касаний в кейсе не существует. Историческая часть считается
на реальных данных и заканчивается честным «стоимости нет».
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src import attribution, romi

ROOT = Path(__file__).parent
DB = ROOT / "tracking" / "tracking.db"
OUT = ROOT / "output"
CALENDAR = OUT / "campaign_calendar.csv"


def money(x) -> str:
    return f"{x:,.0f}".replace(",", " ") if pd.notna(x) else "—"


def main() -> None:
    OUT.mkdir(exist_ok=True)

    if not DB.exists():
        raise SystemExit(
            "Нет базы трекинга. Сначала запустите блок 2:\n"
            "  python3 tracking/links.py && python3 tracking/simulate.py"
        )
    if not CALENDAR.exists():
        raise SystemExit("Нет календаря кампаний. Сначала: python3 run_analysis.py")

    # --- 1. Атрибуция -------------------------------------------------------
    payments, touches = attribution.load_events(DB)
    attributed, summary = attribution.attribute(payments, touches)
    ads = romi.load_costs(DB)

    print("=" * 78)
    print("АТРИБУЦИЯ (синтетические события трекинга)")
    print("=" * 78)
    print(f"окно атрибуции:            {summary['window_days']} дней")
    print(f"оплат всего:               {summary['payments_total']}")
    print(f"из них с 2+ касаниями:     {summary['multitouch_payments']}")
    print(
        f"без источника (органика):  {summary['unattributed_payments']} оплат, "
        f"{money(summary['unattributed_revenue'])} руб. "
        f"({summary['unattributed_share']:.0%} выручки)"
    )
    print("Органика между каналами НЕ распределяется.")

    spread = attribution.sensitivity(attributed)
    print()
    print("Чувствительность к выбору модели, выручка по размещениям:")
    cols = attribution.MODELS + ["spread_pct"]
    view = spread[cols].round(0).astype(int, errors="ignore")
    print(view.to_string())

    # --- 2. ROMI по моделям атрибуции ---------------------------------------
    table = romi.romi_attr(attributed, ads)
    comparison = romi.compare_models(table)

    print()
    print("=" * 78)
    print(f"ROMI_attr (margin = {romi.CONTRIBUTION_MARGIN})")
    print("=" * 78)
    head = f"{'размещение':<16}{'канал':<18}{'затраты':>9}{'опл':>5}{'ROMI min':>10}{'ROMI max':>10}  вердикт"
    print(head)
    print("-" * len(head))
    for pid, r in comparison.iterrows():
        flag = "" if r["decision_ready"] else "  (мало данных)"
        print(
            f"{pid:<16}{str(r['channel'])[:17]:<18}{money(r['cost']):>9}"
            f"{r['payments']:>5.0f}{r['min']:>10.2f}{r['max']:>10.2f}  {r['verdict']}{flag}"
        )

    print()
    print("Точка безубыточности: размещение окупается, если приписанная ему")
    print("выручка превышает C / m — стоимость, делённую на маржинальность.")

    # --- 3. Инкрементальная часть на РЕАЛЬНЫХ данных ------------------------
    hist = romi.historical_incrementality(CALENDAR)
    print()
    print("=" * 78)
    print("ROMI_inc по историческим окнам (реальные данные кейса)")
    print("=" * 78)
    print(f"{'окно':<24}{'тип':<18}{'прирост выручки':>17}{'макс. оправданная цена':>24}")
    print("-" * 83)
    for _, r in hist.iterrows():
        window = f"{r['date_start']} — {r['date_end']}"
        print(
            f"{window:<24}{r['type']:<18}{money(r['incremental_revenue_est']):>17}"
            f"{money(r['max_justified_cost']):>24}"
        )
    print()
    print("ROMI_inc посчитать нельзя: стоимость исторических размещений")
    print("не логировалась. Числитель есть, знаменателя нет. Поэтому вместо")
    print("выдуманного ROMI показан порог: сколько кампания могла стоить,")
    print("чтобы остаться в плюсе. Это и есть цена отсутствия одного поля.")

    # --- 4. Аллокация бюджета -----------------------------------------------
    plan = romi.allocate_budget(comparison, budget=300_000)
    print()
    print("=" * 78)
    print("300 000 РУБ. НА СЛЕДУЮЩИЙ ЗАПУСК")
    print("=" * 78)
    for bucket, grp in plan.groupby("bucket", sort=False):
        print(f"\n{bucket} — {money(grp['amount'].sum())} руб.")
        for _, r in grp.iterrows():
            print(f"   {str(r['channel'])[:22]:<24}{money(r['amount']):>9}   {r['reason']}")

    # --- выгрузки -----------------------------------------------------------
    attributed.to_csv(OUT / "attribution_by_model.csv", index=False)
    spread.to_csv(OUT / "attribution_sensitivity.csv")
    comparison.to_csv(OUT / "romi_by_placement.csv")
    hist.to_csv(OUT / "romi_incremental_historical.csv", index=False)
    plan.to_csv(OUT / "budget_allocation.csv", index=False)

    print()
    print(f"Выгрузки: {OUT}")


if __name__ == "__main__":
    main()
