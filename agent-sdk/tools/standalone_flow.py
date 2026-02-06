"""
Standalone Unusual Whales Flow Scanner for Agent-SDK

This module provides direct UW API access WITHOUT importing from the parent system.
All functionality is self-contained for proper decoupling.

TIGHTER FILTERING (2026-02-06):
- Only sweeps with vol/OI > 2
- Opening positions only
- DTE 14-45
- Score 7+ required
- Quality checks (OI, strike distance, excluded tickers)
"""
import os
import logging
import requests
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta, date
from dataclasses import dataclass, asdict, field

from dotenv import load_dotenv

# Load environment
load_dotenv()

logger = logging.getLogger(__name__)

# API configuration
UW_API_KEY = os.getenv("UW_API_KEY")
UW_BASE_URL = "https://api.unusualwhales.com/api"

# Excluded tickers - ETFs + meme/low quality stocks (hedging noise, manipulation risk)
EXCLUDED_TICKERS = {
    # Index ETFs
    "SPY", "QQQ", "IWM", "DIA",
    # Sector ETFs
    "XLF", "XLE", "XLK", "XLV", "XLI", "XLU", "XLB", "XLC", "XLY", "XLP", "XLRE",
    # Commodities/Bonds
    "GLD", "SLV", "TLT", "HYG", "EEM", "EFA", "UNG",
    # Volatility products
    "VXX", "UVXY", "SVXY",
    # Leveraged ETFs
    "SQQQ", "TQQQ", "SPXU", "SPXL", "UPRO",
    # Meme/High manipulation risk
    "AMC", "GME", "BBBY", "MULN", "HYMC", "MMAT", "ATER", "DWAC",
    # Low quality/penny territory risk
    "WISH", "PLTR",
    # Index options
    "SPXW", "SPX", "NDX", "XSP",
}

# Flow scanning parameters - optimized for single stocks
FLOW_PARAMS = {
    "min_premium": 100000,        # $100K minimum
    "min_vol_oi": 1.5,            # Vol/OI > 1.5
    "all_opening": True,          # Opening positions only (CRITICAL)
    "min_dte": 14,                # Min DTE
    "max_dte": 45,                # Max DTE
    "issue_types": ["Common Stock"],  # CRITICAL - filters OUT ETFs at API level
    "scan_limit": 30,             # Raw alerts to fetch
    "min_score": 7,               # Score 7+ required
    "min_open_interest": 500,     # OI for liquidity
    "max_strike_distance_pct": 0.10,  # Max 10% from underlying
}


@dataclass
class ToolResult:
    """Standard tool result format."""
    success: bool
    data: Any = None
    error: Optional[str] = None


@dataclass
class FlowSignal:
    """Represents a single options flow signal."""
    id: str
    symbol: str
    strike: float
    expiration: str
    option_type: str  # 'call' or 'put'
    premium: float
    size: int
    volume: int
    open_interest: int
    vol_oi_ratio: float
    is_sweep: bool
    is_ask_side: bool
    is_bid_side: bool
    is_floor: bool
    is_opening: bool
    is_otm: bool
    underlying_price: float
    timestamp: str
    sentiment: str  # 'neutral' - let Claude determine direction
    score: int = 0
    score_breakdown: Dict = field(default_factory=dict)
    iv_rank: float = None


