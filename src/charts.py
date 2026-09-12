"""Графики для презентации. Сохраняются в output/charts/."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

PALETTE = {
    "sale": "#d1495b",
    "product_launch": "#2a9d8f",
    "traffic_spike": "#e9c46a",
    "targeted_promo": "#9d8189",
}


def _style(ax, title, ylabel):
    ax.set_title(title, fontsize=13, loc="left", pad=12)
    ax.set_ylabel(ylabel, fontsize=10)
    ax.grid(alpha=0.25, linewidth=0.6)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))


def orders_with_events(daily, calendar, out: Path):
    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax.plot(daily["date"], daily["orders"], color="#264653", lw=1.8, label="заказы, факт")
    ax.plot(
        daily["date"],
        daily["orders_baseline"],
        color="#8d99ae",
        lw=1.4,
        ls="--",
        label="baseline (медиана 14 дней)",
    )
    seen = set()
    for _, e in calendar.iterrows():
        if e["type"] == "product_launch" and e["days"] == 1 and e["orders"] != e["orders"]:
            continue
        c = PALETTE.get(e["type"], "#aaaaaa")
        ax.axvspan(
            e["date_start"],
            e["date_end"],
            color=c,
            alpha=0.18,
            label=e["type"] if e["type"] not in seen else None,
        )
        seen.add(e["type"])
    _style(ax, "Заказы по дням и восстановленные маркетинговые окна", "заказов в день")
    ax.legend(frameon=False, fontsize=9, ncol=2, loc="upper right")
    fig.tight_layout()
    fig.savefig(out / "01_orders_events.png", dpi=160)
    plt.close(fig)


def discount_index(daily, out: Path):
    fig, ax = plt.subplots(figsize=(11, 3.6))
    ax.fill_between(daily["date"], daily["discount_depth"], color="#d1495b", alpha=0.35)
    ax.plot(daily["date"], daily["discount_depth"], color="#d1495b", lw=1.6)
    _style(ax, "Глубина скидки: медианное отклонение цены от прайса", "доля скидки")
    fig.tight_layout()
    fig.savefig(out / "02_discount_index.png", dpi=160)
    plt.close(fig)


def revenue_by_course(lines, out: Path):
    s = lines.groupby("course")["amount"].sum().sort_values()
    fig, ax = plt.subplots(figsize=(8, 5.5))
    ax.barh(s.index, s.values / 1000, color="#2a9d8f")
    ax.set_title("Выручка по курсам, тыс. руб.", fontsize=13, loc="left", pad=12)
    ax.grid(axis="x", alpha=0.25, linewidth=0.6)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    fig.tight_layout()
    fig.savefig(out / "03_revenue_by_course.png", dpi=160)
    plt.close(fig)


def make_all(daily, calendar, lines, out_dir: str | Path):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    orders_with_events(daily, calendar, out)
    discount_index(daily, out)
    revenue_by_course(lines, out)
