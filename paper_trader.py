"""
paper_trader.py — симулятор paper trading
Реальные рыночные цены, виртуальный баланс.
"""

import logging
from datetime import datetime, timezone, date
from dataclasses import dataclass, field, asdict
from typing import Optional
import pandas as pd

log = logging.getLogger("paper_trader")


@dataclass
class Position:
    side:         str     # "long" / "short"
    entry_price:  float
    sl:           float
    tp1:          float
    atr:          float
    size_usd:     float   # полный размер в USD
    size_btc:     float   # кол-во BTC
    commission:   float   # уже вычтена при входе
    opened_at:    str
    tp1_hit:      bool = False
    breakeven_set: bool = False


class PaperTrader:
    def __init__(self, config: dict):
        self.balance       = config["starting_balance"]
        self.risk_pct      = config["risk_per_trade"]      # 0.01
        self.commission    = config["commission"]          # 0.0004
        self.daily_limit   = config["daily_loss_limit"]    # 0.03
        self.tp1_size      = config["tp1_size_pct"]        # 0.5
        self.tp1_rr        = config["tp1_rr"]              # 2.0
        self.atr_mult      = config["atr_mult_sl"]

        self.position: Optional[Position] = None
        self.daily_start_balance = self.balance
        self.daily_date          = date.today()
        self.trades_today        = 0
        self.daily_pnl           = 0.0
        self.total_trades        = 0
        self.equity_curve        = [self.balance]

    # ── Открытие позиции ──────────────────────────────────────────────────────
    def open_position(self, side: str, entry_price: float, sl_price: float,
                      tp1_price: float, atr: float, signal_bar_ts: str) -> Optional[dict]:

        if self.position is not None:
            log.debug("Уже есть открытая позиция, пропускаем")
            return None

        # Размер риска в USD
        risk_usd = self.balance * self.risk_pct
        # Расстояние до стопа
        sl_dist  = abs(entry_price - sl_price)
        if sl_dist < 1.0:
            log.warning("SL дистанция слишком мала, пропускаем")
            return None

        # Размер позиции в BTC
        size_btc = risk_usd / sl_dist
        size_usd = size_btc * entry_price

        # Максимум — 10% баланса за раз (безопасность)
        max_usd = self.balance * 0.10
        if size_usd > max_usd:
            size_btc = max_usd / entry_price
            size_usd = max_usd

        # Комиссия при входе
        comm = size_usd * self.commission
        self.balance -= comm

        self.position = Position(
            side         = side,
            entry_price  = entry_price,
            sl           = sl_price,
            tp1          = tp1_price,
            atr          = atr,
            size_usd     = size_usd,
            size_btc     = size_btc,
            commission   = comm,
            opened_at    = signal_bar_ts,
        )

        log.info(f"Позиция открыта: {side.upper()} {size_btc:.6f} BTC "
                 f"@ {entry_price:.2f} | риск={risk_usd:.2f}$")
        return {
            "side": side, "entry_price": entry_price,
            "sl": sl_price, "tp1": tp1_price,
            "size_usd": size_usd, "risk_usd": risk_usd,
        }

    # ── Проверка выходов каждый цикл ─────────────────────────────────────────
    def check_exits(self, current_price: float, current_atr: float,
                    df1h: pd.DataFrame) -> list:
        if self.position is None:
            return []

        pos    = self.position
        exits  = []
        now_ts = datetime.now(timezone.utc).isoformat()

        # ── 1. Проверка Stop Loss ─────────────────────────────────────────
        sl_hit = (pos.side == "long"  and current_price <= pos.sl) or \
                 (pos.side == "short" and current_price >= pos.sl)

        if sl_hit:
            pnl_usd = self._calc_pnl(pos, pos.sl, pos.size_btc)
            exits.append(self._close_position(pos.sl, pnl_usd, "STOP_LOSS", now_ts))
            return exits

        # ── 2. Проверка TP1 (50% позиции) ────────────────────────────────
        if not pos.tp1_hit:
            tp1_hit = (pos.side == "long"  and current_price >= pos.tp1) or \
                      (pos.side == "short" and current_price <= pos.tp1)

            if tp1_hit:
                # Фиксируем 50%
                partial_btc = pos.size_btc * self.tp1_size
                pnl_usd = self._calc_pnl(pos, pos.tp1, partial_btc)
                self.balance += pnl_usd
                self._update_daily(pnl_usd)

                pos.size_btc    -= partial_btc
                pos.size_usd     = pos.size_btc * current_price
                pos.tp1_hit      = True
                # Переносим стоп в безубыток
                pos.sl           = pos.entry_price * (1 + self.commission
                                   if pos.side == "long" else 1 - self.commission)
                pos.breakeven_set = True

                self.total_trades += 1
                self.trades_today += 1
                self.equity_curve.append(self.balance)
                log.info(f"✅ TP1 hit @ {pos.tp1:.2f} | P&L={pnl_usd:+.2f}$ | SL → безубыток")

        # ── 3. Трейлинг по EMA21 (TP2, оставшиеся 50%) ───────────────────
        if pos.tp1_hit and pos.size_btc > 0:
            ema21_now  = df1h["close"].ewm(span=21, adjust=False).mean().iloc[-2]
            ema21_prev = df1h["close"].ewm(span=21, adjust=False).mean().iloc[-3]
            close_now  = df1h["close"].iloc[-2]
            close_prev = df1h["close"].iloc[-3]

            trail_exit = False
            if pos.side == "long":
                trail_exit = close_prev > ema21_prev and close_now < ema21_now
            elif pos.side == "short":
                trail_exit = close_prev < ema21_prev and close_now > ema21_now

            if trail_exit:
                pnl_usd = self._calc_pnl(pos, current_price, pos.size_btc)
                exits.append(self._close_position(current_price, pnl_usd,
                                                  "TRAIL_EMA21", now_ts))

        return exits

    # ── Принудительное закрытие ───────────────────────────────────────────────
    def force_close(self, price: float, reason: str = "MANUAL") -> Optional[dict]:
        if self.position is None:
            return None
        now_ts = datetime.now(timezone.utc).isoformat()
        pnl = self._calc_pnl(self.position, price, self.position.size_btc)
        return self._close_position(price, pnl, reason, now_ts)

    # ── Внутренние методы ────────────────────────────────────────────────────
    def _calc_pnl(self, pos: Position, exit_price: float, btc: float) -> float:
        comm_exit = btc * exit_price * self.commission
        if pos.side == "long":
            gross = (exit_price - pos.entry_price) * btc
        else:
            gross = (pos.entry_price - exit_price) * btc
        return gross - comm_exit

    def _close_position(self, exit_price: float, pnl_usd: float,
                        reason: str, ts: str) -> dict:
        pos = self.position
        self.balance += pnl_usd
        self._update_daily(pnl_usd)
        self.total_trades += 1
        self.trades_today += 1
        self.equity_curve.append(self.balance)

        trade_record = {
            "side":        pos.side,
            "entry_price": pos.entry_price,
            "exit_price":  exit_price,
            "sl":          pos.sl,
            "tp1":         pos.tp1,
            "size_usd":    pos.size_usd,
            "pnl_usd":     pnl_usd,
            "reason":      reason,
            "opened_at":   pos.opened_at,
            "closed_at":   ts,
            "balance":     self.balance,
        }
        self.position = None
        return trade_record

    def _update_daily(self, pnl: float):
        today = date.today()
        if today != self.daily_date:
            # Новый день — сбрасываем счётчики
            self.daily_date          = today
            self.daily_start_balance = self.balance
            self.trades_today        = 0
            self.daily_pnl           = 0.0
        self.daily_pnl += pnl

    # ── Проверка дневного лимита ──────────────────────────────────────────────
    def daily_loss_exceeded(self) -> bool:
        loss = self.daily_start_balance - self.balance
        return loss > self.daily_start_balance * self.daily_limit

    def has_open_position(self) -> bool:
        return self.position is not None

    # ── Статус ───────────────────────────────────────────────────────────────
    def get_status(self, current_price: float) -> dict:
        unrealized = 0.0
        if self.position and current_price > 0:
            unrealized = self._calc_pnl(
                self.position, current_price, self.position.size_btc
            )
        return {
            "balance":        self.balance,
            "unrealized_pnl": unrealized,
            "equity":         self.balance + unrealized,
            "open_positions": 1 if self.position else 0,
            "trades_today":   self.trades_today,
            "daily_pnl":      self.daily_pnl,
            "total_trades":   self.total_trades,
            "equity_curve":   self.equity_curve,
        }
