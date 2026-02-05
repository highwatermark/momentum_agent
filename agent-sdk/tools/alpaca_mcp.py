"""
Alpaca MCP Tools for AI-Native Options Trading.

Wraps existing options_executor.py functionality as MCP tools.
"""
import sys
import os
from typing import Dict, Any, List, Optional
from datetime import datetime, date
from dataclasses import dataclass, asdict
import logging

# Add parent directory to path to import existing modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from options_executor import (
    get_options_positions as _get_options_positions,
    get_option_quote as _get_option_quote,
    get_stock_quote as _get_stock_quote,
    find_option_contract as _find_option_contract,
    get_account_info as _get_account_info,
    close_options_position as _close_options_position,
    estimate_greeks as _estimate_greeks,
    get_portfolio_greeks as _get_portfolio_greeks,
    execute_options_trade as _execute_options_trade,
    calculate_dte,
)

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    """Standard tool result format."""
    success: bool
    data: Any = None
    error: Optional[str] = None


def get_positions() -> Dict[str, Any]:
    """
    Get all current options positions.

    Returns:
        Dict with positions list, each containing:
        - symbol: underlying symbol
        - contract_symbol: full OCC symbol
        - qty: number of contracts
        - avg_entry_price: average cost basis per share
        - current_price: current market price
        - unrealized_pnl: dollar P/L
        - unrealized_pnl_pct: percentage P/L
        - market_value: current market value
        - side: 'long' or 'short'
        - option_type: 'call' or 'put'
        - strike: strike price
        - expiration: expiration date
        - dte: days to expiration
    """
    try:
        positions = _get_options_positions()

        formatted = []
        for pos in positions:
            # Calculate DTE
            exp_date = getattr(pos, 'expiration', None)
            dte = calculate_dte(exp_date) if exp_date else None

            formatted.append({
                "symbol": getattr(pos, 'symbol', None),
                "contract_symbol": getattr(pos, 'contract_symbol', None),
                "qty": int(getattr(pos, 'qty', 0)),
                "avg_entry_price": float(getattr(pos, 'avg_entry_price', 0)),
                "current_price": float(getattr(pos, 'current_price', 0)),
                "unrealized_pnl": float(getattr(pos, 'unrealized_pl', 0)),
                "unrealized_pnl_pct": float(getattr(pos, 'unrealized_plpc', 0)),
                "market_value": float(getattr(pos, 'market_value', 0)),
                "side": getattr(pos, 'side', 'long'),
                "option_type": getattr(pos, 'option_type', None),
                "strike": float(getattr(pos, 'strike_price', 0)) if getattr(pos, 'strike_price', None) else None,
                "expiration": str(exp_date) if exp_date else None,
                "dte": dte,
            })

        return asdict(ToolResult(
            success=True,
            data={"positions": formatted, "count": len(formatted)}
        ))
    except Exception as e:
        logger.error(f"get_positions error: {e}")
        return asdict(ToolResult(success=False, error=str(e)))


def get_quote(symbol: str, is_option: bool = True) -> Dict[str, Any]:
    """
    Get current quote for a symbol.

    Args:
        symbol: Contract symbol (if option) or underlying symbol
        is_option: Whether this is an option contract

    Returns:
        Dict with bid, ask, last, volume, etc.
    """
    try:
        if is_option:
            quote = _get_option_quote(symbol)
        else:
            quote = _get_stock_quote(symbol)

        if not quote:
            return asdict(ToolResult(success=False, error=f"No quote available for {symbol}"))

        return asdict(ToolResult(
            success=True,
            data={
                "symbol": symbol,
                "bid": quote.get("bid"),
                "ask": quote.get("ask"),
                "last": quote.get("last"),
                "mid": (quote.get("bid", 0) + quote.get("ask", 0)) / 2 if quote.get("bid") and quote.get("ask") else None,
                "spread": quote.get("ask", 0) - quote.get("bid", 0) if quote.get("bid") and quote.get("ask") else None,
                "spread_pct": (quote.get("ask", 0) - quote.get("bid", 0)) / quote.get("mid", 1) if quote.get("mid") else None,
                "volume": quote.get("volume"),
                "open_interest": quote.get("open_interest"),
            }
        ))
    except Exception as e:
        logger.error(f"get_quote error: {e}")
        return asdict(ToolResult(success=False, error=str(e)))


