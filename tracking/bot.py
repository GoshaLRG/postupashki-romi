"""
Telegram-бот, который логирует касания и лиды.

Запуск:
    export BOT_TOKEN=...
    export TRACKING_SALT=...
    python tracking/bot.py

Это тонкая обёртка: вся логика записи — в core.py. Бот только достаёт
из апдейта user_id и payload и передаёт их дальше. Поэтому эмулятор
(simulate.py) гоняет ровно тот же код записи.

Что логируется:
  /start <placement_id>  -> touch (bot_start) с меткой размещения
  /start без метки       -> touch с placement_id = NULL (органика)
  /lead <курс>           -> lead, команда для менеджера
  кнопка "Написать менеджеру" -> lead с зафиксированным источником
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import connect, record_lead, record_touch  # noqa: E402

try:
    from aiogram import Bot, Dispatcher, F
    from aiogram.filters import CommandObject, CommandStart
    from aiogram.types import (
        InlineKeyboardButton,
        InlineKeyboardMarkup,
        CallbackQuery,
        Message,
    )
except ImportError:  # pragma: no cover
    print(
        "Не установлен aiogram. Поставьте: pip install aiogram\n"
        "Проверить логику без токена и без сети: python tracking/simulate.py",
        file=sys.stderr,
    )
    raise SystemExit(1)

TOKEN = os.environ.get("BOT_TOKEN")
MANAGER_URL = os.environ.get("MANAGER_URL", "https://t.me/postupashki_manager")

logging.basicConfig(level=logging.INFO)
dp = Dispatcher()
conn = connect()

WELCOME = (
    "Привет! Это бот Поступашек.\n\n"
    "Здесь можно посмотреть курсы и написать менеджеру — он подберёт "
    "программу и расскажет про текущие условия."
)

kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="Написать менеджеру", callback_data="lead")],
        [InlineKeyboardButton(text="Посмотреть курсы", callback_data="courses")],
    ]
)


@dp.message(CommandStart())
async def on_start(message: Message, command: CommandObject) -> None:
    """Точка входа с рекламы: payload несёт placement_id."""
    row = record_touch(
        conn,
        telegram_user_id=message.from_user.id,
        payload=command.args,
        touch_type="bot_start",
    )
    logging.info(
        "touch: placement=%s campaign=%s", row["placement_id"], row["campaign_id"]
    )
    await message.answer(WELCOME, reply_markup=kb)


@dp.callback_query(F.data == "lead")
async def on_lead(call: CallbackQuery) -> None:
    """Нажатие кнопки = намерение писать менеджеру. Фиксируем лид."""
    record_lead(conn, telegram_user_id=call.from_user.id, manager_id="bot_button")
    await call.message.answer(f"Менеджер здесь: {MANAGER_URL}")
    await call.answer()


@dp.callback_query(F.data == "courses")
async def on_courses(call: CallbackQuery) -> None:
    record_touch(
        conn,
        telegram_user_id=call.from_user.id,
        payload=None,
        touch_type="catalog_view",
    )
    await call.message.answer("Каталог курсов: ...")
    await call.answer()


@dp.message(lambda m: m.text and m.text.startswith("/lead"))
async def manual_lead(message: Message) -> None:
    """Команда для менеджера: /lead Аналитика про"""
    interest = message.text.removeprefix("/lead").strip() or None
    record_lead(
        conn,
        telegram_user_id=message.from_user.id,
        product_interest=interest,
        manager_id=str(message.from_user.id),
    )
    await message.answer(f"Лид зафиксирован: {interest or 'без указания курса'}")


async def main() -> None:
    if not TOKEN:
        raise SystemExit("Не задан BOT_TOKEN")
    await dp.start_polling(Bot(TOKEN))


if __name__ == "__main__":
    asyncio.run(main())
