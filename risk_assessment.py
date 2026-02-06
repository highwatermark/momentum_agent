"""
Risk Assessment Module - Dynamic risk-based decision framework

Replaces hard-coded counters with portfolio risk analysis.
Claude uses this data to make entry/exit decisions.
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

import pytz

from config import (
    RISK_FRAMEWORK,
    RISK_SCORE_THRESHOLDS,
    OPTIONS_CONFIG,
)

logger = logging.getLogger(__name__)
ET = pytz.timezone('America/New_York')


@dataclass
class PortfolioRisk:
    """Current portfolio risk state"""
    # Greeks exposure
    net_delta: float = 0.0
    total_gamma: float = 0.0
    daily_theta: float = 0.0
    total_vega: float = 0.0

    # Concentration
    sector_exposure: Dict[str, float] = field(default_factory=dict)
    underlying_exposure: Dict[str, float] = field(default_factory=dict)

    # Capacity
    equity: float = 0.0
    options_value: float = 0.0
    buying_power: float = 0.0

    # Computed metrics
    risk_score: int = 0
    risk_capacity_pct: float = 1.0
    can_add_positions: bool = True
    risk_level: str = "healthy"


@dataclass
class EntryRiskCheck:
    """Result of entry risk assessment"""
    allowed: bool
    reasons: List[str] = field(default_factory=list)
    risk_score_impact: int = 0
    conviction_required: int = 80
    warnings: List[str] = field(default_factory=list)


@dataclass
class ExitRiskCheck:
    """Result of exit risk assessment"""
    should_exit: bool
    urgency: str = "low"  # low, medium, high, critical
    reasons: List[str] = field(default_factory=list)
    thesis_valid: bool = True
    conviction_current: int = 0


@dataclass
class ThesisState:
    """Tracks the thesis for a position"""
    original_trend: str           # bullish/bearish at entry
    original_conviction: int      # conviction at entry
    entry_price: float
    entry_date: datetime
    catalyst: Optional[str] = None
    catalyst_date: Optional[datetime] = None


def calculate_portfolio_risk(
    positions: List,
    portfolio_greeks: Dict,
    equity: float,
) -> PortfolioRisk:
    """
    Calculate current portfolio risk metrics.

    Args:
        positions: List of current options positions
        portfolio_greeks: Aggregated Greeks from options_executor
        equity: Current account equity

    Returns:
        PortfolioRisk with all computed metrics
    """
    risk = PortfolioRisk()
    risk.equity = equity

    if not positions:
        risk.risk_level = "healthy"
        risk.risk_score = 0
        risk.risk_capacity_pct = 1.0
        risk.can_add_positions = True
        return risk

    # Extract Greeks
    risk.net_delta = abs(portfolio_greeks.get("net_delta", 0))
    risk.total_gamma = abs(portfolio_greeks.get("total_gamma", 0))
    risk.daily_theta = abs(portfolio_greeks.get("daily_theta", 0))
    risk.total_vega = abs(portfolio_greeks.get("total_vega", 0))

    # Calculate options value
    for pos in positions:
        market_value = float(getattr(pos, 'market_value', 0) or 0)
        risk.options_value += abs(market_value)

        # Track underlying exposure
        underlying = getattr(pos, 'symbol', 'UNKNOWN')
        if len(underlying) > 6:
            underlying = underlying[:4].rstrip('0123456789')
        risk.underlying_exposure[underlying] = (
            risk.underlying_exposure.get(underlying, 0) + abs(market_value)
        )

    # Normalize per $100K
    equity_100k = max(equity / 100000, 0.1)
    config = RISK_FRAMEWORK

    # Calculate risk score (0-100)
    score = 0

    # Delta risk (0-25 points)
    delta_per_100k = risk.net_delta / equity_100k
    delta_limit = config["max_portfolio_delta_per_100k"]
    delta_score = min(25, int(25 * (delta_per_100k / delta_limit)))
    score += delta_score

    # Gamma risk (0-25 points)
    gamma_per_100k = risk.total_gamma / equity_100k
    gamma_limit = config["max_portfolio_gamma_per_100k"]
    gamma_score = min(25, int(25 * (gamma_per_100k / gamma_limit)))
    score += gamma_score

    # Theta risk (0-25 points)
    theta_pct = risk.daily_theta / equity if equity > 0 else 0
    theta_limit = config["max_portfolio_theta_daily_pct"]
    theta_score = min(25, int(25 * (theta_pct / theta_limit)))
    score += theta_score

    # Concentration risk (0-25 points)
    max_underlying_pct = 0
    for underlying, value in risk.underlying_exposure.items():
        pct = value / equity if equity > 0 else 0
        max_underlying_pct = max(max_underlying_pct, pct)

    concentration_limit = config["max_single_underlying_pct"]
    concentration_score = min(25, int(25 * (max_underlying_pct / concentration_limit)))
    score += concentration_score

    risk.risk_score = min(100, score)

    # Determine risk level
    if risk.risk_score <= RISK_SCORE_THRESHOLDS["healthy"]:
        risk.risk_level = "healthy"
        risk.can_add_positions = True
    elif risk.risk_score <= RISK_SCORE_THRESHOLDS["cautious"]:
        risk.risk_level = "cautious"
        risk.can_add_positions = True
    elif risk.risk_score <= RISK_SCORE_THRESHOLDS["elevated"]:
        risk.risk_level = "elevated"
        risk.can_add_positions = False
    else:
        risk.risk_level = "critical"
        risk.can_add_positions = False

    # Calculate remaining risk capacity
    risk.risk_capacity_pct = max(0, 1.0 - (risk.risk_score / 100))

    return risk


def check_entry_risk(
    signal_conviction: int,
    signal_symbol: str,
    signal_option_type: str,
    signal_premium: float,
    signal_dte: int,
    signal_iv_rank: Optional[float],
    market_trend: str,
    portfolio_risk: PortfolioRisk,
) -> EntryRiskCheck:
    """
    Evaluate if a new position should be entered based on risk.

    No hard counters - purely risk-based decision.

    Args:
        signal_conviction: Claude's conviction score (0-100)
        signal_symbol: Underlying symbol
        signal_option_type: 'call' or 'put'
        signal_premium: Premium per contract in cents
        signal_dte: Days to expiration
        signal_iv_rank: IV rank (0-100) or None
        market_trend: Current market trend
        portfolio_risk: Current portfolio risk state

    Returns:
        EntryRiskCheck with decision and reasoning
    """
    config = RISK_FRAMEWORK
    result = EntryRiskCheck(allowed=True)

    # -------------------------------------------------------------------------
    # RISK CAPACITY CHECK
    # -------------------------------------------------------------------------
    if portfolio_risk.risk_capacity_pct < config["min_risk_capacity_pct"]:
        # Check for exceptional conviction override
        if signal_conviction >= config["exceptional_conviction_threshold"]:
            result.warnings.append(
                f"Low risk capacity ({portfolio_risk.risk_capacity_pct:.0%}) but exceptional conviction ({signal_conviction}%) - allowing"
            )
        else:
            result.allowed = False
            result.reasons.append(
                f"Insufficient risk capacity: {portfolio_risk.risk_capacity_pct:.0%} < {config['min_risk_capacity_pct']:.0%} required"
            )

    # -------------------------------------------------------------------------
    # CONVICTION CHECK
    # -------------------------------------------------------------------------
    min_conviction = config["min_conviction_for_entry"]

    # Adjust conviction requirement based on risk level
    if portfolio_risk.risk_level == "cautious":
        min_conviction = min(95, min_conviction + 10)
        result.warnings.append(f"Risk level cautious - conviction requirement raised to {min_conviction}%")

    if signal_conviction < min_conviction:
        result.allowed = False
        result.reasons.append(
            f"Conviction too low: {signal_conviction}% < {min_conviction}% required"
        )

    result.conviction_required = min_conviction

    # -------------------------------------------------------------------------
    # CONCENTRATION CHECK
    # -------------------------------------------------------------------------
    current_exposure = portfolio_risk.underlying_exposure.get(signal_symbol, 0)
    max_exposure = portfolio_risk.equity * config["max_single_underlying_pct"]

    if current_exposure > 0:
        result.warnings.append(
            f"Already exposed to {signal_symbol}: ${current_exposure:,.0f}"
        )
        if current_exposure >= max_exposure * 0.8:
            result.allowed = False
            result.reasons.append(
                f"Would exceed concentration limit for {signal_symbol}"
            )

    # -------------------------------------------------------------------------
    # TREND ALIGNMENT CHECK
    # -------------------------------------------------------------------------
    if config["require_trend_alignment"]:
        is_aligned = (
            (market_trend == "bullish" and signal_option_type.lower() == "call") or
            (market_trend == "bearish" and signal_option_type.lower() == "put")
        )
        if not is_aligned and market_trend != "sideways":
            # Allow with exceptional conviction
            if signal_conviction >= config["exceptional_conviction_threshold"]:
                result.warnings.append(
                    f"Counter-trend ({signal_option_type} in {market_trend} market) but exceptional conviction - allowing"
                )
            else:
                result.allowed = False
                result.reasons.append(
                    f"Counter-trend: {signal_option_type} in {market_trend} market"
                )

    # -------------------------------------------------------------------------
    # DTE CHECK
    # -------------------------------------------------------------------------
    if signal_dte < config["min_dte_for_entry"]:
        result.allowed = False
        result.reasons.append(
            f"DTE too short: {signal_dte} < {config['min_dte_for_entry']} minimum"
        )

    # -------------------------------------------------------------------------
    # IV RANK CHECK
    # -------------------------------------------------------------------------
    if signal_iv_rank is not None and signal_iv_rank > config["max_iv_rank_for_entry"]:
        result.allowed = False
        result.reasons.append(
            f"IV rank too high: {signal_iv_rank:.0f}% > {config['max_iv_rank_for_entry']}% (expensive premium)"
        )

    # -------------------------------------------------------------------------
    # PREMIUM CHECK
    # -------------------------------------------------------------------------
    if signal_premium > config["max_premium_per_contract"]:
        result.allowed = False
        result.reasons.append(
            f"Premium too high: ${signal_premium/100:.2f} > ${config['max_premium_per_contract']/100:.2f} (liquidity risk)"
        )

    # Calculate risk score impact of this trade
    result.risk_score_impact = estimate_risk_impact(
        signal_premium, signal_dte, portfolio_risk
    )

    return result


def check_exit_risk(
    position,
    current_pnl_pct: float,
    current_conviction: int,
    original_thesis: ThesisState,
    market_trend: str,
    portfolio_risk: PortfolioRisk,
) -> ExitRiskCheck:
    """
    Evaluate if a position should be exited based on risk and thesis.

    No arbitrary hold times - based on thesis validity and risk.

    Args:
        position: Current position object
        current_pnl_pct: Current P&L percentage
        current_conviction: Current conviction score
        original_thesis: Original thesis at entry
        market_trend: Current market trend
        portfolio_risk: Current portfolio risk state

    Returns:
        ExitRiskCheck with decision and reasoning
    """
    config = RISK_FRAMEWORK
    result = ExitRiskCheck(should_exit=False, thesis_valid=True)
    result.conviction_current = current_conviction

    # Get position details
    option_type = getattr(position, 'option_type', 'call').lower()
    dte = getattr(position, 'dte', 30)

    # -------------------------------------------------------------------------
    # HARD STOPS (always exit)
    # -------------------------------------------------------------------------

    # Profit target hit
    if current_pnl_pct >= config["profit_target_pct"]:
        result.should_exit = True
        result.urgency = "high"
        result.reasons.append(
            f"Profit target reached: +{current_pnl_pct:.0%} >= +{config['profit_target_pct']:.0%}"
        )
        return result

    # Stop loss hit
    if current_pnl_pct <= -config["stop_loss_pct"]:
        result.should_exit = True
        result.urgency = "critical"
        result.reasons.append(
            f"Stop loss triggered: {current_pnl_pct:.0%} <= -{config['stop_loss_pct']:.0%}"
        )
        return result

    # -------------------------------------------------------------------------
    # THESIS VALIDATION
    # -------------------------------------------------------------------------
    if config["exit_on_thesis_invalidation"]:
        # Check trend reversal
        original_trend = original_thesis.original_trend
        trend_aligned_at_entry = (
            (original_trend == "bullish" and option_type == "call") or
            (original_trend == "bearish" and option_type == "put")
        )
        trend_aligned_now = (
            (market_trend == "bullish" and option_type == "call") or
            (market_trend == "bearish" and option_type == "put")
        )

        if trend_aligned_at_entry and not trend_aligned_now:
            result.thesis_valid = False
            result.reasons.append(
                f"Thesis invalidated: trend was {original_trend}, now {market_trend}"
            )
            result.urgency = "high"

        # Check if catalyst has passed
        if original_thesis.catalyst_date:
            now = datetime.now(ET)
            if now > original_thesis.catalyst_date:
                result.reasons.append(
                    f"Catalyst passed: {original_thesis.catalyst} on {original_thesis.catalyst_date.date()}"
                )
                if current_pnl_pct < 0:
                    result.thesis_valid = False
                    result.urgency = "medium"

    # -------------------------------------------------------------------------
    # CONVICTION DROP
    # -------------------------------------------------------------------------
    conviction_drop = original_thesis.original_conviction - current_conviction

    if current_conviction < config["conviction_exit_threshold"]:
        result.should_exit = True
        result.urgency = "medium"
        result.reasons.append(
            f"Conviction dropped: {current_conviction}% < {config['conviction_exit_threshold']}% threshold"
        )
    elif current_conviction < config["conviction_hold_threshold"]:
        result.reasons.append(
            f"Conviction weakening: {current_conviction}% (hold threshold: {config['conviction_hold_threshold']}%)"
        )

    # -------------------------------------------------------------------------
    # GAMMA RISK (near expiration)
    # -------------------------------------------------------------------------
    if config["exit_on_gamma_risk"] and dte <= config["gamma_risk_dte_threshold"]:
        if current_pnl_pct < 0.20:  # Not significantly profitable
            result.should_exit = True
            result.urgency = "high"
            result.reasons.append(
                f"Gamma risk: DTE={dte} with only {current_pnl_pct:.0%} profit"
            )

    # -------------------------------------------------------------------------
    # CONCENTRATION BREACH
    # -------------------------------------------------------------------------
    if config["exit_on_concentration_breach"]:
        underlying = getattr(position, 'symbol', 'UNKNOWN')
        if len(underlying) > 6:
            underlying = underlying[:4].rstrip('0123456789')

        exposure_pct = portfolio_risk.underlying_exposure.get(underlying, 0) / portfolio_risk.equity
        if exposure_pct > config["max_single_underlying_pct"] * 1.1:  # 10% buffer
            result.reasons.append(
                f"Concentration breach: {underlying} at {exposure_pct:.0%} of portfolio"
            )
            result.urgency = "medium"

    # -------------------------------------------------------------------------
    # FINAL DECISION
    # -------------------------------------------------------------------------
    if not result.thesis_valid and not result.should_exit:
        result.should_exit = True
        result.urgency = max(result.urgency, "medium")
        result.reasons.append("Thesis no longer valid - recommend exit")

    return result


def estimate_risk_impact(
    premium: float,
    dte: int,
    portfolio_risk: PortfolioRisk,
) -> int:
    """
    Estimate how much this trade would increase portfolio risk score.

    Returns estimated risk score impact (0-20).
    """
    impact = 0

    # Premium-based impact (larger positions = more risk)
    position_value = premium * 100  # 1 contract
    if portfolio_risk.equity > 0:
        pct_of_portfolio = position_value / portfolio_risk.equity
        impact += int(pct_of_portfolio * 100)  # 1% = 1 point

    # DTE-based impact (shorter DTE = more gamma risk)
    if dte < 7:
        impact += 5
    elif dte < 14:
        impact += 3
    elif dte < 21:
        impact += 1

    return min(20, impact)


def get_risk_summary(portfolio_risk: PortfolioRisk) -> str:
    """Generate a human-readable risk summary for Claude."""
    return f"""
