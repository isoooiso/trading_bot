"""
database.py — SQLite хранилище всех сделок
"""

import sqlite3
import logging
from pathlib import Path

log = logging.getLogger("database")

CREATE_TRADES_SQL = """
CREATE TABLE IF NOT EXISTS trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    side        TEXT    NOT NULL,
    entry_price REAL    NOT NULL,
    exit_price  REAL    NOT NULL,
    sl          REAL,
    tp1         REAL,
    size_usd    REAL,
    pnl_usd     REAL    NOT NULL,
    reason      TEXT,
    opened_at   TEXT,
    closed_at   TEXT    NOT NULL,
    balance     REAL    NOT NULL
);
"""

INSERT_TRADE_SQL = """
INSERT INTO trades
  (side, entry_price, exit_price, sl, tp1, size_usd,
   pnl_usd, reason, opened_at, closed_at, balance)
VALUES
  (:side, :entry_price, :exit_price, :sl, :tp1, :size_usd,
   :pnl_usd, :reason, :opened_at, :closed_at, :balance);
"""


class Database:
    def __init__(self, path: str = "data/trades.db"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute(CREATE_TRADES_SQL)
            conn.commit()
        log.info(f"База данных инициализирована: {self.path}")

    def save_trade(self, trade: dict):
        try:
            with sqlite3.connect(self.path) as conn:
                conn.execute(INSERT_TRADE_SQL, trade)
                conn.commit()
            log.debug(f"Сделка сохранена: {trade.get('side')} P&L={trade.get('pnl_usd'):+.2f}$")
        except Exception as e:
            log.error(f"Ошибка записи в БД: {e}")

    def get_all_trades(self) -> list[dict]:
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM trades ORDER BY closed_at").fetchall()
        return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        trades = self.get_all_trades()
        if not trades:
            return {}

        pnls     = [t["pnl_usd"] for t in trades]
        wins     = [p for p in pnls if p > 0]
        losses   = [p for p in pnls if p <= 0]
        n        = len(pnls)

        win_rate = len(wins) / n if n else 0
        avg_win  = sum(wins)  / len(wins)  if wins   else 0
        avg_loss = sum(losses)/ len(losses) if losses else 0
        gross_w  = sum(wins)
        gross_l  = abs(sum(losses))
        pf       = gross_w / gross_l if gross_l else float("inf")

        # Максимальная просадка по балансу
        balances = [10_000.0] + [t["balance"] for t in trades]
        peak     = balances[0]
        max_dd   = 0.0
        for b in balances:
            if b > peak:
                peak = b
            dd = (peak - b) / peak
            if dd > max_dd:
                max_dd = dd

        return {
            "total_trades": n,
            "win_rate":     win_rate,
            "avg_win_usd":  avg_win,
            "avg_loss_usd": avg_loss,
            "profit_factor":pf,
            "expectancy":   sum(pnls) / n if n else 0,
            "total_pnl":    sum(pnls),
            "max_drawdown": max_dd,
            "final_balance":trades[-1]["balance"] if trades else 10_000,
        }