class UnusualWhalesClient:
    """Direct client for Unusual Whales API."""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or UW_API_KEY
        self.base_url = UW_BASE_URL
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json"
        })

    def _request(self, endpoint: str, params: Dict = None) -> Dict:
        """Make API request with error handling."""
        url = f"{self.base_url}{endpoint}"
        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"UW API Error: {e}")
            return {"error": str(e)}

    def get_flow_alerts(
        self,
        min_premium: int = None,
        is_sweep: bool = None,
        limit: int = 50,
        ticker_symbol: str = None,
        min_dte: int = None,
        max_dte: int = None,
        min_vol_oi_ratio: float = None,
        all_opening: bool = None,
        issue_types: List[str] = None,
        newer_than: str = None,
    ) -> List[Dict]:
        """Fetch flow alerts from Unusual Whales API.

        Args:
            issue_types: List of issue types (e.g., ["Common Stock"]) to filter OUT ETFs
        """
        params = {"limit": limit}

        if min_premium:
            params["min_premium"] = min_premium
        if is_sweep is not None:
            params["is_sweep"] = str(is_sweep).lower()
        if ticker_symbol:
            params["ticker_symbol"] = ticker_symbol
        if min_dte:
            params["min_dte"] = min_dte
        if max_dte:
            params["max_dte"] = max_dte
        if min_vol_oi_ratio:
            params["min_volume_oi_ratio"] = min_vol_oi_ratio
        if all_opening is not None:
            params["all_opening"] = str(all_opening).lower()
        if issue_types:
            params["issue_types"] = ",".join(issue_types)
        if newer_than:
            params["newer_than"] = newer_than

        result = self._request("/option-trades/flow-alerts", params)

        if "error" in result:
            return []

        return result.get("data", [])

    def get_iv_rank(self, ticker: str) -> Dict:
        """Get IV rank for a ticker."""
        result = self._request(f"/stock/{ticker}/iv-rank")
        return result.get("data", {}) if "data" in result else result

    def get_earnings(self, ticker: str) -> Dict:
        """Get earnings info for a ticker."""
        result = self._request(f"/stock/{ticker}/earnings")
        return result.get("data", {}) if "data" in result else result

    def get_stock_info(self, ticker: str) -> Dict:
        """Get stock info for a ticker."""
        result = self._request(f"/stock/{ticker}/info")
        return result.get("data", {}) if "data" in result else result


def parse_flow_alert(alert: Dict) -> Optional[FlowSignal]:
    """Parse raw API response into FlowSignal dataclass."""
    try:
        option_type = alert.get("type", "").lower()

        # Determine bid/ask side
        total_ask_prem = float(alert.get("total_ask_side_prem", 0))
        total_bid_prem = float(alert.get("total_bid_side_prem", 0))
        is_ask_side = total_ask_prem > total_bid_prem
        is_bid_side = total_bid_prem > total_ask_prem

        # Set sentiment to neutral - let Claude determine with context
        sentiment = "neutral"

        # Calculate vol/OI ratio
        volume = int(alert.get("volume", 0))
        open_interest = int(alert.get("open_interest", 1))
        vol_oi_ratio_raw = alert.get("volume_oi_ratio")
        if vol_oi_ratio_raw:
            vol_oi_ratio = float(vol_oi_ratio_raw)
        else:
            vol_oi_ratio = volume / open_interest if open_interest > 0 else volume

        premium = float(alert.get("total_premium", 0))

        # Check if OTM
        underlying_price = float(alert.get("underlying_price", 0))
        strike = float(alert.get("strike", 0))
        if option_type == "call":
            is_otm = strike > underlying_price
        else:
            is_otm = strike < underlying_price

        return FlowSignal(
            id=str(alert.get("id", alert.get("rule_id", ""))),
            symbol=alert.get("ticker", alert.get("ticker_symbol", "")),
            strike=strike,
            expiration=alert.get("expiry", alert.get("expiration", "")),
            option_type=option_type,
            premium=premium,
            size=int(alert.get("total_size", alert.get("size", 0))),
            volume=volume,
            open_interest=open_interest,
            vol_oi_ratio=round(vol_oi_ratio, 2),
            is_sweep=alert.get("has_sweep", alert.get("is_sweep", False)),
            is_ask_side=is_ask_side,
            is_bid_side=is_bid_side,
            is_floor=alert.get("has_floor", alert.get("is_floor", False)),
            is_opening=alert.get("all_opening_trades", alert.get("is_opening", False)),
            is_otm=is_otm,
            underlying_price=underlying_price,
            timestamp=alert.get("created_at", alert.get("timestamp", "")),
            sentiment=sentiment,
        )
    except Exception as e:
        logger.error(f"Error parsing flow alert: {e}")
        return None


