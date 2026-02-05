"""
Telegram MCP Tools for AI-Native Options Trading.

Provides notification capabilities to agents.
"""
import sys
import os
from typing import Dict, Any, Optional
from dataclasses import dataclass, asdict
import logging
import re

# Add parent directory to path to import existing modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    """Standard tool result format."""
    success: bool
    data: Any = None
    error: Optional[str] = None


def escape_markdown(text: str) -> str:
    """Escape special characters for Telegram Markdown."""
    if not text:
        return ""
    # Escape markdown special characters
    escape_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in escape_chars:
        text = text.replace(char, f'\\{char}')
    return text


def _get_telegram_sender():
    """Get telegram sender function."""
    try:
        from flow_listener import send_telegram_sync
        return send_telegram_sync
    except ImportError:
        try:
            from options_monitor import send_telegram_sync
            return send_telegram_sync
        except ImportError:
            return None


def send_notification(
    message: str,
    parse_mode: str = "Markdown",
    escape_content: bool = True,
) -> Dict[str, Any]:
    """
    Send a Telegram notification.

    Args:
        message: Message text
        parse_mode: 'Markdown' or 'HTML' or None
        escape_content: Whether to escape markdown characters

    Returns:
        Dict with success status
    """
    try:
        sender = _get_telegram_sender()

        if not sender:
            logger.warning("Telegram sender not available")
            return asdict(ToolResult(
                success=False,
                error="Telegram not configured"
            ))

        # Escape if requested
        if escape_content and parse_mode == "Markdown":
            # Don't escape the formatting we want to keep
            # Only escape user-provided dynamic content
            pass  # Let caller handle escaping specific parts

        # Try with parse mode first
        try:
            sender(message, parse_mode=parse_mode)
        except Exception:
            # Retry without parse mode
            plain_message = re.sub(r'[*_`\[\]]', '', message)
            sender(plain_message, parse_mode=None)

        return asdict(ToolResult(
            success=True,
            data={"message_sent": True}
        ))
    except Exception as e:
        logger.error(f"send_notification error: {e}")
        return asdict(ToolResult(success=False, error=str(e)))


def send_alert(
    title: str,
    symbol: str,
    details: Dict[str, Any],
    severity: str = "INFO",
) -> Dict[str, Any]:
    """
    Send a formatted alert notification.

    Args:
        title: Alert title
        symbol: Symbol being alerted on
        details: Dict of key-value pairs to include
        severity: 'INFO', 'WARNING', 'CRITICAL'

    Returns:
        Dict with success status
    """
    try:
        # Choose emoji based on severity
        emoji_map = {
            "INFO": "ℹ️",
            "WARNING": "⚠️",
            "CRITICAL": "🚨",
        }
        emoji = emoji_map.get(severity, "ℹ️")

        # Build message
        msg = f"{emoji} *{escape_markdown(title)}* | {escape_markdown(symbol)}\n\n"

        for key, value in details.items():
            key_escaped = escape_markdown(str(key))
            if isinstance(value, float):
                value_str = f"{value:.2f}"
            elif isinstance(value, (int, str)):
                value_str = escape_markdown(str(value))
            else:
                value_str = escape_markdown(str(value))
            msg += f"├── {key_escaped}: {value_str}\n"

        # Add timestamp
        from datetime import datetime
        import pytz
        et = pytz.timezone("America/New_York")
        timestamp = datetime.now(et).strftime("%H:%M:%S ET")
        msg += f"\n{escape_markdown(timestamp)}"

        return send_notification(msg, parse_mode="Markdown", escape_content=False)

    except Exception as e:
        logger.error(f"send_alert error: {e}")
        return asdict(ToolResult(success=False, error=str(e)))


