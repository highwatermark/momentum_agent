# Options Trading System Recovery Documentation

**Date**: 2026-02-05 / 2026-02-06
**Version**: 3.0.0 (Risk-Based Framework)
**Status**: Implemented, Active

---

## Table of Contents

1. [Problem Summary](#problem-summary)
2. [Solution Evolution](#solution-evolution)
3. [Risk-Based Framework](#risk-based-framework)
4. [Implementation Details](#implementation-details)
5. [Configuration Reference](#configuration-reference)
6. [Testing Guide](#testing-guide)
7. [Monitoring](#monitoring)

---

## Problem Summary

### Initial Performance (2026-02-05)
| Metric | Value |
|--------|-------|
| Total Trades | 6 |
| Winners | 0 |
| Win Rate | 0% |
| Total P&L | -$974.00 |
| Average Loss | -17.28% |

### Root Causes Identified

1. **AI Monitor exits too aggressively** - 15% loss trigger was too tight
2. **Sentiment logic was backwards** - Bid/ask side doesn't indicate direction
3. **Claude prompt was profit-biased** - "Missing trades costs money" led to bad trades
4. **Counter-trend trades failing** - Puts in uptrends, calls in downtrends
5. **Hard-coded arbitrary limits** - "Max 3 trades/day" doesn't adapt to conditions

### Day 2 Problems (2026-02-06)
- Flipping direction daily (chasing yesterday's flow)
- 30-minute scalps instead of swing trades
- Short-dated contracts (7-8 DTE)
- Trading noisy ETFs (SPY, QQQ, IWM)
- Daily counter reset at UTC midnight, not ET

---

## Solution Evolution

### Phase 1: Initial Fixes (2026-02-05)
- Increased AI trigger threshold from 15% to 35%
- Added minimum hold time (30 minutes)
- Fixed sentiment logic (neutral instead of bid/ask derived)
- Added counter-trend scoring penalty
- Made AI advisory-only for non-critical exits

### Phase 2: Swing Trade Strategy (2026-02-06 ~01:20 AM ET)
- Added ETF exclusion list
- Changed hold time from minutes to days
- Added minimum DTE filter (14 days)
- Fixed timezone to use ET for daily resets

### Phase 3: Risk-Based Framework (2026-02-06 ~01:30 AM ET)
**Replaced ALL hard-coded counters with dynamic risk assessment.**

Old approach:
```python
"max_executions_per_day": 3      # Arbitrary
"min_hold_days": 2               # Arbitrary
"no_same_day_exit": True         # Blanket rule
```

New approach:
```python
"min_risk_capacity_pct": 0.20    # Trade if risk budget available
"min_conviction_for_entry": 80   # Trade if conviction high enough
"exceptional_conviction_threshold": 90  # Override if exceptional
```

---

## Risk-Based Framework

### Core Philosophy

**No arbitrary limits.** Every decision is based on:
1. **Risk Capacity** - How much risk budget is available
2. **Conviction** - Claude's assessment of trade probability
3. **Thesis Validity** - Is there a clear, logical reason

### Risk Score Calculation

Portfolio risk score (0-100) computed from:
- Delta exposure (0-25 points)
- Gamma concentration (0-25 points)
- Theta decay rate (0-25 points)
- Position concentration (0-25 points)

```python
risk_capacity = 1.0 - (risk_score / 100)
```

### Risk Levels

| Level | Score | Behavior |
|-------|-------|----------|
| HEALTHY | 0-30 | Normal operations, can take positions |
| CAUTIOUS | 31-50 | Selective entries, +10% conviction required |
| ELEVATED | 51-70 | Very selective, only exceptional setups |
| CRITICAL | 71+ | No new positions, consider reducing |

### Entry Decision Logic

```
IF risk_capacity >= 20% AND conviction >= 80%:
    ALLOW entry
ELIF conviction >= 90% (exceptional):
    ALLOW entry with warning (can use extra risk budget)
ELSE:
    BLOCK entry
```

### Exit Decision Logic

```
IF pnl <= -50% (stop loss):
    EXIT immediately (hard rule)
ELIF pnl >= +50% (profit target):
    EXIT immediately (hard rule)
ELIF thesis_invalidated (trend reversed, conviction dropped):
    EXIT (Claude recommends)
ELSE:
    HOLD (thesis still valid)
```

### Conviction Scoring

Claude calculates conviction using these factors:

| Factor | Points |
|--------|--------|
| Strong sweep activity | +15 |
| Floor/institutional trade | +10 |
| Opening position | +10 |
| Vol/OI > 3x | +10 |
| Trend-aligned | +10 |
| Counter-trend | -15 |
| IV rank > 60% | -10 |
| Near earnings (no thesis) | -10 |
| DTE < 14 | -5 |
| OTM | -5 |

---

## Implementation Details

### New Files Created

#### `risk_assessment.py` (340 lines)

Core risk calculation module:

```python
@dataclass
class PortfolioRisk:
    net_delta: float = 0.0
    total_gamma: float = 0.0
    daily_theta: float = 0.0
    total_vega: float = 0.0
    equity: float = 0.0
    risk_score: int = 0
    risk_capacity_pct: float = 1.0
    risk_level: str = "healthy"

def check_entry_risk(
    signal_conviction: int,
    signal_symbol: str,
    market_trend: str,
    portfolio_risk: PortfolioRisk,
) -> EntryRiskCheck:
    """Evaluate if entry allowed based on risk capacity and conviction."""

def check_exit_risk(
    position,
    current_pnl_pct: float,
    current_conviction: int,
    original_thesis: ThesisState,
    market_trend: str,
) -> ExitRiskCheck:
    """Evaluate if exit needed based on thesis validity and risk."""
```

### Modified Files

#### `config.py`

Added `RISK_FRAMEWORK` configuration:

```python
RISK_FRAMEWORK = {
    # Portfolio Risk Limits
    "max_portfolio_delta_per_100k": 150,
    "max_portfolio_gamma_per_100k": 50,
    "max_portfolio_theta_daily_pct": 0.005,

    # Concentration Limits
    "max_sector_concentration": 0.40,
    "max_single_underlying_pct": 0.25,

    # Entry Risk Gates
    "min_conviction_for_entry": 80,
    "min_risk_capacity_pct": 0.20,
    "max_iv_rank_for_entry": 70,
    "exceptional_conviction_threshold": 90,

    # Exit Triggers
    "profit_target_pct": 0.50,
    "stop_loss_pct": 0.50,
    "conviction_exit_threshold": 50,
}

RISK_SCORE_THRESHOLDS = {
    "healthy": 30,
    "cautious": 50,
    "elevated": 70,
    "critical": 100,
}
```

Added ETF exclusion list:
```python
"excluded_etfs": ["SPY", "QQQ", "IWM", "DIA", "XLF", "XLE", "XLK",
                  "XLV", "XLI", "GLD", "SLV", "TLT", "HYG", "EEM",
                  "EFA", "VXX", "UVXY", "SQQQ", "TQQQ"],
```

#### `options_agent.py`

Rewrote Claude system prompt for risk-based decisions:

```python
system_prompt = """You are an AUTONOMOUS OPTIONS TRADING AGENT with full decision authority.
Your decisions are based on RISK CAPACITY and CONVICTION, not arbitrary rules.

=== RISK-BASED DECISION FRAMEWORK ===

You make decisions based on THREE factors:
1. RISK CAPACITY - How much risk budget is available
2. CONVICTION - Your assessment of the trade's probability of success
3. THESIS VALIDITY - Is there a clear, logical reason for this trade

There are NO hard-coded limits like "max 3 trades per day".
Instead, you evaluate RISK vs REWARD dynamically.
...
"""
```

Updated `FlowValidationInput` with risk data:
```python
risk_capacity_pct: float = 1.0
risk_level: str = "healthy"
portfolio_gamma: float = 0.0
portfolio_vega: float = 0.0
concentration: Dict[str, float] = None
```

#### `flow_listener.py`

Replaced hard-coded `safety_gate_check()` with risk-based version:

```python
def safety_gate_check(signal, portfolio, conviction=0):
    """RISK-BASED safety checks - no hard-coded daily limits."""

    # Calculate risk capacity
    risk_capacity = max(0, 1.0 - (risk_score / 100))

    # Check risk capacity (replaces "max 3 trades/day")
    if risk_capacity < min_capacity:
        if conviction >= exceptional_threshold:
            # Allow exceptional conviction to override
            warnings.append("Exceptional conviction - allowing")
        else:
            block_reasons.append(f"Risk capacity {risk_capacity:.0%} insufficient")

    # Check risk level (replaces position count limit)
    if risk_score > RISK_SCORE_THRESHOLDS["critical"]:
        block_reasons.append("Risk level CRITICAL - no new positions")
```

Fixed timezone for daily reset:
```python
def _check_daily_reset(self):
    """Reset at ET midnight, not UTC."""
    et = pytz.timezone('America/New_York')
    today_et = datetime.now(et).date()
    if today_et > self.last_reset_date:
        # Reset counters
```

Added DTE and ETF pre-filters:
```python
# Skip short DTE
if dte < min_dte:
    short_dte_skipped += 1
    continue

# Skip ETFs
if signal.symbol.upper() in excluded_etfs:
    etf_skipped += 1
    continue
```

#### `options_monitor.py`

Replaced time-based hold with thesis-based exit:

```python
def _check_exit_allowed(self, contract_symbol, pnl_pct, reason=""):
    """RISK-BASED exit - no arbitrary hold times."""

    # Hard stops - ALWAYS allowed
    if pnl_pct <= -0.50:  # Stop loss
        return True, "Stop loss triggered"
    if pnl_pct >= 0.50:   # Profit target
        return True, "Profit target reached"

    # Thesis-based exits
    if "thesis_invalidation" in reason or "trend_reversal" in reason:
        return True, f"Valid exit: {reason}"

    # Otherwise, Claude decides
    return True, "Exit delegated to Claude"
```

---

## Configuration Reference

### Environment Variables
```bash
ALPACA_API_KEY=xxx
ALPACA_SECRET_KEY=xxx
UW_API_KEY=xxx
ANTHROPIC_API_KEY=xxx
```

### Risk Framework Settings

| Setting | Value | Purpose |
|---------|-------|---------|
| max_portfolio_delta_per_100k | 150 | Delta exposure limit |
| max_portfolio_gamma_per_100k | 50 | Gamma concentration limit |
| max_sector_concentration | 40% | Sector diversification |
| max_single_underlying_pct | 25% | Position concentration |
| min_conviction_for_entry | 80% | Entry conviction threshold |
| min_risk_capacity_pct | 20% | Minimum risk budget |
| exceptional_conviction_threshold | 90% | Override threshold |
| max_iv_rank_for_entry | 70% | Expensive premium block |
| profit_target_pct | 50% | Take profit trigger |
| stop_loss_pct | 50% | Stop loss trigger |
| conviction_exit_threshold | 50% | Exit conviction floor |

### Excluded Symbols
```python
# ETFs excluded (hedging noise, low signal-to-noise):
SPY, QQQ, IWM, DIA, XLF, XLE, XLK, XLV, XLI,
GLD, SLV, TLT, HYG, EEM, EFA, VXX, UVXY, SQQQ, TQQQ

# Index options excluded:
SPXW, SPX, NDX, XSP
```

---

## Testing Guide

### Risk Framework Test

```bash
source venv/bin/activate
python3 << 'EOF'
from risk_assessment import PortfolioRisk, check_entry_risk, check_exit_risk

# Test entry with healthy portfolio
portfolio = PortfolioRisk(risk_score=25, risk_capacity_pct=0.75)
result = check_entry_risk(
    signal_conviction=85,
    signal_symbol="NVDA",
    signal_option_type="call",
    signal_premium=350,
    signal_dte=21,
    signal_iv_rank=45,
    market_trend="bullish",
    portfolio_risk=portfolio,
)
print(f"Entry allowed: {result.allowed}")
print(f"Reasons: {result.reasons}")
EOF
```

### Flow Listener Test

```bash
# Check process running
ps aux | grep flow_listener

# Check latest logs
tail -50 logs/flow_listener.log

# Look for risk-based messages instead of "Daily limit reached"
grep -E "Risk capacity|Risk level|conviction" logs/flow_listener.log | tail -20
```

---

## Monitoring

### Log Messages to Watch For

**Good (risk-based):**
```
Risk capacity 75% - ALLOWED
Risk level CAUTIOUS - requiring higher conviction
Exceptional conviction (92%) - allowing override
```

**Bad (old hard-coded - should not appear):**
```
Daily limit (3) reached  # OLD - should not see this anymore
```

### Key Metrics

Monitor in Telegram alerts:
- Risk score before/after trade
- Conviction breakdown
- Thesis validation status

### Service Status

```bash
# Flow listener
ps aux | grep flow_listener
tail -f logs/flow_listener.log

# Options monitor
ps aux | grep options_monitor
tail -f logs/options_monitor.log
```

---

## Rollback Procedures

### Quick Rollback (config only)

Restore hard-coded limits in `config.py`:
```python
# Remove RISK_FRAMEWORK section
# Restore:
FLOW_LISTENER_CONFIG = {
    "max_executions_per_day": 3,
    ...
}
OPTIONS_MONITOR_CONFIG = {
    "min_hold_days": 2,
    ...
}
```

### Full Rollback

```bash
git checkout HEAD~3 -- config.py options_agent.py flow_listener.py options_monitor.py
rm risk_assessment.py
```

### Service Restart After Rollback

```bash
pkill -f flow_listener
pkill -f options_monitor
source venv/bin/activate
nohup python flow_listener.py > /dev/null 2>&1 &
nohup python options_monitor.py > /dev/null 2>&1 &
```

---

## Files Changed Summary

| File | Lines | Description |
|------|-------|-------------|
| `config.py` | +100 | RISK_FRAMEWORK, FLOW_CONFIG, EXCLUDED_TICKERS |
| `risk_assessment.py` | +340 | New risk calculation module |
| `options_agent.py` | +60 | Risk-based Claude prompts |
| `flow_listener.py` | +120 | Risk-based safety gate, score_signal(), passes_quality_checks() |
| `flow_scanner.py` | +40 | issue_types param, ETF filter |
| `options_monitor.py` | +50 | Thesis-based exits |
| `agent-sdk/config.py` | +50 | FLOW_CONFIG, EXCLUDED_TICKERS |
| `agent-sdk/tools/standalone_flow.py` | +100 | score_signal(), passes_quality_checks(), issue_types |
| `agent-sdk/agents/hooks.py` | +10 | ET timezone fix |

**Total**: ~870 lines added/modified

---

## Tighter Signal Filtering (v3.2) - FINAL

Updated ~02:40 AM ET on 2026-02-06 to optimize for profitable single-stock trades.

### API-Level Filters (config.py:FLOW_CONFIG)

```python
FLOW_CONFIG = {
    "min_premium": 100000,            # $100K minimum
    "min_vol_oi": 1.5,                # Vol/OI > 1.5
    "all_opening": True,              # CRITICAL - only opening positions
    "min_dte": 14,                    # Minimum DTE
    "max_dte": 45,                    # Maximum DTE
    "issue_types": ["Common Stock"],  # CRITICAL - filters OUT ETFs at API level
    "scan_limit": 30,                 # Raw alerts to fetch
    "min_score": 7,                   # Score 7+ required (0-10 scale)
    "min_open_interest": 500,         # OI for liquidity
    "max_strike_distance_pct": 0.10,  # Max 10% from underlying
}
```

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| `issue_types=["Common Stock"]` | Filters ETFs at API level (more efficient than post-filter) |
| `all_opening=True` | Only new positions, not closing/adjusting (higher conviction) |
| Removed `is_sweep=True` | Sweeps returned ETFs; now reward BOTH sweeps AND floor trades |
| `min_vol_oi=1.5` | Balance between quality and quantity |
| `min_premium=$100K` | Capture institutional activity without being too restrictive |

### Signal Scoring (0-10 scale)

Only signals scoring **7+** are traded. Rewards BOTH sweeps AND floor trades.

| Factor | Points | Rationale |
|--------|--------|-----------|
| Sweep | +2 | Urgency indicator |
| Floor trade | +2 | Institutional activity |
| Opening position | +2 | New conviction (not adjusting) |
| Vol/OI >= 3 | +2 | Strong unusual activity |
| Vol/OI >= 1.5 | +1 | Above-average activity |
| Premium >= $500K | +2 | Major institutional bet |
| Premium >= $250K | +1 | Significant size |
| Trend-aligned | +1 | Higher probability |
| Counter-trend | -3 | Lower probability |
| OTM | -1 | Lower delta |
| IV rank > 70% | -3 | Expensive premium |
| DTE < 7 | -2 | Gamma risk |
| DTE 7-14 | -1 | Elevated theta |

### Quality Checks (must pass ALL)

- Open Interest >= 500 (liquidity)
- Strike within 10% of underlying price
- Not in EXCLUDED_TICKERS
- Not counter-trend (puts in bullish, calls in bearish)
- DTE >= 14

### EXCLUDED_TICKERS (config.py)

```python
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
    # Index options
    "SPXW", "SPX", "NDX", "XSP",
}
```

### Test Results (02:40 AM ET)

```
Raw alerts from API: 30 (all Common Stock due to issue_types filter)
Filtered out:
  - Excluded tickers: 0 (already filtered by API)
  - Quality check fails: 27
  - Low score (<7): 2
Passed all filters: 1 signal

Example passing signal:
  CRH CALL $130 (Floor trade)
  Score: 7/10 | Premium: $1,425,000 | Vol/OI: 5.7x | Opening: True
```

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-02-05 | Initial recovery (AI threshold, sentiment fix) |
| 2.0.0 | 2026-02-05 | Swing trade strategy (ETF filter, hold days) |
| 3.0.0 | 2026-02-06 | Risk-based framework (no hard-coded limits) |
| 3.1.0 | 2026-02-06 | Tighter signal filtering (API filters, score 7+) |
| 3.2.0 | 2026-02-06 | Final config: issue_types for ETF filter, reward both sweep/floor |

---

*Document Version: 3.2*
*Last Updated: 2026-02-06 02:40 AM ET*