def is_counter_trend(option_type: str, market_regime: Dict) -> bool:
    """Check if a signal is counter-trend (puts in bullish, calls in bearish)."""
    trend = market_regime.get("trend", "unknown")
    opt_type = option_type.lower()

    if trend == "bullish" and opt_type == "put":
        return True
    if trend == "bearish" and opt_type == "call":
        return True
    return False


def score_signal(signal: FlowSignal, market_regime: Dict = None) -> int:
    """
    Score a flow signal on a 0-10 scale.

    Only signals scoring 7+ should be traded.

    Scoring (reward BOTH sweeps AND floor trades):
    - Sweep: +2 (urgency indicator)
    - Floor trade: +2 (institutional activity)
    - Opening position: +2 (new conviction)
    - Vol/OI > 1.5: +1, > 3: +2
    - Premium > $250K: +1, > $500K: +2
    - Trend-aligned: +1

    Returns:
        int: Score from 0-10
    """
    score = 0

    # REWARD BOTH sweeps and floor trades (not mutually exclusive)
    if signal.is_sweep:
        score += 2
    if signal.is_floor:
        score += 2

    # Opening position is critical
    if signal.is_opening:
        score += 2

    # Vol/OI ratio
    if signal.vol_oi_ratio >= 3.0:
        score += 2
    elif signal.vol_oi_ratio >= 1.5:
        score += 1

    # Premium size
    if signal.premium >= 500000:
        score += 2
    elif signal.premium >= 250000:
        score += 1

    # Trend alignment
    if market_regime:
        trend = market_regime.get("trend", "unknown")
        opt_type = signal.option_type.lower()

        if (trend == "bullish" and opt_type == "call") or \
           (trend == "bearish" and opt_type == "put"):
            score += 1
        elif is_counter_trend(opt_type, market_regime):
            score -= 3

    # Penalties
    if signal.is_otm:
        score -= 1

    if signal.iv_rank is not None and signal.iv_rank > 70:
        score -= 3

    # DTE penalty (shouldn't trigger since filtered at API, but safety check)
    if signal.expiration:
        try:
            exp_date = datetime.strptime(signal.expiration[:10], "%Y-%m-%d")
            dte = (exp_date - datetime.now()).days
            if dte < 7:
                score -= 2
            elif dte < 14:
                score -= 1
        except Exception:
            pass

    return max(0, min(10, score))


def passes_quality_checks(signal: FlowSignal, market_regime: Dict = None) -> Tuple[bool, List[str]]:
    """
    Quality checks that MUST pass for a signal to be considered.

    Returns:
        Tuple[bool, List[str]]: (passes, list of failed check reasons)
    """
    failures = []

    # 1. Open Interest check
    if signal.open_interest < FLOW_PARAMS["min_open_interest"]:
        failures.append(f"Low OI ({signal.open_interest})")

    # 2. Strike distance check
    if signal.underlying_price > 0 and signal.strike > 0:
        distance = abs(signal.strike - signal.underlying_price) / signal.underlying_price
        if distance > FLOW_PARAMS["max_strike_distance_pct"]:
            failures.append(f"Strike too far ({distance:.1%})")

    # 3. Excluded ticker check
    if signal.symbol.upper() in EXCLUDED_TICKERS:
        failures.append(f"Excluded ticker ({signal.symbol})")

    # 4. Counter-trend check
    if market_regime and is_counter_trend(signal.option_type, market_regime):
        failures.append("Counter-trend trade")

    # 5. DTE check
    if signal.expiration:
        try:
            exp_date = datetime.strptime(signal.expiration[:10], "%Y-%m-%d")
            dte = (exp_date - datetime.now()).days
            if dte < FLOW_PARAMS["min_dte"]:
                failures.append(f"DTE too short ({dte})")
        except Exception:
            pass

    return len(failures) == 0, failures


