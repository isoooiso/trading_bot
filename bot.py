"""
EMA Trend-Momentum Paper Trading Bot
=====================================
Рынок: BTCUSDT
Стратегия: EMA(21/50) + ATR(14)
Таймфрейм: 1H сигнал + 4H фильтр
Режим: Paper Trading (симуляция с реальными ценами Binance)

Запуск:  python bot.py
Логи:    logs/bot.log
База:    data/trades.db
Отчёт:  python report.py
"""

import time
import logging
import schedule
from datetime import datetime, timezone
from pathlib import Path

from strategy import EMAStrategy
from paper_trader import PaperTrader
from data_feed import BinanceDataFeed
from database import Database

# ─── КОНФИГУРАЦИЯ ────────────────────────────────────────────────────────────
CONFIG = {
    "symbol":           "BTCUSDT",
    "tf_signal":        "1h",       # таймфрейм сигнала
    "tf_trend":         "4h",       # таймфрейм тренд-фильтра
    "ema_fast":         21,
    "ema_slow":         50,
    "atr_period":       14,
    "atr_mult_sl":      1.5,        # стоп = вход ± ATR × 1.5
    "atr_min_pct":      0.003,      # минимальный ATR (0.3% от цены)
    "tp1_rr":           2.0,        # TP1 на расстоянии 2R
    "tp1_size_pct":     0.5,        # 50% позиции на TP1
    "risk_per_trade":   0.01,       # 1% депозита на сделку
    "daily_loss_limit": 0.03,       # 3% дневной лимит потерь
    "starting_balance": 10_000.0,   # стартовый баланс ($)
    "commission":       0.0004,     # 0.04% на вход + выход
    "check_interval_s": 60,         # проверка каждые 60 секунд
    "session_filter":   True,       # фильтр по торговой сессии (UTC)
    "session_start_utc": 8,         # 08:00 UTC
    "session_end_utc":  17,         # 17:00 UTC
}

# ─── ЛОГИРОВАНИЕ ─────────────────────────────────────────────────────────────
Path("logs").mkdir(exist_ok=True)
Path("data").mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("bot")


# ─── ОСНОВНАЯ ЛОГИКА ─────────────────────────────────────────────────────────
def run_cycle(feed: BinanceDataFeed, strategy: EMAStrategy,
              trader: PaperTrader, db: Database):
    """Один цикл проверки — запускается каждую минуту."""
    now = datetime.now(timezone.utc)

    # ── Загружаем свежие данные ──────────────────────────────────────────────
    df1h = feed.get_ohlcv("1h", limit=100)
    df4h = feed.get_ohlcv("4h", limit=60)

    if df1h is None or df4h is None:
        log.warning("Нет данных от API, пропускаем цикл")
        return

    # ── Генерируем сигнал ────────────────────────────────────────────────────
    signal = strategy.get_signal(df1h, df4h)

    # ── Обновляем открытые позиции (проверка SL/TP/трейлинг) ────────────────
    current_price = float(df1h["close"].iloc[-1])
    current_atr   = float(df1h["atr"].iloc[-1])
    exits = trader.check_exits(current_price, current_atr, df1h)
    for ex in exits:
        log.info(f"ВЫХОД: {ex['side']} | цена={ex['exit_price']:.2f} "
                 f"| P&L={ex['pnl_usd']:+.2f}$ | причина={ex['reason']}")
        db.save_trade(ex)

    # ── Проверяем дневной лимит убытков ──────────────────────────────────────
    if trader.daily_loss_exceeded():
        log.warning(f"⛔ Дневной лимит убытков достигнут. Новых входов нет.")
        _print_status(trader, current_price)
        return

    # ── Фильтр сессии ────────────────────────────────────────────────────────
    if CONFIG["session_filter"]:
        h = now.hour
        if not (CONFIG["session_start_utc"] <= h < CONFIG["session_end_utc"]):
            return  # тихий период — не логируем, просто пропускаем

    # ── Открываем новую позицию ──────────────────────────────────────────────
    if signal["action"] != "none" and not trader.has_open_position():
        trade = trader.open_position(
            side          = signal["action"],   # "long" / "short"
            entry_price   = signal["entry"],
            sl_price      = signal["sl"],
            tp1_price     = signal["tp1"],
            atr           = signal["atr"],
            signal_bar_ts = signal["ts"],
        )
        if trade:
            log.info(
                f"🟢 ВХОД {trade['side'].upper()} | "
                f"цена={trade['entry_price']:.2f} | "
                f"SL={trade['sl']:.2f} | TP1={trade['tp1']:.2f} | "
                f"размер={trade['size_usd']:.1f}$ | "
                f"риск={trade['risk_usd']:.2f}$"
            )

    # ── Статус каждые 15 минут ───────────────────────────────────────────────
    if now.minute % 15 == 0:
        _print_status(trader, current_price)


def _print_status(trader: PaperTrader, price: float):
    s = trader.get_status(price)
    log.info(
        f"📊 Баланс={s['balance']:.2f}$ | "
        f"Открытых={s['open_positions']} | "
        f"Сделок сегодня={s['trades_today']} | "
        f"PnL сегодня={s['daily_pnl']:+.2f}$ | "
        f"BTC={price:.2f}$"
    )


# ─── ЗАПУСК ──────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 60)
    log.info("  EMA Trend-Momentum Paper Trading Bot — СТАРТ")
    log.info(f"  Символ: {CONFIG['symbol']} | Баланс: ${CONFIG['starting_balance']:,.0f}")
    log.info("=" * 60)

    feed     = BinanceDataFeed(CONFIG["symbol"])
    strategy = EMAStrategy(CONFIG)
    trader   = PaperTrader(CONFIG)
    db       = Database("data/trades.db")

    # Запускаем сразу и затем каждые N секунд
    run_cycle(feed, strategy, trader, db)
    schedule.every(CONFIG["check_interval_s"]).seconds.do(
        run_cycle, feed, strategy, trader, db
    )

    log.info(f"Бот запущен. Проверка каждые {CONFIG['check_interval_s']}с.")
    log.info("Для остановки: Ctrl+C")

    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Бот остановлен пользователем.")
        s = trader.get_status(0)
        log.info(f"Итог: баланс={s['balance']:.2f}$ | "
                 f"всего сделок={s['total_trades']}")


if __name__ == "__main__":
    main()
