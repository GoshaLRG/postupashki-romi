"""
Эмулятор рекламного трафика.

Запуск:
    python tracking/simulate.py

Зачем: показать работающую цепочку размещение → касание → лид → оплата
без боевого бота, токена и сети. Вызывается ТОТ ЖЕ код записи событий,
что и в bot.py, поэтому проверка здесь означает работоспособность там.

ВСЕ данные, созданные этим скриптом, помечены is_synthetic = 1.
Реальные данные в проекте — только base.xlsx (слой оплат).
"""

from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import connect, record_lead, record_payment, record_touch  # noqa: E402

SEED = 42

# Сетка цен взята из реальных данных (см. output/list_prices.csv)
PRICES = [8950, 8450, 7475, 6975, 6490, 4995]
COURSES = ["Аналитика про", "ML про", "AI агенты", "Алгоритмы старт", "Backend про"]

# Разное качество каналов: дешёвый трафик может давать много переходов
# и мало оплат — ради этого вся система и строится.
QUALITY = {
    "c01": (40, 0.28, 0.30),  # (касаний, доля лидов, конверсия лида в оплату)
    "c02": (65, 0.12, 0.15),
    "c03": (22, 0.45, 0.48),
    "c04": (18, 0.30, 0.35),
    "c05": (55, 0.35, 0.40),
    "c06": (12, 0.20, 0.25),
    "c07": (30, 0.18, 0.20),
    "c08": (25, 0.25, 0.30),
}

SCALE = 2               # множитель объёма демонстрации
ORGANIC_TOUCHES = 90       # зашли в бота без метки
SECOND_TOUCH_SHARE = 0.22  # доля пользователей с повторным касанием


def main() -> None:
    rng = random.Random(SEED)
    conn = connect()

    placements = conn.execute(
        "SELECT placement_id, channel_id, publication_time FROM ad_registry"
    ).fetchall()
    if not placements:
        raise SystemExit("Сначала создайте размещения: python tracking/links.py")

    uid = 100_000
    touched: list[tuple[int, str, datetime]] = []

    for p in placements:
        ch = p["channel_id"]
        n, lead_rate, pay_rate = QUALITY.get(ch, (20, 0.2, 0.25))
        n = max(1, int(n * SCALE / 2))  # объём делится между креативами
        pub = (
            datetime.fromisoformat(p["publication_time"])
            if p["publication_time"]
            else datetime(2026, 9, 5, 12, 0, 0)
        )

        for _ in range(n):
            uid += 1
            # переходы затухают: большинство в первые часы после публикации
            delay = timedelta(minutes=int(rng.expovariate(1 / 220)))
            ts = pub + delay
            record_touch(conn, uid, p["placement_id"], ts=ts, is_synthetic=True)
            touched.append((uid, p["placement_id"], ts))

            if rng.random() < lead_rate:
                ts_lead = ts + timedelta(minutes=rng.randint(5, 2880))
                record_lead(
                    conn,
                    uid,
                    product_interest=rng.choice(COURSES),
                    manager_id="m01",
                    ts=ts_lead,
                    is_synthetic=True,
                )
                if rng.random() < pay_rate:
                    ts_pay = ts_lead + timedelta(minutes=rng.randint(10, 4320))
                    record_payment(
                        conn,
                        uid,
                        amount=rng.choice(PRICES),
                        course=rng.choice(COURSES),
                        ts=ts_pay,
                        is_synthetic=True,
                    )

    # Повторные касания: часть пользователей видит рекламу в нескольких
    # каналах. Именно из-за них одна покупка имеет несколько касаний и
    # нужна модель атрибуции, а не просто "последний канал".
    second = rng.sample(touched, int(len(touched) * SECOND_TOUCH_SHARE))
    for user_id, first_pid, ts in second:
        other = [p["placement_id"] for p in placements if p["placement_id"] != first_pid]
        record_touch(
            conn,
            user_id,
            rng.choice(other),
            ts=ts + timedelta(days=rng.randint(1, 6)),
            is_synthetic=True,
        )

    # Органика: запуск бота без метки. Не выдумываем ей источник.
    for _ in range(ORGANIC_TOUCHES):
        uid += 1
        ts = datetime(2026, 9, 1) + timedelta(minutes=rng.randint(0, 60 * 24 * 9))
        record_touch(conn, uid, None, ts=ts, is_synthetic=True)
        if rng.random() < 0.25:
            record_lead(conn, uid, ts=ts + timedelta(hours=2), is_synthetic=True)
            if rng.random() < 0.3:
                record_payment(
                    conn,
                    uid,
                    amount=rng.choice(PRICES),
                    course=rng.choice(COURSES),
                    ts=ts + timedelta(days=1),
                    is_synthetic=True,
                )

    counts = {
        t: conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
        for t in ("touch", "lead", "payment")
    }
    multi = conn.execute(
        "SELECT COUNT(*) c FROM (SELECT user_hash FROM touch GROUP BY user_hash"
        " HAVING COUNT(*) > 1)"
    ).fetchone()["c"]

    print("Эмуляция завершена. Все записи помечены is_synthetic = 1.")
    print(f"  касаний:                {counts['touch']}")
    print(f"  лидов:                  {counts['lead']}")
    print(f"  оплат:                  {counts['payment']}")
    print(f"  пользователей с 2+ касаниями: {multi}")
    print()
    print("Дальше: python tracking/report.py")


if __name__ == "__main__":
    main()
