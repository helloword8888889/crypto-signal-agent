"""Event-style backtester with next-bar execution (no look-ahead).

Entries/exits are decided at bar close and filled at the *next* bar's open.
Stops are checked intrabar against high/low. Fees apply on every fill.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import strategy


class PaperAccount:
    """Minimal paper-trading account shared by backtester and live agent."""

    def __init__(self, equity: float = 10_000.0, fee_rate: float = 0.0005):
        self.equity = equity
        self.fee_rate = fee_rate
        self.position = 0          # +1 long, -1 short, 0 flat
        self.entry_price = 0.0
        self.extreme = 0.0         # best price since entry (for trailing stop)
        self.qty = 0.0
        self.trades: list[dict] = []

    def open(self, direction: int, price: float, size_frac: float, when, reason: str):
        notional = self.equity * size_frac
        self.qty = notional / price
        fee = notional * self.fee_rate
        self.equity -= fee
        self.position = direction
        self.entry_price = price
        self.extreme = price
        self.trades.append({
            "time": when, "side": "OPEN_LONG" if direction > 0 else "OPEN_SHORT",
            "price": price, "qty": self.qty, "fee": fee, "reason": reason,
            "realized_pnl": 0.0, "equity_after": self.equity,
        })

    def close(self, price: float, when, reason: str) -> float:
        if self.position == 0:
            return 0.0
        pnl = (price - self.entry_price) * self.qty * self.position
        fee = price * self.qty * self.fee_rate
        self.equity += pnl - fee
        self.trades.append({
            "time": when,
            "side": "CLOSE_LONG" if self.position > 0 else "CLOSE_SHORT",
            "price": price, "qty": self.qty, "fee": fee, "reason": reason,
            "realized_pnl": pnl, "equity_after": self.equity,
        })
        self.position = 0
        self.qty = 0.0
        return pnl

    def mark(self, bar: pd.Series):
        """Update the trailing-stop extreme with the latest bar."""
        if self.position > 0:
            self.extreme = max(self.extreme, bar["high"])
        elif self.position < 0:
            self.extreme = min(self.extreme, bar["low"])


def run_backtest(df: pd.DataFrame, cfg: dict, start_equity: float = 10_000.0) -> dict:
    strat_cfg = cfg["STRATEGY"]
    risk = cfg["RISK"]
    data = strategy.add_indicators(df, strat_cfg)
    cross = strategy.crosses(data, strat_cfg)

    acct = PaperAccount(start_equity, risk["fee_rate"])
    equity_curve = []
    pending: int | None = None   # position to open at next bar open
    warmup = max(strat_cfg["ema_slow"], strat_cfg["atr_period"]) + 2

    for i in range(len(data)):
        bar = data.iloc[i]
        when = bar["open_time"]

        # Execute pending entry at this bar's open.
        if pending is not None and i >= warmup:
            acct.open(pending, bar["open"], risk["position_size"], when,
                      "EMA cross entry")
            pending = None

        # Trailing-stop exit check (intrabar).
        if acct.position != 0:
            stop = strategy.trailing_stop(acct.position, acct.extreme,
                                          bar["atr"], strat_cfg)
            hit = (acct.position > 0 and bar["low"] <= stop) or \
                  (acct.position < 0 and bar["high"] >= stop)
            if hit:
                acct.close(stop, when, "ATR trailing stop")
            acct.mark(bar)

        # Crossover logic evaluated at close.
        sig = int(cross.iloc[i]) if i >= warmup else 0
        if sig != 0 and strategy.signal_allowed(sig, bar, strat_cfg):
            if acct.position == -sig:
                acct.close(bar["close"], when,
                           "Opposite EMA cross (signal flip)")
            if acct.position == 0 and i + 1 < len(data):
                pending = sig

        equity_curve.append({"time": when, "equity": acct.equity,
                             "close": bar["close"]})

    if acct.position != 0:
        last = data.iloc[-1]
        acct.close(last["close"], last["open_time"], "End of backtest")

    curve = pd.DataFrame(equity_curve).set_index("time")
    trades = pd.DataFrame(acct.trades)
    closes = trades[trades["side"].str.startswith("CLOSE")] if not trades.empty else trades

    total_return = (acct.equity / start_equity - 1) * 100
    bh_return = (data["close"].iloc[-1] / data["close"].iloc[warmup] - 1) * 100
    if not closes.empty:
        wins = (closes["realized_pnl"] > 0).sum()
        win_rate = wins / len(closes) * 100
        gross_profit = closes.loc[closes["realized_pnl"] > 0, "realized_pnl"].sum()
        gross_loss = -closes.loc[closes["realized_pnl"] < 0, "realized_pnl"].sum()
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    else:
        win_rate, profit_factor = 0.0, 0.0

    eq = curve["equity"]
    drawdown = (eq / eq.cummax() - 1).min() * 100
    rets = eq.pct_change().dropna()
    bars_per_year = {"15m": 35040, "30m": 17520, "1h": 8760, "4h": 2190, "1d": 365}
    bpy = bars_per_year.get(str(cfg.get("INTERVAL", "4h")), 2190)
    sharpe = (rets.mean() / rets.std() * np.sqrt(bpy)) if rets.std() > 0 else 0.0

    return {
        "final_equity": acct.equity,
        "total_return_pct": total_return,
        "buy_hold_return_pct": bh_return,
        "num_trades": int(len(closes)),
        "win_rate_pct": float(win_rate),
        "profit_factor": float(profit_factor),
        "max_drawdown_pct": float(drawdown),
        "sharpe": float(sharpe),
        "equity_curve": curve,
        "trades": trades,
    }
