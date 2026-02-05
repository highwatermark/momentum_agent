"""
Unusual Whales MCP Tools for AI-Native Options Trading.

Wraps existing flow_scanner.py functionality as MCP tools.
"""
import sys
import os
from typing import Dict, Any, List, Optional
from datetime import datetime, date, timedelta
from dataclasses import dataclass, asdict
import logging

# Add parent directory to path to import existing modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from flow_scanner import (
    UnusualWhalesClient,
    FlowSignal,
    parse_flow_alert,
    score_flow_signal,
)

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    """Standard tool result format."""
    success: bool
    data: Any = None
    error: Optional[str] = None


# Initialize client (lazy loaded)
_uw_client: Optional[UnusualWhalesClient] = None


def _get_client() -> UnusualWhalesClient:
    """Get or create UW client."""
    global _uw_client
    if _uw_client is None:
        _uw_client = UnusualWhalesClient()
    return _uw_client


def uw_flow_scan(
    min_premium: float = 50000,
    min_score: int = 40,
    limit: int = 20,
) -> Dict[str, Any]:
    """
    Scan Unusual Whales for recent options flow alerts.

    Args:
        min_premium: Minimum premium filter (default $50K)
        min_score: Minimum signal score (default 40)
        limit: Maximum signals to return (default 20)

    Returns:
        Dict with signals list, each containing:
        - symbol: underlying symbol
        - option_type: 'call' or 'put'
        - strike: strike price
        - expiration: expiration date
        - premium: total premium
        - volume: contract volume
        - open_interest: open interest
        - score: signal score (0-100)
        - score_breakdown: detailed scoring factors
        - sentiment: 'bullish', 'bearish', or 'neutral'
        - order_type: 'sweep', 'block', etc.
        - timestamp: alert timestamp
    """
    try:
        client = _get_client()

        # Fetch raw alerts
        raw_alerts = client.get_flow_alerts(limit=limit * 2)  # Fetch more to filter

        if not raw_alerts:
            return asdict(ToolResult(
                success=True,
                data={"signals": [], "count": 0, "message": "No flow alerts available"}
            ))

        # Parse and score signals
        signals = []
        for alert in raw_alerts:
            try:
                signal = parse_flow_alert(alert)
                if signal is None:
                    continue

                # Apply filters
                if signal.premium < min_premium:
                    continue

                # Score the signal
                signal = score_flow_signal(signal)

                if signal.score < min_score:
                    continue

                signals.append({
                    "symbol": signal.symbol,
                    "option_type": signal.option_type,
                    "strike": signal.strike,
                    "expiration": str(signal.expiration) if signal.expiration else None,
                    "premium": signal.premium,
                    "volume": signal.volume,
                    "open_interest": signal.open_interest,
                    "score": signal.score,
                    "score_breakdown": signal.score_breakdown,
                    "sentiment": signal.sentiment,
                    "order_type": signal.order_type,
                    "underlying_price": signal.underlying_price,
                    "bid": signal.bid,
                    "ask": signal.ask,
                    "timestamp": str(signal.timestamp) if signal.timestamp else None,
                    "dte": signal.dte,
                    "volume_oi_ratio": signal.volume / signal.open_interest if signal.open_interest > 0 else 0,
                })
            except Exception as e:
                logger.warning(f"Error parsing alert: {e}")
                continue

        # Sort by score descending
        signals.sort(key=lambda x: x["score"], reverse=True)

        # Limit results
        signals = signals[:limit]

        return asdict(ToolResult(
            success=True,
            data={
                "signals": signals,
                "count": len(signals),
                "scanned": len(raw_alerts),
                "filtered_out": len(raw_alerts) - len(signals),
            }
        ))
    except Exception as e:
        logger.error(f"uw_flow_scan error: {e}")
        return asdict(ToolResult(success=False, error=str(e)))


def iv_rank(symbol: str) -> Dict[str, Any]:
    """
    Get IV rank and percentile for an underlying.

    Args:
        symbol: Underlying symbol (e.g., 'SPY')

    Returns:
        Dict with iv_rank, iv_percentile, current_iv, iv_high, iv_low
    """
    try:
        client = _get_client()

        # Try to get IV data from UW
        iv_data = client.get_iv_data(symbol)

        if not iv_data:
            return asdict(ToolResult(
                success=False,
                error=f"IV data not available for {symbol}"
            ))

        return asdict(ToolResult(
            success=True,
            data={
                "symbol": symbol,
                "iv_rank": iv_data.get("iv_rank"),
                "iv_percentile": iv_data.get("iv_percentile"),
                "current_iv": iv_data.get("current_iv"),
                "iv_high_52w": iv_data.get("iv_high_52w"),
                "iv_low_52w": iv_data.get("iv_low_52w"),
                "hv_20": iv_data.get("hv_20"),  # 20-day historical volatility
                "iv_hv_ratio": iv_data.get("current_iv", 0) / iv_data.get("hv_20", 1) if iv_data.get("hv_20") else None,
            }
        ))
    except Exception as e:
        logger.error(f"iv_rank error: {e}")
        return asdict(ToolResult(success=False, error=str(e)))


