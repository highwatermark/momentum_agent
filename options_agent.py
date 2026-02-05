"""
Options Agent Module - Claude AI for options position management

Three specialized agents:
1. Options Position Reviewer - Assess existing positions, recommend close/hold/roll
2. Options Position Sizer - Calculate optimal contract quantity
3. Options Portfolio Manager - Portfolio-level Greeks management and rebalancing
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict

import anthropic

from config import (
    ANTHROPIC_API_KEY,
    OPTIONS_CONFIG,
    OPTIONS_SAFETY,
    TRADING_CONFIG,
    FLOW_LISTENER_CONFIG,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('options_agent')

# File handler for options agent logs
try:
    from pathlib import Path
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    file_handler = logging.FileHandler(log_dir / "options_agent.log")
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    logger.addHandler(file_handler)
except Exception as e:
    print(f"Warning: Could not set up file logging: {e}")


# ============================================================================
# DATA CLASSES FOR STRUCTURED INPUT/OUTPUT
# ============================================================================

@dataclass
class PositionReviewInput:
    """Input data for position review"""
    contract_symbol: str
    underlying: str
    option_type: str  # 'call' or 'put'
    strike: float
    expiration: str
    quantity: int
    avg_entry_price: float
    current_price: float
    unrealized_pl: float
    unrealized_plpc: float
    # Greeks
    delta: float
    gamma: float
    theta: float  # Daily decay in $
    vega: float
    iv: float
    # Context
    underlying_price: float
    days_to_expiry: int
    # Market context
    spy_change_1d: float = 0
    vix_level: float = 15
    sector: str = "unknown"


@dataclass
class PositionReviewResult:
    """Result from position review agent"""
    contract_symbol: str
    recommendation: str  # 'HOLD', 'CLOSE', 'ROLL', 'TRIM'
    urgency: str  # 'low', 'medium', 'high', 'critical'
    reasoning: str
    risk_factors: List[str]
    # Roll details (if recommendation is ROLL)
    roll_to_expiration: Optional[str] = None
    roll_to_strike: Optional[float] = None
    estimated_roll_cost: Optional[float] = None
    # Confidence
    confidence: float = 0.0
    # Metadata
    agent_used: bool = True
    fallback_reason: Optional[str] = None


@dataclass
class PositionSizingInput:
    """Input data for position sizing"""
    underlying: str
    option_type: str
    strike: float
    expiration: str
    option_price: float
    # Underlying context
    underlying_price: float
    underlying_atr: float
    underlying_iv_rank: float
    # Portfolio state
    account_equity: float
    cash_available: float
    current_options_exposure: float
    current_positions_count: int
    # Portfolio Greeks
    portfolio_delta: float
    portfolio_gamma: float
    portfolio_theta: float
    portfolio_vega: float
    # Sector exposure
    sector: str
    sector_exposure_pct: float
    # Signal context
    signal_score: int = 0
    signal_conviction: float = 0.5


@dataclass
class PositionSizingResult:
    """Result from position sizing agent"""
    recommended_contracts: int
    max_contracts: int
    position_value: float
    position_pct_of_portfolio: float
    reasoning: str
    risk_factors: List[str]
    # Greeks impact
    delta_impact: float
    theta_impact: float
    # Confidence
    confidence: float = 0.0
    # Metadata
    agent_used: bool = True
    fallback_reason: Optional[str] = None


@dataclass
class PortfolioReviewInput:
    """Input data for portfolio-level review"""
    # Account state
    account_equity: float
    cash_available: float
    options_exposure: float
    options_exposure_pct: float
    # Aggregate Greeks
    net_delta: float
    total_gamma: float
    daily_theta: float
    total_vega: float
    # Position details
    positions: List[Dict]
    # Sector breakdown
    sector_allocation: Dict[str, float]
    # Market context
    spy_price: float
    spy_change_1d: float
    spy_change_5d: float
    vix_level: float
    vix_change_1d: float = 0
    # Risk metrics
    max_single_position_pct: float = 0
    positions_expiring_soon: int = 0  # Within 7 days


@dataclass
class PortfolioReviewResult:
    """Result from portfolio manager agent"""
    overall_assessment: str  # 'healthy', 'moderate_risk', 'high_risk', 'critical'
    risk_score: int  # 0-100
    recommendations: List[Dict]  # List of specific actions
    rebalancing_needed: bool
    rebalancing_actions: List[str]
    roll_suggestions: List[Dict]
    risk_factors: List[str]
    summary: str
    # Confidence
    confidence: float = 0.0
    # Metadata
    agent_used: bool = True
    fallback_reason: Optional[str] = None


# ============================================================================
# SYSTEM PROMPTS
# ============================================================================

POSITION_REVIEWER_PROMPT = """You are an expert options position manager. Your role is to review individual options positions and provide actionable recommendations.

## YOUR DECISIONS
For each position, recommend one of:
- **HOLD**: Keep the position, risk is acceptable
- **CLOSE**: Exit the position immediately
- **ROLL**: Close current position and open new one with later expiration
- **TRIM**: Reduce position size (sell some contracts)

## DECISION FRAMEWORK

### CLOSE signals (High Priority):
- DTE <= 3 and position is profitable (lock in gains before theta crush)
- DTE <= 3 and OTM (avoid expiring worthless)
- Loss exceeds 50% of premium paid
- Underlying has moved significantly against position
- IV crush after expected move occurred
- Gamma risk too high (ATM with < 5 DTE)

### ROLL signals:
- DTE <= 7 and want to maintain exposure
- Profitable but theta decay accelerating
- Position working but time value eroding
- Prefer rolling to same strike, 3-4 weeks out

### HOLD signals:
- Thesis intact and DTE > 10
- Position profitable but has more room to run
- Delta exposure aligned with market view
- Theta decay acceptable relative to potential gain

### TRIM signals:
- Position too large relative to portfolio
- Want to lock in partial profits
- Reduce risk while maintaining exposure

## RISK FACTORS TO ASSESS
1. **Theta Risk**: Daily decay vs potential gain
2. **Gamma Risk**: Large delta swings near expiry
3. **Vega Risk**: IV changes impact
4. **Directional Risk**: Delta exposure vs market conditions
5. **Time Risk**: Days to expiry and theta acceleration
6. **Liquidity Risk**: Ability to exit at fair price

## URGENCY LEVELS
- **critical**: Act immediately (expiring today/tomorrow, large loss)
- **high**: Act within hours (DTE <= 3, significant risk)
- **medium**: Act within 1-2 days (DTE <= 7, moderate risk)
- **low**: Monitor but no immediate action needed

