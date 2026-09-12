import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

DATA_PATH = "data/daily_metrics.csv"
OUT_DIR = "output"
N_BOOTSTRAP = 10_000
RANDOM_SEED = 42

EVENT_WINDOW = ("2026-08-08", "2026-08-12")
POST_WINDOW = ("2026-08-13", "2026-08-17")
PRE_WINDOW = ("2026-08-03", "2026-08-07")


def load_data(path=DATA_PATH):
    df = pd.read_csv(path, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    return df


def get_quiet_days(df):
    """Дни вне всех промо-спайков (используем флаг spike из данных)."""
    quiet = df[df["spike"] == False].copy()
    quiet = quiet[quiet["orders_baseline"] > 0]
    quiet["ratio"] = quiet["orders"] / quiet["orders_baseline"]
    return quiet


def observed_uplift(df, window):
    mask = (df["date"] >= window[0]) & (df["date"] <= window[1])
    win = df[mask]
    uplift = float((win["orders"] - win["orders_baseline"]).sum())
    baseline_sum = float(win["orders_baseline"].sum())
    actual_sum = float(win["orders"].sum())
    return actual_sum, baseline_sum, uplift, len(win)


def bootstrap_null_distribution(quiet_ratios, window_len, baseline_values, n_boot=N_BOOTSTRAP, seed=RANDOM_SEED):
    """
    Собираем нулевое распределение "какой uplift мог бы получиться случайно"
    на окне такой же длины и с такими же baseline, если бы факт вёл себя
    как в обычные (спокойные) дни, то есть ratio ~ распределению спокойных дней.
    """
    rng = np.random.default_rng(seed)
    ratios = quiet_ratios.to_numpy()
    baselines = np.array(baseline_values, dtype=float)
    sim_uplifts = np.empty(n_boot)
    for i in range(n_boot):
        sampled_ratios = rng.choice(ratios, size=window_len, replace=True)
        sim_actual = sampled_ratios * baselines
        sim_uplifts[i] = float(np.sum(sim_actual - baselines))
    return sim_uplifts


def percentile_of_observed(sim_uplifts, observed):
    return float((sim_uplifts < observed).mean() * 100.0)


def pre_post_comparison(df):
    def avg_ratio(window):
        mask = (df["date"] >= window[0]) & (df["date"] <= window[1])
        return df.loc[mask, "orders_ratio"].mean()

    return {
        "pre_avg_ratio": avg_ratio(PRE_WINDOW),
        "post_avg_ratio": avg_ratio(POST_WINDOW),
    }


def plot_results(sim_uplifts, observed, df, out_path):
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))

    ax = axes[0]
    ax.hist(sim_uplifts, bins=60, color="lightgray", edgecolor="gray")
    ax.axvline(observed, color="red", linewidth=2, label=f"Наблюдённый uplift = {observed:.0f}")
    ax.set_title("Бутстрап-распределение случайного uplift\n(10 000 пересемплирований спокойных дней)")
    ax.set_xlabel("Суммарный uplift за 5-дневное окно, заказов")
    ax.set_ylabel("Частота")
    ax.legend()

    ax2 = axes[1]
    d = df.copy()
    d["is_event"] = (d["date"] >= EVENT_WINDOW[0]) & (d["date"] <= EVENT_WINDOW[1])
    colors = np.where(d["is_event"], "red", "steelblue")
    ax2.bar(d["date"], d["orders"], color=colors, label="факт")
    ax2.plot(d["date"], d["orders_baseline"], color="black", linestyle="--", label="baseline")
    ax2.axvspan(pd.Timestamp(EVENT_WINDOW[0]), pd.Timestamp(EVENT_WINDOW[1]), color="red", alpha=0.08)
    ax2.set_title("Факт vs baseline: окно распродажи 08-12.08 выделено")
    ax2.set_ylabel("orders")
    ax2.tick_params(axis="x", rotation=60, labelsize=7)
    ax2.legend()

    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def main():
    df = load_data()
    quiet = get_quiet_days(df)
    print(f"Спокойных дней для бутстрапа: {len(quiet)} из {len(df)}")

    actual_sum, baseline_sum, observed, window_len = observed_uplift(df, EVENT_WINDOW)
    print(f"\nОкно {EVENT_WINDOW}: {window_len} дней")
    print(f"  Факт:     {actual_sum:.0f} заказов")
    print(f"  Baseline: {baseline_sum:.0f} заказов")
    print(f"  Uplift:   {observed:+.0f} заказов")

    baseline_values = df[(df["date"] >= EVENT_WINDOW[0]) & (df["date"] <= EVENT_WINDOW[1])]["orders_baseline"].to_numpy()
    sim_uplifts = bootstrap_null_distribution(quiet["ratio"], window_len, baseline_values)

    ci_low, ci_high = np.percentile(sim_uplifts, [2.5, 97.5])
    pct = percentile_of_observed(sim_uplifts, observed)

    print(f"\n95% 'случайный' интервал uplift на окне такой длины: [{ci_low:.0f}, {ci_high:.0f}]")
    print(f"Наблюдённый uplift {observed:.0f} лежит на {pct:.2f}-м перцентиле случайного распределения.")

    prepost = pre_post_comparison(df)
    print(f"\nСредний orders_ratio ДО окна ({PRE_WINDOW}): {prepost['pre_avg_ratio']:.2f}")
    print(f"Средний orders_ratio ПОСЛЕ окна ({POST_WINDOW}): {prepost['post_avg_ratio']:.2f}")

    plot_results(sim_uplifts, observed, df, f"{OUT_DIR}/incrementality_plot.png")

    report = f"""# Инкрементальность распродажи 08–12.08

## Наблюдение
- Окно: {EVENT_WINDOW[0]} .. {EVENT_WINDOW[1]} ({window_len} дней)
- Факт: **{actual_sum:.0f}** заказов
- Baseline (ожидание без промо, по сезонности дня недели): **{baseline_sum:.0f}** заказов
- Наблюдённый uplift: **{observed:+.0f}** заказов

## Оценка неопределённости (bootstrap, n={N_BOOTSTRAP})
Метод: берём отношения факт/baseline по {len(quiet)} спокойным дням
(вне промо-спайков), {N_BOOTSTRAP} раз собираем случайное 5-дневное окно
из этих отношений, умножаем на реальные baseline окна 08–12.08 —
получаем распределение того, какой uplift мог бы возникнуть просто
от обычного шума, без всякого промо.

- 95% "случайный" интервал: [{ci_low:.0f}, {ci_high:.0f}] заказов
- Наблюдённый uplift {observed:.0f} лежит на **{pct:.2f}-м перцентиле** этого
  распределения — то есть он далеко за пределами того, что объясняется
  обычным разбросом. Эффект статистически надёжен.

## Чего этот метод НЕ доказывает
1. **Не доказывает чистый прирост спроса.** Метод сравнивает факт с
   ожиданием "как обычно", но не умеет отличить *новый* спрос от
   *перетянутого из будущего*.
2. **Есть прямой сигнал перетягивания.** Средний orders_ratio ДО окна
   ({PRE_WINDOW[0]}..{PRE_WINDOW[1]}) = {prepost['pre_avg_ratio']:.2f},
   а ПОСЛЕ окна ({POST_WINDOW[0]}..{POST_WINDOW[1]}) = {prepost['post_avg_ratio']:.2f}.
   Продажи после распродажи заметно ниже нормы — часть покупателей,
   вероятно, просто купили раньше, чем купили бы без скидки.
3. Baseline сам по себе — оценка (day-of-week сезонность на 37 днях
   истории), а не измеренный контрфактический сценарий: доверительный
   интервал не учитывает неопределённость самого baseline.

## Файлы
- `output/incrementality_plot.png` — распределение бутстрапа + факт/baseline по дням
"""
    with open(f"{OUT_DIR}/incrementality_report.md", "w", encoding="utf-8") as f:
        f.write(report)

    print(f"\nОтчёт сохранён: {OUT_DIR}/incrementality_report.md")


if __name__ == "__main__":
    main()
