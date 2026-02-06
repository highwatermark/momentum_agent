"""
Flow Scanner - Fetch and score unusual options flow from Unusual Whales API
"""
import os
import requests
from datetime import datetime, timedelta
from typing import Dict as TypeDict  # Additional import for market_regime typing
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from dotenv import load_dotenv

from config import UW_API_KEY, FLOW_CONFIG, FLOW_SCORING

load_dotenv()

# API Configuration
UW_BASE_URL = "https://api.unusualwhales.com/api"


@dataclass
class FlowSignal:
    """Represents a single options flow signal"""
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
    sentiment: str  # 'bullish', 'bearish', or 'neutral' (let Claude decide)
    score: int = 0
    score_breakdown: Dict = field(default_factory=dict)
    db_id: int = None  # Database row ID after logging
    iv_rank: float = None  # IV rank (0-100) for premium evaluation


class UnusualWhalesClient:
    """Client for Unusual Whales API"""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or UW_API_KEY
        self.base_url = UW_BASE_URL
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json"
        })

    def _request(self, endpoint: str, params: Dict = None) -> Dict:
        """Make API request with error handling"""
        url = f"{self.base_url}{endpoint}"
        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"API Error: {e}")
            return {"error": str(e)}

    def get_flow_alerts(
        self,
        min_premium: int = None,
        is_sweep: bool = None,
        is_ask_side: bool = None,
        min_vol_oi_ratio: float = None,
        limit: int = 50,
        ticker_symbol: str = None,
        is_otm: bool = None,
        max_dte: int = None,
        min_dte: int = None,
        is_call: bool = None,
        is_put: bool = None,
        all_opening: bool = None,
        newer_than: str = None,
        older_than: str = None,
        issue_types: List[str] = None,
    ) -> List[Dict]:
        """
        Fetch flow alerts from Unusual Whales API

        Args:
            issue_types: List of issue types to filter (e.g., ["Common Stock"])
                        This filters OUT ETFs at the API level.

        Returns list of flow alert dictionaries
        """
        params = {"limit": limit}

        if min_premium:
            params["min_premium"] = min_premium
        if is_sweep is not None:
            params["is_sweep"] = str(is_sweep).lower()
        if is_ask_side is not None:
            params["is_ask_side"] = str(is_ask_side).lower()
        if min_vol_oi_ratio:
            params["min_volume_oi_ratio"] = min_vol_oi_ratio
        if ticker_symbol:
            params["ticker_symbol"] = ticker_symbol
        if is_otm is not None:
            params["is_otm"] = str(is_otm).lower()
        if max_dte:
            params["max_dte"] = max_dte
        if min_dte:
            params["min_dte"] = min_dte
        if is_call is not None:
            params["is_call"] = str(is_call).lower()
        if is_put is not None:
            params["is_put"] = str(is_put).lower()
        if all_opening is not None:
            params["all_opening"] = str(all_opening).lower()
        if newer_than:
            params["newer_than"] = newer_than
        if older_than:
            params["older_than"] = older_than
        if issue_types:
            params["issue_types"] = ",".join(issue_types)

        result = self._request("/option-trades/flow-alerts", params)

        if "error" in result:
            return []

        return result.get("data", [])

    def get_stock_info(self, ticker: str) -> Dict:
        """Get stock info for a ticker"""
        result = self._request(f"/stock/{ticker}/info")
        return result.get("data", {}) if "data" in result else result

    def get_earnings(self, ticker: str) -> Dict:
        """Get earnings info for a ticker"""
        result = self._request(f"/stock/{ticker}/earnings")
        return result.get("data", {}) if "data" in result else result

    def get_iv_rank(self, ticker: str) -> Dict:
        """Get IV rank for a ticker"""
        result = self._request(f"/stock/{ticker}/iv-rank")
        return result.get("data", {}) if "data" in result else result

    def get_max_pain(self, ticker: str, expiration: str = None) -> Dict:
        """Get max pain for a ticker"""
        params = {}
        if expiration:
            params["expiration"] = expiration
        result = self._request(f"/stock/{ticker}/max-pain", params)
        return result.get("data", {}) if "data" in result else result

    def get_greek_exposure(self, ticker: str) -> Dict:
        """Get greek exposure for a ticker"""
        result = self._request(f"/stock/{ticker}/greek-exposure")
        return result.get("data", {}) if "data" in result else result


