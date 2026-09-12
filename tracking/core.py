"""
Ядро системы трекинга. Здесь нет ни одной зависимости от Telegram.

Так сделано намеренно: бот (bot.py) и эмулятор (simulate.py) вызывают
ОДИН И ТОТ ЖЕ код записи событий. Значит, то, что проверено эмулятором
без токена и без сети, работает и в боевом боте.

Схема соответствует docs/data_model.md.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "tracking.db"

# Соль живёт в переменной окружения и НЕ попадает в репозиторий.
# Значение по умолчанию годится только для демонстрации.
SALT = os.environ.get("TRACKING_SALT", "dev-salt-not-for-production")

# placement_id: c{канал}_p{размещение}_cr{креатив}
PAYLOAD_RE = re.compile(r"^c(?P<channel>[a-z0-9]{1,12})_p(?P<placement>[a-z0-9]{1,8})_cr(?P<creative>[a-z0-9]{1,8})$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS ad_registry (
    placement_id      TEXT PRIMARY KEY,
    channel_id        TEXT NOT NULL,
    channel_title     TEXT,
    campaign_id       TEXT NOT NULL,
    creative_id       TEXT NOT NULL,
    publication_time  TEXT,
    cost              REAL,
    format            TEXT,
    deep_link         TEXT NOT NULL,
    is_synthetic      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS touch (
    touch_id      TEXT PRIMARY KEY,
    user_hash     TEXT NOT NULL,
    placement_id  TEXT,
    campaign_id   TEXT,
    ts            TEXT NOT NULL,
    touch_type    TEXT NOT NULL,
    is_synthetic  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS lead (
    lead_id          TEXT PRIMARY KEY,
    user_hash        TEXT NOT NULL,
    ts_first_message TEXT NOT NULL,
    product_interest TEXT,
    manager_id       TEXT,
    source_declared  TEXT,
    is_synthetic     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS payment (
    payment_id   TEXT PRIMARY KEY,
    user_hash    TEXT NOT NULL,
    ts           TEXT NOT NULL,
    course       TEXT,
    amount       REAL NOT NULL,
    is_synthetic INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_touch_user ON touch(user_hash);
CREATE INDEX IF NOT EXISTS idx_touch_placement ON touch(placement_id);
CREATE INDEX IF NOT EXISTS idx_lead_user ON lead(user_hash);
CREATE INDEX IF NOT EXISTS idx_payment_user ON payment(user_hash);
"""


def connect(db_path: str | Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def hash_user(telegram_user_id: int | str, salt: str = SALT) -> str:
    """
    Анонимный идентификатор пользователя.

    В хранилище не попадают ни username, ни имя, ни телефон: для всей
    аналитики достаточно устойчивого хеша. Выданный в кейсе student_id
    устроен так же, поэтому новая система стыкуется с историей.
    """
    return hashlib.sha256(f"{telegram_user_id}|{salt}".encode()).hexdigest()[:16]


def event_id(*parts: object) -> str:
    return hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:16]


@dataclass
class Placement:
    placement_id: str
    channel_id: str
    campaign_id: str
    creative_id: str


def parse_payload(payload: str | None) -> Placement | None:
    """
    Разбирает payload диплинка t.me/bot?start=c07_p03_cr2

    Возвращает None для органики (запуск бота без метки) и для мусора.
    Органика — это не ошибка, а отдельный сегмент: подменять её догадкой
    о канале нельзя.
    """
    if not payload:
        return None
    m = PAYLOAD_RE.match(payload.strip().lower())
    if not m:
        return None
    g = m.groupdict()
    return Placement(
        placement_id=f"c{g['channel']}_p{g['placement']}_cr{g['creative']}",
        channel_id=f"c{g['channel']}",
        campaign_id="",  # подтянется из ad_registry
        creative_id=f"cr{g['creative']}",
    )


def resolve_campaign(conn: sqlite3.Connection, placement_id: str) -> str | None:
    row = conn.execute(
        "SELECT campaign_id FROM ad_registry WHERE placement_id = ?", (placement_id,)
    ).fetchone()
    return row["campaign_id"] if row else None


def record_touch(
    conn: sqlite3.Connection,
    telegram_user_id: int | str,
    payload: str | None,
    ts: datetime | None = None,
    touch_type: str = "bot_start",
    is_synthetic: bool = False,
) -> dict:
    """Записывает касание. Единственная точка входа и для бота, и для эмулятора."""
    ts = ts or datetime.now()
    user_hash = hash_user(telegram_user_id)
    p = parse_payload(payload)
    placement_id = p.placement_id if p else None
    campaign_id = resolve_campaign(conn, placement_id) if placement_id else None

    # Неизвестный placement — тревожный сигнал: ссылка гуляет, а размещения
    # в журнале нет. Касание пишем, но помечаем, чтобы это было видно.
    if placement_id and campaign_id is None:
        campaign_id = "UNREGISTERED"

    row = {
        "touch_id": event_id(user_hash, ts.isoformat(), placement_id, touch_type),
        "user_hash": user_hash,
        "placement_id": placement_id,
        "campaign_id": campaign_id,
        "ts": ts.isoformat(timespec="seconds"),
        "touch_type": touch_type,
        "is_synthetic": int(is_synthetic),
    }
    conn.execute(
        "INSERT OR IGNORE INTO touch VALUES (:touch_id, :user_hash, :placement_id,"
        " :campaign_id, :ts, :touch_type, :is_synthetic)",
        row,
    )
    conn.commit()
    return row


def record_lead(
    conn: sqlite3.Connection,
    telegram_user_id: int | str,
    product_interest: str | None = None,
    manager_id: str | None = None,
    source_declared: str | None = None,
    ts: datetime | None = None,
    is_synthetic: bool = False,
) -> dict:
    """Фиксирует начало диалога с менеджером."""
    ts = ts or datetime.now()
    user_hash = hash_user(telegram_user_id)
    row = {
        "lead_id": event_id(user_hash, ts.isoformat(), "lead"),
        "user_hash": user_hash,
        "ts_first_message": ts.isoformat(timespec="seconds"),
        "product_interest": product_interest,
        "manager_id": manager_id,
        "source_declared": source_declared,
        "is_synthetic": int(is_synthetic),
    }
    conn.execute(
        "INSERT OR IGNORE INTO lead VALUES (:lead_id, :user_hash, :ts_first_message,"
        " :product_interest, :manager_id, :source_declared, :is_synthetic)",
        row,
    )
    conn.commit()
    return row


def record_payment(
    conn: sqlite3.Connection,
    telegram_user_id: int | str,
    amount: float,
    course: str | None = None,
    ts: datetime | None = None,
    is_synthetic: bool = False,
) -> dict:
    ts = ts or datetime.now()
    user_hash = hash_user(telegram_user_id)
    row = {
        "payment_id": event_id(user_hash, ts.isoformat(), course, amount),
        "user_hash": user_hash,
        "ts": ts.isoformat(timespec="seconds"),
        "course": course,
        "amount": float(amount),
        "is_synthetic": int(is_synthetic),
    }
    conn.execute(
        "INSERT OR IGNORE INTO payment VALUES (:payment_id, :user_hash, :ts,"
        " :course, :amount, :is_synthetic)",
        row,
    )
    conn.commit()
    return row
