"""
Flow Analyzer - Enrich flow signals with context and generate Claude theses
"""
import json
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Dict, Optional

from alpaca.data import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
import anthropic

from config import ALPACA_API_KEY, ALPACA_SECRET_KEY, ANTHROPIC_API_KEY, FLOW_CONFIG
from flow_scanner import FlowSignal, UnusualWhalesClient, run_flow_scan


@dataclass
class EnrichedFlowSignal:
    """Flow signal enriched with price context and Claude analysis"""
    signal: FlowSignal

    # Price context
    current_price: float = 0
    price_change_1d: float = 0
    price_change_5d: float = 0
    volume_today: int = 0
    avg_volume_20d: int = 0
    relative_volume: float = 0

    # Technical context
    sma_20: float = 0
    sma_50: float = 0
    above_sma_20: bool = False
    above_sma_50: bool = False
    rsi_14: float = 50
    atr_14: float = 0

    # Options context
    iv_rank: float = 0
    iv_percentile: float = 0
    earnings_date: str = ""
    days_to_earnings: int = -1
    max_pain: float = 0

    # Generated analysis
    thesis: str = ""
    recommendation: str = "SKIP"  # BUY, WATCH, SKIP
    conviction: float = 0
    risk_factors: List[str] = field(default_factory=list)
    entry_strategy: str = ""
    target_exit: str = ""
    stop_loss: str = ""
    reasoning: str = ""


def get_alpaca_client() -> StockHistoricalDataClient:
    """Get Alpaca data client"""
    return StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)


def get_price_context(client: StockHistoricalDataClient, symbol: str) -> Dict:
    """Get price and technical context from Alpaca"""
    end = datetime.now()
    start = end - timedelta(days=60)

    try:
        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=start,
            end=end
        )
        bars = client.get_stock_bars(request)
        bar_list = bars.data.get(symbol, [])

        if len(bar_list) < 20:
            return {}

        # Sort by timestamp
        bar_list = sorted(bar_list, key=lambda x: x.timestamp)

        closes = [b.close for b in bar_list]
        volumes = [b.volume for b in bar_list]
        highs = [b.high for b in bar_list]
        lows = [b.low for b in bar_list]

        current_price = closes[-1]
        price_1d_ago = closes[-2] if len(closes) > 1 else current_price
        price_5d_ago = closes[-6] if len(closes) > 5 else current_price

        # SMAs
        sma_20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else current_price
        sma_50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else current_price

        # Volume
        volume_today = volumes[-1]
        avg_volume_20d = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else volume_today

        # RSI
        rsi_14 = calculate_rsi(closes)

        # ATR
        atr_14 = calculate_atr(highs, lows, closes)

        return {
            "current_price": current_price,
            "price_change_1d": ((current_price - price_1d_ago) / price_1d_ago) * 100,
            "price_change_5d": ((current_price - price_5d_ago) / price_5d_ago) * 100,
            "volume_today": volume_today,
            "avg_volume_20d": avg_volume_20d,
            "relative_volume": volume_today / avg_volume_20d if avg_volume_20d > 0 else 1,
            "sma_20": sma_20,
            "sma_50": sma_50,
            "above_sma_20": current_price > sma_20,
            "above_sma_50": current_price > sma_50,
            "rsi_14": rsi_14,
            "atr_14": atr_14,
        }
    except Exception as e:
        print(f"Error getting price context for {symbol}: {e}")
        return {}


