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

# Store last flow results
last_flow_results = {
    "timestamp": None,
    "signals": [],
    "analyzed": []
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
        "*Stock Trading:*\n"
        "/status - Account overview\n"
        "/scan - Run momentum scan\n"
        "/candidates - Last scan results\n"
        "/execute SYMBOL - Execute trade\n"
        "/close SYMBOL - Close position\n"
        "/positions - Current positions\n"
        "/orders - Open orders\n\n"
        "*Options Flow:*\n"
        "/flow - Scan options flow\n"
        "/analyze - Analyze with Claude\n"
        "/options - Options positions\n"
        "/greeks - Portfolio Greeks\n"
        "/expirations - DTE alerts\n"
        "/flowperf - Signal factor stats\n"
        "/buyoption SYMBOL - Buy option\n"
        "/closeoption CONTRACT - Close option\n"
        "/reconcile - Sync DB with Alpaca\n\n"
        "*Options AI Agents:*\n"
        "/optionsreview - AI position review\n"
        "/portfolioreview - AI portfolio review\n"
        "/optionsmonitor - Full AI monitoring\n\n"
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


# ============== OPTIONS FLOW COMMANDS ==============

@admin_only
async def cmd_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Run options flow scan"""
    global last_flow_results

    await update.message.reply_text("⏳ Scanning options flow...")

    try:
        from flow_scanner import run_flow_scan, get_flow_summary

        signals = run_flow_scan(
            min_premium=100000,
            min_vol_oi=1.0,
            min_score=8,
            limit=50,
        )

        if not signals:
            last_flow_results = {"timestamp": datetime.now().isoformat(), "signals": [], "analyzed": []}
            await update.message.reply_text("📭 No high-conviction flow signals found.")
            return

        # Store for later execution
        last_flow_results = {"timestamp": datetime.now().isoformat(), "signals": signals, "analyzed": []}

        summary = get_flow_summary(signals)

        msg = f"✅ *Flow Scan Complete*\n"
        msg += f"Found {summary['count']} signals (score >= 8)\n\n"
        msg += f"*Summary:*\n"
        msg += f"├── Total Premium: ${summary['total_premium']:,.0f}\n"
        msg += f"├── Bullish: {summary['bullish_count']} | Bearish: {summary['bearish_count']}\n"
        msg += f"├── Sweeps: {summary['sweeps']} | Floor: {summary['floor_trades']}\n"
        msg += f"└── Avg Score: {summary['avg_score']:.1f}\n\n"

        msg += "*Top 5 Signals:*\n"
        for i, s in enumerate(signals[:5], 1):
            emoji = "📈" if s.sentiment == "bullish" else "📉"
            msg += f"\n{i}. {emoji} *{s.symbol}* {s.option_type.upper()} ${s.strike}\n"
            msg += f"   ${s.premium:,.0f} | Score: {s.score} | Vol/OI: {s.vol_oi_ratio}x\n"

        msg += f"\n\nUse `/analyze` to generate theses or `/buyoption SYMBOL` to trade"

        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Flow scan error: {e}")
        await update.message.reply_text(f"❌ Flow scan failed: {str(e)}")


@admin_only
async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Analyze top flow signals with Claude"""
    global last_flow_results

    if not last_flow_results.get("signals"):
        await update.message.reply_text("📭 No flow signals. Run /flow first.")
        return

    await update.message.reply_text("⏳ Analyzing signals with Claude... (30-60 sec)")

    try:
        from flow_analyzer import analyze_flow_signals, format_flow_analysis_for_telegram

        signals = last_flow_results["signals"][:5]  # Top 5
        enriched = analyze_flow_signals(signals, max_analyze=5)

        last_flow_results["analyzed"] = enriched

        for e in enriched:
            msg = format_flow_analysis_for_telegram(e)
            await update.message.reply_text(msg, parse_mode="Markdown")

        buys = [e for e in enriched if e.recommendation == "BUY"]
        if buys:
            symbols = ", ".join(e.signal.symbol for e in buys)
            await update.message.reply_text(
                f"🟢 *BUY Recommendations:* {symbols}\n\nUse `/buyoption SYMBOL confirm` to execute",
                parse_mode="Markdown"
            )

    except Exception as e:
        logger.error(f"Analysis error: {e}")
        await update.message.reply_text(f"❌ Analysis failed: {str(e)}")


@admin_only
async def cmd_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show options positions and performance"""
    try:
        from options_executor import get_options_positions, get_options_summary
        from db import get_options_performance

        summary = get_options_summary()
        perf = get_options_performance()

        msg = "📊 *Options Positions*\n\n"

        if summary["count"] == 0:
            msg += "No open options positions.\n\n"
        else:
            msg += f"*Open Positions ({summary['count']}):*\n"
            for pos in summary["positions"]:
                emoji = "🟢" if pos["unrealized_pl"] >= 0 else "🔴"
                msg += f"\n{emoji} *{pos['symbol']}* {pos['option_type'].upper()} ${pos['strike']}\n"
                msg += f"   {pos['quantity']}x @ ${pos['avg_entry_price']:.2f}\n"
                msg += f"   P/L: ${pos['unrealized_pl']:.2f} ({pos['unrealized_plpc']*100:.1f}%)\n"

            msg += f"\n*Portfolio:*\n"
            msg += f"├── Total Value: ${summary['total_value']:,.2f}\n"
            msg += f"├── Total P/L: ${summary['total_pnl']:,.2f} ({summary['pnl_pct']:.1f}%)\n"
            msg += f"└── % of Portfolio: {summary['portfolio_pct']:.1f}%\n"

        msg += f"\n*All-Time Performance:*\n"
        msg += f"├── Trades: {perf['total_trades']} ({perf['open_trades']} open)\n"
        msg += f"├── Win Rate: {perf['win_rate']:.0f}%\n"
        msg += f"├── Avg Win: +{perf['avg_win']:.1f}% | Avg Loss: {perf['avg_loss']:.1f}%\n"
        msg += f"└── Total P/L: ${perf['total_pnl']:,.2f}\n"

        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Options status error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")


@admin_only
async def cmd_buyoption(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Execute options trade from analyzed signals"""
    global last_flow_results

    if not context.args:
        await update.message.reply_text("Usage: `/buyoption SYMBOL [confirm]`", parse_mode="Markdown")
        return

    symbol = context.args[0].upper()

    # Find in analyzed signals
    analyzed = last_flow_results.get("analyzed", [])
    enriched = next((e for e in analyzed if e.signal.symbol == symbol), None)

    if not enriched:
        await update.message.reply_text(
            f"⚠️ {symbol} not in analyzed signals.\nRun /flow then /analyze first."
        )
        return

    if enriched.recommendation != "BUY":
        await update.message.reply_text(
            f"⚠️ {symbol} recommendation is {enriched.recommendation}, not BUY.\n"
            f"Conviction: {enriched.conviction:.0%}"
        )
        return

    # Confirmation
    if len(context.args) < 2 or context.args[1].lower() != "confirm":
        signal = enriched.signal
        msg = f"⚠️ *Confirm Options Trade*\n\n"
        msg += f"Symbol: *{symbol}*\n"
        msg += f"Contract: {signal.option_type.upper()} ${signal.strike} exp {signal.expiration[:10]}\n"
        msg += f"Signal Score: {signal.score}/20\n"
        msg += f"Conviction: {enriched.conviction:.0%}\n\n"
        msg += f"Send `/buyoption {symbol} confirm` to execute"
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    # Execute
    await update.message.reply_text(f"⏳ Executing options trade for {symbol}...")

    try:
        from options_executor import execute_flow_trade
        result = execute_flow_trade(enriched)

        if result.get("success"):
            msg = f"✅ *Options Trade Executed*\n\n"
            msg += f"Contract: {result['contract_symbol']}\n"
            msg += f"Quantity: {result['quantity']}\n"
            msg += f"Est. Cost: ${result.get('estimated_cost', 0):,.2f}\n"
            msg += f"Strike: ${result['strike']} | Exp: {result['expiration']}\n\n"
            if result.get('thesis'):
                msg += f"*Thesis:* {result['thesis'][:200]}..."
        else:
            msg = f"❌ Trade failed: {result.get('error')}"

        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Options trade error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")


@admin_only
async def cmd_closeoption(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Close an options position"""
    if not context.args:
        await update.message.reply_text("Usage: `/closeoption CONTRACT_SYMBOL`", parse_mode="Markdown")
        return

    contract = context.args[0].upper()
    reason = " ".join(context.args[1:]) if len(context.args) > 1 else "manual"

    await update.message.reply_text(f"⏳ Closing options position {contract}...")

    try:
        from options_executor import close_options_position
        result = close_options_position(contract, reason)

        if result.get("success"):
            msg = f"✅ Closed {result.get('quantity', 1)}x {contract}\n"
            if "pnl" in result:
                emoji = "🟢" if result["pnl"] >= 0 else "🔴"
                msg += f"{emoji} P/L: ${result['pnl']:.2f} ({result['pnl_pct']*100:.1f}%)"
        else:
            msg = f"❌ Failed: {result.get('error')}"

        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Close option error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")


@admin_only
async def cmd_reconcile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reconcile options positions between DB and Alpaca"""
    await update.message.reply_text("⏳ Reconciling options positions...")

    try:
        from options_executor import reconcile_options_positions

        result = reconcile_options_positions()

        if result["synced"]:
            await update.message.reply_text(
                f"✅ Options positions synced\n"
                f"Alpaca: {result['actual_count']} | DB: {result['db_count']}"
            )
        else:
            msg = "⚠️ *Position Mismatches Found*\n\n"

            for mtype, items in result["mismatches"].items():
                if items:
                    msg += f"*{mtype}:*\n"
                    for item in items:
                        msg += f"  • {item['contract']}: {item.get('action', '')}\n"
                    msg += "\n"

            await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Reconcile error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")


@admin_only
async def cmd_expirations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check expiring options positions"""
    try:
        from options_executor import check_expiration_risk, suggest_roll

        alerts = check_expiration_risk()

        if not alerts:
            await update.message.reply_text("✅ No expiration concerns. All positions have adequate time.")
            return

        msg = "⏰ *Expiration Alerts*\n\n"

        for alert in alerts:
            pos = alert["position"]
            severity = alert["severity"]

            # Severity emoji
            if severity == "CRITICAL":
                emoji = "🔴"
            elif severity == "HIGH":
                emoji = "🟠"
            else:
                emoji = "🟡"

            msg += f"{emoji} *{pos.symbol}* {pos.option_type.upper()} ${pos.strike}\n"
            msg += f"   DTE: {alert['dte']} | {alert['message']}\n"

            # Show roll suggestion for high severity
            if alert["action"] in ["close_or_roll", "close"]:
                roll = suggest_roll(pos)
                if roll.get("can_roll"):
                    cost_str = f"${roll['roll_cost']:.2f} debit" if roll["roll_cost"] > 0 else f"${abs(roll['roll_cost']):.2f} credit"
                    msg += f"   Roll to {roll['new_expiration']}: {cost_str}\n"

            msg += "\n"

        msg += "Use `/closeoption CONTRACT` to close."

        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Expirations error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")


@admin_only
async def cmd_greeks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show portfolio Greeks"""
    await update.message.reply_text("⏳ Calculating portfolio Greeks...")

    try:
        from options_executor import get_portfolio_greeks, check_sector_concentration

        greeks = get_portfolio_greeks()
        concentration = check_sector_concentration()

        if not greeks["positions"]:
            await update.message.reply_text("📭 No options positions to analyze.")
            return

        msg = "📊 *Portfolio Greeks*\n\n"

        # Aggregate Greeks
        msg += "*Net Exposure:*\n"
        delta_emoji = "📈" if greeks["net_delta"] > 0 else "📉" if greeks["net_delta"] < 0 else "➡️"
        msg += f"{delta_emoji} Delta: {greeks['net_delta']:+.0f} shares equivalent\n"
        msg += f"🔄 Gamma: {greeks['total_gamma']:.2f}\n"
        msg += f"⏱️ Theta: ${greeks['daily_theta']:+.2f}/day\n"
        msg += f"📊 Vega: {greeks['total_vega']:.2f}\n\n"

        # Warnings
        warnings = []
        if abs(greeks["net_delta"]) > 500:
            warnings.append(f"⚠️ High delta exposure ({greeks['net_delta']:+.0f})")
        if greeks["daily_theta"] < -50:
            warnings.append(f"⚠️ High theta decay (${greeks['daily_theta']:.0f}/day)")

        if warnings:
            msg += "*Warnings:*\n"
            for w in warnings:
                msg += f"{w}\n"
            msg += "\n"

        # Sector concentration
        if concentration["sectors"]:
            msg += "*Sector Allocation:*\n"
            for sector, pct in sorted(concentration["sectors"].items(), key=lambda x: -x[1]):
                if pct >= 5:  # Only show sectors >= 5%
                    bar_len = int(pct / 10)
                    bar = "█" * bar_len
                    msg += f"├── {sector}: {pct:.0f}% {bar}\n"

            if concentration["concentrated"]:
                msg += f"\n⚠️ *Concentrated:* {concentration['warning']}\n"

        # Per-position Greeks
        msg += "\n*By Position:*\n"
        for p in greeks["positions"]:
            msg += f"\n*{p['symbol']}* {p['option_type'].upper()} ${p['strike']}\n"
            msg += f"   Δ:{p['delta']:+.0f} Θ:${p['theta']:+.2f} IV:{p['iv']:.0f}%\n"

        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Greeks error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")


@admin_only
async def cmd_flowperf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show flow signal factor performance"""
    try:
        from db import get_signal_factor_performance, get_score_tier_performance

        factors = get_signal_factor_performance()
        tiers = get_score_tier_performance()

        msg = "📈 *Flow Signal Performance*\n\n"

        # Score tier performance
        if tiers:
            msg += "*By Score Tier:*\n"
            for t in tiers:
                emoji = "🟢" if t["win_rate"] >= 50 else "🔴"
                msg += f"{emoji} {t['tier']}: {t['win_rate']:.0f}% win ({t['total']} trades)\n"
                msg += f"   Avg P/L: {t['avg_pnl']:+.1f}% | Hold: {t['avg_holding_days']:.0f}d\n"
            msg += "\n"

        # Factor analysis
        if factors:
            msg += "*By Signal Factor:*\n"

            factor_labels = {
                "was_sweep": "Sweep",
                "was_ask_side": "Ask Side",
                "was_floor": "Floor Trade",
                "was_opening": "Opening",
            }

            for factor, results in factors.items():
                if factor in factor_labels and results:
                    label = factor_labels[factor]
                    # Find the "present=True" result
                    present = next((r for r in results if r.get("present")), None)
                    absent = next((r for r in results if not r.get("present")), None)

                    if present and present["total"] >= 3:
                        emoji = "🟢" if present["win_rate"] >= 50 else "🔴"
                        comparison = ""
                        if absent and absent["total"] >= 3:
                            diff = present["win_rate"] - absent["win_rate"]
                            if abs(diff) >= 5:
                                comparison = f" ({diff:+.0f}% vs without)"
                        msg += f"{emoji} {label}: {present['win_rate']:.0f}% win{comparison}\n"

            # Premium tier
            if "premium_tier" in factors and factors["premium_tier"]:
                msg += "\n*By Premium:*\n"
                for tier in factors["premium_tier"]:
                    if tier["total"] >= 2:
                        emoji = "🟢" if tier["win_rate"] >= 50 else "🔴"
                        msg += f"{emoji} {tier['tier']}: {tier['win_rate']:.0f}% ({tier['total']} trades)\n"

        if not tiers and not factors:
            msg += "No signal outcomes recorded yet.\n"
            msg += "Performance data will appear after closing trades."

        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Flow perf error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")


# ============== OPTIONS AI AGENTS COMMANDS ==============

@admin_only
async def cmd_optionsreview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Review options positions using AI agent"""
    await update.message.reply_text("⏳ Reviewing options positions with AI agent...")

    try:
        from options_executor import review_options_positions

        results = review_options_positions(use_agent=True)

        if not results:
            await update.message.reply_text("📭 No options positions to review.")
            return

        msg = "🔍 *Options Position Review*\n\n"

        # Group by urgency
        critical = [r for r in results if r['urgency'] == 'critical']
        high = [r for r in results if r['urgency'] == 'high']
        medium = [r for r in results if r['urgency'] == 'medium']
        low = [r for r in results if r['urgency'] == 'low']

        if critical:
            msg += "🔴 *CRITICAL - Act Now:*\n"
            for r in critical:
                msg += f"• {r['contract_symbol'][:20]}\n"
                msg += f"  {r['recommendation']}: {r['reasoning'][:80]}...\n"
            msg += "\n"

        if high:
            msg += "🟠 *HIGH - Act Today:*\n"
            for r in high:
                msg += f"• {r['contract_symbol'][:20]}\n"
                msg += f"  {r['recommendation']}: {r['reasoning'][:80]}...\n"
            msg += "\n"

        if medium:
            msg += "🟡 *MEDIUM - Monitor:*\n"
            for r in medium:
                msg += f"• {r['contract_symbol'][:20]}: {r['recommendation']}\n"
            msg += "\n"

        if low:
            msg += f"🟢 *LOW - Healthy:* {len(low)} positions\n\n"

        # Summary
        agent_used = sum(1 for r in results if r.get('agent_used', False))
        msg += f"_Agent used: {agent_used}/{len(results)} reviews_"

        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Options review error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")


@admin_only
async def cmd_portfolioreview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Review options portfolio using AI agent"""
    await update.message.reply_text("⏳ Analyzing options portfolio with AI agent...")

    try:
        from options_executor import review_options_portfolio

        result = review_options_portfolio(use_agent=True)

        # Assessment emoji
        assessment_emoji = {
            'healthy': '🟢',
            'moderate_risk': '🟡',
            'high_risk': '🟠',
            'critical': '🔴'
        }

        emoji = assessment_emoji.get(result['overall_assessment'], '⚪')

        msg = f"{emoji} *Options Portfolio Review*\n\n"
        msg += f"*Assessment:* {result['overall_assessment'].replace('_', ' ').title()}\n"
        msg += f"*Risk Score:* {result['risk_score']}/100\n\n"

        if result['summary']:
            msg += f"*Summary:*\n{result['summary']}\n\n"

        if result['risk_factors']:
            msg += "*Risk Factors:*\n"
            for rf in result['risk_factors'][:5]:
                msg += f"• {rf}\n"
            msg += "\n"

        if result['rebalancing_needed'] and result['rebalancing_actions']:
            msg += "⚠️ *Rebalancing Recommended:*\n"
            for action in result['rebalancing_actions'][:3]:
                msg += f"• {action}\n"
            msg += "\n"

        if result['roll_suggestions']:
            msg += "🔄 *Roll Suggestions:*\n"
            for roll in result['roll_suggestions'][:3]:
                contract = roll.get('contract', 'N/A')
                reason = roll.get('reason', '')
                msg += f"• {contract[:20]}: {reason}\n"
            msg += "\n"

        if result['recommendations']:
            msg += "*Actions:*\n"
            for rec in result['recommendations'][:3]:
                priority = rec.get('priority', 'medium')
                action = rec.get('action', 'N/A')
                p_emoji = "🔴" if priority == 'high' else "🟡" if priority == 'medium' else "🟢"
                msg += f"{p_emoji} {action}\n"

        agent_str = "AI Agent" if result.get('agent_used', False) else "Rules-based"
        msg += f"\n_Analysis: {agent_str} (confidence: {result.get('confidence', 0):.0%})_"

        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Portfolio review error: {e}")
        await update.message.reply_text(f"❌ Error: {str(e)}")


@admin_only
async def cmd_optionsmonitor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Run full options monitoring cycle with AI"""
    await update.message.reply_text("⏳ Running full options monitoring cycle...")

    try:
        from options_executor import run_options_monitor

        results = run_options_monitor(use_agent=True)

        # Build summary message
        msg = "📊 *Options Monitor Complete*\n\n"

        # Position reviews summary
        position_reviews = results.get('position_reviews', [])
        urgent = sum(1 for r in position_reviews if r['urgency'] in ['critical', 'high'])
        msg += f"*Positions Reviewed:* {len(position_reviews)}\n"
        if urgent > 0:
            msg += f"⚠️ *Urgent positions:* {urgent}\n"
        msg += "\n"

        # Portfolio review summary
        portfolio = results.get('portfolio_review', {})
        if portfolio:
            assessment_emoji = {
                'healthy': '🟢',
                'moderate_risk': '🟡',
                'high_risk': '🟠',
                'critical': '🔴'
            }
            emoji = assessment_emoji.get(portfolio.get('overall_assessment', 'unknown'), '⚪')
            msg += f"*Portfolio:* {emoji} {portfolio.get('overall_assessment', 'N/A')}\n"
            msg += f"Risk Score: {portfolio.get('risk_score', 0)}/100\n\n"

        # Actions taken
        actions = results.get('actions_taken', [])
        if actions:
            msg += f"*Actions Taken:* {len(actions)}\n"
            for action in actions[:5]:
                msg += f"• {action.get('action', 'N/A')}: {action.get('contract', 'N/A')[:15]}\n"
            msg += "\n"

        # Alerts
        alerts = results.get('alerts', [])
        if alerts:
            msg += f"*Alerts:* {len(alerts)}\n"
            for alert in alerts:
                alert_type = alert.get('type', 'unknown')
                if alert_type == 'urgent_positions':
                    msg += f"🔴 {alert.get('count', 0)} urgent positions\n"
                elif alert_type == 'portfolio_risk':
                    msg += f"⚠️ Portfolio risk: {alert.get('assessment', 'N/A')}\n"
                elif alert_type == 'expiration_risk':
                    positions = alert.get('positions', [])
                    msg += f"⏰ {len(positions)} positions expiring soon\n"

        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Options monitor error: {e}")
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

    # Options flow handlers
    app.add_handler(CommandHandler("flow", cmd_flow))
    app.add_handler(CommandHandler("analyze", cmd_analyze))
    app.add_handler(CommandHandler("options", cmd_options))
    app.add_handler(CommandHandler("greeks", cmd_greeks))
    app.add_handler(CommandHandler("expirations", cmd_expirations))
    app.add_handler(CommandHandler("flowperf", cmd_flowperf))
    app.add_handler(CommandHandler("buyoption", cmd_buyoption))
    app.add_handler(CommandHandler("closeoption", cmd_closeoption))
    app.add_handler(CommandHandler("reconcile", cmd_reconcile))

    # Options AI agent handlers
    app.add_handler(CommandHandler("optionsreview", cmd_optionsreview))
    app.add_handler(CommandHandler("portfolioreview", cmd_portfolioreview))
    app.add_handler(CommandHandler("optionsmonitor", cmd_optionsmonitor))

    # Natural language handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Error handler
    app.add_error_handler(error_handler)
    
    # Start polling
    print("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

