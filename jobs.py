"""
Background Jobs - Scheduled tasks for DQL training data and performance tracking
"""
import argparse
from datetime import datetime
from config import ALPACA_API_KEY, ALPACA_SECRET_KEY


def get_data_client():
    """Initialize Alpaca data client"""
    from alpaca.data import StockHistoricalDataClient
    return StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)


def get_trading_client():
    """Initialize Alpaca trading client"""
    from alpaca.trading.client import TradingClient
    return TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)


def daily_snapshot():
    """
    Run at market close - log daily performance.
    Captures equity, trades, and SPY comparison.
    """
    from db import log_daily_performance, get_connection
    from executor import get_account_info, get_positions
    from scanner import get_market_context, get_data_client as get_scanner_data_client

    print(f"[{datetime.now()}] Running daily performance snapshot...")

    try:
        # Get account info
        account = get_account_info()
        positions = get_positions()

        # Get SPY change for the day
        data_client = get_scanner_data_client()
        spy_data, _ = get_market_context(data_client)
        spy_change = spy_data.get('change_1d', 0)

        # Get today's date
        today = datetime.now().strftime('%Y-%m-%d')

        # Count trades opened/closed today
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT COUNT(*) FROM trades
            WHERE date(entry_date) = ? AND status = 'open'
        """, (today,))
        trades_opened = cursor.fetchone()[0]

        cursor.execute("""
            SELECT COUNT(*),
                   SUM(CASE WHEN pnl_pct > 0 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN pnl_pct <= 0 THEN 1 ELSE 0 END)
            FROM trades
            WHERE date(exit_date) = ? AND status = 'closed'
        """, (today,))
        row = cursor.fetchone()
        trades_closed = row[0] or 0
        win_count = row[1] or 0
        loss_count = row[2] or 0

        conn.close()

        # Calculate cash percentage
        cash_pct = account['cash'] / account['equity'] if account['equity'] > 0 else 1.0

        # Log daily performance
        log_daily_performance(
            date=today,
            starting_equity=account['equity'],  # We don't have yesterday's value, using current
            ending_equity=account['equity'],
            trades_opened=trades_opened,
            trades_closed=trades_closed,
            win_count=win_count,
            loss_count=loss_count,
            positions_held=len(positions),
            cash_pct=cash_pct,
            spy_change=spy_change
        )

        print(f"  Equity: ${account['equity']:,.2f}")
        print(f"  Positions: {len(positions)}")
        print(f"  Trades today: {trades_opened} opened, {trades_closed} closed")
        print(f"  SPY change: {spy_change:+.2f}%")
        print("  Daily snapshot logged.")

    except Exception as e:
        print(f"  Error in daily snapshot: {e}")


def update_outcomes():
    """
    Run daily - fill in 1d/5d/10d prices for past candidates.
    This data is used for DQL training to evaluate skipped trades.

    Should be run 1+ hour after market close (21:00 UTC = 4 PM ET)
    for reliable price data.
    """
    from db import update_candidate_outcomes
    import pytz

    print(f"[{datetime.now()}] Updating candidate outcomes...")

    # Check if market has been closed for at least 30 minutes
    et = pytz.timezone('America/New_York')
    now_et = datetime.now(et)
    market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)

    if now_et.weekday() < 5:  # Weekday
        minutes_since_close = (now_et - market_close).total_seconds() / 60
        if 0 < minutes_since_close < 30:
            print(f"  Market closed {minutes_since_close:.0f} minutes ago - waiting for data to settle")
            print(f"  Recommend running this job at 22:00+ UTC (5 PM+ ET)")

    try:
        data_client = get_data_client()
        updated = update_candidate_outcomes(data_client)
        print(f"  Updated outcomes for {updated} candidates")

    except Exception as e:
        print(f"  Error updating outcomes: {e}")


def update_position_tracking():
    """
    Run periodically - update max gain/drawdown for open positions.
    This data is used for DQL reward calculation.
    """
    from db import update_position_tracking
    from executor import get_positions

    print(f"[{datetime.now()}] Updating position tracking...")

    try:
        positions = get_positions()

        for p in positions:
            symbol = p['symbol']
            current_price = p['current_price']
            entry_price = p['avg_entry_price']

            update_position_tracking(symbol, current_price, entry_price)

        print(f"  Updated tracking for {len(positions)} positions")

    except Exception as e:
        print(f"  Error updating position tracking: {e}")


def calculate_trade_rewards():
    """
    Run after trade closes - calculate DQL reward.
    Called automatically by update_trade_exit, but can be run manually.
    """
    from db import get_connection, update_trade_reward

    print(f"[{datetime.now()}] Calculating trade rewards...")

    try:
        conn = get_connection()
        cursor = conn.cursor()

        # Find closed trades without rewards
        cursor.execute("""
            SELECT id FROM trades
            WHERE status = 'closed' AND reward IS NULL
        """)

        trades = cursor.fetchall()
        conn.close()

        for trade in trades:
            update_trade_reward(trade['id'])

        print(f"  Calculated rewards for {len(trades)} trades")

    except Exception as e:
        print(f"  Error calculating rewards: {e}")


def cleanup_old_data():
    """
    Run weekly - clean up old candidate snapshots (keep last 90 days).
    """
    from db import get_connection

    print(f"[{datetime.now()}] Cleaning up old data...")

    try:
        conn = get_connection()
        cursor = conn.cursor()

        # Delete candidate snapshots older than 90 days
        cursor.execute("""
            DELETE FROM candidate_snapshots
            WHERE timestamp < datetime('now', '-90 days')
        """)
        deleted_candidates = cursor.rowcount

        # Delete market snapshots older than 90 days
        cursor.execute("""
            DELETE FROM market_snapshots
            WHERE timestamp < datetime('now', '-90 days')
        """)
        deleted_snapshots = cursor.rowcount

        # Delete position checks older than 30 days
        cursor.execute("""
            DELETE FROM position_checks
            WHERE check_time < datetime('now', '-30 days')
        """)
        deleted_checks = cursor.rowcount

        conn.commit()
        conn.close()

        print(f"  Deleted {deleted_candidates} old candidate snapshots")
        print(f"  Deleted {deleted_snapshots} old market snapshots")
        print(f"  Deleted {deleted_checks} old position checks")

    except Exception as e:
        print(f"  Error cleaning up data: {e}")


def backfill_dqn_experiences_job():
    """
    Run daily after market close - add new closed trades to DQN experiences.
    This populates training data for the DQN model.
    """
    from db import backfill_dqn_experiences, get_dqn_stats

    print(f"[{datetime.now()}] Backfilling DQN experiences...")

    try:
        backfilled, skipped = backfill_dqn_experiences()
        print(f"  Backfilled {backfilled} new experiences, skipped {skipped}")

        # Print current stats
        stats = get_dqn_stats()
        print(f"  DQN Stats: {stats['total']} total experiences, "
              f"{stats['win_rate']:.1f}% win rate, "
              f"avg reward: {stats['avg_reward']:.4f}")

    except Exception as e:
        print(f"  Error backfilling DQN experiences: {e}")


def run_all_daily_jobs():
    """Run all daily jobs in sequence"""
    print("=" * 60)
    print(f"DAILY JOBS - {datetime.now()}")
    print("=" * 60)

    daily_snapshot()
    update_outcomes()
    update_position_tracking()
    calculate_trade_rewards()
    backfill_dqn_experiences_job()

    print("=" * 60)
    print("Daily jobs complete.")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Momentum Agent Background Jobs")
    parser.add_argument(
        "job",
        choices=[
            "daily_snapshot",
            "update_outcomes",
            "update_tracking",
            "calculate_rewards",
            "backfill_dqn",
            "cleanup",
            "all"
        ],
        help="Job to run"
    )

    args = parser.parse_args()

    if args.job == "daily_snapshot":
        daily_snapshot()
    elif args.job == "update_outcomes":
        update_outcomes()
    elif args.job == "update_tracking":
        update_position_tracking()
    elif args.job == "calculate_rewards":
        calculate_trade_rewards()
    elif args.job == "backfill_dqn":
        backfill_dqn_experiences_job()
    elif args.job == "cleanup":
        cleanup_old_data()
    elif args.job == "all":
        run_all_daily_jobs()


if __name__ == "__main__":
    main()