Respond with JSON only:
{
    "recommendation": "HOLD|CLOSE|ROLL|TRIM",
    "urgency": "low|medium|high|critical",
    "reasoning": "Detailed explanation of recommendation",
    "risk_factors": ["list", "of", "identified", "risks"],
    "roll_to_expiration": "YYYY-MM-DD if rolling",
    "roll_to_strike": null or strike price if rolling,
    "estimated_roll_cost": null or cost estimate,
    "confidence": 0.0-1.0
}"""

POSITION_SIZER_PROMPT = """You are an expert options position sizing specialist. Your role is to determine the optimal number of contracts for a new options trade.

## POSITION SIZING PRINCIPLES

### Base Sizing Rules:
- Never risk more than 2% of portfolio on a single options trade
- Maximum 10% total portfolio in options
- Consider existing Greeks exposure
- Account for sector concentration

### Adjustment Factors:

**Increase size when:**
- Signal score >= 15 (high conviction)
- IV rank < 30% (cheap premium)
- Portfolio delta is low and adding directional exposure
- Sector underweight
- Strong trend alignment

**Decrease size when:**
- Signal score < 10 (lower conviction)
- IV rank > 50% (expensive premium)
- Would create excessive sector concentration (>50%)
- Portfolio already has high theta decay
- Adding to correlated positions
- Short DTE (< 14 days)
- High gamma exposure near expiry

### Maximum Constraints:
- Single underlying: Max 30% of options allocation
- Single sector: Max 50% of options allocation
- Max contracts per trade: 10
- Min contracts: 1

### Greeks Impact Assessment:
- Calculate delta impact on portfolio
- Assess theta impact (daily $ decay)
- Consider gamma concentration
- Evaluate vega exposure vs IV environment

## OUTPUT REQUIREMENTS
Provide specific contract count with reasoning.
Consider both upside potential and downside risk.

Respond with JSON only:
{
    "recommended_contracts": 1-10,
    "max_contracts": maximum_safe_size,
    "position_value": dollar_value,
    "position_pct_of_portfolio": percentage,
    "reasoning": "Detailed sizing rationale",
    "risk_factors": ["factors", "considered"],
    "delta_impact": expected_portfolio_delta_change,
    "theta_impact": expected_daily_theta_change,
    "confidence": 0.0-1.0
}"""

PORTFOLIO_MANAGER_PROMPT = """You are an expert options portfolio manager. Your role is to assess the overall health of an options portfolio and provide strategic recommendations.

## PORTFOLIO ASSESSMENT FRAMEWORK

### Health Levels:
- **healthy**: Risk metrics within acceptable ranges, balanced exposure
- **moderate_risk**: Some metrics elevated, minor adjustments needed
- **high_risk**: Multiple risk factors present, action required soon
- **critical**: Immediate action required to prevent significant loss

### Key Metrics to Monitor:

1. **Net Delta**: Overall directional exposure
   - Healthy: -50 to +50 per $100K equity
   - Concerning: > |100| per $100K equity

2. **Daily Theta**: Time decay
   - Healthy: < 0.1% of portfolio per day
   - Concerning: > 0.2% of portfolio per day

3. **Gamma Exposure**: Delta sensitivity
   - High gamma near expiry = high risk
   - Monitor positions within 7 DTE

4. **Vega Exposure**: IV sensitivity
   - High vega + high IV rank = risk if IV drops
   - Low vega + low IV rank = risk if IV spikes

5. **Concentration**:
   - Single position: Max 30% of options
   - Single sector: Max 50% of options

6. **Expiration Risk**:
   - Multiple positions expiring same week = event risk
   - Positions within 3 DTE need attention

### Rebalancing Triggers:
- Net delta > |100| (per $100K)
- Single sector > 50%
- Daily theta > 0.2% of portfolio
- Multiple positions DTE < 7
- Correlation too high between positions

### Roll Recommendations:
- Identify positions needing roll (DTE < 7, profitable)
- Suggest target expiration (typically 3-4 weeks out)
- Estimate roll cost/credit

## RISK SCORING (0-100)
- 0-25: Low risk, healthy portfolio
- 26-50: Moderate risk, monitor closely
- 51-75: High risk, action recommended
- 76-100: Critical risk, immediate action required

Score components:
- Theta decay rate: 0-20 points
- Gamma concentration: 0-20 points
- Delta imbalance: 0-20 points
- Concentration risk: 0-20 points
- Expiration risk: 0-20 points

Respond with JSON only:
{
    "overall_assessment": "healthy|moderate_risk|high_risk|critical",
    "risk_score": 0-100,
    "recommendations": [
        {"action": "description", "priority": "high|medium|low", "symbol": "XXX"}
    ],
    "rebalancing_needed": true|false,
    "rebalancing_actions": ["list of specific actions"],
    "roll_suggestions": [
        {"contract": "XXX", "roll_to": "YYYY-MM-DD", "reason": "why"}
    ],
    "risk_factors": ["identified", "risk", "factors"],
    "summary": "Executive summary of portfolio state",
    "confidence": 0.0-1.0
}"""


# ============================================================================
# AGENT CLIENT
# ============================================================================

def get_agent_client() -> Optional[anthropic.Anthropic]:
    """Initialize Anthropic client with error handling"""
    if not ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not configured")
        return None
    try:
        return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    except Exception as e:
        logger.error(f"Failed to initialize Anthropic client: {e}")
        return None


def call_agent(
    system_prompt: str,
    user_prompt: str,
    agent_name: str,
    max_tokens: int = 1024
) -> Tuple[Optional[Dict], Optional[str]]:
    """
    Call Claude agent with error handling and logging.

    Returns:
        Tuple of (parsed_response, error_message)
    """
    client = get_agent_client()
    if not client:
        return None, "Agent client not available"

    logger.info(f"[{agent_name}] Calling agent...")

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )

        response_text = response.content[0].text.strip()
        logger.debug(f"[{agent_name}] Raw response: {response_text[:500]}...")

        # Clean up response if wrapped in markdown
        if response_text.startswith("```"):
            response_text = response_text.split("```")[1]
            if response_text.startswith("json"):
                response_text = response_text[4:]
        response_text = response_text.strip()

        # Parse JSON
        parsed = json.loads(response_text)
        logger.info(f"[{agent_name}] Agent response parsed successfully")
        return parsed, None

    except json.JSONDecodeError as e:
        error_msg = f"Failed to parse agent response as JSON: {e}"
        logger.error(f"[{agent_name}] {error_msg}")
        return None, error_msg
    except anthropic.APIError as e:
        error_msg = f"Anthropic API error: {e}"
        logger.error(f"[{agent_name}] {error_msg}")
        return None, error_msg
    except Exception as e:
        error_msg = f"Unexpected error calling agent: {e}"
        logger.error(f"[{agent_name}] {error_msg}")
        return None, error_msg


# ============================================================================
# OPTIONS POSITION REVIEWER
# ============================================================================

def review_position(
    position: PositionReviewInput,
    use_agent: bool = True
) -> PositionReviewResult:
    """
    Review a single options position and recommend action.

    Args:
        position: Position data with Greeks and context
        use_agent: Whether to use Claude agent (falls back to rules if False or agent fails)

    Returns:
        PositionReviewResult with recommendation and reasoning
    """
    logger.info(f"[PositionReviewer] Reviewing {position.contract_symbol}")
    logger.info(f"  DTE: {position.days_to_expiry}, P/L: {position.unrealized_plpc:.1%}")
    logger.info(f"  Greeks: D={position.delta:.2f}, G={position.gamma:.4f}, T=${position.theta:.2f}")

    # Try agent first if enabled
    if use_agent:
        result = _review_position_with_agent(position)
        if result:
            logger.info(f"[PositionReviewer] Agent recommendation: {result.recommendation} ({result.urgency})")
            return result
        logger.warning("[PositionReviewer] Agent failed, falling back to rules")

    # Fallback to rules-based review
    result = _review_position_rules_based(position)
    logger.info(f"[PositionReviewer] Rules-based recommendation: {result.recommendation} ({result.urgency})")
    return result


def _review_position_with_agent(position: PositionReviewInput) -> Optional[PositionReviewResult]:
    """Use Claude agent to review position"""

    # Build prompt with position details
    user_prompt = f"""Review this options position:

