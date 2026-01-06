"""
Main Entry Point - Autonomous Momentum Trading Agent
"""
import argparse
import json
from datetime import datetime
from scanner import run_scan
from agent import get_portfolio_decision
from config import get_cap_config, TRADING_CONFIG, get_runtime_config
from executor import (
    execute_trade,
    get_account_info,
    get_positions,
    close_position,
    get_open_orders
)
from monitor import calculate_reversal_signals, get_data_client, get_historical_bars
from db import log_scan, get_open_trades, get_recent_trades
import requests
import os
import fcntl
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID")
LOCK_FILE = "/tmp/momentum_agent_scan.lock"


class ScanLock:
    """File-based lock to prevent concurrent scans"""
    def __init__(self):
        self.lock_file = None
        self.locked = False

    def acquire(self, timeout: int = 0) -> bool:
        """Try to acquire the lock. Returns True if successful."""
        try:
            self.lock_file = open(LOCK_FILE, 'w')
            fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.lock_file.write(f"{os.getpid()}\n{datetime.now().isoformat()}")
            self.lock_file.flush()
            self.locked = True
            return True
        except (IOError, OSError):
            if self.lock_file:
                self.lock_file.close()
            self.lock_file = None
            return False

    def release(self):
        """Release the lock"""
        if self.lock_file:
            try:
                fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_UN)
                self.lock_file.close()
            except Exception:
                pass
            self.lock_file = None
            self.locked = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