def parse_flow_alert(alert: Dict) -> Optional[FlowSignal]:
    """Parse raw API response into FlowSignal dataclass"""
    try:
        # Get option type
        option_type = alert.get("type", "").lower()

        # Determine if ask side or bid side based on premiums
        total_ask_prem = float(alert.get("total_ask_side_prem", 0))
        total_bid_prem = float(alert.get("total_bid_side_prem", 0))
        is_ask_side = total_ask_prem > total_bid_prem
        is_bid_side = total_bid_prem > total_ask_prem

        # IMPORTANT: Bid/ask side does NOT reliably indicate directional intent
        # Buying at ask could be hedging, selling at bid could be closing
        # Set sentiment to neutral - let Claude determine with full market context
        sentiment = "neutral"

        # Calculate vol/OI ratio
        volume = int(alert.get("volume", 0))
        open_interest = int(alert.get("open_interest", 1))
        vol_oi_ratio_raw = alert.get("volume_oi_ratio")
        if vol_oi_ratio_raw:
            vol_oi_ratio = float(vol_oi_ratio_raw)
        else:
            vol_oi_ratio = volume / open_interest if open_interest > 0 else volume

        # Get premium (total_premium is string in API response)
        premium = float(alert.get("total_premium", 0))

        # Check if OTM based on strike vs underlying
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
        print(f"Error parsing flow alert: {e}")
        return None


def score_flow_signal(signal: FlowSignal, earnings_data: Dict = None, market_regime: Dict = None) -> FlowSignal:
    """
    Apply conviction scoring to a flow signal

    Scoring criteria from FLOW_SCORING config.
    Now includes market regime awareness and IV rank penalty.
    """
    score = 0
    breakdown = {}

    # Sweep (urgency)
    if signal.is_sweep:
        score += FLOW_SCORING["sweep"]
        breakdown["sweep"] = FLOW_SCORING["sweep"]

    # Ask side - reduced importance since it doesn't reliably indicate direction
    # Keep small bonus for aggressive order execution
    if signal.is_ask_side:
        score += 1  # Reduced from FLOW_SCORING["ask_side"] (was 2)
        breakdown["ask_side"] = 1

    # High premium - indicates conviction but not necessarily good trade
    if signal.premium >= 100000:
        score += FLOW_SCORING["high_premium"]
        breakdown["high_premium"] = FLOW_SCORING["high_premium"]

    # Very high premium bonus
    if signal.premium >= 250000:
        score += FLOW_SCORING["very_high_premium"]
        breakdown["very_high_premium"] = FLOW_SCORING["very_high_premium"]

    # High vol/OI ratio - strong indicator of new positioning
    if signal.vol_oi_ratio >= 1.0:
        score += FLOW_SCORING["high_vol_oi"]
        breakdown["high_vol_oi"] = FLOW_SCORING["high_vol_oi"]

    # Very high vol/OI bonus
    if signal.vol_oi_ratio >= 3.0:
        score += FLOW_SCORING["very_high_vol_oi"]
        breakdown["very_high_vol_oi"] = FLOW_SCORING["very_high_vol_oi"]

    # Floor trade (institutional)
    if signal.is_floor:
        score += FLOW_SCORING["floor_trade"]
        breakdown["floor_trade"] = FLOW_SCORING["floor_trade"]

    # OTM - CHANGED: Lower probability of profit, penalize instead of bonus
    if signal.is_otm:
        score -= 1  # Was +1, now -1 (OTM has lower delta and win rate)
        breakdown["otm_penalty"] = -1

    # Opening trade
    if signal.is_opening:
        score += FLOW_SCORING["opening_trade"]
        breakdown["opening_trade"] = FLOW_SCORING["opening_trade"]

    # Near earnings - be cautious, IV crush risk
    if earnings_data:
        earnings_date = earnings_data.get("next_earnings_date")
        if earnings_date:
            try:
                days_to_earnings = (datetime.fromisoformat(earnings_date.replace('Z', '+00:00')) - datetime.now()).days
                if 0 < days_to_earnings <= 14:
                    # Changed: near earnings is a risk factor, not a bonus
                    score -= 1  # Was +1, now -1 (IV crush risk)
                    breakdown["near_earnings_risk"] = -1
            except Exception:
                pass

    # Low DTE (< 30 days) - high theta decay risk
    if signal.expiration:
        try:
            exp_date = datetime.strptime(signal.expiration[:10], "%Y-%m-%d")
            dte = (exp_date - datetime.now()).days
            if 0 < dte < 7:
                score -= 2  # Very short DTE - high risk
                breakdown["very_low_dte_risk"] = -2
            elif 0 < dte < 14:
                score -= 1  # Short DTE - moderate risk
                breakdown["low_dte_risk"] = -1
            elif 14 <= dte < 30:
                score += 1  # Sweet spot for theta/gamma balance
                breakdown["good_dte"] = 1
        except Exception:
            pass

    # IV rank penalty (if available) - buying expensive premium is -EV
    if signal.iv_rank is not None:
        if signal.iv_rank > 70:
            score -= 3  # Expensive premium - major penalty
            breakdown["high_iv_rank_penalty"] = -3
        elif signal.iv_rank > 50:
            score -= 1  # Moderately expensive
            breakdown["elevated_iv_rank"] = -1
        elif signal.iv_rank < 30:
            score += 1  # Cheap premium - bonus
            breakdown["low_iv_rank_bonus"] = 1

    # Market regime alignment check
    if market_regime:
        trend = market_regime.get("trend", "unknown")
        option_type = signal.option_type.lower()

        # Penalize counter-trend trades
        if trend == "bullish" and option_type == "put":
            score -= 3  # Puts in uptrend - counter-trend
            breakdown["counter_trend_penalty"] = -3
        elif trend == "bearish" and option_type == "call":
            score -= 3  # Calls in downtrend - counter-trend
            breakdown["counter_trend_penalty"] = -3
        elif (trend == "bullish" and option_type == "call") or \
             (trend == "bearish" and option_type == "put"):
            score += 1  # Trend-aligned
            breakdown["trend_aligned_bonus"] = 1

    signal.score = max(0, score)  # Floor at 0
    signal.score_breakdown = breakdown
    return signal