## Position Details
- Contract: {position.contract_symbol}
- Underlying: {position.underlying} @ ${position.underlying_price:.2f}
- Type: {position.option_type.upper()} ${position.strike}
- Expiration: {position.expiration}
- Days to Expiry: {position.days_to_expiry}
- Quantity: {position.quantity} contracts

## P/L Status
- Entry Price: ${position.avg_entry_price:.2f}
- Current Price: ${position.current_price:.2f}
- Unrealized P/L: ${position.unrealized_pl:.2f} ({position.unrealized_plpc:.1%})

## Greeks (per contract)
- Delta: {position.delta:.3f}
- Gamma: {position.gamma:.5f}
- Theta: ${position.theta:.2f}/day
- Vega: {position.vega:.3f}
- IV: {position.iv:.1%}

## Market Context
- SPY 1D Change: {position.spy_change_1d:+.1%}
- VIX Level: {position.vix_level:.1f}
- Sector: {position.sector}

Provide your recommendation with detailed reasoning."""

    response, error = call_agent(
        system_prompt=POSITION_REVIEWER_PROMPT,
        user_prompt=user_prompt,
        agent_name="PositionReviewer"
    )

    if error or not response:
        return None

    return PositionReviewResult(
        contract_symbol=position.contract_symbol,
        recommendation=response.get("recommendation", "HOLD"),
        urgency=response.get("urgency", "low"),
        reasoning=response.get("reasoning", ""),
        risk_factors=response.get("risk_factors", []),
        roll_to_expiration=response.get("roll_to_expiration"),
        roll_to_strike=response.get("roll_to_strike"),
        estimated_roll_cost=response.get("estimated_roll_cost"),
        confidence=response.get("confidence", 0.5),
        agent_used=True,
        fallback_reason=None
    )


def _review_position_rules_based(position: PositionReviewInput) -> PositionReviewResult:
    """Rules-based position review (fallback)"""

    recommendation = "HOLD"
    urgency = "low"
    reasoning_parts = []
    risk_factors = []
    roll_to_exp = None

    dte = position.days_to_expiry
    pnl_pct = position.unrealized_plpc

    # Critical DTE checks
    if dte <= 1:
        urgency = "critical"
        if pnl_pct > 0:
            recommendation = "CLOSE"
            reasoning_parts.append(f"Expiring tomorrow with {pnl_pct:.1%} profit - lock in gains")
        else:
            recommendation = "CLOSE"
            reasoning_parts.append(f"Expiring tomorrow with {pnl_pct:.1%} loss - avoid total loss")
        risk_factors.append("Expiration imminent")

    elif dte <= 3:
        urgency = "high"
        risk_factors.append(f"Only {dte} DTE remaining")

        if pnl_pct >= 0.3:  # 30%+ profit
            recommendation = "CLOSE"
            reasoning_parts.append(f"Strong profit ({pnl_pct:.1%}) with DTE={dte} - lock in gains before theta crush")
        elif pnl_pct <= -0.4:  # 40%+ loss
            recommendation = "CLOSE"
            reasoning_parts.append(f"Significant loss ({pnl_pct:.1%}) with low DTE - cut losses")
        else:
            recommendation = "ROLL"
            roll_to_exp = (datetime.now() + timedelta(days=28)).strftime("%Y-%m-%d")
            reasoning_parts.append(f"DTE={dte} but position not decisive - consider rolling to {roll_to_exp}")

    elif dte <= 7:
        urgency = "medium"
        risk_factors.append(f"DTE={dte} approaching theta acceleration")

        if pnl_pct >= 0.5:  # 50%+ profit
            recommendation = "CLOSE"
            reasoning_parts.append(f"Excellent profit ({pnl_pct:.1%}) - book gains")
        elif pnl_pct >= 0.2:
            recommendation = "ROLL"
            roll_to_exp = (datetime.now() + timedelta(days=28)).strftime("%Y-%m-%d")
            reasoning_parts.append(f"Good profit ({pnl_pct:.1%}) but theta accelerating - consider rolling")

    # Loss management
    if pnl_pct <= -0.5:  # 50% loss
        if urgency not in ["critical", "high"]:
            urgency = "high"
        recommendation = "CLOSE"
        reasoning_parts.append(f"Loss of {pnl_pct:.1%} exceeds 50% stop threshold")
        risk_factors.append("Position at max loss")

    # Theta decay check
    daily_decay_pct = abs(position.theta) / (position.current_price * 100) if position.current_price > 0 else 0
    if daily_decay_pct > 0.05:  # >5% daily decay
        risk_factors.append(f"High theta decay: {daily_decay_pct:.1%}/day")
        if recommendation == "HOLD":
            reasoning_parts.append("Monitor theta decay closely")

    # Gamma risk check
    if dte <= 5 and abs(position.gamma) > 0.05:
        risk_factors.append("High gamma risk near expiry")

    # Default reasoning if nothing triggered
    if not reasoning_parts:
        reasoning_parts.append(f"Position within normal parameters: DTE={dte}, P/L={pnl_pct:.1%}")
        reasoning_parts.append("Continue to hold and monitor")

    return PositionReviewResult(
        contract_symbol=position.contract_symbol,
        recommendation=recommendation,
        urgency=urgency,
        reasoning=" | ".join(reasoning_parts),
        risk_factors=risk_factors,
        roll_to_expiration=roll_to_exp,
        roll_to_strike=position.strike,  # Same strike for roll
        estimated_roll_cost=None,
        confidence=0.7,  # Lower confidence for rules-based
        agent_used=False,
        fallback_reason="Agent not used or unavailable"
    )


# ============================================================================
# OPTIONS POSITION SIZER
# ============================================================================

def calculate_position_size(
    sizing_input: PositionSizingInput,
    use_agent: bool = True
) -> PositionSizingResult:
    """
    Calculate optimal position size for a new options trade.

    Args:
        sizing_input: Trade details and portfolio context
        use_agent: Whether to use Claude agent (falls back to rules if False or agent fails)

    Returns:
        PositionSizingResult with recommended contracts and reasoning
    """
    logger.info(f"[PositionSizer] Sizing {sizing_input.underlying} {sizing_input.option_type} ${sizing_input.strike}")
    logger.info(f"  Option price: ${sizing_input.option_price:.2f}, Signal score: {sizing_input.signal_score}")
    logger.info(f"  Portfolio: ${sizing_input.account_equity:,.0f}, Options exposure: {sizing_input.current_options_exposure / sizing_input.account_equity:.1%}")

    # Try agent first if enabled
    if use_agent:
        result = _calculate_size_with_agent(sizing_input)
        if result:
            logger.info(f"[PositionSizer] Agent recommendation: {result.recommended_contracts} contracts (${result.position_value:,.0f})")
            return result
        logger.warning("[PositionSizer] Agent failed, falling back to rules")

    # Fallback to rules-based sizing
    result = _calculate_size_rules_based(sizing_input)
    logger.info(f"[PositionSizer] Rules-based: {result.recommended_contracts} contracts (${result.position_value:,.0f})")
    return result


def _calculate_size_with_agent(sizing_input: PositionSizingInput) -> Optional[PositionSizingResult]:
    """Use Claude agent to calculate position size"""

    user_prompt = f"""Calculate optimal position size for this options trade:

