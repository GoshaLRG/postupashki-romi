"""
Операционный календарь: шаблон для ручного логирования кампаний.

Запуск: python3 tools/make_ad_registry.py
Выход:  tools/operational_calendar.xlsx

Сейчас запуски, скидки и размещения нигде не фиксируются, и через месяц
никто не помнит, что было 5 сентября. Инструмент закрывает это без
разработки: менеджер заполняет пять полей, файл сам собирает
placement_id, диплинк и помечает размещения, по которым ROMI посчитать
нельзя.
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

OUT = Path(__file__).resolve().parent / "operational_calendar.xlsx"

FONT = "Arial"
INK = "1E2761"
HEAD_FILL = PatternFill("solid", fgColor="1E2761")
INPUT_FILL = PatternFill("solid", fgColor="FFF7D6")   # жёлтый — заполнять руками
AUTO_FILL = PatternFill("solid", fgColor="EEF3F5")    # серый — считается само
ALERT_FILL = PatternFill("solid", fgColor="FBD5DA")
THIN = Side(style="thin", color="C9D2D8")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

BOT = "PostupashkiBot"

ADS_COLS = [
    ("Канал (id)", 12, "c01", "input"),
    ("Название канала", 22, "Физтех лайф", "input"),
    ("Кампания", 16, "launch_sep", "input"),
    ("№ размещения", 14, 1, "input"),
    ("№ креатива", 12, 1, "input"),
    ("Формат", 20, "пост", "input"),
    ("Дата и время публикации", 24, "2026-09-04 12:00", "input"),
    ("Стоимость, руб.", 16, 18000, "input"),
    ("placement_id", 20, None, "auto"),
    ("Ссылка для рекламного поста", 42, None, "auto"),
    ("Статус", 16, None, "auto"),
]

POSTS_COLS = [
    ("Дата и время", 22, "2026-09-05 10:00", "input"),
    ("Тип публикации", 20, "sale", "input"),
    ("Заголовок / о чём", 38, "Старт осеннего потока", "input"),
    ("Скидка, %", 12, 30, "input"),
    ("Кампания", 18, "launch_sep", "input"),
    ("Запуск нового курса", 20, "нет", "input"),
]

FORMATS = '"пост,репост,нативная интеграция,кружок,сторис,подборка"'
POST_TYPES = '"sale,discount,native,content,launch"'
YESNO = '"да,нет"'

N_ROWS = 60


def header(ws, cols, row: int = 1) -> None:
    for i, (title, width, _, _) in enumerate(cols, start=1):
        c = ws.cell(row=row, column=i, value=title)
        c.font = Font(name=FONT, size=10, bold=True, color="FFFFFF")
        c.fill = HEAD_FILL
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = BORDER
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.row_dimensions[row].height = 32
    ws.freeze_panes = ws.cell(row=row + 1, column=1)


def body_style(ws, cols, first: int, last: int) -> None:
    for r in range(first, last + 1):
        for i, (_, _, _, kind) in enumerate(cols, start=1):
            c = ws.cell(row=r, column=i)
            c.font = Font(name=FONT, size=10)
            c.border = BORDER
            c.fill = INPUT_FILL if kind == "input" else AUTO_FILL


def build_ads(ws) -> None:
    header(ws, ADS_COLS)
    body_style(ws, ADS_COLS, 2, N_ROWS)

    # пример заполнения — показывает ожидаемый формат каждого поля
    for i, (_, _, example, kind) in enumerate(ADS_COLS, start=1):
        if kind == "input":
            ws.cell(row=2, column=i, value=example)

    for r in range(2, N_ROWS + 1):
        ws.cell(
            row=r,
            column=9,
            value=(
                f'=IF($A{r}="","",'
                f'$A{r}&"_p"&TEXT($D{r},"00")&"_cr"&$E{r})'
            ),
        )
        ws.cell(
            row=r,
            column=10,
            value=f'=IF($I{r}="","","https://t.me/{BOT}?start="&$I{r})',
        )
        ws.cell(
            row=r,
            column=11,
            value=(
                f'=IF($A{r}="","",'
                f'IF(OR($G{r}="",$H{r}=""),"НЕИЗМЕРИМО","готово"))'
            ),
        )

    ws.add_data_validation(_dv(FORMATS, f"F2:F{N_ROWS}"))
    ws.add_data_validation(
        _dv(
            None,
            f"H2:H{N_ROWS}",
            kind="decimal",
            formula1="0",
            title="Стоимость размещения",
            prompt="Сколько заплатили за этот слот, в рублях. "
            "Без этого поля ROMI не считается.",
        )
    )

    ws.conditional_formatting.add(
        f"K2:K{N_ROWS}",
        FormulaRule(formula=[f'$K2="НЕИЗМЕРИМО"'], fill=ALERT_FILL, font=Font(name=FONT, size=10, bold=True, color="8B1A2B")),
    )

    ws.cell(row=N_ROWS + 2, column=1, value=(
        "Жёлтые ячейки заполняются руками, серые считаются автоматически. "
        "Строка 2 — пример, её можно перезаписать."
    )).font = Font(name=FONT, size=9, italic=True, color="6B7A86")


def build_posts(ws) -> None:
    header(ws, POSTS_COLS)
    body_style(ws, POSTS_COLS, 2, N_ROWS)
    for i, (_, _, example, kind) in enumerate(POSTS_COLS, start=1):
        if kind == "input":
            ws.cell(row=2, column=i, value=example)

    ws.add_data_validation(_dv(POST_TYPES, f"B2:B{N_ROWS}"))
    ws.add_data_validation(_dv(YESNO, f"F2:F{N_ROWS}"))

    ws.cell(row=N_ROWS + 2, column=1, value=(
        "sale — распродажа · discount — точечная скидка · native — нативный контент · "
        "content — обучающий пост · launch — запуск курса"
    )).font = Font(name=FONT, size=9, italic=True, color="6B7A86")


def _dv(options, ref, kind="list", formula1=None, title=None, prompt=None) -> DataValidation:
    dv = DataValidation(
        type=kind,
        formula1=formula1 if formula1 is not None else options,
        operator="greaterThanOrEqual" if kind == "decimal" else None,
        allow_blank=True,
        showDropDown=False,
    )
    if title:
        dv.promptTitle, dv.prompt, dv.showInputMessage = title, prompt, True
    dv.add(ref)
    return dv


def build_readme(ws) -> None:
    ws.column_dimensions["A"].width = 100
    lines = [
        ("Операционный календарь Поступашек", True, 14),
        ("", False, 10),
        ("Зачем это нужно", True, 11),
        ("Сейчас запуски, скидки и рекламные размещения нигде не фиксируются.", False, 10),
        ("Через месяц никто не помнит, что было 5 сентября — а именно в этот день", False, 10),
        ("продажи выросли в 2,6 раза. Восстановить источник задним числом невозможно.", False, 10),
        ("", False, 10),
        ("Лист «Размещения» — внешняя реклама", True, 11),
        ("Заполняется ДО публикации. Пять полей: канал, кампания, номер размещения,", False, 10),
        ("номер креатива, формат. Плюс два обязательных: время публикации и стоимость.", False, 10),
        ("Файл сам соберёт placement_id и ссылку — её и отдаём в рекламный пост.", False, 10),
        ("", False, 10),
        ("Правило: без времени публикации и стоимости размещение помечается", True, 11),
        ("«НЕИЗМЕРИМО». ROMI по нему не считается. Это не придирка: без этих", False, 10),
        ("двух чисел формула ROMI не имеет знаменателя.", False, 10),
        ("", False, 10),
        ("Лист «Публикации» — свой канал", True, 11),
        ("Каждый продающий пост, скидка, нативный материал и запуск. Ровно это", False, 10),
        ("позволяет отличить «продажи выросли из-за рекламы» от «выросли из-за", False, 10),
        ("своего поста со скидкой».", False, 10),
        ("", False, 10),
        ("Куда дальше", True, 11),
        ("Файл — временное решение на первый месяц. Дальше его содержимое", False, 10),
        ("переезжает в таблицу ad_registry системы трекинга: структура полей", False, 10),
        ("совпадает один в один, переносится выгрузкой CSV.", False, 10),
    ]
    for i, (text, bold, size) in enumerate(lines, start=1):
        c = ws.cell(row=i, column=1, value=text)
        c.font = Font(name=FONT, size=size, bold=bold, color=INK if bold else "1D2B36")


def main() -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Инструкция"
    build_readme(ws)
    build_ads(wb.create_sheet("Размещения"))
    build_posts(wb.create_sheet("Публикации"))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT)
    print(f"Шаблон сохранён: {OUT}")


if __name__ == "__main__":
    main()