def get_market_regime() -> Dict:
    """
    Calculate current market regime based on SPY trend.

    Returns:
        Dict with trend ('bullish', 'bearish', 'sideways'), vix level, etc.
    """
    try:
        from alpaca.data.historical.stock import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        from config import ALPACA_API_KEY, ALPACA_SECRET_KEY

        client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)

        # Get SPY bars for SMA calculation
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

        # Try to get VIX
        vix = 20  # Default
        try:
            uw_client = UnusualWhalesClient()
            vix_data = uw_client.get_stock_info("VIX")
            if vix_data and "price" in vix_data:
                vix = float(vix_data["price"])
        except Exception:
            pass

        return {
            "trend": trend,
            "sma_7": sma_7,
            "sma_20": sma_20,
            "spy_price": current_price,
            "vix": vix,
        }
    except Exception as e:
        print(f"Error getting market regime: {e}")
        return {"trend": "unknown", "vix": 20}


def run_flow_scan(
    min_premium: int = None,
    min_vol_oi: float = None,
    sweeps_only: bool = False,
    ask_side_only: bool = False,
    opening_only: bool = False,
    min_score: int = None,
    limit: int = None,
    ticker: str = None,
    include_puts: bool = True,
    max_dte: int = None,
    min_dte: int = None,
    include_market_regime: bool = True,
) -> List[FlowSignal]:
    """
    Run a flow scan with filters and scoring

    Returns list of FlowSignal objects sorted by score

    IMPORTANT: ETFs (SPY, QQQ, IWM, etc.) are excluded - too much hedging noise.
    Only single stocks with clear directional flow are traded.
    """
    # Use defaults from config
    min_premium = min_premium or FLOW_CONFIG["min_premium"]
    min_vol_oi = min_vol_oi or FLOW_CONFIG["min_vol_oi"]
    min_score = min_score or FLOW_CONFIG["min_score"]
    limit = limit or FLOW_CONFIG["scan_limit"]
    min_dte = min_dte or FLOW_CONFIG.get("min_dte", 14)
    max_dte = max_dte or FLOW_CONFIG.get("max_dte", 45)

    # ETF exclusion list (too much hedging noise, low signal-to-noise ratio)
    from config import OPTIONS_CONFIG
    excluded_etfs = set(OPTIONS_CONFIG.get("excluded_etfs", []))

    print(f"[{datetime.now()}] Running flow scan...")
    print(f"  Filters: min_premium=${min_premium:,}, min_vol_oi={min_vol_oi}x, min_score={min_score}")
    print(f"  DTE range: {min_dte}-{max_dte} days")
    print(f"  Excluded ETFs: {len(excluded_etfs)} symbols")

    client = UnusualWhalesClient()

    # Get market regime for trend-aware scoring
    market_regime = None
    if include_market_regime:
        market_regime = get_market_regime()
        print(f"  Market regime: {market_regime.get('trend', 'unknown')}, VIX: {market_regime.get('vix', 'N/A')}")

    # Fetch flow alerts
    alerts = client.get_flow_alerts(
        min_premium=min_premium,
        min_vol_oi_ratio=min_vol_oi,
        is_sweep=True if sweeps_only else None,
        is_ask_side=True if ask_side_only else None,
        all_opening=True if opening_only else None,
        max_dte=max_dte,
        min_dte=min_dte,
        limit=limit,
        ticker_symbol=ticker,
    )

    if not alerts:
        print("  No flow alerts found matching criteria")
        return []

    print(f"  Found {len(alerts)} raw alerts")

    # Parse and score signals
    signals = []
    seen_symbols = set()
    iv_rank_cache = {}
    etf_skipped = 0

    for alert in alerts:
        signal = parse_flow_alert(alert)
        if not signal:
            continue

        # CRITICAL: Skip ETFs - too much hedging noise, institutions hedge through these
        if signal.symbol.upper() in excluded_etfs:
            etf_skipped += 1
            continue

        # Skip puts if not included
        if not include_puts and signal.option_type == "put":
            continue

        # Get earnings data and IV rank for scoring (cache by symbol)
        earnings_data = None
        if signal.symbol not in seen_symbols:
            try:
                earnings_data = client.get_earnings(signal.symbol)
            except Exception:
                pass

            # Fetch IV rank for the symbol
            try:
                iv_data = client.get_iv_rank(signal.symbol)
                if iv_data:
                    iv_rank_cache[signal.symbol] = iv_data.get("iv_rank")
            except Exception:
                pass

            seen_symbols.add(signal.symbol)

        # Set IV rank on signal
        signal.iv_rank = iv_rank_cache.get(signal.symbol)

        # Score the signal with market regime awareness
        signal = score_flow_signal(signal, earnings_data, market_regime)

        # Filter by minimum score
        if signal.score >= min_score:
            signals.append(signal)

    # Sort by score descending
    signals.sort(key=lambda x: x.score, reverse=True)

    print(f"  {len(signals)} signals passed score filter (>= {min_score})")
    if etf_skipped > 0:
        print(f"  {etf_skipped} ETF signals skipped (hedging noise)")

    return signals


