"""
Standalone Alpaca Options Trading Tools for Agent-SDK

This module provides direct Alpaca API access WITHOUT importing from the parent system.
All functionality is self-contained for proper decoupling.
"""
import os
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, date
from dataclasses import dataclass, asdict

from dotenv import load_dotenv

# Load environment
load_dotenv()

logger = logging.getLogger(__name__)

# API credentials from environment
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")


@dataclass
class ToolResult:
    """Standard tool result format."""
    success: bool
    data: Any = None
    error: Optional[str] = None


def _get_trading_client():
    """Get Alpaca trading client."""
    from alpaca.trading.client import TradingClient
    return TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)


def _get_options_client():
    """Get Alpaca options data client."""
    from alpaca.data.historical.option import OptionHistoricalDataClient
    return OptionHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)


def _get_stock_client():
    """Get Alpaca stock data client."""
    from alpaca.data.historical.stock import StockHistoricalDataClient
    return StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)


def get_account_info() -> Dict[str, Any]:
    """
    Get account information directly from Alpaca.

    Returns:
        Dict with equity, buying_power, cash, etc.
    """
    try:
        client = _get_trading_client()
        account = client.get_account()

        return asdict(ToolResult(
            success=True,
            data={
                "equity": float(account.equity),
                "buying_power": float(account.buying_power),
                "cash": float(account.cash),
                "options_buying_power": float(getattr(account, 'options_buying_power', account.buying_power)),
                "portfolio_value": float(account.portfolio_value),
                "status": account.status.value if hasattr(account.status, 'value') else str(account.status),
            }
        ))
    except Exception as e:
        logger.error(f"get_account_info error: {e}")
        return asdict(ToolResult(success=False, error=str(e)))


def get_positions() -> Dict[str, Any]:
    """
    Get all current options positions directly from Alpaca.

    Returns:
        Dict with positions list
    """
    try:
        client = _get_trading_client()
        all_positions = client.get_all_positions()

        # Filter for options (contracts have longer symbols with strike/exp info)
        options_positions = []
        for pos in all_positions:
            symbol = pos.symbol
            # Options symbols are typically >10 characters (OCC format)
            if len(symbol) > 10 or (hasattr(pos, 'asset_class') and 'option' in str(pos.asset_class).lower()):
                # Calculate DTE from symbol if possible
                dte = None
                expiration = None
                try:
                    # OCC format: SYMBOL + YYMMDD + C/P + Strike
                    if len(symbol) > 10:
                        # Extract date portion (chars 6-12 typically)
                        date_part = symbol[-15:-9]  # Get YYMMDD
                        exp_date = datetime.strptime(f"20{date_part}", "%Y%m%d").date()
                        expiration = exp_date.isoformat()
                        dte = (exp_date - date.today()).days
                except Exception:
                    pass

                options_positions.append({
                    "symbol": pos.symbol,
                    "contract_symbol": pos.symbol,
                    "qty": int(pos.qty),
                    "avg_entry_price": float(pos.avg_entry_price),
                    "current_price": float(pos.current_price) if pos.current_price else 0,
                    "unrealized_pnl": float(pos.unrealized_pl) if pos.unrealized_pl else 0,
                    "unrealized_pnl_pct": float(pos.unrealized_plpc) if pos.unrealized_plpc else 0,
                    "market_value": float(pos.market_value) if pos.market_value else 0,
                    "side": pos.side.value if hasattr(pos.side, 'value') else str(pos.side),
                    "expiration": expiration,
                    "dte": dte,
                })

        return asdict(ToolResult(
            success=True,
            data={"positions": options_positions, "count": len(options_positions)}
        ))
    except Exception as e:
        logger.error(f"get_positions error: {e}")
        return asdict(ToolResult(success=False, error=str(e)))