def earnings_check(symbol: str, blackout_days: int = 2) -> Dict[str, Any]:
    """
    Check if earnings are within blackout window.

    Args:
        symbol: Underlying symbol
        blackout_days: Days before earnings to avoid (default 2)

    Returns:
        Dict with earnings_date, days_until, in_blackout, safe_to_trade
    """
    try:
        client = _get_client()

        # Get earnings calendar
        earnings_data = client.get_earnings_calendar(symbol)

        if not earnings_data or not earnings_data.get("next_earnings_date"):
            # No earnings data - assume safe
            return asdict(ToolResult(
                success=True,
                data={
                    "symbol": symbol,
                    "earnings_date": None,
                    "days_until": None,
                    "in_blackout": False,
                    "safe_to_trade": True,
                    "message": "No upcoming earnings found"
                }
            ))

        earnings_date = earnings_data["next_earnings_date"]
        if isinstance(earnings_date, str):
            earnings_date = datetime.strptime(earnings_date, "%Y-%m-%d").date()

        days_until = (earnings_date - date.today()).days
        in_blackout = 0 <= days_until <= blackout_days

        return asdict(ToolResult(
            success=True,
            data={
                "symbol": symbol,
                "earnings_date": str(earnings_date),
                "days_until": days_until,
                "in_blackout": in_blackout,
                "safe_to_trade": not in_blackout,
                "blackout_window": blackout_days,
                "timing": earnings_data.get("timing", "unknown"),  # BMO, AMC
            }
        ))
    except Exception as e:
        logger.error(f"earnings_check error: {e}")
        return asdict(ToolResult(success=False, error=str(e)))


def stock_quote(symbol: str) -> Dict[str, Any]:
    """
    Get current stock quote for underlying.

    Args:
        symbol: Stock symbol

    Returns:
        Dict with price, volume, change, etc.
    """
    try:
        # Use Alpaca for stock quotes (more reliable)
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        from options_executor import get_stock_quote as _get_stock_quote

        quote = _get_stock_quote(symbol)

        if not quote:
            return asdict(ToolResult(success=False, error=f"No quote for {symbol}"))

        return asdict(ToolResult(
            success=True,
            data={
                "symbol": symbol,
                "price": quote.get("price"),
                "bid": quote.get("bid"),
                "ask": quote.get("ask"),
                "volume": quote.get("volume"),
                "change": quote.get("change"),
                "change_pct": quote.get("change_pct"),
            }
        ))
    except Exception as e:
        logger.error(f"stock_quote error: {e}")
        return asdict(ToolResult(success=False, error=str(e)))


def sector_concentration(positions: List[Dict]) -> Dict[str, Any]:
    """
    Calculate sector concentration from positions.

    Args:
        positions: List of position dicts with 'symbol' keys

    Returns:
        Dict with sector breakdown and concentration warnings
    """
    try:
        # Sector mapping (simplified - in production use proper API)
        SECTOR_MAP = {
            "SPY": "Index",
            "QQQ": "Index",
            "IWM": "Index",
            "DIA": "Index",
            "AAPL": "Technology",
            "MSFT": "Technology",
            "GOOGL": "Technology",
            "AMZN": "Consumer",
            "META": "Technology",
            "NVDA": "Technology",
            "AMD": "Technology",
            "TSLA": "Consumer",
            "JPM": "Financial",
            "BAC": "Financial",
            "GS": "Financial",
            "XOM": "Energy",
            "CVX": "Energy",
        }

        sector_exposure = {}
        total_exposure = 0

        for pos in positions:
            symbol = pos.get("symbol", "").upper()
            value = abs(pos.get("market_value", 0))
            sector = SECTOR_MAP.get(symbol, "Other")

            sector_exposure[sector] = sector_exposure.get(sector, 0) + value
            total_exposure += value

        # Calculate percentages
        sector_pct = {}
        max_concentration = 0
        max_sector = None

        for sector, value in sector_exposure.items():
            pct = value / total_exposure if total_exposure > 0 else 0
            sector_pct[sector] = pct
            if pct > max_concentration:
                max_concentration = pct
                max_sector = sector

        # Check for warnings
        warnings = []
        if max_concentration > 0.5:
            warnings.append(f"High concentration in {max_sector}: {max_concentration:.1%}")

        return asdict(ToolResult(
            success=True,
            data={
                "sector_breakdown": sector_pct,
                "max_concentration": max_concentration,
                "max_sector": max_sector,
                "total_exposure": total_exposure,
                "diversified": max_concentration <= 0.5,
                "warnings": warnings,
            }
        ))
    except Exception as e:
        logger.error(f"sector_concentration error: {e}")
        return asdict(ToolResult(success=False, error=str(e)))