PORTFOLIO RISK STATE:
- Risk Score: {portfolio_risk.risk_score}/100 ({portfolio_risk.risk_level})
- Risk Capacity: {portfolio_risk.risk_capacity_pct:.0%} available
- Can Add Positions: {'Yes' if portfolio_risk.can_add_positions else 'No'}

GREEKS EXPOSURE:
- Net Delta: {portfolio_risk.net_delta:.1f}
- Total Gamma: {portfolio_risk.total_gamma:.2f}
- Daily Theta: ${portfolio_risk.daily_theta:.2f}
- Total Vega: {portfolio_risk.total_vega:.2f}

CONCENTRATION:
- Options Value: ${portfolio_risk.options_value:,.0f}
- Top Exposures: {', '.join(f'{k}: ${v:,.0f}' for k, v in sorted(portfolio_risk.underlying_exposure.items(), key=lambda x: -x[1])[:3])}
"""


def format_entry_decision_for_claude(
    signal: Dict,
    portfolio_risk: PortfolioRisk,
    entry_check: EntryRiskCheck,
    market_context: Dict,
) -> str:
    """
    Format all risk data for Claude's entry decision.

    This replaces hard-coded rules with risk context that Claude evaluates.
    """
    return f"""
=== ENTRY DECISION REQUEST ===

