"""
Telegram Bot - Interface for Momentum Trading Agent
"""
import os
import json
import logging
import subprocess
from datetime import datetime
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from dotenv import load_dotenv

# Import agent components
from scanner import run_scan
from executor import (
    get_account_info,
    get_positions,
    execute_trade,
    close_position,
    get_open_orders
)
from db import (
    get_recent_trades,
    get_signal_performance,
    log_scan,
    get_baseline_metrics,
    get_weekly_metrics,
    get_monthly_metrics,
    export_trades_csv,
    export_candidates_csv,
    get_poor_signal_summary,
    get_recent_errors,
    get_error_summary,
    log_error,
    get_recent_scan_decisions
)
from config import get_runtime_config, set_runtime_config, MONITOR_CONFIG

load_dotenv()

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_USER_ID = int(os.getenv("TELEGRAM_ADMIN_ID", "0"))

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Store last scan results
last_scan_results = {
    "timestamp": None,
    "candidates": [],
    "decision": None
}


def admin_only(func):
    """Decorator to restrict commands to admin only"""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != ADMIN_USER_ID:
            logger.warning(f"Unauthorized access attempt by user {user_id}")
            return
        return await func(update, context)
    return wrapper


def get_monitor_status() -> dict:
    """Get position monitor timer status"""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "position-monitor.timer"],
            capture_output=True, text=True, timeout=5
        )
        is_active = result.stdout.strip() == "active"

        # Get next trigger time
        next_run = None
        if is_active:
            result = subprocess.run(
                ["systemctl", "show", "position-monitor.timer", "--property=NextElapseUSecRealtime"],
                capture_output=True, text=True, timeout=5
            )
            if result.stdout:
                # Parse the timestamp
                timestamp_str = result.stdout.strip().split("=")[1] if "=" in result.stdout else None
                if timestamp_str and timestamp_str != "n/a":
                    next_run = timestamp_str

        return {"active": is_active, "next_run": next_run}
    except Exception:
        return {"active": False, "next_run": None}


# ============== COMMANDS ==============

@admin_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command - show welcome message"""
    await update.message.reply_text(
        "🤖 *Momentum Agent Active*\n\n"
        "*Trading:*\n"
        "/status - Account overview\n"
        "/scan - Run momentum scan\n"
        "/candidates - Last scan results\n"
        "/execute SYMBOL - Execute trade\n"
        "/close SYMBOL - Close position\n"
        "/positions - Current positions\n"
        "/orders - Open orders\n\n"
        "*Analytics:*\n"
        "/metrics - Baseline performance\n"
        "/weekly - Last 7 days report\n"
        "/monthly - Last 30 days report\n"
        "/history - Trade history\n"
        "/performance - Signal stats\n"
        "/export - Export data to CSV\n\n"
        "*Diagnostics:*\n"
        "/error - Show recent errors (from logs)\n"
        "/errorstatus - Detailed error analysis (from DB)\n"
        "/scandecisions - Recent scan decisions\n\n"
        "*Settings:*\n"
        "/settings - View monitor settings\n"
        "/set - Change settings\n"
        "/help - Show this message",
        parse_mode="Markdown"
    )


@admin_only
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command"""
    await cmd_start(update, context)


