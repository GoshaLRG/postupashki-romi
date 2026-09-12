"""
Генератор диплинков для внешних размещений.

Запуск:
    python tracking/links.py --channels tracking/channels_example.csv

На вход — список каналов, на выход — уникальная ссылка на каждое
сочетание канал × размещение × креатив и строки в ad_registry.

Смысл: различать пользователей, пришедших из разных каналов, размещений
и креативов, можно только если метка выдана ДО публикации. Поэтому
генератор одновременно заводит запись в журнале размещений — с полями
publication_time и cost, без которых ROMI не считается в принципе.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from core import connect

BOT_USERNAME = "PostupashkiBot"


def build_link(placement_id: str, bot: str = BOT_USERNAME) -> str:
    return f"https://t.me/{bot}?start={placement_id}"


def generate(channels_csv: Path, bot: str, is_synthetic: bool) -> list[dict]:
    conn = connect()
    rows: list[dict] = []

    with open(channels_csv, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            n_creatives = int(r.get("creatives") or 1)
            placement_no = r.get("placement_no") or "01"
            for cr in range(1, n_creatives + 1):
                pid = f"{r['channel_id']}_p{placement_no}_cr{cr}"
                row = {
                    "placement_id": pid,
                    "channel_id": r["channel_id"],
                    "channel_title": r.get("channel_title", ""),
                    "campaign_id": r["campaign_id"],
                    "creative_id": f"cr{cr}",
                    "publication_time": r.get("publication_time") or None,
                    # стоимость размещения делится между креативами:
                    # это один и тот же оплаченный слот, показанный по-разному
                    "cost": round(float(r["cost"]) / n_creatives, 2) if r.get("cost") else None,
                    "format": r.get("format", ""),
                    "deep_link": build_link(pid, bot),
                    "is_synthetic": int(is_synthetic),
                }
                conn.execute(
                    "INSERT OR REPLACE INTO ad_registry VALUES (:placement_id,"
                    " :channel_id, :channel_title, :campaign_id, :creative_id,"
                    " :publication_time, :cost, :format, :deep_link, :is_synthetic)",
                    row,
                )
                rows.append(row)
    conn.commit()
    return rows


def main() -> None:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--channels", default=str(here / "channels_example.csv"))
    ap.add_argument("--bot", default=BOT_USERNAME)
    ap.add_argument("--out", default=str(here / "placements.csv"))
    ap.add_argument("--real", action="store_true", help="данные реальные, не тестовые")
    args = ap.parse_args()

    rows = generate(Path(args.channels), args.bot, is_synthetic=not args.real)

    with open(args.out, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    missing = [r["placement_id"] for r in rows if not r["cost"] or not r["publication_time"]]
    print(f"Создано размещений: {len(rows)}")
    print(f"Файл со ссылками для менеджера: {args.out}")
    if missing:
        print()
        print(f"ВНИМАНИЕ: у {len(missing)} размещений не заполнены cost/publication_time.")
        print("Такие размещения считаются неизмеримыми, ROMI по ним не рассчитывается:")
        for pid in missing[:10]:
            print(f"  - {pid}")


if __name__ == "__main__":
    main()