SIGNAL:
- Symbol: {signal.get('symbol')}
- Type: {signal.get('option_type', '').upper()}
- Strike: ${signal.get('strike', 0)}
- Expiration: {signal.get('expiration')} ({signal.get('dte', 'N/A')} DTE)
- Premium: ${signal.get('premium', 0)/100:.2f}
- IV Rank: {signal.get('iv_rank', 'N/A')}%
- Signal Conviction: {signal.get('conviction', 0)}%

MARKET CONTEXT:
- Trend: {market_context.get('trend', 'unknown')}
- VIX: {market_context.get('vix', 'N/A')}
- SPY Change: {market_context.get('spy_change_pct', 0):.1%}

{get_risk_summary(portfolio_risk)}

RISK FRAMEWORK ASSESSMENT:
- Entry Allowed: {'Yes' if entry_check.allowed else 'No'}
- Conviction Required: {entry_check.conviction_required}%
- Risk Impact: +{entry_check.risk_score_impact} points

{f"BLOCKERS: {chr(10).join('- ' + r for r in entry_check.reasons)}" if entry_check.reasons else "No blockers."}

{f"WARNINGS: {chr(10).join('- ' + w for w in entry_check.warnings)}" if entry_check.warnings else ""}

Your task: Evaluate this entry considering ALL factors above.
- If risk framework blocked it, explain why you agree or disagree
- If allowed, confirm the thesis is sound
- Provide final EXECUTE, ALERT, or SKIP recommendation with reasoning
"""


def format_exit_decision_for_claude(
    position: Dict,
    exit_check: ExitRiskCheck,
    portfolio_risk: PortfolioRisk,
    market_context: Dict,
) -> str:
    """
    Format all risk data for Claude's exit decision.

    This replaces arbitrary hold times with thesis-based evaluation.
    """
    return f"""
