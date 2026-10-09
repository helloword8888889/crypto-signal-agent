"""Trend-following strategy: EMA crossover + RSI filter + ATR trailing stop.

Long signal : EMA(fast) crosses above EMA(slow) and RSI below overbought.
Short signal: EMA(fast) crosses below EMA(slow) and RSI above oversold.
Exit       : opposite crossover, or price hits the ATR trailing stop.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def add_indicators(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    out = df.copy()
    close = out["close"]
    out["ema_fast"] = close.ewm(span=cfg["ema_fast"], adjust=False).mean()
    out["ema_slow"] = close.ewm(span=cfg["ema_slow"], adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / cfg["rsi_period"], adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / cfg["rsi_period"], adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out["rsi"] = (100 - 100 / (1 + rs)).fillna(50)

    prev_close = close.shift(1)
    tr = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr"] = tr.ewm(alpha=1 / cfg["atr_period"], adjust=False).mean()
    return out


def desired_position(row: pd.Series, cfg: dict) -> int:
    """Target position sign from the trend state alone: +1 long, -1 short, 0 flat."""
    if pd.isna(row["ema_slow"]) or pd.isna(row["atr"]):
        return 0
    if row["ema_fast"] > row["ema_slow"]:
        return 1
    if row["ema_fast"] < row["ema_slow"]:
        return -1
    return 0


def crosses(df: pd.DataFrame, cfg: dict) -> pd.Series:
    """+1 on bullish cross, -1 on bearish cross, else 0 (per bar)."""
    diff = df["ema_fast"] - df["ema_slow"]
    prev = diff.shift(1)
    up = (diff > 0) & (prev <= 0)
    down = (diff < 0) & (prev >= 0)
    out = pd.Series(0, index=df.index)
    out[up] = 1
    out[down] = -1
    return out


def signal_allowed(direction: int, row: pd.Series, cfg: dict) -> bool:
    """RSI gate: avoid chasing extremes."""
    if direction > 0:
        return row["rsi"] < cfg["rsi_overbought"]
    if direction < 0:
        return row["rsi"] > cfg["rsi_oversold"]
    return False


def trailing_stop(direction: int, extreme: float, atr: float, cfg: dict) -> float:
    """Trailing stop price given the best price seen since entry."""
    if direction > 0:
        return extreme - cfg["atr_stop_mult"] * atr
    return extreme + cfg["atr_stop_mult"] * atr
