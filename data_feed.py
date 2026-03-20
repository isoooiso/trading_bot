"""
data_feed.py — получение данных с Binance (публичный API, без ключей)
"""

import time
import logging
import requests
import pandas as pd
from datetime import datetime, timezone

log = logging.getLogger("data_feed")

BINANCE_URL = "https://api.binance.com/api/v3/klines"

# Карта таймфреймов
TF_MAP = {
    "1m": "1m", "5m": "5m", "15m": "15m",
    "1h": "1h", "4h": "4h", "1d": "1d",
}


class BinanceDataFeed:
    def __init__(self, symbol: str = "BTCUSDT", retries: int = 3):
        self.symbol  = symbol
        self.retries = retries

    def get_ohlcv(self, timeframe: str, limit: int = 100) -> pd.DataFrame | None:
        """
        Возвращает DataFrame с колонками: open, high, low, close, volume
        Индекс: datetime (UTC)
        """
        interval = TF_MAP.get(timeframe)
        if not interval:
            log.error(f"Неизвестный таймфрейм: {timeframe}")
            return None

        params = {
            "symbol":   self.symbol,
            "interval": interval,
            "limit":    limit + 1,  # +1 чтобы последняя незакрытая не учиталась
        }

        for attempt in range(self.retries):
            try:
                resp = requests.get(BINANCE_URL, params=params, timeout=10)
                resp.raise_for_status()
                raw = resp.json()

                df = pd.DataFrame(raw, columns=[
                    "ts", "open", "high", "low", "close", "volume",
                    "close_time", "quote_vol", "trades",
                    "taker_base", "taker_quote", "ignore"
                ])

                df["ts"]    = pd.to_datetime(df["ts"], unit="ms", utc=True)
                df          = df.set_index("ts")
                for col in ["open", "high", "low", "close", "volume"]:
                    df[col] = df[col].astype(float)

                # Убираем последнюю (незакрытую) свечу
                df = df.iloc[:-1]

                return df[["open", "high", "low", "close", "volume"]]

            except requests.exceptions.RequestException as e:
                log.warning(f"Ошибка API (попытка {attempt+1}/{self.retries}): {e}")
                if attempt < self.retries - 1:
                    time.sleep(5 * (attempt + 1))

        log.error(f"Не удалось получить данные после {self.retries} попыток")
        return None

    def get_current_price(self) -> float | None:
        """Текущая цена через ticker."""
        try:
            r = requests.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": self.symbol}, timeout=5
            )
            r.raise_for_status()
            return float(r.json()["price"])
        except Exception as e:
            log.error(f"Ошибка получения текущей цены: {e}")
            return None