def calculate_rsi(closes: List[float], period: int = 14) -> float:
    """Calculate RSI"""
    if len(closes) < period + 1:
        return 50.0

    gains = []
    losses = []

    for i in range(1, len(closes)):
        change = closes[i] - closes[i-1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    recent_gains = gains[-period:]
    recent_losses = losses[-period:]

    avg_gain = sum(recent_gains) / period
    avg_loss = sum(recent_losses) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calculate_atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
    """Calculate Average True Range"""
    if len(closes) < period + 1:
        return 0

    true_ranges = []
    for i in range(1, len(closes)):
        high_low = highs[i] - lows[i]
        high_close = abs(highs[i] - closes[i-1])
        low_close = abs(lows[i] - closes[i-1])
        true_ranges.append(max(high_low, high_close, low_close))

    return sum(true_ranges[-period:]) / period


def get_options_context(uw_client: UnusualWhalesClient, symbol: str) -> Dict:
    """Get options context from Unusual Whales"""
    context = {}

    try:
        # IV Rank
        iv_data = uw_client.get_iv_rank(symbol)
        if iv_data and 'error' not in iv_data:
            context["iv_rank"] = float(iv_data.get("iv_rank", 0) or 0)
            context["iv_percentile"] = float(iv_data.get("iv_percentile", 0) or 0)
    except Exception:
        pass

    try:
        # Earnings
        earnings_data = uw_client.get_earnings(symbol)
        if earnings_data and 'error' not in earnings_data:
            next_earnings = earnings_data.get("next_earnings_date")
            if next_earnings:
                context["earnings_date"] = next_earnings
                try:
                    earnings_dt = datetime.fromisoformat(next_earnings.replace('Z', '+00:00'))
                    context["days_to_earnings"] = (earnings_dt - datetime.now(earnings_dt.tzinfo)).days
                except Exception:
                    pass
    except Exception:
        pass

    try:
        # Max Pain
        max_pain_data = uw_client.get_max_pain(symbol)
        if max_pain_data and 'error' not in max_pain_data:
            context["max_pain"] = float(max_pain_data.get("price", 0) or 0)
    except Exception:
        pass

    return context


def enrich_flow_signal(
    signal: FlowSignal,
    alpaca_client: StockHistoricalDataClient,
    uw_client: UnusualWhalesClient
) -> EnrichedFlowSignal:
    """Enrich a flow signal with price and options context"""
    enriched = EnrichedFlowSignal(signal=signal)

    # Get price context
    price_ctx = get_price_context(alpaca_client, signal.symbol)
    if price_ctx:
        enriched.current_price = price_ctx.get("current_price", 0)
        enriched.price_change_1d = price_ctx.get("price_change_1d", 0)
        enriched.price_change_5d = price_ctx.get("price_change_5d", 0)
        enriched.volume_today = price_ctx.get("volume_today", 0)
        enriched.avg_volume_20d = price_ctx.get("avg_volume_20d", 0)
        enriched.relative_volume = price_ctx.get("relative_volume", 0)
        enriched.sma_20 = price_ctx.get("sma_20", 0)
        enriched.sma_50 = price_ctx.get("sma_50", 0)
        enriched.above_sma_20 = price_ctx.get("above_sma_20", False)
        enriched.above_sma_50 = price_ctx.get("above_sma_50", False)
        enriched.rsi_14 = price_ctx.get("rsi_14", 50)
        enriched.atr_14 = price_ctx.get("atr_14", 0)

    # Get options context
    options_ctx = get_options_context(uw_client, signal.symbol)
    if options_ctx:
        enriched.iv_rank = options_ctx.get("iv_rank", 0)
        enriched.iv_percentile = options_ctx.get("iv_percentile", 0)
        enriched.earnings_date = options_ctx.get("earnings_date", "")
        enriched.days_to_earnings = options_ctx.get("days_to_earnings", -1)
        enriched.max_pain = options_ctx.get("max_pain", 0)

    return enriched


def generate_thesis(enriched: EnrichedFlowSignal) -> EnrichedFlowSignal:
    """Use Claude to generate a trade thesis"""
    signal = enriched.signal

    # Build context for Claude
    prompt = f"""Analyze this unusual options flow signal and generate a trade thesis.

## Flow Signal
- Symbol: {signal.symbol}
- Type: {signal.option_type.upper()} ${signal.strike} exp {signal.expiration}
- Premium: ${signal.premium:,.0f}
- Size: {signal.size:,} contracts
- Volume: {signal.volume:,} | Open Interest: {signal.open_interest:,}
- Vol/OI Ratio: {signal.vol_oi_ratio}x
- Is Sweep: {signal.is_sweep}
- Is Ask Side: {signal.is_ask_side}
- Is Floor Trade: {signal.is_floor}
- Is Opening: {signal.is_opening}
- Is OTM: {signal.is_otm}
- Underlying Price: ${signal.underlying_price:.2f}
- Sentiment: {signal.sentiment}
- Conviction Score: {signal.score}/20

## Price Context
- Current Price: ${enriched.current_price:.2f}
- 1-Day Change: {enriched.price_change_1d:+.2f}%
- 5-Day Change: {enriched.price_change_5d:+.2f}%
- Above SMA20: {enriched.above_sma_20} | Above SMA50: {enriched.above_sma_50}
- RSI(14): {enriched.rsi_14:.1f}
- Relative Volume: {enriched.relative_volume:.1f}x

## Options Context
- IV Rank: {enriched.iv_rank:.0f}%
- IV Percentile: {enriched.iv_percentile:.0f}%
- Days to Earnings: {enriched.days_to_earnings if enriched.days_to_earnings > 0 else 'N/A'}
- Max Pain: ${enriched.max_pain:.2f}

Provide your analysis as JSON:
{{
    "thesis": "2-3 sentence thesis explaining the likely reasoning behind this flow",
    "recommendation": "BUY|WATCH|SKIP",
    "conviction": 0.0-1.0,
    "entry_strategy": "How to enter this trade",
    "target_exit": "Profit target",
    "stop_loss": "Stop loss level",
    "risk_factors": ["list", "of", "risks"],
    "reasoning": "Detailed reasoning for recommendation"
}}
"""

    system_prompt = """You are an expert options flow analyst. Analyze unusual options flow signals and generate actionable trade theses.

Signal Quality Factors (Positive):
- Sweeps: Urgency - willing to pay across exchanges
- Ask-side: Paying up = conviction
- High premium ($100K+): Serious money
- Vol/OI > 1: Unusual activity
- Floor trades: Institutional
- Opening trades: New positions
- Price above SMAs: Trend aligned
- Low IV rank: Cheap premium

Risk Factors (Negative):
- High IV rank (>50%): Premium expensive
- Near earnings: Binary risk
- Short DTE + OTM: Theta decay
- Against trend: Fighting momentum
- Low conviction score: Weak signal
- Single contract: Could be hedge

Be conservative with BUY recommendations - only recommend BUY for truly compelling setups.
Respond ONLY with valid JSON, no other text."""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}]
        )

        # Parse response
        response_text = response.content[0].text.strip()

        # Clean up response if needed
        if response_text.startswith("```"):
            response_text = response_text.split("```")[1]
            if response_text.startswith("json"):
                response_text = response_text[4:]
        response_text = response_text.strip()

        analysis = json.loads(response_text)

        enriched.thesis = analysis.get("thesis", "")
        enriched.recommendation = analysis.get("recommendation", "SKIP")
        enriched.conviction = float(analysis.get("conviction", 0))
        enriched.entry_strategy = analysis.get("entry_strategy", "")
        enriched.target_exit = analysis.get("target_exit", "")
        enriched.stop_loss = analysis.get("stop_loss", "")
        enriched.risk_factors = analysis.get("risk_factors", [])
        enriched.reasoning = analysis.get("reasoning", "")

    except Exception as e:
        print(f"Error generating thesis for {signal.symbol}: {e}")
        enriched.thesis = f"Error analyzing signal: {e}"
        enriched.recommendation = "SKIP"
        enriched.conviction = 0

    return enriched


