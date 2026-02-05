"""
MCP Tool definitions for AI-Native Options Flow Trading System.

These tools wrap existing functionality and expose them to Claude agents.
"""
from .alpaca_mcp import (
    get_positions,
    get_quote,
    get_account_info,
    find_contract,
    check_liquidity,
    place_order,
    close_position,
    execute_roll,
    estimate_greeks,
    portfolio_greeks,
)
from .unusual_whales_mcp import (
    uw_flow_scan,
    iv_rank,
    earnings_check,
)
from .telegram_mcp import (
    send_notification,
    send_alert,
)

# Tool registry for agent configuration
TOOL_REGISTRY = {
    # Alpaca trading tools
    "get_positions": get_positions,
    "get_quote": get_quote,
    "get_account_info": get_account_info,
    "find_contract": find_contract,
    "check_liquidity": check_liquidity,
    "place_order": place_order,
    "close_position": close_position,
    "execute_roll": execute_roll,
    "estimate_greeks": estimate_greeks,
    "portfolio_greeks": portfolio_greeks,

    # Unusual Whales tools
    "uw_flow_scan": uw_flow_scan,
    "iv_rank": iv_rank,
    "earnings_check": earnings_check,

    # Telegram tools
    "send_notification": send_notification,
    "send_alert": send_alert,
}

# Tool descriptions for Claude
TOOL_DESCRIPTIONS = {
    "get_positions": "Get all current options positions with Greeks and P/L",
    "get_quote": "Get current quote for an option contract or underlying",
    "get_account_info": "Get account equity, buying power, and status",
    "find_contract": "Search for an option contract by underlying, strike, expiry, type",
    "check_liquidity": "Check bid-ask spread, volume, and OI for a contract",
    "place_order": "Place a limit order to buy an option contract",
    "close_position": "Close an existing options position",
    "execute_roll": "Roll a position to a later expiration",
    "estimate_greeks": "Calculate Greeks for a specific position",
    "portfolio_greeks": "Get aggregate portfolio Greeks and risk metrics",
    "uw_flow_scan": "Scan Unusual Whales for recent options flow alerts",
    "iv_rank": "Get IV rank and percentile for an underlying",
    "earnings_check": "Check if earnings are within blackout window",
    "send_notification": "Send a Telegram notification message",
    "send_alert": "Send a high-priority Telegram alert",
}

__all__ = [
    "TOOL_REGISTRY",
    "TOOL_DESCRIPTIONS",
    "get_positions",
    "get_quote",
    "get_account_info",
    "find_contract",
    "check_liquidity",
    "place_order",
    "close_position",
    "execute_roll",
    "estimate_greeks",
    "portfolio_greeks",
    "uw_flow_scan",
    "iv_rank",
    "earnings_check",
    "send_notification",
    "send_alert",
]
