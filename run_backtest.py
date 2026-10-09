"""Backtest entry point.

    python run_backtest.py                 # uses config.yaml
    python run_backtest.py --bars 1000

Outputs:
    results/backtest_stats.json   headline metrics
    results/trades.csv            every fill
    results/equity_curve.png      equity vs buy & hold chart
"""
from __future__ import annotations

import argparse
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import yaml

from src import data
from src.backtest import run_backtest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--bars", type=int, default=1000)
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    print(f"Fetching {args.bars} bars: {cfg['EXCHANGE']} {cfg['SYMBOL']} {cfg['INTERVAL']} ...")
    df = data.fetch_klines(cfg["EXCHANGE"], cfg["SYMBOL"], cfg["INTERVAL"], limit=args.bars)
    print(f"Got {len(df)} bars: {df['open_time'].iloc[0]} -> {df['open_time'].iloc[-1]}")

    res = run_backtest(df, cfg)

    os.makedirs("results", exist_ok=True)
    stats = {k: v for k, v in res.items() if k not in ("equity_curve", "trades")}
    stats.update({
        "symbol": cfg["SYMBOL"], "interval": cfg["INTERVAL"],
        "bars": len(df), "start_equity": 10_000.0,
    })
    with open("results/backtest_stats.json", "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)
    res["trades"].to_csv("results/trades.csv", index=False)

    warm = cfg["STRATEGY"]["ema_slow"] + 2
    bh = res["equity_curve"]["close"] / res["equity_curve"]["close"].iloc[warm] * 10_000
    fig, ax = plt.subplots(figsize=(12, 5))
    res["equity_curve"]["equity"].plot(ax=ax, label="Strategy equity")
    bh.plot(ax=ax, label="Buy & hold", alpha=0.7)
    ax.set_title(f"{cfg['SYMBOL']} {cfg['INTERVAL']} — EMA crossover + ATR stop (paper)")
    ax.set_ylabel("Equity (USDT)")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig("results/equity_curve.png", dpi=120)

    print("\n=== Backtest summary ===")
    for k, v in stats.items():
        print(f"{k:>24}: {v}")


if __name__ == "__main__":
    main()