def analyze_flow_signals(
    signals: List[FlowSignal],
    max_analyze: int = None
) -> List[EnrichedFlowSignal]:
    """Analyze multiple flow signals with enrichment and Claude thesis"""
    max_analyze = max_analyze or FLOW_CONFIG.get("max_analyze", 10)

    print(f"[{datetime.now()}] Analyzing {min(len(signals), max_analyze)} flow signals...")

    alpaca_client = get_alpaca_client()
    uw_client = UnusualWhalesClient()

    enriched_signals = []

    for i, signal in enumerate(signals[:max_analyze]):
        print(f"  [{i+1}/{min(len(signals), max_analyze)}] Analyzing {signal.symbol}...")

        # Enrich with context
        enriched = enrich_flow_signal(signal, alpaca_client, uw_client)

        # Generate thesis with Claude
        enriched = generate_thesis(enriched)

        enriched_signals.append(enriched)

        # Rate limiting
        if i < max_analyze - 1:
            import time
            time.sleep(0.5)  # Small delay between API calls

    print(f"  Analysis complete. {len(enriched_signals)} signals analyzed.")

    return enriched_signals


def get_buy_recommendations(enriched_signals: List[EnrichedFlowSignal]) -> List[EnrichedFlowSignal]:
    """Filter for BUY recommendations"""
    return [e for e in enriched_signals if e.recommendation == "BUY"]