## Trade Details
- Underlying: {sizing_input.underlying} @ ${sizing_input.underlying_price:.2f}
- Option: {sizing_input.option_type.upper()} ${sizing_input.strike}
- Expiration: {sizing_input.expiration}
- Option Price: ${sizing_input.option_price:.2f}
- ATR (14): ${sizing_input.underlying_atr:.2f}
- IV Rank: {sizing_input.underlying_iv_rank:.0f}%

## Signal Quality
- Signal Score: {sizing_input.signal_score}/20
- Conviction: {sizing_input.signal_conviction:.0%}

## Portfolio State
- Account Equity: ${sizing_input.account_equity:,.0f}
- Cash Available: ${sizing_input.cash_available:,.0f}
- Current Options Exposure: ${sizing_input.current_options_exposure:,.0f} ({sizing_input.current_options_exposure / sizing_input.account_equity * 100:.1f}%)
- Open Options Positions: {sizing_input.current_positions_count}

## Current Portfolio Greeks
- Net Delta: {sizing_input.portfolio_delta:.1f}
- Total Gamma: {sizing_input.portfolio_gamma:.4f}
- Daily Theta: ${sizing_input.portfolio_theta:.2f}
- Total Vega: {sizing_input.portfolio_vega:.2f}

## Sector Exposure
- Sector: {sizing_input.sector}
- Current Sector Exposure: {sizing_input.sector_exposure_pct:.1f}%
- Max Sector Exposure: {OPTIONS_SAFETY.get('max_single_sector_pct', 50)}%

## Risk Limits (from config)
- Max options exposure: {OPTIONS_CONFIG.get('max_portfolio_risk_options', 0.10) * 100:.0f}% of portfolio
- Max position size: {OPTIONS_CONFIG.get('position_size_pct', 0.02) * 100:.0f}% of portfolio
- Max contracts per trade: {OPTIONS_CONFIG.get('max_contracts_per_trade', 10)}