def get_flow_summary(signals: List[FlowSignal]) -> Dict:
    """Generate summary statistics for a list of flow signals"""
    if not signals:
        return {
            "count": 0,
            "total_premium": 0,
            "bullish_count": 0,
            "bearish_count": 0,
            "sweeps": 0,
            "floor_trades": 0,
            "avg_score": 0,
        }

    return {
        "count": len(signals),
        "total_premium": sum(s.premium for s in signals),
        "bullish_count": sum(1 for s in signals if s.sentiment == "bullish"),
        "bearish_count": sum(1 for s in signals if s.sentiment == "bearish"),
        "sweeps": sum(1 for s in signals if s.is_sweep),
        "floor_trades": sum(1 for s in signals if s.is_floor),
        "avg_score": sum(s.score for s in signals) / len(signals),
    }


def format_flow_signal(signal: FlowSignal) -> str:
    """Format a flow signal for display"""
    emoji = "📈" if signal.sentiment == "bullish" else "📉"
    sweep_tag = "🔥SWEEP" if signal.is_sweep else ""
    floor_tag = "🏦FLOOR" if signal.is_floor else ""

    tags = " ".join(filter(None, [sweep_tag, floor_tag]))

    return (
        f"{emoji} {signal.symbol} {signal.option_type.upper()} ${signal.strike} "
        f"exp {signal.expiration[:10]}\n"
        f"   Premium: ${signal.premium:,.0f} | Size: {signal.size:,}\n"
        f"   Vol/OI: {signal.vol_oi_ratio}x | Score: {signal.score}/20\n"
        f"   {tags}"
    )


if __name__ == "__main__":
    # Test the scanner
    print("Testing Unusual Whales Flow Scanner\n")

    # Check API key
    if not UW_API_KEY:
        print("ERROR: UW_API_KEY not set in environment")
        exit(1)

    print(f"API Key: {UW_API_KEY[:8]}...{UW_API_KEY[-4:]}")

    # Run scan
    signals = run_flow_scan(
        min_premium=100000,
        min_vol_oi=1.0,
        min_score=6,  # Lower threshold for testing
        limit=20,
    )

    if signals:
        print(f"\n=== Top Signals ===\n")
        for i, signal in enumerate(signals[:10], 1):
            print(f"{i}. {format_flow_signal(signal)}\n")

        summary = get_flow_summary(signals)
        print(f"\n=== Summary ===")
        print(f"Total signals: {summary['count']}")
        print(f"Total premium: ${summary['total_premium']:,.0f}")
        print(f"Bullish: {summary['bullish_count']} | Bearish: {summary['bearish_count']}")
        print(f"Avg score: {summary['avg_score']:.1f}")
    else:
        print("\nNo signals found. Try lowering filters or check API key.")
