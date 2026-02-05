"""
Flow Scanner - Fetch and score unusual options flow from Unusual Whales API
"""
import os
import requests
from datetime import datetime, timedelta
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
    sentiment: str  # 'bullish' or 'bearish'
    score: int = 0
    score_breakdown: Dict = field(default_factory=dict)
    db_id: int = None  # Database row ID after logging


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
    ) -> List[Dict]:
        """
        Fetch flow alerts from Unusual Whales API

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

        # Bullish: Call bought at ask OR Put sold at bid
        # Bearish: Put bought at ask OR Call sold at bid
        if option_type == "call":
            sentiment = "bullish" if is_ask_side else "bearish"
        else:  # put
            sentiment = "bearish" if is_ask_side else "bullish"

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


def score_flow_signal(signal: FlowSignal, earnings_data: Dict = None) -> FlowSignal:
    """
    Apply conviction scoring to a flow signal

    Scoring criteria from FLOW_SCORING config
    """
    score = 0
    breakdown = {}

    # Sweep (urgency)
    if signal.is_sweep:
        score += FLOW_SCORING["sweep"]
        breakdown["sweep"] = FLOW_SCORING["sweep"]

    # Ask side (bullish conviction)
    if signal.is_ask_side:
        score += FLOW_SCORING["ask_side"]
        breakdown["ask_side"] = FLOW_SCORING["ask_side"]

    # High premium
    if signal.premium >= 100000:
        score += FLOW_SCORING["high_premium"]
        breakdown["high_premium"] = FLOW_SCORING["high_premium"]

    # Very high premium bonus
    if signal.premium >= 250000:
        score += FLOW_SCORING["very_high_premium"]
        breakdown["very_high_premium"] = FLOW_SCORING["very_high_premium"]

    # High vol/OI ratio
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

    # OTM (more speculative = more conviction)
    if signal.is_otm:
        score += FLOW_SCORING["otm"]
        breakdown["otm"] = FLOW_SCORING["otm"]

    # Opening trade
    if signal.is_opening:
        score += FLOW_SCORING["opening_trade"]
        breakdown["opening_trade"] = FLOW_SCORING["opening_trade"]

    # Near earnings
    if earnings_data:
        earnings_date = earnings_data.get("next_earnings_date")
        if earnings_date:
            try:
                days_to_earnings = (datetime.fromisoformat(earnings_date.replace('Z', '+00:00')) - datetime.now()).days
                if 0 < days_to_earnings <= 14:
                    score += FLOW_SCORING["near_earnings"]
                    breakdown["near_earnings"] = FLOW_SCORING["near_earnings"]
            except Exception:
                pass

    # Low DTE (< 30 days)
    if signal.expiration:
        try:
            exp_date = datetime.strptime(signal.expiration[:10], "%Y-%m-%d")
            dte = (exp_date - datetime.now()).days
            if 0 < dte < 30:
                score += FLOW_SCORING["low_dte"]
                breakdown["low_dte"] = FLOW_SCORING["low_dte"]
        except Exception:
            pass

    signal.score = score
    signal.score_breakdown = breakdown
    return signal


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
    max_dte: int = 60,
) -> List[FlowSignal]:
    """
    Run a flow scan with filters and scoring

    Returns list of FlowSignal objects sorted by score
    """
    # Use defaults from config
    min_premium = min_premium or FLOW_CONFIG["min_premium"]
    min_vol_oi = min_vol_oi or FLOW_CONFIG["min_vol_oi"]
    min_score = min_score or FLOW_CONFIG["min_score"]
    limit = limit or FLOW_CONFIG["scan_limit"]

    print(f"[{datetime.now()}] Running flow scan...")
    print(f"  Filters: min_premium=${min_premium:,}, min_vol_oi={min_vol_oi}x, min_score={min_score}")

    client = UnusualWhalesClient()

    # Fetch flow alerts
    alerts = client.get_flow_alerts(
        min_premium=min_premium,
        min_vol_oi_ratio=min_vol_oi,
        is_sweep=True if sweeps_only else None,
        is_ask_side=True if ask_side_only else None,
        all_opening=True if opening_only else None,
        max_dte=max_dte,
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

    for alert in alerts:
        signal = parse_flow_alert(alert)
        if not signal:
            continue

        # Skip puts if not included
        if not include_puts and signal.option_type == "put":
            continue

        # Get earnings data for scoring (cache by symbol)
        earnings_data = None
        if signal.symbol not in seen_symbols:
            try:
                earnings_data = client.get_earnings(signal.symbol)
                seen_symbols.add(signal.symbol)
            except Exception:
                pass

        # Score the signal
        signal = score_flow_signal(signal, earnings_data)

        # Filter by minimum score
        if signal.score >= min_score:
            signals.append(signal)

    # Sort by score descending
    signals.sort(key=lambda x: x.score, reverse=True)

    print(f"  {len(signals)} signals passed score filter (>= {min_score})")

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