def format_flow_analysis_for_telegram(enriched: EnrichedFlowSignal) -> str:
    """Format enriched signal for Telegram"""
    signal = enriched.signal

    # Emoji based on recommendation
    if enriched.recommendation == "BUY":
        rec_emoji = "🟢"
    elif enriched.recommendation == "WATCH":
        rec_emoji = "🟡"
    else:
        rec_emoji = "🔴"

    sentiment_emoji = "📈" if signal.sentiment == "bullish" else "📉"
    sweep_tag = "🔥SWEEP " if signal.is_sweep else ""
    floor_tag = "🏦FLOOR " if signal.is_floor else ""

    msg = f"{sentiment_emoji} *{signal.symbol}* {signal.option_type.upper()} ${signal.strike}\n"
    msg += f"Exp: {signal.expiration[:10]} | {sweep_tag}{floor_tag}\n\n"

    msg += f"*Flow Data:*\n"
    msg += f"├── Premium: ${signal.premium:,.0f}\n"
    msg += f"├── Size: {signal.size:,} contracts\n"
    msg += f"├── Vol/OI: {signal.vol_oi_ratio}x\n"
    msg += f"└── Score: {signal.score}/20\n\n"

    msg += f"*Price Context:*\n"
    msg += f"├── Price: ${enriched.current_price:.2f}\n"
    msg += f"├── 1D: {enriched.price_change_1d:+.1f}% | 5D: {enriched.price_change_5d:+.1f}%\n"
    msg += f"├── RSI: {enriched.rsi_14:.0f} | RVol: {enriched.relative_volume:.1f}x\n"
    msg += f"└── IV Rank: {enriched.iv_rank:.0f}%\n\n"

    msg += f"{rec_emoji} *{enriched.recommendation}* (Conviction: {enriched.conviction:.0%})\n\n"

    if enriched.thesis:
        msg += f"*Thesis:* {enriched.thesis}\n\n"

    if enriched.risk_factors:
        msg += f"*Risks:* {', '.join(enriched.risk_factors[:3])}\n"

    return msg


if __name__ == "__main__":
    # Test the analyzer
    print("Testing Flow Analyzer\n")

    # Get some signals
    signals = run_flow_scan(
        min_premium=100000,
        min_vol_oi=1.0,
        min_score=8,
        limit=10,
    )

    if signals:
        print(f"\nAnalyzing top 3 signals...\n")
        enriched = analyze_flow_signals(signals[:3], max_analyze=3)

        for e in enriched:
            print(format_flow_analysis_for_telegram(e))
            print("-" * 50)
    else:
        print("No signals to analyze")