def get_option_quote(symbol: str) -> Dict[str, Any]:
    """
    Get current quote for an option contract.

    Args:
        symbol: Full OCC option symbol

    Returns:
        Dict with bid, ask, last, volume, etc.
    """
    try:
        from alpaca.data.requests import OptionLatestQuoteRequest

        client = _get_options_client()
        request = OptionLatestQuoteRequest(symbol_or_symbols=[symbol])
        quotes = client.get_option_latest_quote(request)

        if symbol in quotes:
            quote = quotes[symbol]
            bid = float(quote.bid_price) if quote.bid_price else 0
            ask = float(quote.ask_price) if quote.ask_price else 0
            mid = (bid + ask) / 2 if bid and ask else 0
            spread = ask - bid if bid and ask else 0

            return asdict(ToolResult(
                success=True,
                data={
                    "symbol": symbol,
                    "bid": bid,
                    "ask": ask,
                    "mid": mid,
                    "spread": spread,
                    "spread_pct": spread / mid if mid > 0 else 1.0,
                    "bid_size": int(quote.bid_size) if quote.bid_size else 0,
                    "ask_size": int(quote.ask_size) if quote.ask_size else 0,
                }
            ))
        else:
            return asdict(ToolResult(success=False, error=f"No quote for {symbol}"))

    except Exception as e:
        logger.error(f"get_option_quote error: {e}")
        return asdict(ToolResult(success=False, error=str(e)))


def get_stock_quote(symbol: str) -> Dict[str, Any]:
    """
    Get current quote for a stock.

    Args:
        symbol: Stock symbol

    Returns:
        Dict with bid, ask, price, etc.
    """
    try:
        from alpaca.data.requests import StockLatestQuoteRequest

        client = _get_stock_client()
        request = StockLatestQuoteRequest(symbol_or_symbols=[symbol])
        quotes = client.get_stock_latest_quote(request)

        if symbol in quotes:
            quote = quotes[symbol]
            bid = float(quote.bid_price) if quote.bid_price else 0
            ask = float(quote.ask_price) if quote.ask_price else 0

            return asdict(ToolResult(
                success=True,
                data={
                    "symbol": symbol,
                    "bid": bid,
                    "ask": ask,
                    "mid": (bid + ask) / 2 if bid and ask else 0,
                }
            ))
        else:
            return asdict(ToolResult(success=False, error=f"No quote for {symbol}"))

    except Exception as e:
        logger.error(f"get_stock_quote error: {e}")
        return asdict(ToolResult(success=False, error=str(e)))


def place_order(
    symbol: str,
    qty: int,
    limit_price: float,
    side: str = "buy",
) -> Dict[str, Any]:
    """
    Place a limit order for an option contract.

    Args:
        symbol: Full OCC option symbol
        qty: Number of contracts
        limit_price: Limit price per share (not per contract)
        side: 'buy' or 'sell'

    Returns:
        Dict with order status, order ID
    """
    try:
        from alpaca.trading.requests import LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        client = _get_trading_client()

        order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL

        order_request = LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=order_side,
            time_in_force=TimeInForce.DAY,
            limit_price=limit_price,
        )

        order = client.submit_order(order_request)

        return asdict(ToolResult(
            success=True,
            data={
                "order_id": order.id,
                "status": order.status.value if hasattr(order.status, 'value') else str(order.status),
                "symbol": symbol,
                "qty": qty,
                "limit_price": limit_price,
                "side": side,
                "submitted_at": order.submitted_at.isoformat() if order.submitted_at else None,
            }
        ))

    except Exception as e:
        logger.error(f"place_order error: {e}")
        return asdict(ToolResult(success=False, error=str(e)))


def close_position(
    symbol: str,
    qty: Optional[int] = None,
    reason: str = "manual",
) -> Dict[str, Any]:
    """
    Close an existing options position.

    Args:
        symbol: Full OCC option symbol
        qty: Number of contracts to close (None = all)
        reason: Reason for closing (logged but not sent to API)

    Returns:
        Dict with close order details
    """
    try:
        client = _get_trading_client()

        # Get current position to determine quantity
        positions = client.get_all_positions()
        position = None
        for pos in positions:
            if pos.symbol == symbol:
                position = pos
                break

        if not position:
            return asdict(ToolResult(success=False, error=f"No position found for {symbol}"))

        # Close position (Alpaca handles market order for close)
        if qty is None or int(qty) >= int(position.qty):
            # Close all
            order = client.close_position(symbol)
        else:
            # Partial close - submit sell order
            from alpaca.trading.requests import MarketOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce

            order_request = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
            )
            order = client.submit_order(order_request)

        # Calculate P/L
        entry_price = float(position.avg_entry_price)
        current_price = float(position.current_price) if position.current_price else 0
        qty_closed = qty if qty else int(position.qty)
        realized_pnl = (current_price - entry_price) * qty_closed * 100  # Options are 100 shares
        pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0

        return asdict(ToolResult(
            success=True,
            data={
                "order_id": order.id if hasattr(order, 'id') else None,
                "status": "submitted",
                "symbol": symbol,
                "qty_closed": qty_closed,
                "fill_price": current_price,  # Approximate
                "realized_pnl": realized_pnl,
                "pnl_pct": pnl_pct,
                "reason": reason,
            }
        ))

    except Exception as e:
        logger.error(f"close_position error: {e}")
        return asdict(ToolResult(success=False, error=str(e)))


