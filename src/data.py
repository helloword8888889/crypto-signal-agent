"""Market data fetchers for Binance / Bybit / Gate (public endpoints only).

Returns a pandas DataFrame with columns:
    open_time (datetime, UTC), open, high, low, close, volume
Oldest bar first. Symbols use exchange-native format, e.g. BTCUSDT (binance,
bybit) or BTC_USDT (gate). For binance, SYMBOL is used as-is; for gate the
code converts BTCUSDT -> BTC_USDT automatically.
"""
from __future__ import annotations

import time
import requests
import pandas as pd

BINANCE_BASES = ["https://api.binance.com", "https://api.binance.us"]
BYBIT_BASE = "https://api.bybit.com"
GATE_BASE = "https://api.gateio.ws/api/v4"

_BINANCE_INTERVALS = {"15m", "30m", "1h", "4h", "1d"}
_BYBIT_INTERVALS = {"15m": "15", "30m": "30", "1h": "60", "4h": "240", "1d": "D"}
_GATE_INTERVALS = {"15m": "15m", "30m": "30m", "1h": "1h", "4h": "4h", "1d": "1d"}


def _get(url: str, params: dict, timeout: int = 15) -> requests.Response:
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r


def fetch_binance(symbol: str, interval: str, limit: int = 500) -> pd.DataFrame:
    if interval not in _BINANCE_INTERVALS:
        raise ValueError(f"Unsupported binance interval: {interval}")
    last_err: Exception | None = None
    for base in BINANCE_BASES:
        try:
            r = _get(f"{base}/api/v3/klines",
                     {"symbol": symbol, "interval": interval, "limit": limit})
            raw = r.json()
            break
        except Exception as e:  # try next mirror
            last_err = e
    else:
        raise ConnectionError(f"Binance unreachable: {last_err}")
    rows = [[k[0], k[1], k[2], k[3], k[4], k[5]] for k in raw]
    df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume"])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    return df


def fetch_bybit(symbol: str, interval: str, limit: int = 500) -> pd.DataFrame:
    iv = _BYBIT_INTERVALS.get(interval)
    if iv is None:
        raise ValueError(f"Unsupported bybit interval: {interval}")
    r = _get(f"{BYBIT_BASE}/v5/market/kline",
             {"category": "linear", "symbol": symbol, "interval": iv, "limit": limit})
    payload = r.json()
    if payload.get("retCode") != 0:
        raise ConnectionError(f"Bybit error: {payload.get('retMsg')}")
    rows = payload["result"]["list"][::-1]  # API returns newest first
    df = pd.DataFrame(
        [[int(k[0]), k[1], k[2], k[3], k[4], k[5]] for k in rows],
        columns=["open_time", "open", "high", "low", "close", "volume"],
    )
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    return df


def fetch_gate(symbol: str, interval: str, limit: int = 500) -> pd.DataFrame:
    iv = _GATE_INTERVALS.get(interval)
    if iv is None:
        raise ValueError(f"Unsupported gate interval: {interval}")
    pair = symbol if "_" in symbol else symbol.replace("USDT", "_USDT")
    r = _get(f"{GATE_BASE}/spot/candlesticks",
             {"currency_pair": pair, "interval": iv, "limit": limit})
    raw = r.json()
    df = pd.DataFrame(
        [[int(k[0]) * 1000, k[3], k[4], k[5], k[2], k[1]] for k in raw],
        columns=["open_time", "open", "high", "low", "close", "volume"],
    )
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    return df


FETCHERS = {"binance": fetch_binance, "bybit": fetch_bybit, "gate": fetch_gate}


def fetch_klines(exchange: str, symbol: str, interval: str, limit: int = 500) -> pd.DataFrame:
    fn = FETCHERS.get(exchange.lower())
    if fn is None:
        raise ValueError(f"Unknown exchange: {exchange} (choose from {list(FETCHERS)})")
    return fn(symbol, interval, limit)


def latest_closed_bar(df: pd.DataFrame, interval: str) -> pd.Series:
    """Return the most recent fully-closed bar (drop a still-forming last bar)."""
    seconds = {"15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400}[interval]
    now = pd.Timestamp.now(tz="UTC")
    closed = df[df["open_time"] + pd.Timedelta(seconds=seconds) <= now]
    if closed.empty:
        return df.iloc[-1]
    return closed.iloc[-1]
