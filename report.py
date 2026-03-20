"""
report.py — отчёт по результатам paper trading
Запуск: python report.py
"""

from database import Database
from datetime import datetime


def print_report():
    db     = Database("data/trades.db")
    trades = db.get_all_trades()
    stats  = db.get_stats()

    print("\n" + "=" * 65)
    print("  📊 EMA TREND-MOMENTUM — ОТЧЁТ PAPER TRADING")
    print("=" * 65)

    if not trades:
        print("  Сделок пока нет. Бот только начал работу.")
        return

    print(f"\n  Период:         {trades[0]['opened_at'][:10]} → {trades[-1]['closed_at'][:10]}")
    print(f"  Всего сделок:   {stats['total_trades']}")
    print(f"  Win Rate:       {stats['win_rate']:.1%}")
    print(f"  Avg Win:        +${stats['avg_win_usd']:.2f}")
    print(f"  Avg Loss:       -${abs(stats['avg_loss_usd']):.2f}")
    print(f"  Profit Factor:  {stats['profit_factor']:.2f}")
    print(f"  Expectancy:     ${stats['expectancy']:+.2f} / сделку")
    print(f"  Total P&L:      ${stats['total_pnl']:+.2f}")
    print(f"  Max Drawdown:   {stats['max_drawdown']:.1%}")
    print(f"  Итоговый баланс: ${stats['final_balance']:.2f}")
    print(f"  Return:         {(stats['final_balance']/10000-1)*100:+.2f}%")

    # Оценка
    print("\n  ── Оценка ──────────────────────────────────────────────")
    pf = stats["profit_factor"]
    if pf >= 1.5:
        verdict = "✅ Хорошо (Profit Factor ≥ 1.5)"
    elif pf >= 1.2:
        verdict = "⚠️  Удовлетворительно (1.2 ≤ PF < 1.5)"
    elif pf >= 1.0:
        verdict = "🔶 Слабо (1.0 ≤ PF < 1.2, почти breakeven)"
    else:
        verdict = "❌ Система убыточна (PF < 1.0)"
    print(f"  {verdict}")

    if stats['total_trades'] < 50:
        print(f"  ⚠️  Выборка мала ({stats['total_trades']} сделок). "
              "Нужно ≥ 100 для вывода.")

    # Последние 10 сделок
    print("\n  ── Последние сделки ─────────────────────────────────────")
    print(f"  {'#':<4} {'Side':<6} {'Entry':>8} {'Exit':>8} {'P&L':>9} {'Причина'}")
    print("  " + "-" * 55)
    for i, t in enumerate(trades[-10:], 1):
        emoji = "✅" if t["pnl_usd"] > 0 else "❌"
        print(f"  {i:<4} {t['side']:<6} "
              f"{t['entry_price']:>8.1f} {t['exit_price']:>8.1f} "
              f"{t['pnl_usd']:>+8.2f}$ {emoji} {t['reason']}")

    print("\n" + "=" * 65 + "\n")


if __name__ == "__main__":
    print_report()