=== EXIT DECISION REQUEST ===

POSITION:
- Symbol: {position.get('symbol')}
- Type: {position.get('option_type', '').upper()}
- Strike: ${position.get('strike', 0)}
- DTE: {position.get('dte', 'N/A')}
- Entry Price: ${position.get('entry_price', 0):.2f}
- Current Price: ${position.get('current_price', 0):.2f}
- P&L: {position.get('pnl_pct', 0):.1%}
- Days Held: {position.get('days_held', 0)}

ORIGINAL THESIS:
- Entry Trend: {position.get('entry_trend', 'unknown')}
- Entry Conviction: {position.get('entry_conviction', 0)}%
- Catalyst: {position.get('catalyst', 'None')}

CURRENT STATE:
- Market Trend: {market_context.get('trend', 'unknown')}
- Current Conviction: {exit_check.conviction_current}%
- Thesis Valid: {'Yes' if exit_check.thesis_valid else 'NO - INVALIDATED'}

{get_risk_summary(portfolio_risk)}

RISK FRAMEWORK ASSESSMENT:
- Recommend Exit: {'Yes' if exit_check.should_exit else 'No'}
- Urgency: {exit_check.urgency.upper()}

{f"EXIT REASONS: {chr(10).join('- ' + r for r in exit_check.reasons)}" if exit_check.reasons else "No exit triggers."}

Your task: Evaluate this position considering ALL factors above.
- Is the original thesis still valid?
- Does the risk/reward still make sense?
- Provide final HOLD, CLOSE, or ROLL recommendation with reasoning
"""