def send_telegram_message(message: str):
    """Send a message to Telegram admin"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ADMIN_ID:
        print("Telegram not configured, skipping notification")
        return

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_ADMIN_ID,
            "text": message,
            "parse_mode": "Markdown"
        }
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Failed to send Telegram message: {e}")


def run_autonomous_scan(scan_type: str = "open", dry_run: bool = False, cap: str = None, max_buys: int = None):
    """
    Autonomous scan and trade execution loop.

    1. Run scanner to find candidates
    2. Get reversal scores for current positions
    3. Send everything to Claude agent for decision
    4. Execute agent's decisions (closes and buys)
    5. Send summary to Telegram

    Args:
        scan_type: Type of scan (open/midday/close)
        dry_run: If True, don't execute trades
        cap: Market cap filter (large/mid/small/None for all)
        max_buys: Maximum number of buys to execute (None for no limit)
    """
    # Acquire lock to prevent concurrent scans
    lock = ScanLock()
    if not lock.acquire():
        print("=" * 70)
        print("SCAN SKIPPED - Another scan is already running")
        print(f"Time: {datetime.now()}")
        print("=" * 70)
        return {"skipped": True, "reason": "Another scan in progress"}

    try:
        cap_label = cap.upper() if cap else "ALL"
        cap_config = get_cap_config(cap)
        effective_max_buys = max_buys if max_buys is not None else cap_config.get('max_buys_per_scan', 2)
        print("=" * 70)
        print(f"MOMENTUM AGENT - Autonomous {scan_type.upper()} Scan")
        print(f"Time: {datetime.now()}")
        print(f"Mode: {'DRY RUN' if dry_run else 'LIVE EXECUTION'}")
        print(f"Cap: {cap_label} | Max Positions: {cap_config['max_positions']}/{TRADING_CONFIG['max_positions']} | Max Buys: {effective_max_buys}")
        print("=" * 70)

        # ========== STEP 1: Get Account & Positions ==========
        print("\n[1/5] Fetching account and positions...")

        try:
            account = get_account_info()
            positions = get_positions()
            print(f"  Account Equity: ${account['equity']:,.2f}")
            print(f"  Buying Power: ${account['buying_power']:,.2f}")
            print(f"  Current Positions: {len(positions)}")
        except Exception as e:
            error_msg = f"Error getting account info: {e}"
            print(error_msg)
            send_telegram_message(f"❌ *Scan Failed*\n{error_msg}")
            return

        # Show current positions
        if positions:
            print("\n  Current Positions:")
            for p in positions:
                pnl_pct = p['unrealized_plpc'] * 100
                emoji = "🟢" if pnl_pct >= 0 else "🔴"
                print(f"    {emoji} {p['symbol']}: {p['qty']} shares, P/L: {pnl_pct:+.1f}%")

        # ========== STEP 2: Get Reversal Scores for Positions ==========
        print("\n[2/5] Calculating reversal scores for positions...")

        reversal_scores = {}
        if positions:
            data_client = get_data_client()
            for p in positions:
                symbol = p['symbol']
                try:
                    bars = get_historical_bars(data_client, symbol, days=30)
                    if bars:
                        result = calculate_reversal_signals(symbol, bars, p)
                        reversal_scores[symbol] = {
                            "score": result.get('score', 0),
                            "signals": result.get('signals', [])
                        }
                        print(f"    {symbol}: Reversal Score = {result.get('score', 0)}/13")
                    else:
                        print(f"    {symbol}: No bar data available")
                        reversal_scores[symbol] = {"score": 0, "signals": []}
                except Exception as e:
                    print(f"    {symbol}: Error calculating reversal - {e}")
                    reversal_scores[symbol] = {"score": 0, "signals": []}

        # Check if we should skip new buys (positions healthy AND near capacity)
        runtime_config = get_runtime_config()
        skip_buys_when_healthy = runtime_config.get('skip_buys_when_healthy', True)
        healthy_threshold = runtime_config.get('healthy_threshold', 3)
        min_positions_for_skip = runtime_config.get('min_positions_for_skip', 3)  # Need at least this many positions to skip
        skip_new_buys = False

        if skip_buys_when_healthy and positions and reversal_scores:
            # Count positions for this cap category
            from executor import get_symbol_cap
            cap_positions = sum(1 for p in positions if get_symbol_cap(p['symbol']) == cap) if cap else len(positions)
            max_cap_positions = cap_config.get('max_positions', 2)
            max_total_positions = TRADING_CONFIG['max_positions']

            all_healthy = all(
                rs.get('score', 0) < healthy_threshold
                for rs in reversal_scores.values()
            )

            # Only skip if we have meaningful position count AND all are healthy
            # Skip if: at max for this cap, OR total positions >= min threshold
            at_cap_limit = cap and cap_positions >= max_cap_positions
            has_enough_positions = len(positions) >= min_positions_for_skip

            if all_healthy and (at_cap_limit or has_enough_positions):
                skip_new_buys = True
                if at_cap_limit:
                    print(f"\n  ✓ At max {cap} cap positions ({cap_positions}/{max_cap_positions}) and all healthy")
                else:
                    print(f"\n  ✓ {len(positions)} positions (>= {min_positions_for_skip}) and all healthy (score < {healthy_threshold})")
                print(f"    → Will log scan results but SKIP new buys to let winners run")
            elif all_healthy:
                print(f"\n  ✓ All {len(positions)} positions healthy, but only {len(positions)}/{min_positions_for_skip} min for skip mode")
                print(f"    → Will continue scanning for opportunities")

        # ========== STEP 3: Run Scanner ==========
        print("\n[3/5] Running momentum scanner...")

        candidates = run_scan(cap=cap)

        if candidates:
            print(f"\n  Found {len(candidates)} candidates:")
            for c in candidates[:5]:
                print(f"    {c['symbol']}: Score={c['composite_score']}, "
                      f"ROC={c['roc_10d']:+.1f}%, Vol={c['volume_surge']:.1f}x")
            if len(candidates) > 5:
                print(f"    ... and {len(candidates) - 5} more")
        else:
            print("  No candidates found matching criteria")

        # ========== STEP 4: Get Agent Decision ==========
        print("\n[4/5] Getting Claude agent decision...")

        decision = get_portfolio_decision(
            account=account,
            positions=positions,
            candidates=candidates,
            reversal_scores=reversal_scores,
            scan_type=scan_type
        )

        print(f"\n  Market Assessment: {decision.get('market_assessment', 'N/A')[:100]}...")
        print(f"  Portfolio Summary: {decision.get('portfolio_summary', 'N/A')[:100]}...")

        execution_plan = decision.get('execution_plan', {})
        closes = execution_plan.get('closes', [])
        buys = execution_plan.get('buys', [])
        watchlist = execution_plan.get('new_watchlist', [])

        # Get cap config for max_buys if not specified via CLI
        cap_config = get_cap_config(cap)
        effective_max_buys = max_buys if max_buys is not None else cap_config.get('max_buys_per_scan', 2)

        # Limit buys based on effective max
        if len(buys) > effective_max_buys:
            print(f"\n  Limiting buys from {len(buys)} to {effective_max_buys} (cap config)")
            watchlist = buys[effective_max_buys:] + watchlist  # Move excess to watchlist
            buys = buys[:effective_max_buys]

        print(f"\n  Execution Plan:")
        print(f"    CLOSE: {closes or 'None'}")
        print(f"    BUY: {buys or 'None'}")
        print(f"    WATCHLIST: {watchlist or 'None'}")

        # ========== STEP 5: Execute Decisions ==========
        print("\n[5/5] Executing decisions...")

        results = {
            "closes": [],
            "buys": [],
            "errors": []
        }

        # Execute CLOSE orders
        for symbol in closes:
            if dry_run:
                print(f"  [DRY RUN] Would close: {symbol}")
                results["closes"].append({"symbol": symbol, "status": "dry_run"})
            else:
                print(f"  Closing {symbol}...")
                try:
                    # Find reasoning from decision
                    reason = next(
                        (a['reasoning'] for a in decision.get('position_actions', [])
                         if a['symbol'] == symbol and a['action'] == 'CLOSE'),
                        'Agent decision'
                    )
                    result = close_position(symbol, reason)
                    if result.get('success'):
                        print(f"    ✓ Closed {symbol}: {result.get('qty')} shares")
                        results["closes"].append({"symbol": symbol, "status": "success", **result})
                    else:
                        print(f"    ✗ Failed to close {symbol}: {result.get('error')}")
                        results["errors"].append({"symbol": symbol, "action": "close", "error": result.get('error')})
                except Exception as e:
                    print(f"    ✗ Error closing {symbol}: {e}")
                    results["errors"].append({"symbol": symbol, "action": "close", "error": str(e)})

        # Execute BUY orders (skip if all positions are healthy)
        if skip_new_buys and buys:
            print(f"  ⏸️ SKIPPING {len(buys)} buys - all positions healthy, letting winners run")
            print(f"     Skipped: {', '.join(buys)}")
            watchlist = buys + watchlist  # Move to watchlist for tracking
            buys = []  # Clear buys

        for symbol in buys:
            # Find candidate data
            candidate = next((c for c in candidates if c['symbol'] == symbol), None)
            if not candidate:
                print(f"  ✗ Cannot buy {symbol}: Not in candidates list")
                results["errors"].append({"symbol": symbol, "action": "buy", "error": "Not in candidates"})
                continue

            if dry_run:
                print(f"  [DRY RUN] Would buy: {symbol} @ ${candidate['price']:.2f}")
                results["buys"].append({"symbol": symbol, "status": "dry_run", "price": candidate['price']})
            else:
                print(f"  Buying {symbol}...")
                try:
                    result = execute_trade(symbol, candidate, decision, cap=cap)
                    if result.get('success'):
                        print(f"    ✓ Bought {symbol}: {result.get('qty')} shares @ ~${candidate['price']:.2f}")
                        results["buys"].append({"symbol": symbol, "status": "success", **result})
                    else:
                        print(f"    ✗ Failed to buy {symbol}: {result.get('error')}")
                        results["errors"].append({"symbol": symbol, "action": "buy", "error": result.get('error')})
                except Exception as e:
                    print(f"    ✗ Error buying {symbol}: {e}")
                    results["errors"].append({"symbol": symbol, "action": "buy", "error": str(e)})

        # ========== Log and Notify ==========
        log_scan(candidates, decision, buys[0] if buys else None)

        # Build Telegram summary
        scan_emoji = {"open": "🌅", "midday": "☀️", "close": "🌆"}.get(scan_type, "📊")
        cap_emoji = {"large": "🔵", "mid": "🟡", "small": "🟢"}.get(cap, "⚪")
        max_total_positions = TRADING_CONFIG["max_positions"]
        summary_lines = [
            f"{scan_emoji} *{scan_type.upper()} SCAN COMPLETE*",
            f"{cap_emoji} Cap: {cap_label}" if cap else "",
            f"_{datetime.now().strftime('%Y-%m-%d %H:%M')} ET_\n",
            f"💰 Equity: ${account['equity']:,.2f}",
            f"📊 Positions: {len(positions)}/{max_total_positions}\n",
        ]
        summary_lines = [line for line in summary_lines if line]  # Remove empty lines

        if decision.get('market_assessment'):
            summary_lines.append(f"*Market:* {decision['market_assessment'][:150]}\n")

        if closes:
            summary_lines.append(f"🔴 *CLOSED:* {', '.join(closes)}")
        if buys:
            summary_lines.append(f"🟢 *BOUGHT:* {', '.join(buys)}")
        if skip_new_buys:
            summary_lines.append("⏸️ _Buys skipped - positions healthy, letting winners run_")
        if watchlist:
            summary_lines.append(f"👀 *WATCHING:* {', '.join(watchlist)}")
        if not closes and not buys and not skip_new_buys:
            summary_lines.append("_No trades executed_")

        if results["errors"]:
            summary_lines.append(f"\n⚠️ *Errors:* {len(results['errors'])}")

        send_telegram_message("\n".join(summary_lines))

        print("\n" + "=" * 70)
        print("Scan complete.")
        print("=" * 70)

        return results

    finally:
        lock.release()


def check_positions():
    """Position check (legacy)"""
    print("=" * 60)
    print(f"POSITION CHECK - {datetime.now()}")
    print("=" * 60)

    account = get_account_info()
    print(f"\nAccount Equity: ${account['equity']:,.2f}")

    positions = get_positions()
    if not positions:
        print("\nNo open positions.")
        return

    print(f"\nOpen Positions ({len(positions)}):")
    for p in positions:
        pnl_emoji = "✓" if p['unrealized_pl'] > 0 else "✗"
        print(f"  {pnl_emoji} {p['symbol']}: {p['qty']} shares @ ${p['avg_entry_price']:.2f}")
        print(f"      Current: ${p['current_price']:.2f}, P/L: ${p['unrealized_pl']:.2f} ({p['unrealized_plpc']*100:.1f}%)")

    orders = get_open_orders()
    if orders:
        print(f"\nOpen Orders ({len(orders)}):")
        for o in orders:
            print(f"  {o['symbol']}: {o['side']} {o['qty']} ({o['type']})")


def show_history():
    """Show trade history"""
    print("=" * 60)
    print("TRADE HISTORY")
    print("=" * 60)

    trades = get_recent_trades(limit=20)

    if not trades:
        print("\nNo trade history yet.")
        return

    wins = sum(1 for t in trades if t.get("pnl_pct", 0) > 0 and t["status"] == "closed")
    losses = sum(1 for t in trades if t.get("pnl_pct", 0) <= 0 and t["status"] == "closed")
    open_trades = sum(1 for t in trades if t["status"] == "open")

    print(f"\nSummary: {wins}W / {losses}L / {open_trades} Open")

    print("\nRecent Trades:")
    for t in trades:
        status = t["status"]
        if status == "closed":
            pnl = t.get("pnl_pct", 0)
            emoji = "✓" if pnl > 0 else "✗"
            print(f"  {emoji} {t['symbol']}: {pnl:.1f}% ({t.get('exit_reason', 'N/A')})")
        else:
            print(f"  ○ {t['symbol']}: OPEN @ ${t['entry_price']:.2f}")


def main():
    parser = argparse.ArgumentParser(description="Momentum Trading Agent")
    parser.add_argument(
        "command",
        choices=["scan", "check", "history", "positions"],
        help="Command to run"
    )
    parser.add_argument(
        "--type",
        choices=["open", "midday", "close"],
        default="open",
        help="Scan type (open/midday/close)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run scan without executing trades"
    )
    parser.add_argument(
        "--cap",
        choices=["large", "mid", "small"],
        default=None,
        help="Market cap filter (large/mid/small, default: all)"
    )
    parser.add_argument(
        "--max-buys",
        type=int,
        default=None,
        help="Maximum number of buys to execute per scan (default: no limit)"
    )

    args = parser.parse_args()

    if args.command == "scan":
        run_autonomous_scan(
            scan_type=args.type,
            dry_run=args.dry_run,
            cap=args.cap,
            max_buys=args.max_buys
        )
    elif args.command == "check":
        check_positions()
    elif args.command == "positions":
        check_positions()
    elif args.command == "history":
        show_history()


if __name__ == "__main__":
    main()