def get_account_info() -> Dict[str, Any]:
    """
    Get account information.

    Returns:
        Dict with equity, buying_power, cash, options_buying_power
    """
    try:
        account = _get_account_info()

        return asdict(ToolResult(
            success=True,
            data={
                "equity": float(account.get("equity", 0)),
                "buying_power": float(account.get("buying_power", 0)),
                "cash": float(account.get("cash", 0)),
                "options_buying_power": float(account.get("options_buying_power", 0)),
                "portfolio_value": float(account.get("portfolio_value", 0)),
                "status": account.get("status", "unknown"),
            }
        ))
    except Exception as e:
        logger.error(f"get_account_info error: {e}")
        return asdict(ToolResult(success=False, error=str(e)))


def find_contract(
    underlying: str,
    expiration: str,
    strike: float,
    option_type: str
) -> Dict[str, Any]:
    """
    Find a specific option contract.

    Args:
        underlying: Underlying symbol (e.g., 'SPY')
        expiration: Expiration date (YYYY-MM-DD)
        strike: Strike price
        option_type: 'call' or 'put'

    Returns:
        Dict with contract details including OCC symbol
    """
    try:
        contract = _find_option_contract(
            underlying=underlying,
            expiration=expiration,
            strike=strike,
            option_type=option_type
        )

        if not contract:
            return asdict(ToolResult(
                success=False,
                error=f"Contract not found: {underlying} {strike} {option_type} {expiration}"
            ))

        return asdict(ToolResult(
            success=True,
            data={
                "symbol": contract.get("symbol"),
                "underlying": underlying,
                "strike": strike,
                "option_type": option_type,
                "expiration": expiration,
                "dte": calculate_dte(expiration),
            }
        ))
    except Exception as e:
        logger.error(f"find_contract error: {e}")
        return asdict(ToolResult(success=False, error=str(e)))


def check_liquidity(symbol: str) -> Dict[str, Any]:
    """
    Check liquidity metrics for an option contract.

    Args:
        symbol: Full OCC option symbol

    Returns:
        Dict with spread_pct, volume, open_interest, liquidity_score
    """
    try:
        quote = _get_option_quote(symbol)

        if not quote:
            return asdict(ToolResult(success=False, error=f"No quote for {symbol}"))

        bid = quote.get("bid", 0)
        ask = quote.get("ask", 0)
        mid = (bid + ask) / 2 if bid and ask else 0
        spread = ask - bid if bid and ask else 0
        spread_pct = spread / mid if mid > 0 else 1.0

        volume = quote.get("volume", 0)
        oi = quote.get("open_interest", 0)

        # Calculate liquidity score (0-100)
        score = 0
        if spread_pct < 0.05:
            score += 40
        elif spread_pct < 0.10:
            score += 30
        elif spread_pct < 0.15:
            score += 20
        elif spread_pct < 0.20:
            score += 10

        if volume > 1000:
            score += 30
        elif volume > 500:
            score += 20
        elif volume > 100:
            score += 10

        if oi > 5000:
            score += 30
        elif oi > 1000:
            score += 20
        elif oi > 500:
            score += 10

        return asdict(ToolResult(
            success=True,
            data={
                "symbol": symbol,
                "bid": bid,
                "ask": ask,
                "mid": mid,
                "spread": spread,
                "spread_pct": spread_pct,
                "volume": volume,
                "open_interest": oi,
                "liquidity_score": score,
                "tradeable": spread_pct < 0.15 and volume >= 100,
            }
        ))
    except Exception as e:
        logger.error(f"check_liquidity error: {e}")
        return asdict(ToolResult(success=False, error=str(e)))