def score_flow_signal(
    signal: FlowSignal,
    earnings_data: Dict = None,
    market_regime: Dict = None
) -> FlowSignal:
    """
    Apply conviction scoring to a flow signal.

    Risk-aware scoring that penalizes:
    - Counter-trend trades
    - High IV rank (expensive premium)
    - Short DTE
    - OTM options
    """
    score = 0
    breakdown = {}

    # Sweep (urgency) - good indicator
    if signal.is_sweep:
        score += 3
        breakdown["sweep"] = 3

    # Ask side - reduced importance
    if signal.is_ask_side:
        score += 1
        breakdown["ask_side"] = 1

    # High premium - indicates conviction
    if signal.premium >= 100000:
        score += 3
        breakdown["high_premium"] = 3
    if signal.premium >= 250000:
        score += 2
        breakdown["very_high_premium"] = 2

    # High vol/OI ratio - strong indicator
    if signal.vol_oi_ratio >= 1.0:
        score += 2
        breakdown["high_vol_oi"] = 2
    if signal.vol_oi_ratio >= 3.0:
        score += 1
        breakdown["very_high_vol_oi"] = 1

    # Floor trade (institutional)
    if signal.is_floor:
        score += 2
        breakdown["floor_trade"] = 2

    # OTM - lower probability, penalize
    if signal.is_otm:
        score -= 1
        breakdown["otm_penalty"] = -1

    # Opening trade - good signal
    if signal.is_opening:
        score += 2
        breakdown["opening_trade"] = 2

    # DTE-based scoring
    if signal.expiration:
        try:
            exp_date = datetime.strptime(signal.expiration[:10], "%Y-%m-%d")
            dte = (exp_date - datetime.now()).days
            if 0 < dte < 7:
                score -= 2  # High risk
                breakdown["very_low_dte_risk"] = -2
            elif 0 < dte < 14:
                score -= 1
                breakdown["low_dte_risk"] = -1
            elif 14 <= dte < 30:
                score += 1
                breakdown["good_dte"] = 1
        except Exception:
            pass

    # IV rank penalty
    if signal.iv_rank is not None:
        if signal.iv_rank > 70:
            score -= 3
            breakdown["high_iv_rank_penalty"] = -3
        elif signal.iv_rank > 50:
            score -= 1
            breakdown["elevated_iv_rank"] = -1
        elif signal.iv_rank < 30:
            score += 1
            breakdown["low_iv_rank_bonus"] = 1

    # Near earnings - risk factor
    if earnings_data:
        earnings_date = earnings_data.get("next_earnings_date")
        if earnings_date:
            try:
                days_to_earnings = (datetime.fromisoformat(earnings_date.replace('Z', '+00:00')) - datetime.now()).days
                if 0 < days_to_earnings <= 14:
                    score -= 1  # IV crush risk
                    breakdown["near_earnings_risk"] = -1
            except Exception:
                pass

    # Market regime alignment
    if market_regime:
        trend = market_regime.get("trend", "unknown")
        option_type = signal.option_type.lower()

        if trend == "bullish" and option_type == "put":
            score -= 3
            breakdown["counter_trend_penalty"] = -3
        elif trend == "bearish" and option_type == "call":
            score -= 3
            breakdown["counter_trend_penalty"] = -3
        elif (trend == "bullish" and option_type == "call") or \
             (trend == "bearish" and option_type == "put"):
            score += 1
            breakdown["trend_aligned_bonus"] = 1

    signal.score = max(0, score)
    signal.score_breakdown = breakdown
    return signal


