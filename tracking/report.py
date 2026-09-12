"""
Воронка в разрезе размещений: то, чего у бизнеса сейчас нет вообще.

Запуск:
    python tracking/report.py

Атрибуция здесь намеренно простая — last touch. Сравнение моделей
(first / linear / time decay) и расчёт ROMI — блок 3, src/attribution.py.
Здесь показывается главное: после запуска трекинга каждая оплата
привязана к размещению, а каждое размещение — к стоимости.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import connect  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "output" / "placement_funnel.csv"

QUERY = """
WITH last_touch AS (
    SELECT p.payment_id, p.amount, p.user_hash,
           (SELECT t.placement_id FROM touch t
             WHERE t.user_hash = p.user_hash
               AND t.ts <= p.ts
               AND t.placement_id IS NOT NULL
             ORDER BY t.ts DESC LIMIT 1) AS placement_id
    FROM payment p
)
SELECT
    a.placement_id,
    a.channel_title,
    a.campaign_id,
    a.creative_id,
    a.cost,
    (SELECT COUNT(*) FROM touch t WHERE t.placement_id = a.placement_id) AS touches,
    (SELECT COUNT(DISTINCT l.lead_id) FROM lead l
       WHERE l.user_hash IN (SELECT user_hash FROM touch t
                              WHERE t.placement_id = a.placement_id)) AS leads,
    (SELECT COUNT(*) FROM last_touch lt
       WHERE lt.placement_id = a.placement_id) AS payments,
    (SELECT COALESCE(SUM(lt.amount), 0) FROM last_touch lt
       WHERE lt.placement_id = a.placement_id) AS revenue
FROM ad_registry a
ORDER BY revenue DESC;
"""


def main() -> None:
    conn = connect()
    rows = [dict(r) for r in conn.execute(QUERY)]

    for r in rows:
        r["cr_touch_to_pay"] = (
            round(r["payments"] / r["touches"], 3) if r["touches"] else None
        )
        r["cac"] = round(r["cost"] / r["payments"]) if r["cost"] and r["payments"] else None
        r["measurable"] = bool(r["cost"])

    unattributed = conn.execute(
        "SELECT COUNT(*) c, COALESCE(SUM(amount), 0) s FROM payment p"
        " WHERE NOT EXISTS (SELECT 1 FROM touch t WHERE t.user_hash = p.user_hash"
        "                    AND t.placement_id IS NOT NULL AND t.ts <= p.ts)"
    ).fetchone()
    total = conn.execute("SELECT COUNT(*) c, SUM(amount) s FROM payment").fetchone()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    head = f"{'размещение':<16}{'канал':<18}{'касаний':>9}{'лидов':>8}{'оплат':>8}{'выручка':>11}{'CAC':>9}"
    print(head)
    print("-" * len(head))
    for r in rows:
        cac = f"{r['cac']:,}".replace(",", " ") if r["cac"] else "—"
        rev = f"{int(r['revenue']):,}".replace(",", " ")
        print(
            f"{r['placement_id']:<16}{(r['channel_title'] or '')[:17]:<18}"
            f"{r['touches']:>9}{r['leads']:>8}{r['payments']:>8}{rev:>11}{cac:>9}"
        )

    share = unattributed["c"] / total["c"] if total["c"] else 0
    print()
    print(
        f"Без источника (органика): {unattributed['c']} оплат из {total['c']} "
        f"({share:.0%}), выручка {int(unattributed['s']):,}".replace(",", " ")
    )
    print("Эти оплаты НЕ распределяются между каналами — доля неизвестного")
    print("является метрикой качества системы, а не поводом для догадок.")

    no_cost = [r["placement_id"] for r in rows if not r["measurable"]]
    if no_cost:
        print()
        print(f"Неизмеримые размещения (нет cost): {', '.join(no_cost)}")

    print()
    print(f"Выгрузка: {OUT}")


if __name__ == "__main__":
    main()
