"""Live paper-trading agent.

Polls the exchange for the latest closed candle, evaluates the EMA/RSI/ATR
strategy, flips a simulated position when the trend changes, checks the ATR
trailing stop, and notifies (console / log / Telegram) on every event.

State persists to JSON so restarts resume with the open position intact.
No real orders are ever placed; swap PaperAccount for a signed private-API
client to go live.

Usage:
    python agent.py --once        # single evaluation cycle (demo / cron)
    python agent.py               # continuous loop (config AGENT.poll_seconds)
"""
from __future__ import annotations

import argparse
import json
import os
import time

import yaml

from src import data, notify, strategy
from src.backtest import PaperAccount


def load_config(path: str = "config.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    return cfg


def save_state(path: str, acct: PaperAccount, last_bar_time: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    state = {
        "equity": acct.equity,
        "position": acct.position,
        "entry_price": acct.entry_price,
        "extreme": acct.extreme,
        "qty": acct.qty,
        "last_bar_time": last_bar_time,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def load_state(path: str, fee_rate: float) -> PaperAccount:
    acct = PaperAccount(10_000.0, fee_rate)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            s = json.load(f)
        acct.equity = s["equity"]
        acct.position = s["position"]
        acct.entry_price = s["entry_price"]
        acct.extreme = s["extreme"]
        acct.qty = s["qty"]
        acct._last_bar_time = s.get("last_bar_time", "")
    else:
        acct._last_bar_time = ""
    return acct


def run_cycle(cfg: dict, acct: PaperAccount) -> PaperAccount:
    strat = cfg["STRATEGY"]
    risk = cfg["RISK"]
    log_file = cfg["AGENT"]["log_file"]
    state_file = cfg["AGENT"]["state_file"]

    df = data.fetch_klines(cfg["EXCHANGE"], cfg["SYMBOL"], cfg["INTERVAL"], limit=200)
    df = strategy.add_indicators(df, strat)
    bar = data.latest_closed_bar(df, cfg["INTERVAL"])
    row = df[df["open_time"] == bar["open_time"]].iloc[-1]
    bar_time = str(bar["open_time"])

    if bar_time == getattr(acct, "_last_bar_time", ""):
        return acct  # already processed this bar

    price = float(bar["close"])
    target = strategy.desired_position(row, strat)
    stop = None
    if acct.position != 0:
        acct.mark(bar)
        stop = strategy.trailing_stop(acct.position, acct.extreme,
                                      float(row["atr"]), strat)
        stop_hit = (acct.position > 0 and float(bar["low"]) <= stop) or \
                   (acct.position < 0 and float(bar["high"]) >= stop)
        if stop_hit:
            acct.close(stop, bar_time, "ATR trailing stop")
            notify.send(
                f"STOP HIT {cfg['SYMBOL']} {cfg['INTERVAL']} @ {stop:,.2f} | "
                f"equity ${acct.equity:,.2f}", log_file)

    if target != 0 and target != acct.position and \
            strategy.signal_allowed(target, row, strat):
        if acct.position == -target:
            acct.close(price, bar_time, "Opposite EMA cross (signal flip)")
        side = "LONG" if target > 0 else "SHORT"
        acct.open(target, price, risk["position_size"], bar_time,
                  f"EMA{strat['ema_fast']}/{strat['ema_slow']} trend")
        notify.send(
            f"SIGNAL {side} {cfg['SYMBOL']} {cfg['INTERVAL']} @ {price:,.2f} | "
            f"EMA{strat['ema_fast']}={row['ema_fast']:,.1f} "
            f"EMA{strat['ema_slow']}={row['ema_slow']:,.1f} "
            f"RSI={row['rsi']:.1f} ATR={row['atr']:,.1f} | "
            f"equity ${acct.equity:,.2f}", log_file)
    else:
        pos = {1: "LONG", -1: "SHORT", 0: "FLAT"}[acct.position]
        stop_txt = f" stop={stop:,.2f}" if stop else ""
        notify.send(
            f"HEARTBEAT {cfg['SYMBOL']} {cfg['INTERVAL']} close={price:,.2f} "
            f"pos={pos}{stop_txt} RSI={row['rsi']:.1f} "
            f"equity=${acct.equity:,.2f}", log_file)

    acct._last_bar_time = bar_time
    save_state(state_file, acct, bar_time)
    return acct


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true",
                    help="run a single evaluation cycle and exit")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    cfg = load_config(args.config)
    acct = load_state(cfg["AGENT"]["state_file"], cfg["RISK"]["fee_rate"])
    notify.send(
        f"Agent started: {cfg['EXCHANGE']} {cfg['SYMBOL']} {cfg['INTERVAL']} "
        f"(paper trading)", cfg["AGENT"]["log_file"])

    if args.once:
        run_cycle(cfg, acct)
        return

    while True:
        try:
            acct = run_cycle(cfg, acct)
        except Exception as e:
            notify.send(f"Agent error: {e}", cfg["AGENT"]["log_file"])
        time.sleep(cfg["AGENT"]["poll_seconds"])


if __name__ == "__main__":
    main()