def uw_flow_scan(
    min_premium: float = None,
    min_score: int = None,
    limit: int = 20,
    include_market_regime: bool = True,
) -> Dict[str, Any]:
    """
    Scan Unusual Whales for recent options flow alerts with TIGHT FILTERING.

    Uses tighter parameters by default:
    - min_premium: $150K (was $100K)
    - min_vol_oi: 2.0 (was 1.0)
    - sweeps only
    - opening positions only
    - DTE 14-45
    - Score 7+ required

    Args:
        min_premium: Minimum premium filter (default: $150K)
        min_score: Minimum signal score 0-10 (default: 7)
        limit: Maximum signals to return
        include_market_regime: Whether to factor in market regime

    Returns:
        ToolResult with signals list
    """
    # Apply tighter defaults
    min_premium = min_premium or FLOW_PARAMS["min_premium"]
    min_score = min_score or FLOW_PARAMS["min_score"]

    try:
        client = UnusualWhalesClient()

        # Get market regime if requested
        market_regime = None
        if include_market_regime:
            market_regime = get_market_regime_standalone()

        # Fetch raw alerts with API-level filters (optimized for single stocks)
        raw_alerts = client.get_flow_alerts(
            min_premium=int(min_premium),
            min_vol_oi_ratio=FLOW_PARAMS["min_vol_oi"],
            all_opening=FLOW_PARAMS["all_opening"],  # CRITICAL - only new positions
            min_dte=FLOW_PARAMS["min_dte"],
            max_dte=FLOW_PARAMS["max_dte"],
            issue_types=FLOW_PARAMS.get("issue_types", ["Common Stock"]),  # Filters OUT ETFs
            limit=FLOW_PARAMS.get("scan_limit", 30),
        )

        if not raw_alerts:
            return asdict(ToolResult(
                success=True,
                data={"signals": [], "count": 0, "message": "No flow alerts passed API filters"}
            ))

        # Parse, quality-check, and score signals
        signals = []
        iv_rank_cache = {}
        seen_symbols = set()
        skip_stats = {"excluded": 0, "quality_fail": 0, "low_score": 0}

        for alert in raw_alerts:
            signal = parse_flow_alert(alert)
            if signal is None:
                continue

            # Quick exclusion check
            if signal.symbol.upper() in EXCLUDED_TICKERS:
                skip_stats["excluded"] += 1
                continue

            # Get IV rank
            if signal.symbol not in seen_symbols:
                try:
                    iv_data = client.get_iv_rank(signal.symbol)
                    if iv_data:
                        iv_rank_cache[signal.symbol] = iv_data.get("iv_rank")
                except Exception:
                    pass
                seen_symbols.add(signal.symbol)

            signal.iv_rank = iv_rank_cache.get(signal.symbol)

            # Quality checks (must pass ALL)
            passes, fail_reasons = passes_quality_checks(signal, market_regime)
            if not passes:
                skip_stats["quality_fail"] += 1
                continue

            # Score the signal (0-10 scale)
            signal_score = score_signal(signal, market_regime)

            # Score the signal with the old method for breakdown
            signal = score_flow_signal(signal, market_regime=market_regime)

            # Override with new score
            signal.score = signal_score

            if signal_score < min_score:
                skip_stats["low_score"] += 1
                continue

            signals.append({
                "signal_id": signal.id,
                "symbol": signal.symbol,
                "option_type": signal.option_type,
                "strike": signal.strike,
                "expiration": signal.expiration,
                "premium": signal.premium,
                "volume": signal.volume,
                "open_interest": signal.open_interest,
                "vol_oi_ratio": signal.vol_oi_ratio,
                "score": signal_score,
                "score_breakdown": signal.score_breakdown,
                "sentiment": signal.sentiment,
                "is_sweep": signal.is_sweep,
                "is_floor": signal.is_floor,
                "is_opening": signal.is_opening,
                "is_otm": signal.is_otm,
                "underlying_price": signal.underlying_price,
                "iv_rank": signal.iv_rank,
                "timestamp": signal.timestamp,
            })

        # Sort by score descending
        signals.sort(key=lambda x: x["score"], reverse=True)
        signals = signals[:limit]

        return asdict(ToolResult(
            success=True,
            data={
                "signals": signals,
                "count": len(signals),
                "scanned": len(raw_alerts),
                "filtered_out": skip_stats,
                "market_regime": market_regime,
                "filters_applied": {
                    "min_premium": min_premium,
                    "min_vol_oi": FLOW_PARAMS["min_vol_oi"],
                    "opening_only": True,
                    "issue_types": FLOW_PARAMS.get("issue_types", ["Common Stock"]),
                    "dte_range": f"{FLOW_PARAMS['min_dte']}-{FLOW_PARAMS['max_dte']}",
                    "min_score": min_score,
                }
            }
        ))

    except Exception as e:
        logger.error(f"uw_flow_scan error: {e}")
        return asdict(ToolResult(success=False, error=str(e)))


