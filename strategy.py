"""
strategy.py — логика EMA Trend-Momentum стратегии
Только объективные правила, без субъективных оценок.
"""

import pandas as pd
import numpy as np
import logging

log = logging.getLogger("strategy")


class EMAStrategy:
    def __init__(self, config: dict):
        self.fast       = config["ema_fast"]        # 21
        self.slow       = config["ema_slow"]        # 50
        self.atr_period = config["atr_period"]      # 14
        self.atr_mult   = config["atr_mult_sl"]     # 1.5
        self.atr_min    = config["atr_min_pct"]     # 0.003
        self.tp1_rr     = config["tp1_rr"]          # 2.0
        self.commission = config["commission"]      # 0.0004

    # ── Расчёт индикаторов ────────────────────────────────────────────────────
    @staticmethod
    def _ema(series: pd.Series, n: int) -> pd.Series:
        return series.ewm(span=n, adjust=False).mean()

    @staticmethod
    def _atr(df: pd.DataFrame, n: int) -> pd.Series:
        hi, lo, cl = df["high"], df["low"], df["close"]
        tr = pd.concat([
            hi - lo,
            (hi - cl.shift()).abs(),
            (lo - cl.shift()).abs(),
        ], axis=1).max(axis=1)
        return tr.ewm(span=n, adjust=False).mean()

    def _add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["ema_fast"] = self._ema(df["close"], self.fast)
        df["ema_slow"] = self._ema(df["close"], self.slow)
        df["atr"]      = self._atr(df, self.atr_period)
        return df

    # ── Основной сигнал ──────────────────────────────────────────────────────
    def get_signal(self, df1h: pd.DataFrame, df4h: pd.DataFrame) -> dict:
        """
        Возвращает:
          {"action": "long"|"short"|"none", "entry":..., "sl":..., "tp1":..., "atr":..., "ts":...}
        """
        try:
            df1 = self._add_indicators(df1h)
            df4 = self._add_indicators(df4h)
        except Exception as e:
            log.error(f"Ошибка расчёта индикаторов: {e}")
            return self._no_signal()

        # Последние две закрытые свечи 1H (не текущая незакрытая)
        bar   = df1.iloc[-2]   # последняя ЗАКРЫТАЯ свеча (сигнальная)
        prev  = df1.iloc[-3]   # предыдущая

        # Последняя закрытая 4H
        bar4  = df4.iloc[-2]
        prev4 = df4.iloc[-3]

        close    = bar["close"]
        ema_f    = bar["ema_fast"]
        ema_s    = bar["ema_slow"]
        atr      = bar["atr"]

        # ── Условие 1: ATR-фильтр (не флэт) ──────────────────────────────
        if atr < close * self.atr_min:
            return self._no_signal("ATR_filter")

        # ── Условие 2: 4H тренд ──────────────────────────────────────────
        bull_4h = (bar4["ema_fast"] > bar4["ema_slow"] and
                   bar4["ema_fast"] > prev4["ema_fast"])
        bear_4h = (bar4["ema_fast"] < bar4["ema_slow"] and
                   bar4["ema_fast"] < prev4["ema_fast"])

        # ── Условие 3: 1H кроссовер ───────────────────────────────────────
        cross_up   = prev["ema_fast"] < prev["ema_slow"] and ema_f > ema_s
        cross_down = prev["ema_fast"] > prev["ema_slow"] and ema_f < ema_s

        # ── Условие 4: Close по обе стороны от EMA ────────────────────────
        price_above = close > ema_f and close > ema_s
        price_below = close < ema_f and close < ema_s

        # ── LONG ──────────────────────────────────────────────────────────
        if bull_4h and cross_up and price_above:
            # Стоп-ордер чуть выше хая сигнальной свечи
            entry = bar["high"] * (1 + 0.0005)
            sl    = entry - atr * self.atr_mult
            # SL не выше последнего swing low (мин за 10 свечей 1H)
            swing_low = df1["low"].iloc[-12:-2].min()
            sl = min(sl, swing_low * 0.999)
            tp1 = entry + (entry - sl) * self.tp1_rr
            log.debug(f"LONG сигнал: entry={entry:.2f} SL={sl:.2f} TP1={tp1:.2f}")
            return {
                "action": "long", "entry": entry,
                "sl": sl, "tp1": tp1, "atr": atr,
                "ts": str(bar.name),
            }

        # ── SHORT ─────────────────────────────────────────────────────────
        if bear_4h and cross_down and price_below:
            entry = bar["low"] * (1 - 0.0005)
            sl    = entry + atr * self.atr_mult
            swing_high = df1["high"].iloc[-12:-2].max()
            sl = max(sl, swing_high * 1.001)
            tp1 = entry - (sl - entry) * self.tp1_rr
            log.debug(f"SHORT сигнал: entry={entry:.2f} SL={sl:.2f} TP1={tp1:.2f}")
            return {
                "action": "short", "entry": entry,
                "sl": sl, "tp1": tp1, "atr": atr,
                "ts": str(bar.name),
            }

        return self._no_signal()

    def _no_signal(self, reason: str = "") -> dict:
        return {"action": "none", "entry": 0, "sl": 0, "tp1": 0,
                "atr": 0, "ts": "", "reason": reason}

    # ── Трейлинг EMA для TP2 ─────────────────────────────────────────────────
    def get_trail_stop(self, df1h: pd.DataFrame, side: str) -> float:
        """
        Возвращает уровень трейлинг-стопа по EMA21.
        Для long: если цена закрылась ниже EMA21 → закрыть.
        Для short: если цена закрылась выше EMA21 → закрыть.
        """
        df1 = self._add_indicators(df1h)
        ema21_now  = df1["ema_fast"].iloc[-2]   # последняя закрытая свеча
        close_now  = df1["close"].iloc[-2]

        if side == "long":
            # EMA21 пересекает цену вниз — сигнал выхода
            ema21_prev = df1["ema_fast"].iloc[-3]
            close_prev = df1["close"].iloc[-3]
            cross_down = close_prev > ema21_prev and close_now < ema21_now
            return ema21_now if cross_down else 0.0
        elif side == "short":
            ema21_prev = df1["ema_fast"].iloc[-3]
            close_prev = df1["close"].iloc[-3]
            cross_up   = close_prev < ema21_prev and close_now > ema21_now
            return ema21_now if cross_up else 0.0

        return 0.0