Calculate the optimal number of contracts considering risk limits and portfolio Greeks."""

    response, error = call_agent(
        system_prompt=POSITION_SIZER_PROMPT,
        user_prompt=user_prompt,
        agent_name="PositionSizer"
    )

    if error or not response:
        return None

    recommended = response.get("recommended_contracts", 1)
    option_price = sizing_input.option_price

    return PositionSizingResult(
        recommended_contracts=recommended,
        max_contracts=response.get("max_contracts", recommended),
        position_value=recommended * option_price * 100,
        position_pct_of_portfolio=response.get("position_pct_of_portfolio",
            (recommended * option_price * 100) / sizing_input.account_equity * 100),
        reasoning=response.get("reasoning", ""),
        risk_factors=response.get("risk_factors", []),
        delta_impact=response.get("delta_impact", 0),
        theta_impact=response.get("theta_impact", 0),
        confidence=response.get("confidence", 0.5),
        agent_used=True,
        fallback_reason=None
    )


def _calculate_size_rules_based(sizing_input: PositionSizingInput) -> PositionSizingResult:
    """Rules-based position sizing (fallback)"""

    equity = sizing_input.account_equity
    option_price = sizing_input.option_price
    contract_value = option_price * 100  # Options are 100 shares

    risk_factors = []
    reasoning_parts = []

    # Base position size from config
    max_position_pct = OPTIONS_CONFIG.get("position_size_pct", 0.02)
    max_position_value = equity * max_position_pct
    base_contracts = int(max_position_value / contract_value) if contract_value > 0 else 1

    reasoning_parts.append(f"Base: {max_position_pct:.0%} of ${equity:,.0f} = ${max_position_value:,.0f}")

    # Adjustment for signal quality
    signal_score = sizing_input.signal_score
    if signal_score >= 15:
        multiplier = 1.5
        reasoning_parts.append(f"High conviction signal ({signal_score}/20): +50%")
    elif signal_score >= 12:
        multiplier = 1.25
        reasoning_parts.append(f"Good signal ({signal_score}/20): +25%")
    elif signal_score < 8:
        multiplier = 0.5
        reasoning_parts.append(f"Weak signal ({signal_score}/20): -50%")
        risk_factors.append("Low signal score")
    else:
        multiplier = 1.0
        reasoning_parts.append(f"Average signal ({signal_score}/20): no adjustment")

    adjusted_contracts = int(base_contracts * multiplier)

    # Cap by max contracts config
    max_contracts = OPTIONS_CONFIG.get("max_contracts_per_trade", 10)
    if adjusted_contracts > max_contracts:
        adjusted_contracts = max_contracts
        reasoning_parts.append(f"Capped at max {max_contracts} contracts")

    # Check sector concentration
    max_sector_pct = OPTIONS_SAFETY.get("max_single_sector_pct", 50.0)
    if sizing_input.sector_exposure_pct > max_sector_pct * 0.7:  # >70% of limit
        adjusted_contracts = max(1, adjusted_contracts // 2)
        risk_factors.append(f"Sector concentration: {sizing_input.sector_exposure_pct:.0f}% in {sizing_input.sector}")
        reasoning_parts.append("Reduced for sector concentration")

    # Check total options exposure
    max_options_pct = OPTIONS_CONFIG.get("max_portfolio_risk_options", 0.10)
    current_exposure_pct = sizing_input.current_options_exposure / equity
    new_exposure = sizing_input.current_options_exposure + (adjusted_contracts * contract_value)
    new_exposure_pct = new_exposure / equity

    if new_exposure_pct > max_options_pct:
        # Reduce to fit within limit
        available = (equity * max_options_pct) - sizing_input.current_options_exposure
        adjusted_contracts = max(1, int(available / contract_value))
        risk_factors.append(f"Options exposure limit: {new_exposure_pct:.1%} > {max_options_pct:.0%}")
        reasoning_parts.append(f"Reduced to stay within {max_options_pct:.0%} options limit")

    # Ensure at least 1 contract
    adjusted_contracts = max(1, adjusted_contracts)

    # Calculate final values
    position_value = adjusted_contracts * contract_value
    position_pct = position_value / equity * 100

    # Estimate Greeks impact (rough approximation)
    # Assume delta ~0.5 for ATM options
    delta_impact = adjusted_contracts * 100 * 0.5 * (1 if sizing_input.option_type == 'call' else -1)
    theta_impact = adjusted_contracts * contract_value * 0.02  # ~2% daily decay estimate

    return PositionSizingResult(
        recommended_contracts=adjusted_contracts,
        max_contracts=max_contracts,
        position_value=position_value,
        position_pct_of_portfolio=position_pct,
        reasoning=" | ".join(reasoning_parts),
        risk_factors=risk_factors,
        delta_impact=delta_impact,
        theta_impact=theta_impact,
        confidence=0.7,
        agent_used=False,
        fallback_reason="Agent not used or unavailable"
    )


# ============================================================================
# OPTIONS PORTFOLIO MANAGER
# ============================================================================

def review_portfolio(
    portfolio_input: PortfolioReviewInput,
    use_agent: bool = True
) -> PortfolioReviewResult:
    """
    Review the overall options portfolio and provide strategic recommendations.

    Args:
        portfolio_input: Portfolio state with Greeks and positions
        use_agent: Whether to use Claude agent (falls back to rules if False or agent fails)

    Returns:
        PortfolioReviewResult with assessment and recommendations
    """
    logger.info("[PortfolioManager] Reviewing options portfolio...")
    logger.info(f"  Equity: ${portfolio_input.account_equity:,.0f}, Options: ${portfolio_input.options_exposure:,.0f} ({portfolio_input.options_exposure_pct:.1f}%)")
    logger.info(f"  Greeks: Delta={portfolio_input.net_delta:.1f}, Theta=${portfolio_input.daily_theta:.2f}/day")
    logger.info(f"  Positions: {len(portfolio_input.positions)}, Expiring <7d: {portfolio_input.positions_expiring_soon}")

    # Try agent first if enabled
    if use_agent:
        result = _review_portfolio_with_agent(portfolio_input)
        if result:
            logger.info(f"[PortfolioManager] Agent assessment: {result.overall_assessment} (risk score: {result.risk_score})")
            return result
        logger.warning("[PortfolioManager] Agent failed, falling back to rules")

    # Fallback to rules-based review
    result = _review_portfolio_rules_based(portfolio_input)
    logger.info(f"[PortfolioManager] Rules-based assessment: {result.overall_assessment} (risk score: {result.risk_score})")
    return result


def _review_portfolio_with_agent(portfolio_input: PortfolioReviewInput) -> Optional[PortfolioReviewResult]:
    """Use Claude agent to review portfolio"""

    # Format positions for prompt
    positions_text = ""
    for i, pos in enumerate(portfolio_input.positions[:10]):  # Limit to 10 for prompt size
        positions_text += f"""
Position {i+1}: {pos.get('symbol', 'N/A')}
  - Contract: {pos.get('contract_symbol', 'N/A')}
  - Type: {pos.get('option_type', 'N/A')} ${pos.get('strike', 0)}
  - DTE: {pos.get('days_to_expiry', 'N/A')}
  - P/L: {pos.get('unrealized_plpc', 0):.1%}
  - Delta: {pos.get('delta', 0):.2f}, Theta: ${pos.get('theta', 0):.2f}
"""

    # Format sector allocation
    sector_text = "\n".join([f"  - {k}: {v:.1f}%" for k, v in portfolio_input.sector_allocation.items()])

    user_prompt = f"""Review this options portfolio:

## Account Overview
- Total Equity: ${portfolio_input.account_equity:,.0f}
- Cash Available: ${portfolio_input.cash_available:,.0f}
- Options Exposure: ${portfolio_input.options_exposure:,.0f} ({portfolio_input.options_exposure_pct:.1f}%)
- Max Single Position: {portfolio_input.max_single_position_pct:.1f}% of options

## Aggregate Greeks
- Net Delta: {portfolio_input.net_delta:.1f} (share equivalents)
- Total Gamma: {portfolio_input.total_gamma:.4f}
- Daily Theta: ${portfolio_input.daily_theta:.2f}
- Total Vega: {portfolio_input.total_vega:.2f}

## Market Context
- SPY: ${portfolio_input.spy_price:.2f}
- SPY 1D: {portfolio_input.spy_change_1d:+.1%}
- SPY 5D: {portfolio_input.spy_change_5d:+.1%}
- VIX: {portfolio_input.vix_level:.1f}

## Sector Allocation
{sector_text}

## Positions ({len(portfolio_input.positions)} total, {portfolio_input.positions_expiring_soon} expiring <7 days)
{positions_text}