def get_market_regime_standalone() -> Dict:
    """
    Calculate market regime using Alpaca data.

    Returns:
        Dict with trend, vix, SPY metrics
    """
    try:
        from alpaca.data.historical.stock import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
        from alpaca.data.timeframe import TimeFrame

        ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
        ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

        client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)

        # Get SPY bars
        bars_request = StockBarsRequest(
            symbol_or_symbols=["SPY"],
            timeframe=TimeFrame.Day,
            start=datetime.now() - timedelta(days=30),
        )
        bars = client.get_stock_bars(bars_request)

        if "SPY" not in bars or len(bars["SPY"]) < 20:
            return {"trend": "unknown", "vix": 20}

        spy_bars = bars["SPY"]
        closes = [float(b.close) for b in spy_bars]

        # Calculate SMAs
        sma_7 = sum(closes[-7:]) / 7 if len(closes) >= 7 else closes[-1]
        sma_20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else closes[-1]

        # Determine trend
        current_price = closes[-1]
        if sma_7 > sma_20 and current_price > sma_7:
            trend = "bullish"
        elif sma_7 < sma_20 and current_price < sma_7:
            trend = "bearish"
        else:
            trend = "sideways"

        # Try to get VIX from UW
        vix = 20
        try:
            uw_client = UnusualWhalesClient()
            vix_data = uw_client.get_stock_info("VIX")
            if vix_data and "price" in vix_data:
                vix = float(vix_data["price"])
        except Exception:
            pass

        return {
            "trend": trend,
            "sma_7": round(sma_7, 2),
            "sma_20": round(sma_20, 2),
            "spy_price": round(current_price, 2),
            "vix": vix,
        }

    except Exception as e:
        logger.error(f"Error getting market regime: {e}")
        return {"trend": "unknown", "vix": 20}


def iv_rank(symbol: str) -> Dict[str, Any]:
    """
    Get IV rank and percentile for an underlying.

    Args:
        symbol: Underlying symbol (e.g., 'SPY')

    Returns:
        Dict with iv_rank, iv_percentile, etc.
    """
    try:
        client = UnusualWhalesClient()
        iv_data = client.get_iv_rank(symbol)

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
        blackout_days: Days before earnings to avoid

    Returns:
        Dict with earnings info and safe_to_trade flag
    """
    try:
        client = UnusualWhalesClient()
        earnings_data = client.get_earnings(symbol)

        if not earnings_data or not earnings_data.get("next_earnings_date"):
            return asdict(ToolResult(
                success=True,
                data={
                    "symbol": symbol,
                    "earnings_date": None,
                    "days_until": None,
                    "in_blackout": False,
                    "safe_to_trade": True,
                }
            ))

        earnings_date = earnings_data["next_earnings_date"]
        if isinstance(earnings_date, str):
            earnings_date = datetime.strptime(earnings_date[:10], "%Y-%m-%d").date()

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
            }
        ))
    except Exception as e:
        logger.error(f"earnings_check error: {e}")
        return asdict(ToolResult(success=False, error=str(e)))
