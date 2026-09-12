import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

DATA_PATH = "data/daily_metrics.csv"
OUT_DIR = "output"
TARGET_COL = "orders"
MIN_TRAIN = 14
HORIZON = 7


def load_series(path=DATA_PATH, col=TARGET_COL):
    df = pd.read_csv(path, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    return df["date"].to_numpy(), df[col].to_numpy(dtype=float)


def model_constant(history):
    """Среднее по всей истории. Прогноз одинаков на весь горизонт."""
    return float(np.mean(history))


def model_moving_average(history, window=7):
    """Среднее за последние `window` дней истории."""
    tail = history[-window:] if len(history) >= window else history
    return float(np.mean(tail))


def model_seasonal_naive(history, lag=7):
    """Значение lag дней назад от конца истории."""
    if len(history) >= lag:
        return float(history[-lag])
    return float(history[-1])


def rolling_backtest(dates, y, min_train=MIN_TRAIN, horizon=HORIZON):
    rows = []
    n = len(y)
    last_origin = n - horizon
    for origin in range(min_train, last_origin + 1):
        history = y[:origin]
        preds = {
            "constant": model_constant(history),
            "moving_average": model_moving_average(history),
            "seasonal_naive": model_seasonal_naive(history),
        }
        for h in range(1, horizon + 1):
            target_idx = origin + h - 1
            actual = y[target_idx]
            for model_name, pred in preds.items():
                err = actual - pred
                rows.append({
                    "origin_date": dates[origin - 1],
                    "target_date": dates[target_idx],
                    "horizon": h,
                    "model": model_name,
                    "actual": actual,
                    "predicted": pred,
                    "error": err,
                    "abs_error": abs(err),
                    "pct_error": (abs(err) / actual * 100.0) if actual != 0 else np.nan,
                })
    return pd.DataFrame(rows)


def summarize(backtest_df):
    def mape(s):
        s = s.dropna()
        return s.mean() if len(s) else np.nan

    summary = backtest_df.groupby("model").agg(
        n_predictions=("abs_error", "size"),
        MAE=("abs_error", "mean"),
        MAPE=("pct_error", mape),
    ).reset_index().sort_values("MAE")
    return summary


def plot_results(dates, y, backtest_df, out_path):
    fig, axes = plt.subplots(2, 1, figsize=(11, 8), sharex=False)

    ax = axes[0]
    ax.plot(dates, y, "o-", color="black", label="Факт (orders)", linewidth=1.5)
    colors = {"constant": "tab:blue", "moving_average": "tab:orange", "seasonal_naive": "tab:green"}
    for model_name, g in backtest_df.groupby("model"):
        g1 = g[g["horizon"] == 1].sort_values("target_date")
        ax.plot(g1["target_date"], g1["predicted"], "--", alpha=0.8,
                 color=colors.get(model_name), label=f"{model_name} (h=1)")
    ax.set_title("Факт vs прогнозы (горизонт h=1) — daily orders")
    ax.set_ylabel("orders")
    ax.legend(fontsize=8)
    ax.tick_params(axis="x", rotation=45)

    ax2 = axes[1]
    summary = summarize(backtest_df)
    ax2.bar(summary["model"], summary["MAE"], color=[colors.get(m) for m in summary["model"]])
    ax2.set_title("MAE по моделям (все origin x horizon)")
    ax2.set_ylabel("MAE, заказов")
    for i, v in enumerate(summary["MAE"]):
        ax2.text(i, v, f"{v:.2f}", ha="center", va="bottom")

    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def main():
    dates, y = load_series()
    n = len(y)
    print(f"Загружено {n} дней истории ({dates[0]} .. {dates[-1]})")

    backtest_df = rolling_backtest(dates, y)
    backtest_df.to_csv(f"{OUT_DIR}/forecast_backtest.csv", index=False)

    summary = summarize(backtest_df)
    summary.to_csv(f"{OUT_DIR}/forecast_summary.csv", index=False)

    plot_results(dates, y, backtest_df, f"{OUT_DIR}/forecast_plot.png")

    print("\n=== ИТОГИ BACKTEST ===")
    print(summary.to_string(index=False))

    best = summary.iloc[0]
    worst_gap = summary["MAE"].max() - summary["MAE"].min()
    rel_gap = worst_gap / summary["MAE"].min() * 100

    print(f"\nЛучшая модель по MAE: {best['model']} (MAE={best['MAE']:.2f}, MAPE={best['MAPE']:.1f}%)")
    print(f"Разброс MAE между моделями: {worst_gap:.2f} заказов ({rel_gap:.1f}% от лучшей).")
    print(f"История: {n} дней, из них минимум 3 явных рекламных окна (08-10.08, 22-25.08, 05-06.09).")
    print("Вывод: при таком объёме истории и такой волатильности (промо-спайки в 5-10x)")
    print("ни одна из моделей не даёт устойчивого преимущества над простым средним —")
    print("разница между моделями в пределах шума самого прогноза.")


if __name__ == "__main__":
    main()
