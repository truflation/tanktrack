"""Historical backfill from IMF PortWatch — daily Strait of Hormuz transits.

Pulls the last N days (default 120 = ~4 months), writes CSV, renders PNG,
prints a summary with rolling averages and drawdown detection.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from tanktrack.portwatch import chokepoint_transits

RESULTS = Path(__file__).resolve().parents[1] / "results"
RESULTS.mkdir(exist_ok=True)

DARK_BG = "#0a0a0a"
PANEL = "#161616"
TEXT = "#e6e6e6"


def _style() -> None:
    plt.rcParams.update({
        "figure.facecolor": DARK_BG,
        "axes.facecolor": PANEL,
        "savefig.facecolor": DARK_BG,
        "axes.edgecolor": TEXT,
        "axes.labelcolor": TEXT,
        "xtick.color": TEXT,
        "ytick.color": TEXT,
        "text.color": TEXT,
        "axes.titlecolor": TEXT,
        "axes.grid": True,
        "grid.color": "#333",
        "grid.alpha": 0.4,
    })


def _to_dataframe(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # Compose a proper date column
    if {"year", "month", "day"}.issubset(df.columns):
        df["date"] = pd.to_datetime(
            dict(year=df["year"], month=df["month"], day=df["day"])
        )
    df = df.sort_values("date").reset_index(drop=True)
    return df


def _summary(df: pd.DataFrame) -> str:
    total_col = "n_total" if "n_total" in df.columns else None
    if not total_col:
        return "(no n_total column found)"

    series = df[total_col]
    n = len(series)

    def window_avg(end_from_last: int, size: int) -> float:
        """Mean of `size` rows ending `end_from_last` rows from the last row."""
        start = max(0, n - end_from_last - size)
        stop = max(0, n - end_from_last)
        slice_ = series.iloc[start:stop]
        return float(slice_.mean()) if len(slice_) else 0.0

    last_7 = window_avg(0, 7)
    prev_7 = window_avg(7, 7)
    last_30 = window_avg(0, 30)
    first_30 = float(series.head(30).mean()) if n >= 30 else float(series.mean())

    last_total = int(series.tail(7).sum())
    baseline_weekly = float(series.mean() * 7)

    change_pct = (last_7 - first_30) / first_30 * 100 if first_30 > 0 else 0.0

    out = []
    out.append("=" * 70)
    out.append(f"Strait of Hormuz — {len(df)} days of PortWatch data")
    out.append(f"Range: {df['date'].iloc[0].date()} → {df['date'].iloc[-1].date()}")
    out.append("=" * 70)
    out.append(f"  avg daily transits, first 30d of window:  {first_30:>7.1f}")
    out.append(f"  avg daily transits, last 30d of window:   {last_30:>7.1f}")
    out.append(f"  avg daily transits, last 7d:              {last_7:>7.1f}")
    out.append(f"  prev 7d (days -14 to -7):                 {prev_7:>7.1f}")
    out.append(f"  total transits last 7d:                   {last_total}")
    out.append(f"  full-window avg × 7 (baseline week):      {baseline_weekly:>7.1f}")
    out.append(f"  window-over-window change (first30 → last7): {change_pct:+.1f}%")

    # Obvious drop detection
    rolling7 = df[total_col].rolling(7).mean()
    if len(rolling7.dropna()) >= 30:
        peak = rolling7.max()
        trough = rolling7.iloc[-7:].min()
        if peak > 0 and trough < 0.5 * peak:
            out.append("")
            out.append(f"  ⚠ last-week rolling avg is {(1 - trough/peak)*100:.0f}% below the window peak")
    return "\n".join(out)


def _plot(df: pd.DataFrame, out_path: Path) -> None:
    _style()
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 7), dpi=140, sharex=True)

    # Top: total transits + 7-day rolling mean
    if "n_total" in df.columns:
        ax1.bar(df["date"], df["n_total"], color="#4a90e2", alpha=0.55,
                label="daily transits")
        ax1.plot(df["date"], df["n_total"].rolling(7).mean(),
                 color="#f39c12", linewidth=2, label="7-day rolling mean")
        ax1.set_title("Strait of Hormuz — daily vessel transits (IMF PortWatch)",
                      fontsize=13, pad=12)
        ax1.set_ylabel("vessels / day")
        ax1.legend(facecolor=PANEL, edgecolor=TEXT)

    # Bottom: vessel-type breakdown
    type_cols = ["n_tanker", "n_cargo", "n_container", "n_dry_bulk", "n_general_cargo", "n_roro"]
    have_cols = [c for c in type_cols if c in df.columns]
    if have_cols:
        for c in have_cols:
            if df[c].sum() > 0:
                ax2.plot(df["date"], df[c].rolling(7).mean(),
                         linewidth=1.8, label=c.replace("n_", ""))
        ax2.set_title("by vessel type (7-day rolling mean)", fontsize=11, pad=8)
        ax2.set_ylabel("vessels / day")
        ax2.legend(facecolor=PANEL, edgecolor=TEXT, ncol=3, fontsize=9)

    for a in (ax1, ax2):
        a.tick_params(axis="x", rotation=0)

    plt.tight_layout()
    plt.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=120, help="backfill window in days")
    parser.add_argument("--chokepoint", default="Strait of Hormuz")
    args = parser.parse_args()

    print(f"pulling {args.days} days of '{args.chokepoint}' from IMF PortWatch...")
    rows = await chokepoint_transits(args.chokepoint, days=args.days)
    print(f"received {len(rows)} rows")

    df = _to_dataframe(rows)
    if df.empty:
        print("no data returned")
        return

    name_slug = args.chokepoint.lower().replace(" ", "_")
    csv_path = RESULTS / f"portwatch_{name_slug}_{args.days}d.csv"
    png_path = RESULTS / f"portwatch_{name_slug}_{args.days}d.png"

    df.to_csv(csv_path, index=False)
    _plot(df, png_path)

    print(f"\nCSV: {csv_path}")
    print(f"PNG: {png_path}")
    print()
    print(_summary(df))


if __name__ == "__main__":
    asyncio.run(main())