def place_order(
    symbol: str,
    qty: int,
    limit_price: float,
    underlying: str,
    option_type: str,
    strike: float,
    expiration: str,
    signal_score: int = 0,
    signal_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Place a limit order to buy an option contract.

    Args:
        symbol: Full OCC option symbol
        qty: Number of contracts
        limit_price: Limit price per share (not per contract)
        underlying: Underlying symbol
        option_type: 'call' or 'put'
        strike: Strike price
        expiration: Expiration date
        signal_score: Score of the triggering signal (0-100)
        signal_id: Database ID of the triggering flow signal

    Returns:
        Dict with order status, fill price, order ID
    """
    try:
        result = _execute_options_trade(
            symbol=underlying,
            option_type=option_type,
            strike=strike,
            expiration=expiration,
            qty=qty,
            limit_price=limit_price,
            signal_score=signal_score,
            flow_signal_id=signal_id,
        )

        if result.get("success"):
            return asdict(ToolResult(
                success=True,
                data={
                    "status": "filled",
                    "order_id": result.get("order_id"),
                    "fill_price": result.get("fill_price", limit_price),
                    "qty": qty,
                    "symbol": symbol,
                    "total_cost": result.get("total_cost", limit_price * qty * 100),
                }
            ))
        else:
            return asdict(ToolResult(
                success=False,
                error=result.get("error", "Order failed")
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
        reason: Reason for closing

    Returns:
        Dict with fill price, realized P/L
    """
    try:
        result = _close_options_position(
            contract_symbol=symbol,
            qty=qty,
            reason=reason,
        )

        if result.get("success"):
            return asdict(ToolResult(
                success=True,
                data={
                    "status": "filled",
                    "symbol": symbol,
                    "qty": result.get("qty_closed", qty),
                    "fill_price": result.get("fill_price"),
                    "realized_pnl": result.get("realized_pnl", 0),
                    "pnl_pct": result.get("pnl_pct", 0),
                    "reason": reason,
                }
            ))
        else:
            return asdict(ToolResult(
                success=False,
                error=result.get("error", "Close failed")
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

    Args:
        symbol: Current contract symbol
        new_expiration: New expiration date (YYYY-MM-DD)
        new_strike: New strike price (None = same strike)

    Returns:
        Dict with close result and open result
    """
    try:
        # Import execute_roll from options_executor if available
        from options_executor import execute_roll as _execute_roll

        result = _execute_roll(
            contract_symbol=symbol,
            new_expiration=new_expiration,
            new_strike=new_strike,
        )

        return asdict(ToolResult(
            success=result.get("success", False),
            data=result if result.get("success") else None,
            error=result.get("error") if not result.get("success") else None,
        ))
    except ImportError:
        return asdict(ToolResult(
            success=False,
            error="Roll functionality not implemented in base executor"
        ))
    except Exception as e:
        logger.error(f"execute_roll error: {e}")
        return asdict(ToolResult(success=False, error=str(e)))


def estimate_greeks(
    symbol: str,
    underlying_price: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Calculate Greeks for an option contract.

    Args:
        symbol: Full OCC option symbol
        underlying_price: Current underlying price (fetched if not provided)

    Returns:
        Dict with delta, gamma, theta, vega, iv
    """
    try:
        greeks = _estimate_greeks(symbol, underlying_price)

        if not greeks:
            return asdict(ToolResult(success=False, error=f"Could not calculate Greeks for {symbol}"))

        return asdict(ToolResult(
            success=True,
            data={
                "symbol": symbol,
                "delta": greeks.get("delta"),
                "gamma": greeks.get("gamma"),
                "theta": greeks.get("theta"),
                "vega": greeks.get("vega"),
                "iv": greeks.get("iv"),
                "underlying_price": greeks.get("underlying_price"),
            }
        ))
    except Exception as e:
        logger.error(f"estimate_greeks error: {e}")
        return asdict(ToolResult(success=False, error=str(e)))


def portfolio_greeks() -> Dict[str, Any]:
    """
    Get aggregate portfolio Greeks.

    Returns:
        Dict with net_delta, total_gamma, daily_theta, total_vega, risk_score
    """
    try:
        greeks = _get_portfolio_greeks()

        return asdict(ToolResult(
            success=True,
            data={
                "net_delta": greeks.get("net_delta", 0),
                "total_gamma": greeks.get("total_gamma", 0),
                "daily_theta": greeks.get("daily_theta", 0),
                "total_vega": greeks.get("total_vega", 0),
                "position_count": greeks.get("position_count", 0),
                "total_exposure": greeks.get("total_exposure", 0),
                "risk_score": greeks.get("risk_score", 0),
            }
        ))
    except Exception as e:
        logger.error(f"portfolio_greeks error: {e}")
        return asdict(ToolResult(success=False, error=str(e)))