def send_trade_entry(
    symbol: str,
    contract: str,
    option_type: str,
    strike: float,
    expiration: str,
    fill_price: float,
    qty: int,
    signal_score: int,
    conviction: str = "MEDIUM",
) -> Dict[str, Any]:
    """
    Send a trade entry notification.

    Args:
        symbol: Underlying symbol
        contract: Full contract symbol
        option_type: 'call' or 'put'
        strike: Strike price
        expiration: Expiration date
        fill_price: Fill price per share
        qty: Number of contracts
        signal_score: Signal score (0-100)
        conviction: 'LOW', 'MEDIUM', 'HIGH'

    Returns:
        Dict with success status
    """
    try:
        emoji = "📈" if option_type.lower() == "call" else "📉"

        msg = f"{emoji} *ENTRY* | {escape_markdown(symbol)}\n\n"
        msg += f"Contract: {escape_markdown(contract)}\n"
        msg += f"├── Type: {escape_markdown(option_type.upper())}\n"
        msg += f"├── Strike: ${strike:.2f}\n"
        msg += f"├── Expiry: {escape_markdown(expiration)}\n"
        msg += f"├── Fill: ${fill_price:.2f}\n"
        msg += f"├── Qty: {qty}\n"
        msg += f"├── Cost: ${fill_price * qty * 100:.2f}\n"
        msg += f"├── Score: {signal_score}/100\n"
        msg += f"└── Conviction: {escape_markdown(conviction)}\n"

        from datetime import datetime
        import pytz
        et = pytz.timezone("America/New_York")
        timestamp = datetime.now(et).strftime("%H:%M:%S ET")
        msg += f"\n{escape_markdown(timestamp)}"

        return send_notification(msg, parse_mode="Markdown", escape_content=False)

    except Exception as e:
        logger.error(f"send_trade_entry error: {e}")
        return asdict(ToolResult(success=False, error=str(e)))


def send_trade_exit(
    symbol: str,
    contract: str,
    exit_price: float,
    entry_price: float,
    qty: int,
    pnl: float,
    pnl_pct: float,
    reason: str,
    hold_time: str = "",
) -> Dict[str, Any]:
    """
    Send a trade exit notification.

    Args:
        symbol: Underlying symbol
        contract: Full contract symbol
        exit_price: Exit fill price
        entry_price: Original entry price
        qty: Number of contracts
        pnl: Dollar P/L
        pnl_pct: Percentage P/L
        reason: Exit reason
        hold_time: Optional hold time string

    Returns:
        Dict with success status
    """
    try:
        emoji = "✅" if pnl >= 0 else "❌"

        msg = f"{emoji} *EXIT* | {escape_markdown(symbol)}\n\n"
        msg += f"Contract: {escape_markdown(contract)}\n"
        msg += f"├── Entry: ${entry_price:.2f}\n"
        msg += f"├── Exit: ${exit_price:.2f}\n"
        msg += f"├── Qty: {qty}\n"
        msg += f"├── P/L: ${pnl:+.2f} ({pnl_pct:+.1%})\n"
        msg += f"├── Reason: {escape_markdown(reason)}\n"
        if hold_time:
            msg += f"└── Held: {escape_markdown(hold_time)}\n"

        from datetime import datetime
        import pytz
        et = pytz.timezone("America/New_York")
        timestamp = datetime.now(et).strftime("%H:%M:%S ET")
        msg += f"\n{escape_markdown(timestamp)}"

        return send_notification(msg, parse_mode="Markdown", escape_content=False)

    except Exception as e:
        logger.error(f"send_trade_exit error: {e}")
        return asdict(ToolResult(success=False, error=str(e)))


def send_daily_summary(
    date_str: str,
    trades_count: int,
    wins: int,
    losses: int,
    total_pnl: float,
    positions_held: int,
    signals_seen: int,
    signals_traded: int,
) -> Dict[str, Any]:
    """
    Send end-of-day summary.

    Args:
        date_str: Date string
        trades_count: Total trades executed
        wins: Winning trades
        losses: Losing trades
        total_pnl: Total P/L
        positions_held: Positions still open
        signals_seen: Total signals scanned
        signals_traded: Signals that led to trades

    Returns:
        Dict with success status
    """
    try:
        emoji = "📊"
        result_emoji = "🟢" if total_pnl >= 0 else "🔴"

        win_rate = (wins / trades_count * 100) if trades_count > 0 else 0
        trade_rate = (signals_traded / signals_seen * 100) if signals_seen > 0 else 0

        msg = f"{emoji} *DAILY SUMMARY* | {escape_markdown(date_str)}\n\n"
        msg += f"*Results* {result_emoji}\n"
        msg += f"├── P/L: ${total_pnl:+,.2f}\n"
        msg += f"├── Trades: {trades_count} ({wins}W / {losses}L)\n"
        msg += f"├── Win Rate: {win_rate:.0f}%\n"
        msg += f"└── Positions: {positions_held} open\n\n"
        msg += f"*Activity*\n"
        msg += f"├── Signals Seen: {signals_seen}\n"
        msg += f"├── Signals Traded: {signals_traded}\n"
        msg += f"└── Trade Rate: {trade_rate:.1f}%\n"

        return send_notification(msg, parse_mode="Markdown", escape_content=False)

    except Exception as e:
        logger.error(f"send_daily_summary error: {e}")
        return asdict(ToolResult(success=False, error=str(e)))