Provide comprehensive portfolio assessment with specific recommendations."""

    response, error = call_agent(
        system_prompt=PORTFOLIO_MANAGER_PROMPT,
        user_prompt=user_prompt,
        agent_name="PortfolioManager",
        max_tokens=2048
    )

    if error or not response:
        return None

    return PortfolioReviewResult(
        overall_assessment=response.get("overall_assessment", "moderate_risk"),
        risk_score=response.get("risk_score", 50),
        recommendations=response.get("recommendations", []),
        rebalancing_needed=response.get("rebalancing_needed", False),
        rebalancing_actions=response.get("rebalancing_actions", []),
        roll_suggestions=response.get("roll_suggestions", []),
        risk_factors=response.get("risk_factors", []),
        summary=response.get("summary", ""),
        confidence=response.get("confidence", 0.5),
        agent_used=True,
        fallback_reason=None
    )


def _review_portfolio_rules_based(portfolio_input: PortfolioReviewInput) -> PortfolioReviewResult:
    """Rules-based portfolio review (fallback)"""

    equity = portfolio_input.account_equity
    risk_score = 0
    risk_factors = []
    recommendations = []
    rebalancing_actions = []
    roll_suggestions = []

    # 1. Theta decay check (0-20 points)
    daily_theta_pct = abs(portfolio_input.daily_theta) / equity if equity > 0 else 0
    if daily_theta_pct > 0.003:  # >0.3% daily decay
        risk_score += 20
        risk_factors.append(f"Very high theta decay: ${portfolio_input.daily_theta:.0f}/day ({daily_theta_pct:.2%})")
    elif daily_theta_pct > 0.002:
        risk_score += 12
        risk_factors.append(f"High theta decay: ${portfolio_input.daily_theta:.0f}/day")
    elif daily_theta_pct > 0.001:
        risk_score += 5

    # 2. Delta imbalance check (0-20 points)
    delta_per_100k = abs(portfolio_input.net_delta) / (equity / 100000) if equity > 0 else 0
    if delta_per_100k > 150:
        risk_score += 20
        risk_factors.append(f"Very high delta exposure: {portfolio_input.net_delta:.0f}")
        rebalancing_actions.append(f"Reduce delta exposure (currently {portfolio_input.net_delta:.0f})")
    elif delta_per_100k > 100:
        risk_score += 12
        risk_factors.append(f"High delta exposure: {portfolio_input.net_delta:.0f}")
    elif delta_per_100k > 50:
        risk_score += 5

    # 3. Expiration risk (0-20 points)
    if portfolio_input.positions_expiring_soon >= 3:
        risk_score += 20
        risk_factors.append(f"{portfolio_input.positions_expiring_soon} positions expiring within 7 days")
        recommendations.append({
            "action": "Review all positions expiring soon",
            "priority": "high",
            "symbol": "MULTIPLE"
        })
    elif portfolio_input.positions_expiring_soon >= 2:
        risk_score += 12
    elif portfolio_input.positions_expiring_soon >= 1:
        risk_score += 5

    # 4. Concentration risk (0-20 points)
    max_sector_exposure = max(portfolio_input.sector_allocation.values()) if portfolio_input.sector_allocation else 0
    if max_sector_exposure > 60:
        risk_score += 20
        top_sector = max(portfolio_input.sector_allocation, key=portfolio_input.sector_allocation.get)
        risk_factors.append(f"Over-concentration: {max_sector_exposure:.0f}% in {top_sector}")
        rebalancing_actions.append(f"Reduce {top_sector} exposure")
    elif max_sector_exposure > 50:
        risk_score += 12
    elif max_sector_exposure > 40:
        risk_score += 5

    # 5. Position count / exposure check (0-20 points)
    max_positions = OPTIONS_CONFIG.get("max_options_positions", 4)
    if len(portfolio_input.positions) > max_positions * 1.5:
        risk_score += 15
        risk_factors.append(f"Too many positions: {len(portfolio_input.positions)}")

    if portfolio_input.options_exposure_pct > 12:
        risk_score += 15
        risk_factors.append(f"High options exposure: {portfolio_input.options_exposure_pct:.1f}%")
    elif portfolio_input.options_exposure_pct > 10:
        risk_score += 8

    # Generate roll suggestions for positions <7 DTE
    for pos in portfolio_input.positions:
        dte = pos.get('days_to_expiry', 999)
        pnl = pos.get('unrealized_plpc', 0)
        if dte <= 7 and pnl > -0.3:  # Not a big loser
            roll_date = (datetime.now() + timedelta(days=28)).strftime("%Y-%m-%d")
            roll_suggestions.append({
                "contract": pos.get('contract_symbol', 'N/A'),
                "roll_to": roll_date,
                "reason": f"DTE={dte}, P/L={pnl:.1%}"
            })

    # Determine overall assessment
    if risk_score >= 75:
        assessment = "critical"
    elif risk_score >= 50:
        assessment = "high_risk"
    elif risk_score >= 25:
        assessment = "moderate_risk"
    else:
        assessment = "healthy"

    # Build summary
    summary_parts = [f"Portfolio risk score: {risk_score}/100"]
    if risk_factors:
        summary_parts.append(f"Key risks: {', '.join(risk_factors[:2])}")
    if not risk_factors:
        summary_parts.append("No significant risks identified")

    return PortfolioReviewResult(
        overall_assessment=assessment,
        risk_score=risk_score,
        recommendations=recommendations,
        rebalancing_needed=risk_score >= 50 or len(rebalancing_actions) > 0,
        rebalancing_actions=rebalancing_actions,
        roll_suggestions=roll_suggestions,
        risk_factors=risk_factors,
        summary=" | ".join(summary_parts),
        confidence=0.7,
        agent_used=False,
        fallback_reason="Agent not used or unavailable"
    )


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

def review_all_positions(
    positions: List[Dict],
    market_context: Dict = None,
    use_agent: bool = True
) -> List[PositionReviewResult]:
    """
    Review all open options positions.

    Args:
        positions: List of position dictionaries (from options_executor.get_options_positions)
        market_context: Optional dict with spy_change_1d, vix_level
        use_agent: Whether to use Claude agent

    Returns:
        List of PositionReviewResult for each position
    """
    market_context = market_context or {}
    results = []

    for pos in positions:
        try:
            # Build input from position dict
            review_input = PositionReviewInput(
                contract_symbol=pos.get('contract_symbol', ''),
                underlying=pos.get('symbol', ''),
                option_type=pos.get('option_type', 'call'),
                strike=pos.get('strike', 0),
                expiration=pos.get('expiration', ''),
                quantity=pos.get('quantity', 0),
                avg_entry_price=pos.get('avg_entry_price', 0),
                current_price=pos.get('current_price', 0),
                unrealized_pl=pos.get('unrealized_pl', 0),
                unrealized_plpc=pos.get('unrealized_plpc', 0),
                delta=pos.get('delta', 0),
                gamma=pos.get('gamma', 0),
                theta=pos.get('theta', 0),
                vega=pos.get('vega', 0),
                iv=pos.get('iv', 0.3),
                underlying_price=pos.get('underlying_price', pos.get('strike', 100)),
                days_to_expiry=pos.get('days_to_expiry', 30),
                spy_change_1d=market_context.get('spy_change_1d', 0),
                vix_level=market_context.get('vix_level', 15),
                sector=pos.get('sector', 'unknown')
            )

            result = review_position(review_input, use_agent=use_agent)
            results.append(result)

        except Exception as e:
            logger.error(f"Error reviewing position {pos.get('contract_symbol')}: {e}")
            # Return a default HOLD result
            results.append(PositionReviewResult(
                contract_symbol=pos.get('contract_symbol', 'ERROR'),
                recommendation="HOLD",
                urgency="low",
                reasoning=f"Error during review: {e}",
                risk_factors=["Review error"],
                confidence=0,
                agent_used=False,
                fallback_reason=str(e)
            ))

    return results


def log_agent_decision(
    agent_name: str,
    input_data: Dict,
    result: Dict,
    execution_time_ms: float = 0
):
    """Log agent decision to database for analysis"""
    try:
        from db import get_connection
        conn = get_connection()
        cursor = conn.cursor()

        # Create table if not exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS options_agent_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                agent_name TEXT NOT NULL,
                input_data TEXT,
                output_data TEXT,
                agent_used INTEGER,
                fallback_reason TEXT,
                execution_time_ms REAL,
                confidence REAL
            )
        """)

        cursor.execute("""
            INSERT INTO options_agent_logs
            (agent_name, input_data, output_data, agent_used, fallback_reason, execution_time_ms, confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            agent_name,
            json.dumps(input_data),
            json.dumps(result),
            1 if result.get('agent_used', False) else 0,
            result.get('fallback_reason'),
            execution_time_ms,
            result.get('confidence', 0)
        ))

        conn.commit()
        conn.close()

    except Exception as e:
        logger.error(f"Failed to log agent decision: {e}")


# ============================================================================
# CLI FOR TESTING
# ============================================================================

if __name__ == "__main__":
    import sys

    print("=" * 60)
    print("OPTIONS AGENT MODULE - TEST")
    print("=" * 60)

    # Test 1: Position Review
    print("\n[TEST 1] Position Review")
    print("-" * 40)

    test_position = PositionReviewInput(
        contract_symbol="AAPL240315C00175000",
        underlying="AAPL",
        option_type="call",
        strike=175.0,
        expiration="2024-03-15",
        quantity=2,
        avg_entry_price=5.50,
        current_price=7.20,
        unrealized_pl=340.0,
        unrealized_plpc=0.31,
        delta=0.65,
        gamma=0.02,
        theta=-0.15,
        vega=0.25,
        iv=0.28,
        underlying_price=180.50,
        days_to_expiry=5,
        spy_change_1d=0.005,
        vix_level=14.5,
        sector="tech"
    )

    result = review_position(test_position, use_agent=True)
    print(f"Recommendation: {result.recommendation}")
    print(f"Urgency: {result.urgency}")
    print(f"Reasoning: {result.reasoning}")
    print(f"Agent Used: {result.agent_used}")
    print(f"Confidence: {result.confidence:.0%}")

    # Test 2: Position Sizing
    print("\n[TEST 2] Position Sizing")
    print("-" * 40)

    test_sizing = PositionSizingInput(
        underlying="NVDA",
        option_type="call",
        strike=500.0,
        expiration="2024-04-19",
        option_price=15.50,
        underlying_price=495.0,
        underlying_atr=12.5,
        underlying_iv_rank=35,
        account_equity=100000,
        cash_available=40000,
        current_options_exposure=5000,
        current_positions_count=2,
        portfolio_delta=150,
        portfolio_gamma=0.05,
        portfolio_theta=-25,
        portfolio_vega=50,
        sector="tech",
        sector_exposure_pct=30,
        signal_score=14,
        signal_conviction=0.75
    )

    result = calculate_position_size(test_sizing, use_agent=True)
    print(f"Recommended Contracts: {result.recommended_contracts}")
    print(f"Position Value: ${result.position_value:,.0f}")
    print(f"Portfolio %: {result.position_pct_of_portfolio:.1f}%")
    print(f"Reasoning: {result.reasoning}")
    print(f"Agent Used: {result.agent_used}")

    # Test 3: Portfolio Review
    print("\n[TEST 3] Portfolio Review")
    print("-" * 40)

    test_portfolio = PortfolioReviewInput(
        account_equity=100000,
        cash_available=40000,
        options_exposure=8000,
        options_exposure_pct=8.0,
        net_delta=200,
        total_gamma=0.08,
        daily_theta=-45,
        total_vega=120,
        positions=[
            {
                "symbol": "AAPL",
                "contract_symbol": "AAPL240315C00175000",
                "option_type": "call",
                "strike": 175,
                "days_to_expiry": 5,
                "unrealized_plpc": 0.31,
                "delta": 0.65,
                "theta": -0.15
            },
            {
                "symbol": "NVDA",
                "contract_symbol": "NVDA240419C00500000",
                "option_type": "call",
                "strike": 500,
                "days_to_expiry": 35,
                "unrealized_plpc": -0.15,
                "delta": 0.45,
                "theta": -0.20
            }
        ],
        sector_allocation={"tech": 65, "finance": 20, "consumer": 15},
        spy_price=510.0,
        spy_change_1d=0.008,
        spy_change_5d=0.025,
        vix_level=14.5,
        max_single_position_pct=35,
        positions_expiring_soon=1
    )

    result = review_portfolio(test_portfolio, use_agent=True)
    print(f"Assessment: {result.overall_assessment}")
    print(f"Risk Score: {result.risk_score}/100")
    print(f"Rebalancing Needed: {result.rebalancing_needed}")
    print(f"Risk Factors: {result.risk_factors}")
    print(f"Summary: {result.summary}")
    print(f"Agent Used: {result.agent_used}")

    print("\n" + "=" * 60)
    print("TEST COMPLETE")
    print("=" * 60)


# ============================================================================
# FLOW VALIDATOR - Claude-based flow signal validation
# ============================================================================

@dataclass
class FlowSignalInput:
    """Input data for a single flow signal"""
    signal_id: str
    symbol: str
    strike: float
    expiration: str
    option_type: str  # 'call' or 'put'
    premium: float
    size: int
    vol_oi_ratio: float
    is_sweep: bool
    is_ask_side: bool
    is_floor: bool
    is_opening: bool
    is_otm: bool
    underlying_price: float
    sentiment: str  # 'bullish' or 'bearish'
    # Symbol context
    days_to_earnings: int = 999
    iv_rank: float = 50.0
    sector: str = "unknown"


@dataclass
class FlowValidationInput:
    """Input data for flow validation (batched signals + context)"""
    signals: List[FlowSignalInput]
    # Market context
    spy_price: float
    spy_change_pct: float
    spy_trend: str  # 'uptrend', 'downtrend', 'sideways'
    vix_level: float
    sector_performance: Dict[str, float]
    current_time: str
    # Portfolio context
    equity: float
    options_positions: List[Dict]
    net_delta: float
    daily_theta: float
    options_exposure_pct: float
    risk_score: int
    risk_assessment: str
    available_capital: float
    position_count: int
    max_positions: int


@dataclass
class FlowValidationResult:
    """Result for a single signal validation"""
    signal_id: str
    symbol: str
    recommendation: str  # 'EXECUTE', 'ALERT', 'SKIP'
    conviction: int  # 0-100
    thesis: str
    risk_factors: List[str]
    suggested_contracts: int
    profit_target: str
    stop_loss: str


def validate_flow_signals(
    validation_input: FlowValidationInput,
    use_agent: bool = True
) -> List[FlowValidationResult]:
    """
    Validate flow signals using Claude AI.

    Returns list of FlowValidationResult sorted by execution priority.
    """
    logger.info(f"[FlowValidator] Validating {len(validation_input.signals)} signals")

    if not validation_input.signals:
        return []

    if use_agent and ANTHROPIC_API_KEY:
        result = _validate_with_agent(validation_input)
        if result:
            return result
        logger.warning("[FlowValidator] Agent failed, no fallback for flow validation")

    # No rules-based fallback - flow validation requires Claude
    logger.warning("[FlowValidator] Skipping validation - Claude unavailable")
    return []


def _validate_with_agent(validation_input: FlowValidationInput) -> Optional[List[FlowValidationResult]]:
    """Use Claude to validate flow signals"""

    # Format signals for prompt
    signals_text = ""
    for i, sig in enumerate(validation_input.signals, 1):
        sweep_tag = " [SWEEP]" if sig.is_sweep else ""
        floor_tag = " [FLOOR]" if sig.is_floor else ""
        ask_tag = " [ASK-SIDE]" if sig.is_ask_side else ""
        opening_tag = " [OPENING]" if sig.is_opening else ""
        otm_tag = " [OTM]" if sig.is_otm else ""

        signals_text += f"""
Signal {i} (ID: {sig.signal_id}):
- Symbol: {sig.symbol}
- Flow: {sig.option_type.upper()} ${sig.strike} exp {sig.expiration}
- Premium: ${sig.premium:,.0f}
- Characteristics:{sweep_tag}{floor_tag}{ask_tag}{opening_tag}{otm_tag}
- Vol/OI: {sig.vol_oi_ratio:.1f}x
- Underlying: ${sig.underlying_price:.2f}
- Sentiment: {sig.sentiment}
- Earnings: {sig.days_to_earnings} days away
- IV Rank: {sig.iv_rank:.0f}
- Sector: {sig.sector}
"""

    # Format positions
    positions_text = ""
    if validation_input.options_positions:
        for pos in validation_input.options_positions[:5]:
            pnl = pos.get('unrealized_plpc', 0) * 100
            emoji = "+" if pnl >= 0 else ""
            positions_text += f"  {pos.get('symbol', 'N/A')} {pos.get('option_type', '').upper()} ${pos.get('strike', 0)} | {emoji}{pnl:.1f}% | Delta: {pos.get('delta', 0):.2f}\n"
    else:
        positions_text = "  (no current positions)"

    # Build prompt
    prompt = f"""CURRENT MARKET CONTEXT:
- SPY: ${validation_input.spy_price:.2f} ({validation_input.spy_change_pct:+.1%}), Trend: {validation_input.spy_trend}
- VIX: {validation_input.vix_level:.1f}
- Time: {validation_input.current_time}

PORTFOLIO CONTEXT:
- Equity: ${validation_input.equity:,.0f}
- Current Options Positions: {validation_input.position_count}/{validation_input.max_positions}
{positions_text}
- Net Delta: {validation_input.net_delta:.0f}
- Daily Theta: ${validation_input.daily_theta:.0f}
- Options Exposure: {validation_input.options_exposure_pct:.1f}%
- Risk Score: {validation_input.risk_score}/100 ({validation_input.risk_assessment})
- Available for new position: ~${validation_input.available_capital:,.0f}

SIGNALS TO ANALYZE:
{signals_text}

For each signal, provide a JSON object with:
- signal_id: string (the ID from the signal)
- symbol: string
- recommendation: "EXECUTE" | "ALERT" | "SKIP"
- conviction: 0-100 (75+ for EXECUTE, 50-74 for ALERT, <50 for SKIP)
- thesis: Profit-focused reasoning (why this trade or why not)
- risk_factors: list of concerns
- suggested_contracts: 1-5 (0 if SKIP)
- profit_target: target like "50%" or specific price
- stop_loss: stop like "50%" or specific condition

Return a JSON array ranked by execution priority. Focus on PROFIT POTENTIAL."""

    system_prompt = """You are an autonomous options flow trading agent. Your PRIMARY OBJECTIVE is to
GENERATE PROFITS by identifying and executing high-conviction options trades
based on unusual institutional flow.

PROFIT MANDATE:
- You are measured by P/L performance
- Capital preservation is important, but excessive caution destroys returns
- The best traders have ~40-50% win rate with 2:1+ reward/risk ratio
- Missing a profitable trade is as costly as taking a losing trade
- Act decisively on high-conviction signals

DECISION FRAMEWORK:
- EXECUTE: High conviction (75%+), clear institutional signal, favorable risk/reward
- ALERT: Interesting signal worth human review (50-74% conviction)
- SKIP: Low conviction, unclear thesis, or unfavorable conditions (<50%)

PORTFOLIO-AWARE DECISIONS:
- Consider current delta exposure when adding directional trades
- Avoid concentration in single sector or underlying
- Factor in existing theta decay when adding positions
- Respect position limits but don't be overly conservative

Return ONLY valid JSON array, no other text."""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}]
        )

        response_text = response.content[0].text.strip()

        # Parse JSON response
        # Handle potential markdown code blocks
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            response_text = "\n".join(lines[1:-1])

        results_data = json.loads(response_text)

        # Convert to FlowValidationResult objects
        results = []
        for item in results_data:
            result = FlowValidationResult(
                signal_id=item.get("signal_id", ""),
                symbol=item.get("symbol", ""),
                recommendation=item.get("recommendation", "SKIP"),
                conviction=item.get("conviction", 0),
                thesis=item.get("thesis", ""),
                risk_factors=item.get("risk_factors", []),
                suggested_contracts=item.get("suggested_contracts", 0),
                profit_target=item.get("profit_target", "50%"),
                stop_loss=item.get("stop_loss", "50%"),
            )
            results.append(result)

        logger.info(f"[FlowValidator] Validated {len(results)} signals")
        for r in results:
            logger.info(f"  {r.symbol}: {r.recommendation} ({r.conviction}%)")

        return results

    except json.JSONDecodeError as e:
        logger.error(f"[FlowValidator] JSON parse error: {e}")
        logger.error(f"[FlowValidator] Raw response: {response_text[:500]}")
        return None
    except Exception as e:
        logger.exception(f"[FlowValidator] Error: {e}")
        return None


def format_flow_validation_result(result: FlowValidationResult) -> str:
    """Format a validation result for display/logging"""
    emoji = {"EXECUTE": "🚀", "ALERT": "👀", "SKIP": "⏭️"}.get(result.recommendation, "❓")

    return f"""{emoji} {result.symbol} - {result.recommendation} ({result.conviction}%)
Thesis: {result.thesis}
Risk: {', '.join(result.risk_factors) if result.risk_factors else 'None'}
Size: {result.suggested_contracts} contracts
Target: {result.profit_target} | Stop: {result.stop_loss}"""