@admin_only
async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current monitor settings"""
    config = get_runtime_config()

    auto_close_status = "ON" if config.get('auto_close_enabled', True) else "OFF"
    auto_close_emoji = "🟢" if config.get('auto_close_enabled', True) else "🔴"

    msg = "*Monitor Settings*\n\n"
    msg += f"{auto_close_emoji} Auto-Close: *{auto_close_status}*\n"
    msg += f"├── Threshold: *{config.get('auto_close_threshold', 5)}*/13 (reversal score)\n"
    msg += f"└── Alert at: *{config.get('alert_threshold', 3)}*/13\n\n"
    msg += "*Commands:*\n"
    msg += "`/set autoclose on` - Enable auto-close\n"
    msg += "`/set autoclose off` - Disable auto-close\n"
    msg += "`/set threshold 5` - Set auto-close threshold\n"
    msg += "`/set alert 3` - Set alert threshold"

    await update.message.reply_text(msg, parse_mode="Markdown")


@admin_only
async def cmd_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set monitor configuration"""
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "*Usage:*\n"
            "`/set autoclose on|off` - Enable/disable auto-close\n"
            "`/set threshold 5` - Set auto-close threshold (0-13)\n"
            "`/set alert 3` - Set alert threshold (0-13)",
            parse_mode="Markdown"
        )
        return

    setting = context.args[0].lower()
    value = context.args[1].lower()

    if setting == "autoclose":
        if value in ["on", "true", "1", "yes"]:
            set_runtime_config("auto_close_enabled", True)
            await update.message.reply_text("✅ Auto-close *ENABLED*", parse_mode="Markdown")
        elif value in ["off", "false", "0", "no"]:
            set_runtime_config("auto_close_enabled", False)
            await update.message.reply_text("✅ Auto-close *DISABLED*", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ Use `/set autoclose on` or `/set autoclose off`", parse_mode="Markdown")

    elif setting == "threshold":
        try:
            threshold = int(value)
            if 0 <= threshold <= 13:
                set_runtime_config("auto_close_threshold", threshold)
                await update.message.reply_text(f"✅ Auto-close threshold set to *{threshold}/13*", parse_mode="Markdown")
            else:
                await update.message.reply_text("❌ Threshold must be between 0 and 13")
        except ValueError:
            await update.message.reply_text("❌ Threshold must be a number (0-13)")

    elif setting == "alert":
        try:
            threshold = int(value)
            if 0 <= threshold <= 13:
                set_runtime_config("alert_threshold", threshold)
                await update.message.reply_text(f"✅ Alert threshold set to *{threshold}/13*", parse_mode="Markdown")
            else:
                await update.message.reply_text("❌ Threshold must be between 0 and 13")
        except ValueError:
            await update.message.reply_text("❌ Threshold must be a number (0-13)")

    else:
        await update.message.reply_text(
            "❌ Unknown setting. Available: `autoclose`, `threshold`, `alert`",
            parse_mode="Markdown"
        )


@admin_only
async def cmd_error(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show recent errors from logs"""
    import re
    from pathlib import Path

    log_dir = Path("/home/ubuntu/momentum-agent/logs")
    errors = []

    # Check scan.log for trade errors
    scan_log = log_dir / "scan.log"
    if scan_log.exists():
        try:
            content = scan_log.read_text()
            lines = content.split('\n')
            # Get last 100 lines and find errors
            for line in lines[-100:]:
                if '✗ Failed' in line or 'Error' in line:
                    errors.append(('scan', line.strip()))
        except Exception as e:
            errors.append(('scan', f"Could not read scan.log: {e}"))

    # Check jobs.log for job errors
    jobs_log = log_dir / "jobs.log"
    if jobs_log.exists():
        try:
            content = jobs_log.read_text()
            lines = content.split('\n')
            for line in lines[-50:]:
                if 'Error' in line or 'error' in line:
                    errors.append(('jobs', line.strip()))
        except Exception as e:
            errors.append(('jobs', f"Could not read jobs.log: {e}"))

    # Check monitor.log for monitor errors
    monitor_log = log_dir / "monitor.log"
    if monitor_log.exists():
        try:
            content = monitor_log.read_text()
            lines = content.split('\n')
            for line in lines[-50:]:
                if 'Error' in line or 'error' in line:
                    errors.append(('monitor', line.strip()))
        except Exception as e:
            errors.append(('monitor', f"Could not read monitor.log: {e}"))

    if not errors:
        await update.message.reply_text("No recent errors found in logs.")
        return

    # Format message - show last 10 unique errors
    seen = set()
    unique_errors = []
    for source, error in reversed(errors):
        error_key = error[:50]  # Use first 50 chars as key
        if error_key not in seen:
            seen.add(error_key)
            unique_errors.append((source, error))
        if len(unique_errors) >= 10:
            break

    msg = "*Recent Errors*\n\n"
    for source, error in unique_errors:
        # Truncate long errors
        if len(error) > 150:
            error = error[:150] + "..."
        msg += f"[{source}] `{error}`\n\n"

    await update.message.reply_text(msg, parse_mode="Markdown")


@admin_only
async def cmd_errorstatus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show detailed error analysis from database"""
    try:
        # Get error summary
        summary = get_error_summary(days=7)

        msg = "📋 *Error Status (Last 7 Days)*\n\n"
        msg += f"Total Errors: {summary['total_errors']}\n"
        msg += f"Unresolved: {summary['unresolved']}\n\n"

        if summary['by_type']:
            msg += "*By Type:*\n"
            for error_type, count in summary['by_type'].items():
                msg += f"  • {error_type}: {count}\n"
            msg += "\n"

        if summary['by_operation']:
            msg += "*By Operation:*\n"
            for op, count in summary['by_operation'].items():
                msg += f"  • {op}: {count}\n"
            msg += "\n"

        if summary['common_errors']:
            msg += "*Most Common:*\n"
            for error_msg, count in summary['common_errors'][:3]:
                truncated = error_msg[:60] + "..." if len(error_msg) > 60 else error_msg
                msg += f"  • ({count}x) `{truncated}`\n"
            msg += "\n"

        # Get recent errors with details
        recent = get_recent_errors(limit=5)
        if recent:
            msg += "*Recent Errors:*\n"
            for err in recent:
                timestamp = err['timestamp'][:16] if err['timestamp'] else 'N/A'
                symbol = f" [{err['symbol']}]" if err['symbol'] else ""
                msg += f"\n`{timestamp}`{symbol}\n"
                msg += f"  Type: {err['error_type']} | Op: {err['operation']}\n"
                msg += f"  {err['error_message'][:100]}\n"

        if summary['total_errors'] == 0:
            msg = "✅ *No errors in the last 7 days!*"

        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(f"❌ Error fetching error status: {e}")


@admin_only
async def cmd_scandecisions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show recent scan decisions"""
    try:
        decisions = get_recent_scan_decisions(limit=5)

        if not decisions:
            await update.message.reply_text("No scan decisions recorded yet.")
            return

        msg = "📊 *Recent Scan Decisions*\n\n"

        for d in decisions:
            timestamp = d['timestamp'][:16] if d['timestamp'] else 'N/A'
            cap = d['cap_category'].upper() if d['cap_category'] else 'ALL'
            scan_type = d['scan_type'] or 'unknown'

            msg += f"*{timestamp}* ({cap} - {scan_type})\n"
            msg += f"  Stage1: {d['stage1_count']} → Stage2: {d['stage2_count']}\n"

            # Show filter breakdown
            filters = []
            if d['filtered_by_rsi'] and d['filtered_by_rsi'] > 0:
                filters.append(f"RSI:{d['filtered_by_rsi']}")
            if d['filtered_by_breakout'] and d['filtered_by_breakout'] > 0:
                filters.append(f"Breakout:{d['filtered_by_breakout']}")
            if d['filtered_by_volume'] and d['filtered_by_volume'] > 0:
                filters.append(f"Vol:{d['filtered_by_volume']}")
            if d['filtered_by_momentum'] and d['filtered_by_momentum'] > 0:
                filters.append(f"Mom:{d['filtered_by_momentum']}")
            if filters:
                msg += f"  Filtered: {', '.join(filters)}\n"

            # Show agent actions
            try:
                buys = json.loads(d['agent_buys']) if d['agent_buys'] else []
                watches = json.loads(d['agent_watches']) if d['agent_watches'] else []
                if buys:
                    msg += f"  🟢 BUY: {', '.join(buys)}\n"
                if watches:
                    msg += f"  👀 WATCH: {', '.join(watches[:3])}\n"
            except:
                pass

            # Show execution results
            try:
                executed = json.loads(d['executed_buys']) if d['executed_buys'] else []
                failed = json.loads(d['failed_buys']) if d['failed_buys'] else []
                if executed:
                    msg += f"  ✅ Executed: {', '.join(executed)}\n"
                if failed:
                    msg += f"  ❌ Failed: {', '.join(failed)}\n"
            except:
                pass

            msg += "\n"

        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(f"❌ Error fetching scan decisions: {e}")


@admin_only
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get account status"""
    await update.message.reply_text("⏳ Fetching account status...")
    
    try:
        account = get_account_info()
        positions = get_positions()
        
        # Calculate total P&L
        total_pl = sum(p["unrealized_pl"] for p in positions)
        total_pl_pct = sum(p["unrealized_plpc"] for p in positions) / len(positions) * 100 if positions else 0
        
        pl_emoji = "🟢" if total_pl >= 0 else "🔴"
        
        msg = (
            f"📊 *Account Status*\n\n"
            f"💰 Equity: ${account['equity']:,.2f}\n"
            f"💵 Cash: ${account['cash']:,.2f}\n"
            f"💳 Buying Power: ${account['buying_power']:,.2f}\n\n"
            f"📈 Positions: {len(positions)}\n"
            f"{pl_emoji} Unrealized P&L: ${total_pl:,.2f} ({total_pl_pct:.1f}%)\n"
        )
        
        if positions:
            msg += "\n*Open Positions:*\n"
            for p in positions:
                emoji = "🟢" if p["unrealized_pl"] >= 0 else "🔴"
                msg += f"{emoji} {p['symbol']}: {p['qty']} @ ${p['current_price']:.2f} ({p['unrealized_plpc']*100:.1f}%)\n"

        # Add monitor status
        monitor = get_monitor_status()
        monitor_emoji = "🟢" if monitor["active"] else "🔴"
        msg += f"\n*Position Monitor:* {monitor_emoji} {'Active' if monitor['active'] else 'Inactive'}"
        if monitor["next_run"]:
            msg += f"\n⏰ Next check: {monitor['next_run'][:19]}"

        await update.message.reply_text(msg, parse_mode="Markdown")
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


@admin_only
async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Run momentum scan"""
    global last_scan_results

    await update.message.reply_text("⏳ Running momentum scan... (this may take 30-60 seconds)")

    try:
        candidates = run_scan()

        if not candidates:
            last_scan_results = {
                "timestamp": datetime.now().isoformat(),
                "candidates": [],
                "decision": None
            }
            await update.message.reply_text("📭 No candidates found matching criteria.")
            return

        # Store results
        last_scan_results = {
            "timestamp": datetime.now().isoformat(),
            "candidates": candidates,
            "decision": None
        }

        # Log scan
        log_scan(candidates, {}, None)

        # Format response
        msg = f"✅ *Scan Complete*\n"
        msg += f"Found {len(candidates)} candidates\n\n"
        msg += "*Top 5:*\n"

        for i, c in enumerate(candidates[:5], 1):
            msg += (
                f"\n{i}. *{c['symbol']}* (Score: {c['composite_score']})\n"
                f"   💲 ${c['price']:.2f} | ROC: {c['roc_10d']}%\n"
                f"   📊 Vol: {c['volume_surge']}x | Gap: {c['gap_up']}% | Breakout: {c['breakout_5d']}\n"
            )

        msg += f"\n\nUse `/execute SYMBOL` to trade"

        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        logger.error(f"Scan error: {e}\n{error_details}")
        await update.message.reply_text(f"❌ Scan failed: {str(e)}\n\nCheck logs for details.")


@admin_only
async def cmd_candidates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show last scan results"""
    global last_scan_results
    
    if not last_scan_results["timestamp"]:
        await update.message.reply_text("📭 No scan results yet. Run /scan first.")
        return
    
    candidates = last_scan_results["candidates"]
    
    if not candidates:
        await update.message.reply_text("📭 Last scan found no candidates.")
        return
    
    msg = f"📋 *Last Scan Results*\n"
    msg += f"🕐 {last_scan_results['timestamp'][:16]}\n\n"
    
    for i, c in enumerate(candidates[:10], 1):
        msg += (
            f"{i}. *{c['symbol']}* - Score: {c['composite_score']}\n"
            f"   ${c['price']:.2f} | ROC: {c['roc_10d']}% | Vol: {c['volume_surge']}x\n"
        )
    
    await update.message.reply_text(msg, parse_mode="Markdown")


@admin_only
async def cmd_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Execute trade for a symbol"""
    global last_scan_results
    
    if not context.args:
        await update.message.reply_text("Usage: `/execute SYMBOL`\nExample: `/execute NVDA`", parse_mode="Markdown")
        return
    
    symbol = context.args[0].upper()
    
    # Check if symbol is in last scan
    candidate = next(
        (c for c in last_scan_results.get("candidates", []) if c["symbol"] == symbol),
        None
    )
    
    if not candidate:
        await update.message.reply_text(
            f"⚠️ {symbol} not in last scan results.\n"
            f"Run /scan first or check /candidates"
        )
        return
    
    # Confirmation
    if len(context.args) < 2 or context.args[1].lower() != "confirm":
        msg = (
            f"⚠️ *Confirm Trade*\n\n"
            f"Symbol: *{symbol}*\n"
            f"Price: ${candidate['price']:.2f}\n"
            f"Score: {candidate['composite_score']}\n"
            f"ROC: {candidate['roc_10d']}%\n\n"
            f"Send `/execute {symbol} confirm` to proceed"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
        return
    
    # Execute
    await update.message.reply_text(f"⏳ Executing trade for {symbol}...")
    
    try:
        result = execute_trade(symbol, candidate, {})
        
        if result.get("success"):
            msg = (
                f"✅ *Trade Executed*\n\n"
                f"Symbol: {symbol}\n"
                f"Shares: {result['qty']}\n"
                f"Est. Cost: ${result['estimated_cost']:,.2f}\n"
                f"Trailing Stop: {result['trailing_stop_pct']}%"
            )
        else:
            msg = f"❌ Trade failed: {result.get('error')}"
        
        await update.message.reply_text(msg, parse_mode="Markdown")
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


@admin_only
async def cmd_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Close a position"""
    if not context.args:
        await update.message.reply_text("Usage: `/close SYMBOL`", parse_mode="Markdown")
        return
    
    symbol = context.args[0].upper()
    reason = " ".join(context.args[1:]) if len(context.args) > 1 else "manual"
    
    await update.message.reply_text(f"⏳ Closing position for {symbol}...")
    
    try:
        result = close_position(symbol, reason)
        
        if result.get("success"):
            msg = f"✅ Closed {result['qty']} shares of {symbol}"
        else:
            msg = f"❌ Failed: {result.get('error')}"
        
        await update.message.reply_text(msg)
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


@admin_only
async def cmd_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current positions"""
    try:
        positions = get_positions()
        
        if not positions:
            await update.message.reply_text("📭 No open positions.")
            return
        
        msg = f"📊 *Open Positions* ({len(positions)})\n\n"
        
        for p in positions:
            emoji = "🟢" if p["unrealized_pl"] >= 0 else "🔴"
            msg += (
                f"{emoji} *{p['symbol']}*\n"
                f"   Qty: {p['qty']} @ ${p['avg_entry_price']:.2f}\n"
                f"   Now: ${p['current_price']:.2f}\n"
                f"   P&L: ${p['unrealized_pl']:.2f} ({p['unrealized_plpc']*100:.1f}%)\n\n"
            )
        
        await update.message.reply_text(msg, parse_mode="Markdown")
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


@admin_only
async def cmd_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show open orders"""
    try:
        orders = get_open_orders()
        
        if not orders:
            await update.message.reply_text("📭 No open orders.")
            return
        
        msg = f"📋 *Open Orders* ({len(orders)})\n\n"
        
        for o in orders:
            msg += f"• {o['symbol']}: {o['side']} {o['qty']} ({o['type']})\n"
        
        await update.message.reply_text(msg, parse_mode="Markdown")
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


@admin_only
async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show trade history"""
    try:
        trades = get_recent_trades(limit=10)
        
        if not trades:
            await update.message.reply_text("📭 No trade history yet.")
            return
        
        # Stats
        closed = [t for t in trades if t["status"] == "closed"]
        wins = sum(1 for t in closed if t.get("pnl_pct", 0) > 0)
        losses = len(closed) - wins
        win_rate = wins / len(closed) * 100 if closed else 0
        
        msg = f"📊 *Trade History*\n\n"
        msg += f"Record: {wins}W / {losses}L ({win_rate:.0f}% win rate)\n\n"
        
        for t in trades[:10]:
            if t["status"] == "closed":
                emoji = "🟢" if t.get("pnl_pct", 0) > 0 else "🔴"
                msg += f"{emoji} {t['symbol']}: {t.get('pnl_pct', 0):.1f}% ({t.get('exit_reason', 'N/A')})\n"
            else:
                msg += f"⏳ {t['symbol']}: OPEN @ ${t['entry_price']:.2f}\n"
        
        await update.message.reply_text(msg, parse_mode="Markdown")
        
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


@admin_only
async def cmd_performance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show signal performance"""
    try:
        perf = get_signal_performance()

        if not perf:
            await update.message.reply_text("📭 No performance data yet. Need more trades.")
            return

        msg = "📊 *Signal Performance*\n\n"

        for signal, stats in perf.items():
            win_rate = stats.get("win_rate", 0) * 100
            emoji = "🟢" if win_rate >= 50 else "🔴"
            msg += f"{emoji} {signal}: {win_rate:.0f}% ({stats.get('count', 0)} trades)\n"

        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


@admin_only
async def cmd_metrics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show baseline metrics since inception"""
    try:
        metrics = get_baseline_metrics()

        if metrics['total_trades'] == 0:
            await update.message.reply_text("📭 No trades yet. Metrics will appear after first closed trade.")
            return

        # Get current account for portfolio values
        account = get_account_info()

        msg = "📈 *BASELINE METRICS*\n"
        msg += f"_Since {metrics['first_trade'][:10] if metrics['first_trade'] else 'inception'}_\n\n"

        msg += "*Performance:*\n"
        msg += f"├── Total Trades: {metrics['total_trades']}\n"
        msg += f"├── Win Rate: {metrics['win_rate']}%\n"
        msg += f"├── Avg Win: +{metrics['avg_win']}%\n"
        msg += f"├── Avg Loss: -{metrics['avg_loss']}%\n"
        msg += f"├── Win/Loss Ratio: {metrics['win_loss_ratio']}\n"
        msg += f"├── Profit Factor: {metrics['profit_factor']}\n"
        msg += f"├── Max Drawdown: -{metrics['max_drawdown']}%\n"
        msg += f"├── Sharpe Ratio: {metrics['sharpe_ratio']}\n"
        msg += f"└── Total P&L: ${metrics['total_pnl']:,.2f}\n\n"

        msg += "*Portfolio:*\n"
        msg += f"├── Current Equity: ${account['equity']:,.2f}\n"
        msg += f"└── Cash: ${account['cash']:,.2f}\n"

        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


@admin_only
async def cmd_weekly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show last 7 days performance"""
    try:
        metrics = get_weekly_metrics(weeks_back=1)

        msg = "📊 *WEEKLY REPORT*\n"
        msg += f"_{metrics['period']}_\n\n"

        msg += "*Performance:*\n"
        msg += f"├── P&L: ${metrics['total_pnl']:,.2f}\n"
        msg += f"├── Trades: {metrics['total_trades']}\n"
        msg += f"├── Wins: {metrics['wins']} ({metrics['win_rate']}%)\n"
        msg += f"└── Losses: {metrics['losses']}\n"

        if metrics['best_trade']:
            msg += f"\n🏆 Best: {metrics['best_trade']['symbol']} +{metrics['best_trade']['pnl']}%\n"
        if metrics['worst_trade']:
            msg += f"📉 Worst: {metrics['worst_trade']['symbol']} {metrics['worst_trade']['pnl']}%\n"

        msg += f"\n*Scanning:*\n"
        msg += f"├── Scans Run: {metrics['scans_run']}\n"
        msg += f"└── Candidates Found: {metrics['candidates_found']}\n"

        # Add poor signals summary for self-learning
        poor_signals = get_poor_signal_summary(days=7)
        if poor_signals['total_poor_signals'] > 0:
            msg += f"\n⚠️ *Poor Signals (Reversal Exits):* {poor_signals['total_poor_signals']}\n"
            if poor_signals['avg_pnl']:
                msg += f"├── Avg PnL: {poor_signals['avg_pnl']:+.1f}%\n"
            if poor_signals['common_reversal_signals']:
                top_signal = poor_signals['common_reversal_signals'][0]
                msg += f"├── Top reversal: {top_signal[0]} ({top_signal[1]}x)\n"
            if poor_signals['common_entry_signals']:
                top_entry = poor_signals['common_entry_signals'][0]
                msg += f"└── Entry signal review: {top_entry[0]}\n"
            msg += "\n📝 *Action:* Review agent prompt for signal quality"

        if metrics['spy_change']:
            beat_emoji = "✅" if metrics['beat_spy'] else "❌"
            msg += f"\n*vs SPY:* {beat_emoji} SPY: {metrics['spy_change']:+.1f}%"

        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


@admin_only
async def cmd_monthly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show last 30 days performance"""
    try:
        metrics = get_monthly_metrics(months_back=1)

        msg = "📅 *MONTHLY REPORT*\n"
        msg += f"_{metrics['period']}_\n\n"

        msg += "*Performance:*\n"
        msg += f"├── P&L: ${metrics['total_pnl']:,.2f}\n"
        msg += f"├── Trades: {metrics['total_trades']}\n"
        msg += f"├── Win Rate: {metrics['win_rate']}%\n"
        msg += f"├── Avg Trade: {metrics['avg_trade']:+.1f}%\n"
        msg += f"└── Max DD: -{metrics['max_drawdown']}%\n"

        if metrics['weekly_breakdown']:
            msg += "\n*By Week:*\n"
            for i, week in enumerate(metrics['weekly_breakdown'][-4:], 1):
                emoji = "🟢" if week['pnl'] >= 0 else "🔴"
                msg += f"{emoji} Week {i}: {week['pnl']:+.1f}%\n"

        if metrics['top_winners']:
            msg += "\n*Top Winners:*\n"
            for w in metrics['top_winners'][:3]:
                msg += f"🟢 {w['symbol']}: +{w['pnl']}%\n"

        if metrics['top_losers']:
            msg += "\n*Worst Losers:*\n"
            for l in metrics['top_losers'][:3]:
                msg += f"🔴 {l['symbol']}: {l['pnl']}%\n"

        # Add poor signals summary for self-learning
        poor_signals = get_poor_signal_summary(days=30)
        if poor_signals['total_poor_signals'] > 0:
            msg += f"\n⚠️ *Poor Signals (Reversal Exits):* {poor_signals['total_poor_signals']}\n"
            if poor_signals['avg_pnl']:
                msg += f"├── Avg PnL: {poor_signals['avg_pnl']:+.1f}%\n"
            if poor_signals['avg_holding_days']:
                msg += f"├── Avg Hold: {poor_signals['avg_holding_days']:.1f} days\n"
            if poor_signals['common_reversal_signals']:
                msg += f"├── Common reversals:\n"
                for sig, count in poor_signals['common_reversal_signals'][:3]:
                    msg += f"│   • {sig}: {count}x\n"
            if poor_signals['common_entry_signals']:
                msg += f"└── Entry signals to review:\n"
                for sig, count in poor_signals['common_entry_signals'][:3]:
                    msg += f"    • {sig}: {count}x\n"
            msg += "\n📝 *Weekly Action:* Review agent prompt for signal quality"

        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


@admin_only
async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Export trade data to CSV"""
    try:
        import os
        from pathlib import Path

        # Create exports directory
        export_dir = Path("/home/ubuntu/momentum-agent/exports")
        export_dir.mkdir(exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Export trades
        trades_file = export_dir / f"trades_{timestamp}.csv"
        trades_count = export_trades_csv(str(trades_file))

        # Export candidates
        candidates_file = export_dir / f"candidates_{timestamp}.csv"
        candidates_count = export_candidates_csv(str(candidates_file))

        msg = "📤 *Data Exported*\n\n"
        msg += f"*Trades:* {trades_count} records\n"
        msg += f"└── `{trades_file}`\n\n"
        msg += f"*Candidates:* {candidates_count} records\n"
        msg += f"└── `{candidates_file}`\n\n"
        msg += "_Files saved on server. Use SCP to download._"

        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")


@admin_only
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle natural language messages"""
    text = update.message.text.lower()
    
    # Simple intent matching (can be expanded with Claude API later)
    if "status" in text or "how" in text and "doing" in text:
        await cmd_status(update, context)
    elif "scan" in text or "find" in text or "search" in text:
        await cmd_scan(update, context)
    elif "position" in text or "holding" in text:
        await cmd_positions(update, context)
    elif "history" in text or "past" in text:
        await cmd_history(update, context)
    elif "performance" in text or "signal" in text:
        await cmd_performance(update, context)
    else:
        await update.message.reply_text(
            "I understood you said something, but I'm not sure what action to take.\n\n"
            "Try /help for available commands."
        )


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    logger.error(f"Error: {context.error}")
    if update and update.message:
        await update.message.reply_text("❌ An error occurred. Check logs.")


def main():
    """Start the bot"""
    if not TELEGRAM_BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not set in .env")
        return
    
    if ADMIN_USER_ID == 0:
        print("ERROR: TELEGRAM_ADMIN_ID not set in .env")
        return
    
    print(f"Starting Momentum Agent Bot...")
    print(f"Admin User ID: {ADMIN_USER_ID}")
    
    # Create application
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("error", cmd_error))
    app.add_handler(CommandHandler("errorstatus", cmd_errorstatus))
    app.add_handler(CommandHandler("scandecisions", cmd_scandecisions))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("set", cmd_set))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("candidates", cmd_candidates))
    app.add_handler(CommandHandler("execute", cmd_execute))
    app.add_handler(CommandHandler("close", cmd_close))
    app.add_handler(CommandHandler("positions", cmd_positions))
    app.add_handler(CommandHandler("orders", cmd_orders))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("performance", cmd_performance))
    app.add_handler(CommandHandler("metrics", cmd_metrics))
    app.add_handler(CommandHandler("weekly", cmd_weekly))
    app.add_handler(CommandHandler("monthly", cmd_monthly))
    app.add_handler(CommandHandler("export", cmd_export))
    
    # Natural language handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Error handler
    app.add_error_handler(error_handler)
    
    # Start polling
    print("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