def execute_roll(
    symbol: str,
    new_expiration: str,
    new_strike: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Roll a position to a new expiration.

    This closes the current position and opens a new one.

    Args:
        symbol: Current contract symbol
        new_expiration: New expiration date (YYYY-MM-DD)
        new_strike: New strike price (None = same strike)

    Returns:
        Dict with close and open results
    """
    try:
        # First, get the current position details
        positions_result = get_positions()
        if not positions_result.get("success"):
            return asdict(ToolResult(success=False, error="Failed to get positions"))

        positions = positions_result.get("data", {}).get("positions", [])
        current_pos = None
        for pos in positions:
            if pos.get("contract_symbol") == symbol or pos.get("symbol") == symbol:
                current_pos = pos
                break

        if not current_pos:
            return asdict(ToolResult(success=False, error=f"Position {symbol} not found"))

        # Close the current position
        close_result = close_position(symbol, reason="roll")
        if not close_result.get("success"):
            return asdict(ToolResult(
                success=False,
                error=f"Failed to close position: {close_result.get('error')}"
            ))

        # Build new contract symbol (simplified - may need adjustment)
        # This is a placeholder - actual implementation would need proper OCC symbol construction
        return asdict(ToolResult(
            success=True,
            data={
                "old_contract": symbol,
                "close_result": close_result.get("data"),
                "note": "New position needs to be opened separately with find_contract + place_order",
                "new_expiration": new_expiration,
                "new_strike": new_strike,
            }
        ))

    except Exception as e:
        logger.error(f"execute_roll error: {e}")
        return asdict(ToolResult(success=False, error=str(e)))


def get_portfolio_greeks() -> Dict[str, Any]:
    """
    Calculate aggregate portfolio Greeks.

    Note: Alpaca doesn't provide Greeks directly. This is a placeholder
    that returns position count and exposure.

    Returns:
        Dict with position-based metrics
    """
    try:
        positions_result = get_positions()
        if not positions_result.get("success"):
            return positions_result

        positions = positions_result.get("data", {}).get("positions", [])

        total_exposure = sum(abs(p.get("market_value", 0)) for p in positions)
        total_pnl = sum(p.get("unrealized_pnl", 0) for p in positions)

        return asdict(ToolResult(
            success=True,
            data={
                "position_count": len(positions),
                "total_exposure": total_exposure,
                "total_unrealized_pnl": total_pnl,
                # Greeks would require external calculation (e.g., Black-Scholes)
                "net_delta": None,
                "total_gamma": None,
                "daily_theta": None,
                "total_vega": None,
                "note": "Greeks calculation requires external pricing model",
            }
        ))

    except Exception as e:
        logger.error(f"get_portfolio_greeks error: {e}")
        return asdict(ToolResult(success=False, error=str(e)))


def check_liquidity(symbol: str, max_spread_pct: float = 0.15) -> Dict[str, Any]:
    """
    Check if an option contract has acceptable liquidity.

    Args:
        symbol: Full OCC option symbol
        max_spread_pct: Maximum acceptable bid-ask spread percentage

    Returns:
        Dict with liquidity metrics and tradeable flag
    """
    quote_result = get_option_quote(symbol)
    if not quote_result.get("success"):
        return quote_result

    data = quote_result.get("data", {})
    spread_pct = data.get("spread_pct", 1.0)
    bid_size = data.get("bid_size", 0)

    tradeable = spread_pct <= max_spread_pct and bid_size >= 10

    return asdict(ToolResult(
        success=True,
        data={
            **data,
            "max_spread_pct": max_spread_pct,
            "tradeable": tradeable,
            "liquidity_ok": tradeable,
        }
    ))
