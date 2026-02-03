# Session Log - January 2, 2026

---

## Session 13: Options AI Agents Implementation (February 3, 2026)

### Overview

Implemented three Claude-powered AI agents for options position management with rules-based fallbacks when the agent is unavailable. This addresses a major blind spot identified in the systems review where options trading lacked the AI decision-making present in stock trading.

### New File: `options_agent.py` (~700 lines)

Contains three specialized agents:

| Agent | Purpose |
|-------|---------|
| **Options Position Reviewer** | Review individual positions, recommend HOLD/CLOSE/ROLL/TRIM |
| **Options Position Sizer** | Calculate optimal contract quantity based on portfolio state |
| **Options Portfolio Manager** | Aggregate portfolio review with risk scoring |

### Agent 1: Options Position Reviewer

**Decisions:** HOLD, CLOSE, ROLL, TRIM

**System Prompt Key Rules:**

```
CLOSE signals (High Priority):
- DTE <= 3 and position profitable (lock gains before theta crush)
- DTE <= 3 and OTM (avoid expiring worthless)
- Loss exceeds 50% of premium paid
- Underlying moved significantly against position
- Gamma risk too high (ATM with < 5 DTE)

ROLL signals:
- DTE <= 7 and want to maintain exposure
- Profitable but theta decay accelerating
- Prefer rolling to same strike, 3-4 weeks out

HOLD signals:
- Thesis intact and DTE > 10
- Position profitable but has room to run
- Theta decay acceptable relative to potential gain

TRIM signals:
- Position too large relative to portfolio
- Want to lock in partial profits
```

**Urgency Levels:**
- `critical`: Act immediately (expiring today/tomorrow)
- `high`: Act within hours (DTE <= 3)
- `medium`: Act within 1-2 days (DTE <= 7)
- `low`: Monitor but no action needed

**Risk Factors Assessed:**
1. Theta Risk (daily decay vs potential)
2. Gamma Risk (delta swings near expiry)
3. Vega Risk (IV changes impact)
4. Directional Risk (delta vs market)
5. Time Risk (DTE and theta acceleration)
6. Liquidity Risk (ability to exit)

### Agent 2: Options Position Sizer

**System Prompt Key Rules:**

```
Base Sizing:
- Never risk > 2% of portfolio on single options trade
- Maximum 10% total portfolio in options
- Consider existing Greeks exposure
- Account for sector concentration

Increase size when:
- Signal score >= 15 (high conviction)
- IV rank < 30% (cheap premium)
- Portfolio delta is low
- Sector underweight
- Strong trend alignment

Decrease size when:
- Signal score < 10 (lower conviction)
- IV rank > 50% (expensive premium)
- Would create sector concentration > 50%
- Portfolio already has high theta decay
- Short DTE (< 14 days)
- High gamma exposure near expiry

Maximum Constraints:
- Single underlying: Max 30% of options allocation
- Single sector: Max 50% of options allocation
- Max contracts per trade: 10
```

**Greeks Impact Assessment:**
- Calculates delta impact on portfolio
- Assesses theta impact (daily $ decay)
- Considers gamma concentration
- Evaluates vega exposure vs IV environment

### Agent 3: Options Portfolio Manager

**Health Levels:** healthy, moderate_risk, high_risk, critical

**Risk Scoring (0-100 points):**

| Component | Points | Criteria |
|-----------|--------|----------|
| Theta decay | 0-20 | Daily decay as % of portfolio |
| Gamma concentration | 0-20 | High gamma near expiry |
| Delta imbalance | 0-20 | Net delta per $100K |
| Concentration risk | 0-20 | Single sector/position |
| Expiration risk | 0-20 | Multiple positions same week |

**Key Metrics Monitored:**
- **Net Delta**: Healthy < |50| per $100K, Concerning > |100|
- **Daily Theta**: Healthy < 0.1% portfolio/day, Concerning > 0.2%
- **Sector Concentration**: Max 50% single sector

**Rebalancing Triggers:**
- Net delta > |100| per $100K
- Single sector > 50%
- Daily theta > 0.2% of portfolio
- Multiple positions DTE < 7

### Fallback Logic

Each agent has rules-based fallback when Claude is unavailable:

```python
def review_position(position, use_agent=True):
    if use_agent:
        result = _review_position_with_agent(position)
        if result:
            return result
        logger.warning("Agent failed, falling back to rules")

    return _review_position_rules_based(position)
```

**Position Reviewer Fallback:**
- DTE <= 1: CLOSE (critical)
- DTE <= 3 + profit > 30%: CLOSE
- DTE <= 3 + loss > 40%: CLOSE
- DTE <= 7 + profit > 50%: CLOSE
- Loss > 50%: CLOSE
- Otherwise: HOLD

**Position Sizer Fallback:**
- Base: 2% of equity / contract cost
- Signal score >= 15: +50%
- Signal score >= 12: +25%
- Signal score < 8: -50%
- Sector concentration > 35%: -50%
- Cap at max_contracts_per_trade (10)

**Portfolio Manager Fallback:**
- Calculates risk score from thresholds
- Identifies positions needing roll (DTE < 7)
- Checks sector concentration limits

### Integration with `options_executor.py`

**Updated Functions:**

1. `calculate_options_position_size()` - Now accepts additional parameters:
   ```python
   def calculate_options_position_size(
       account_equity, option_price, conviction=0.5,
       underlying=None, option_type="call", strike=0,
       expiration=None, signal_score=0, use_agent=True
   )
   ```

2. `execute_flow_trade()` - Now uses agent-based sizing:
   ```python
   quantity = calculate_options_position_size(
       account_equity=account["equity"],
       option_price=estimated_price,
       conviction=enriched_signal.conviction,
       underlying=signal.symbol,
       option_type=signal.option_type,
       strike=contract['strike'],
       expiration=contract['expiration'],
       signal_score=signal.score,
       use_agent=True
   )
   ```

**New Functions Added:**

| Function | Description |
|----------|-------------|
| `review_options_positions()` | Review all positions with AI agent |
| `review_options_portfolio()` | Portfolio-level AI review |
| `run_options_monitor()` | Full monitoring cycle |

### New Telegram Bot Commands

| Command | Description |
|---------|-------------|
| `/optionsreview` | AI-powered review of each position |
| `/portfolioreview` | AI portfolio risk assessment |
| `/optionsmonitor` | Run full monitoring cycle |

### Logging Infrastructure

**File Logging:**
```
logs/options_agent.log
```

**Database Table:**
```sql
CREATE TABLE options_agent_logs (
    id INTEGER PRIMARY KEY,
    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
    agent_name TEXT NOT NULL,
    input_data TEXT,      -- JSON
    output_data TEXT,     -- JSON
    agent_used INTEGER,   -- 1 if Claude, 0 if fallback
    fallback_reason TEXT,
    execution_time_ms REAL,
    confidence REAL
)
```

### Data Classes Added

**Input Classes:**
- `PositionReviewInput` - Position data with Greeks and context
- `PositionSizingInput` - Trade details and portfolio state
- `PortfolioReviewInput` - Account state with aggregate Greeks

**Output Classes:**
- `PositionReviewResult` - Recommendation with reasoning
- `PositionSizingResult` - Contract count with risk factors
- `PortfolioReviewResult` - Assessment with recommendations

### Sample Agent Outputs

**Position Review:**
```json
{
    "recommendation": "ROLL",
    "urgency": "medium",
    "reasoning": "Position is profitable (+31%) but DTE=5 with accelerating theta decay. Roll to maintain exposure while avoiding gamma risk.",
    "risk_factors": ["theta_acceleration", "gamma_near_expiry"],
    "roll_to_expiration": "2024-04-19",
    "confidence": 0.85
}
```

**Position Sizing:**
```json
{
    "recommended_contracts": 3,
    "max_contracts": 5,
    "position_value": 4650.00,
    "position_pct_of_portfolio": 4.65,
    "reasoning": "High conviction signal (14/20), sector underweight, reasonable IV rank. Sizing at 1.5x base due to signal quality.",
    "delta_impact": 150.0,
    "theta_impact": -25.50,
    "confidence": 0.78
}
```

**Portfolio Review:**
```json
{
    "overall_assessment": "moderate_risk",
    "risk_score": 42,
    "rebalancing_needed": false,
    "roll_suggestions": [
        {"contract": "AAPL240315C175", "roll_to": "2024-04-19", "reason": "DTE=5"}
    ],
    "risk_factors": ["high_tech_concentration", "theta_elevated"],
    "summary": "Portfolio has moderate risk with 65% tech concentration and elevated theta. Consider closing AAPL position approaching expiry.",
    "confidence": 0.82
}
```

### Files Modified

| File | Changes |
|------|---------|
| `options_agent.py` | **NEW** - All three agents (~700 lines) |
| `options_executor.py` | Added agent integration, 3 new review functions |
| `bot.py` | Added 3 new commands, updated help |

### Testing

Run the test suite:
```bash
./venv/bin/python options_agent.py
```

Tests:
1. Position Review (with/without agent)
2. Position Sizing (with/without agent)
3. Portfolio Review (with/without agent)

### Configuration

Agents use existing config from `config.py`:
- `OPTIONS_CONFIG` - Position limits, sizing percentages
- `OPTIONS_SAFETY` - Spread limits, concentration limits

No new configuration required.

---

## Session 12: DQN Backfill & Data-Driven Prompt Review (January 20, 2026)

### Overview

Backfilled the DQN experiences table from trade history and performed comprehensive data analysis to identify prompt improvements based on actual trade outcomes.

### DQN Experience Backfill

Created `dqn_experiences` table and backfilled all 22 closed trades with:
- 18-feature normalized state vectors
- Calculated reward values
- Market context (VIX, SPY trend)
- Portfolio state

**New DB Functions Added:**
| Function | Purpose |
|----------|---------|
| `backfill_dqn_experiences()` | Populate table from trade history |
| `get_dqn_training_data()` | Return experiences for DQN model training |
| `get_dqn_stats()` | Summary statistics for DQN data |

**DQN Stats After Backfill:**
| Metric | Value |
|--------|-------|
| Total Experiences | 22 |
| Avg Reward | -0.0483 |
| Best Reward | +0.1337 (KTOS) |
| Win Rate | 27.3% |

### Comprehensive Trade Analysis

**Overall Performance:**
- Total Closed: 22 trades
- Wins: 6 | Losses: 16
- Win Rate: **27.3%** (critically low)
- Total P/L: **-$595.12**
- Avg P/L: -0.55%

#### Key Finding 1: Same-Day Exits Are Catastrophic

| Hold Time | Count | Avg P/L | Win Rate |
|-----------|-------|---------|----------|
| Same Day (0d) | 12 | **-1.61%** | **8.3%** |
| 1+ Days | 10 | **+0.73%** | **50.0%** |

**Conclusion:** Same-day exits are the #1 performance killer. The minimum hold rule must be absolute.

#### Key Finding 2: RSI Filter Not Strict Enough

| RSI at Entry | Count | Avg P/L | Win Rate |
|--------------|-------|---------|----------|
| 80+ | 10 | -0.37% | 20.0% |
| 70-79 | 7 | -0.39% | 42.9% |
| 60-69 | 5 | -1.12% | 20.0% |

Despite RSI < 70 rule being added, 17/22 trades entered with RSI 70+.

**Candidate Outcome Data (next-day returns):**
| RSI Group | Candidates | Up Next Day | Avg Return |
|-----------|------------|-------------|------------|
| RSI < 70 | 358 | 50.3% | **+0.16%** |
| RSI > 70 | 225 | 40.0% | **-0.57%** |

**Conclusion:** RSI > 70 stocks significantly underperform. Need enforcement at scanner level.

#### Key Finding 3: Holding Period Matters

| Days Held | Count | Avg P/L | Win Rate |
|-----------|-------|---------|----------|
| 0 | 12 | -1.61% | 8.3% |
| 1 | 6 | -1.00% | 50.0% |
| 2 | 1 | +3.81% | 100% |
| 3 | 1 | -0.68% | 0% |
| 4 | 2 | +5.06% | 50% |

**Winners:** KTOS (4d, +10.59%), RKLB (2d, +3.81%), MU (1d, +2.15%)
**Losers:** ONDS (0d, -5.06%), WDC (1d, -4.64%), UUUU (0d, -4.11%)

**Conclusion:** Winners are held 2-4 days. Losers often exited same-day.

### Recommended Prompt & Scanner Updates

#### HIGH PRIORITY - Enforce RSI < 70 at Scanner Level

**Current State:** RSI filter only in agent prompt (advisory)
**Recommendation:** Add hard filter in `scanner.py` to exclude RSI >= 70 before candidates reach agent

```python
# In scanner.py Stage 2 filter
if rsi_14 >= 70:
    skip_reason = f"RSI overbought ({rsi_14:.0f})"
    continue  # Don't include in candidates
```

**Expected Impact:** Prevents agent from even seeing overbought stocks, removing temptation.

#### HIGH PRIORITY - Absolute Minimum Hold Rule

**Current Rule:** "Do NOT close on same day unless P/L < -5%"
**Problem:** Agent still closed 12 trades same-day
**Recommendation:** Remove the -5% exception for day 0

```
MINIMUM HOLD RULE (ABSOLUTE):
- Day 0: NEVER close. No exceptions. Let the trailing stop do its job.
- Day 1: Only close if P/L < -5%
- Day 2+: Normal exit criteria apply
```

**Expected Impact:** Eliminates 8.3% win rate same-day exits, forces trades to develop.

#### MEDIUM PRIORITY - Tighten RSI Entry to < 65

**Current Rule:** RSI < 70
**Recommendation:** RSI < 65 for new entries

**Data Support:** RSI 70-79 bucket had -0.39% avg P/L, only slightly better than 80+

```
BUY if:
- RSI < 65 (NOT approaching overbought)
- RSI 65-70: WATCH only (wait for pullback)
```

#### MEDIUM PRIORITY - Increase Volume Requirement

**Observation:** Winners had stronger volume
**Recommendation:** Increase minimum volume surge from 1.5x to 2.0x

```
BUY if:
- Volume surge >= 2.0x (was 1.5x)
```

#### LOW PRIORITY - 2-Day Evaluation Rule

**Recommendation:** Don't evaluate exit criteria until day 2

```
EXIT EVALUATION:
- Days 0-1: NO exit evaluation (trailing stop only protection)
- Day 2+: Evaluate reversal score, dead money, thesis break
```

### Implementation Priority

| Priority | Change | File | Expected Impact |
|----------|--------|------|-----------------|
| 1 | RSI < 70 hard filter in scanner | scanner.py | Prevent overbought entries |
| 2 | Absolute no-exit rule for day 0 | agent.py | Eliminate same-day exits |
| 3 | RSI < 65 for BUY, 65-70 WATCH | agent.py | Better entry timing |
| 4 | Volume >= 2.0x requirement | agent.py | Higher quality entries |
| 5 | 2-day evaluation delay | agent.py | Let trades develop |

### RSI Hard Filter Implementation

**Added RSI < 70 enforcement at scanner level** (not just advisory in prompt).

**Change in `scanner.py` (lines 466-498):**
```python
# Added RSI filter
rsi_ok = r.get("rsi_14", 50) < 70  # CRITICAL: Filter out overbought stocks
passes_filter = breakout and volume_ok and momentum_ok and rsi_ok

# Added to skip_reason logging
if not rsi_ok:
    skip_reason.append(f"overbought(RSI={r.get('rsi_14', 0):.0f})")
```

**Test Results:**
```
Stocks blocked by RSI filter:
  COST: RSI=81.5, Score=19 <- BLOCKED
  LMT: RSI=81.0, Score=14 <- BLOCKED
  HON: RSI=79.4, Score=19 <- BLOCKED
  RTX: RSI=78.3, Score=12 <- BLOCKED
  MU: RSI=74.3, Score=19 <- BLOCKED
```

These high-scoring but overbought stocks will no longer reach the agent.

### Comprehensive Logging Implementation

Added full error tracking and scan decision logging to database.

**New Database Tables:**

1. **`error_log`** - Tracks all errors with context
   - error_type: 'scan', 'trade', 'monitor', 'api', 'system'
   - operation: what was attempted
   - symbol, error_message, error_details
   - context: JSON with relevant state
   - resolved flag and resolution_notes

2. **`scan_decisions`** - Tracks agent decisions with reasoning
   - Filter stats (stage1/2 counts, filtered_by_rsi, etc.)
   - Agent actions (buys, watches, skips)
   - Execution results (executed, failed, errors)

**New Bot Commands:**

| Command | Description |
|---------|-------------|
| `/errorstatus` | Detailed error analysis from DB (by type, operation, common errors) |
| `/scandecisions` | Recent scan decisions with filter breakdown |

**Error Logging Added To:**
- `executor.py`: Trade buy/sell errors with full context
- `scanner.py`: Filter statistics tracking (RSI blocked count, etc.)

### Files Modified This Session

| File | Changes |
|------|---------|
| `db.py` | Added `dqn_experiences` table, `error_log` table, `scan_decisions` table, `log_error()`, `get_recent_errors()`, `get_error_summary()`, `log_scan_decision()`, `get_recent_scan_decisions()` |
| `scanner.py` | Added RSI < 70 hard filter, filter statistics tracking, `get_last_filter_stats()` |
| `executor.py` | Added error logging to `execute_trade()` and `close_position()` |
| `bot.py` | Added `/errorstatus` and `/scandecisions` commands |

### Verification for Tomorrow's Run

The scanner now enforces:
1. **RSI < 70** - Hard filter at scanner level (overbought stocks never reach agent)
2. Breakout pattern (gap + follow-through OR 5D breakout)
3. Volume surge >= 1.3x (1.5x for small caps)
4. ROC 10D >= 3% (5% for small caps)

### Next Steps

1. ~~Implement RSI hard filter in scanner.py~~ ✓ DONE
2. Monitor tomorrow's scan to verify RSI filter working in production
3. Consider making day-0 no-exit rule absolute (remove -5% exception)
4. Re-run DQN backfill after more trades accumulate

---

## Session 11: Performance Analysis & Critical Fixes (January 20, 2026)

### Overview

Comprehensive review identified the agent performing poorly with 27.3% win rate (6/16) and -$595 total P/L. Root cause analysis revealed multiple systemic issues that were fixed.

### Performance Metrics (Pre-Fix)

| Metric | Value | Assessment |
|--------|-------|------------|
| Win Rate | 27.3% | CRITICALLY LOW |
| Total Closed Trades | 22 | |
| Total P/L | -$595.12 | Negative |
| Avg P/L per Trade | -0.55% | |
| DQL Training Data | 0% outcome coverage | BROKEN |

### Root Causes Identified

1. **Agent cutting winners too early** - KTOS exited at +10.59% as "poor signal"
2. **Same-day exits destroying performance** - Multiple trades closed day of entry
3. **DQL training data completely broken** - 0/798 candidates with outcome data
4. **Skip-buy mode too restrictive** - Required 6 positions (max) to activate
5. **Entering overbought stocks** - RSI > 70 on most losing trades
6. **Agent recommending non-candidates** - Stocks from wrong cap scans

### Fixes Implemented

#### Fix 1: DQL Training Pipeline (CRITICAL)

**File**: `db.py` - `update_candidate_outcomes()`

**Problem**: Single-symbol API calls failing with "No item with that key" right after market close.

**Solution**:
- Batch API calls (50 symbols at a time) for efficiency
- Added fallback to historical bars API when snapshots fail
- Added warning when running too soon after market close

**Code Changes**:
```python
# Before: Single symbol queries
for candidate in candidates:
    snapshot = data_client.get_stock_snapshot(request)

# After: Batched queries with fallback
for i in range(0, len(symbols), batch_size):
    batch = symbols[i:i + batch_size]
    snapshots = data_client.get_stock_snapshot(request)
    # Plus: fallback to StockBarsRequest for missing symbols
```

#### Fix 2: Minimum Hold Time Rule (CRITICAL)

**File**: `agent.py` - System prompt

**Problem**: Positions being closed same day as entry, destroying momentum thesis.

**Solution**: Added explicit rule preventing same-day exits unless P/L < -5%.

**New Rules**:
```
MINIMUM HOLD TIME RULE (CRITICAL):
- Do NOT close positions on the SAME DAY as entry unless P/L < -5%
- Momentum trades need time to develop - same-day exits destroy performance
```

#### Fix 3: Let Winners Run (CRITICAL)

**File**: `agent.py` - System prompt

**Problem**: Winning positions (+5% to +15%) cut early due to "failed breakout" patterns.

**Solution**: Revised exit criteria to favor holding winners.

**Changes**:
| Rule | Before | After |
|------|--------|-------|
| Take profit | > +15% | > +20% |
| Cut loss threshold | < -3% with rev >= 3 | < -5% (any time) OR < -3% with rev >= 4 AND held >= 2 days |
| Winner handling | Often cut at +5-10% | HOLD if +5% to +20% - let it run |

**Added Explicit Warning**:
```
IMPORTANT: Do NOT exit winning positions (+5% to +20%) just because
you see a "failed breakout" pattern. Winners should run. The 5%
trailing stop protects downside.
```

#### Fix 4: RSI Entry Filter (HIGH)

**File**: `agent.py` - System prompt + `format_candidates_for_prompt()`

**Problem**: Entering overbought stocks (RSI > 70) that reversed immediately.

**Analysis of Losers**:
| Symbol | Entry RSI | Result |
|--------|-----------|--------|
| FCX | 83.4 | -1.05% |
| WDC | 75.2 | -4.64% |
| ZETA | 82.9 | -1.53% |

**Solution**:
- Added mandatory RSI < 70 filter for BUY decisions
- RSI now prominently displayed in candidate prompt
- Visual warnings for RSI >= 65 (⚡ HIGH) and >= 70 (⚠️ OVERBOUGHT)

**New Rule**:
```
BUY if:
- Score >= 12 AND momentum_breakout = True
- **RSI < 70** (NOT overbought) - this is mandatory
```

#### Fix 5: Dead Money Revision (MEDIUM)

**File**: `agent.py` - System prompt

**Problem**: Positions classified as "dead money" within hours of entry.

**Solution**: Dead money rule only applies after 10+ days with P/L between -2% and +3%.

**Clarification Added**:
```
HOLD if (DEFAULT - prefer holding):
- Held < 3 days - let the trade develop unless P/L < -5%
```

#### Fix 6: Skip-Buy Threshold (MEDIUM)

**File**: `config.py` - `MONITOR_CONFIG`

**Problem**: `min_positions_for_skip` was 6 (max positions), meaning skip-buy mode never activated until portfolio was full.

**Solution**: Reduced threshold to 4 positions.

```python
# Before
"min_positions_for_skip": 6,  # Never activated

# After
"min_positions_for_skip": 4,  # Activates with 4+ healthy positions
```

#### Fix 7: Constrain Agent Recommendations (MEDIUM)

**File**: `agent.py` - System prompt + `format_candidates_for_prompt()`

**Problem**: Agent recommending HON during mid-cap scan when HON was only in large-cap candidates.

**Solution**: Added explicit constraint in prompt.

```
CRITICAL: You can ONLY recommend stocks from the NEW CANDIDATES list below.
Do NOT recommend stocks from watchlist or previous scans - only current scan candidates.
```

**Validation**: Candidates list now includes reminder header.

#### Fix 8: Days Held in Position Display

**File**: `agent.py` - `format_positions_for_prompt()`

**Problem**: Agent couldn't see how long positions were held, leading to premature exits.

**Solution**: Added days held calculation and same-day warning.

```python
lines.append(f"  - **Days Held: {days_held}**")
hold_warning = "⚠️ SAME DAY - hold unless -5%" if days_held == 0 else ""
```

### Files Modified

| File | Changes |
|------|---------|
| `db.py` | Rewrote `update_candidate_outcomes()` with batching and fallback |
| `agent.py` | Major system prompt rewrite, added RSI display, days held calculation |
| `config.py` | Changed `min_positions_for_skip` from 6 to 4 |
| `jobs.py` | Added market close timing check for `update_outcomes()` |

### Expected Improvements

| Issue | Fix | Expected Impact |
|-------|-----|-----------------|
| 0% outcome data | Batched API + fallback | 90%+ coverage |
| Same-day exits | Minimum hold rule | Eliminate churning |
| Cutting winners | Revised exit criteria | Let +5-15% run |
| Overbought entries | RSI < 70 filter | Fewer immediate reversals |
| Skip-buy never activating | Threshold 6→4 | Better capital deployment |

### Post-Fix Validation

To validate fixes, monitor:
```bash
# Check DQL outcome coverage after next job run
./venv/bin/python3 -c "
import sqlite3
conn = sqlite3.connect('data/trades.db')
cursor = conn.cursor()
cursor.execute('SELECT COUNT(*) FROM candidate_snapshots WHERE price_1d_later IS NOT NULL')
with_outcomes = cursor.fetchone()[0]
cursor.execute('SELECT COUNT(*) FROM candidate_snapshots')
total = cursor.fetchone()[0]
print(f'Outcome coverage: {with_outcomes}/{total} ({100*with_outcomes/total:.1f}%)')
"

# Run outcome update manually
./venv/bin/python jobs.py update_outcomes
```

### Agent Prompt Changes Summary

**Entry Rules**:
- ✅ RSI < 70 mandatory
- ✅ Only recommend from current candidates list
- ✅ Score >= 12 with breakout

**Exit Rules**:
- ✅ No same-day exits unless P/L < -5%
- ✅ Hold winners (+5% to +20%)
- ✅ Reversal score >= 5 still triggers exit
- ✅ Dead money only after 10+ days

---

## Session 10: Code Review, Conflict Resolution & Self-Learning Loop (January 6, 2026)

### Overview

Comprehensive code review to identify conflicts, position limit issues, and order errors. Implemented self-learning loop for poor signal tracking and skip-buy logic when positions are healthy.

### Code Review Findings

#### HIGH Severity Issues (Fixed)

| Issue | Location | Fix |
|-------|----------|-----|
| Agent prompt said "3 positions" but config is 6 | `agent.py:34` | Updated to "6 concurrent positions" |
| Agent prompt said "50% exposure" but config is 60% | `agent.py:36` | Updated to "60% maximum total exposure" |
| No locking mechanism for concurrent scans | `main.py` | Added `ScanLock` class with file-based locking |
| Trailing stop failures had no retry | `executor.py` | Added 3-attempt retry with 1s delay |

#### MEDIUM Severity Issues (Fixed)

| Issue | Location | Fix |
|-------|----------|-----|
| Agent unaware of per-cap limits | `agent.py:38` | Added "Per-cap limits: Max 2 large cap, 2 mid cap, 2 small cap" |
| Trade exit not logged to DB | `executor.py:314-330` | Added `update_trade_exit()` call in `close_position()` |
| Critical alert missing for stop failures | `executor.py:214-217` | Added clear warning with manual intervention guidance |

### New Features Implemented

#### 1. Self-Learning Loop

**Purpose:** Track trades that triggered reversal exits to identify patterns in poor entry signals.

**Components:**

| Component | Description |
|-----------|-------------|
| `poor_signals` table | Stores trades closed due to reversal with entry/exit signals |
| `log_poor_signal()` | Records poor signal when reversal triggers close |
| `get_poor_signal_summary()` | Aggregates patterns for reports |
| `format_poor_signals_for_prompt()` | Formats patterns for agent context |

**Agent Integration:**
- Added `SELF-LEARNING LOOP` section to system prompt
- Agent receives recent poor signal patterns before making BUY decisions
- Instruction: "Check poor signal patterns above before recommending BUY on any candidate"

**Weekly Reports:**
- Poor signal summary added to `/weekly` and `/monthly` commands
- Shows common reversal triggers and problematic entry signals
- Includes "Action: Review agent prompt for signal quality" reminder

#### 2. Skip-Buy Logic

**Purpose:** Don't interfere with winning positions by taking new trades.

**Configuration:**
```python
MONITOR_CONFIG = {
    "skip_buys_when_healthy": True,   # Enable skip-buy mode
    "healthy_threshold": 3,           # Reversal score below which position is "healthy"
}
```

**Behavior:**
- When all positions have reversal score < threshold → positions are "healthy"
- Scans still run and results are logged
- New buys are **skipped** to let winners run
- Telegram summary indicates "SKIP MODE" when active

#### 3. Scan Locking

**Purpose:** Prevent concurrent scans from interfering with each other.

**Implementation:**
```python
class ScanLock:
    """File-based lock using fcntl to prevent concurrent scans"""
    LOCK_FILE = "/tmp/momentum_scan.lock"

    def acquire(self, timeout: int = 0) -> bool
    def release(self)
```

**Behavior:**
- Uses `fcntl.LOCK_EX | fcntl.LOCK_NB` for non-blocking exclusive lock
- Only one scan can run at a time across all cron jobs
- Lock automatically released when scan completes or errors

#### 4. Trailing Stop Retry Logic

**Purpose:** Ensure positions always have stop protection.

**Implementation:**
```python
# Retry trailing stop placement up to 3 times
for attempt in range(3):
    try:
        stop_order = client.submit_order(trailing_stop)
        break  # Success
    except Exception as stop_err:
        print(f"Warning: Trailing stop attempt {attempt + 1} failed: {stop_err}")
        if attempt < 2:
            time.sleep(1)  # Wait before retry

if stop_order is None:
    print(f"⚠️ CRITICAL: Position {symbol} has NO trailing stop protection!")
```

### PDT (Pattern Day Trading) Recommendation

**Question:** Should we use separate accounts per market cap or one $30k+ account?

**Recommendation:** **Single account with $30k+ equity**

| Factor | Separate Accounts | Single Account |
|--------|-------------------|----------------|
| Capital efficiency | Poor (split across 3) | Good (full deployment) |
| Per-cap limits | Already enforced in code | Already enforced in code |
| Maintenance | 3x complexity | Simple |
| PDT risk | Still possible per account | Avoided with $25k+ |

The code already enforces per-cap position limits (2 per cap, 6 total), so separate accounts add no safety benefit while reducing capital efficiency.

### Files Modified

| File | Changes |
|------|---------|
| `config.py` | Added `skip_buys_when_healthy`, `healthy_threshold` to MONITOR_CONFIG |
| `db.py` | Added `poor_signals` table, `log_poor_signal()`, `get_poor_signal_summary()`, `mark_poor_signal_reviewed()` |
| `executor.py` | Added `reversal_signals` param to `close_position()`, poor signal logging, trailing stop retry logic |
| `monitor.py` | Pass `reversal_signals` to `close_position()` |
| `main.py` | Added `ScanLock` class, skip-buy logic when positions healthy |
| `bot.py` | Added poor signal summary to weekly/monthly reports |
| `agent.py` | Added `SELF-LEARNING LOOP` section, `format_poor_signals_for_prompt()`, updated position limits text |

### Database Schema Addition

```sql
CREATE TABLE IF NOT EXISTS poor_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    trade_id INTEGER,
    entry_date TEXT NOT NULL,
    exit_date TEXT NOT NULL,
    entry_price REAL,
    exit_price REAL,
    pnl_pct REAL,
    holding_days INTEGER,
    exit_reason TEXT,
    reversal_score INTEGER,
    reversal_signals TEXT,  -- JSON
    entry_signals TEXT,     -- JSON
    composite_score INTEGER,
    notes TEXT,
    reviewed INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (trade_id) REFERENCES trades(id)
);
```

### Testing

Verify all imports work correctly:
```bash
python -c "import db; import agent; import main; print('All imports OK')"
```

---

## Session 9: Per-Cap Scanning & Position Limits (January 6, 2026)

### Overview

Implemented separate scanning for large/mid/small cap stocks, per-cap position limits, cap-specific thresholds, and runtime configuration via bot commands.

### Key Changes

#### 1. Per-Cap Configuration (`config.py`)

Added `CAP_CONFIG` with per-cap thresholds and limits:

```python
CAP_CONFIG = {
    "large": {
        "max_positions": 2,
        "max_buys_per_scan": 2,
        "min_volume_surge": 1.3,
        "min_gap_up": 0.01,      # 1% gap
        "min_roc_10d": 0.03,     # 3% ROC
    },
    "mid": {
        "max_positions": 2,
        "max_buys_per_scan": 2,
        "min_volume_surge": 1.3,
        "min_gap_up": 0.01,
        "min_roc_10d": 0.03,
    },
    "small": {
        "max_positions": 2,
        "max_buys_per_scan": 2,
        "min_volume_surge": 1.5,  # Higher for small caps
        "min_gap_up": 0.03,       # 3% gap (higher)
        "min_roc_10d": 0.05,      # 5% ROC (higher)
    },
}
```

**Small caps require higher thresholds** to filter out noise and ensure stronger momentum signals.

#### 2. Universe Classification (`data/universe.json`)

Restructured universe file with cap classification:

| Category | Count | Market Cap |
|----------|-------|------------|
| Large | 108 | > $10B |
| Mid | 61 | $2B - $10B |
| Small | 42 | < $2B |

```json
{
  "symbols": {
    "large": ["AAPL", "MSFT", "GOOGL", ...],
    "mid": ["PYPL", "SQ", "SNAP", ...],
    "small": ["LYFT", "HOOD", "SOFI", ...]
  }
}
```

#### 3. CLI Parameters (`main.py`)

Added `--cap` and `--max-buys` parameters:

```bash
# Run scan for large caps only
./venv/bin/python main.py scan --type open --cap large

# Run scan with custom max buys
./venv/bin/python main.py scan --type open --cap small --max-buys 1
```

#### 4. Position Limits (`executor.py`)

Updated position limit enforcement:

| Limit | Value | Description |
|-------|-------|-------------|
| Total Max | 6 | Maximum concurrent positions |
| Per-Cap Max | 2 | Maximum positions per cap category |
| Per-Scan Max | 2 | Maximum buys per scan (from config) |

```python
# Check total position limit
if len(unique_symbols) >= max_total_positions:
    return {"success": False, "error": f"Max total positions ({max_total_positions}) reached"}

# Check per-cap position limit
cap_positions = sum(1 for p in positions if get_symbol_cap(p.symbol) == cap)
if cap_positions >= max_cap_positions:
    return {"success": False, "error": f"Max {cap} cap positions ({max_cap_positions}) reached"}
```

#### 5. Scanner Cap Filtering (`scanner.py`)

Scanner now applies cap-specific thresholds in Stage 2:

```python
def run_scan(cap: str = None):
    cap_config = get_cap_config(cap)
    min_volume_surge = cap_config["min_volume_surge"]
    min_gap_up = cap_config["min_gap_up"] * 100
    min_roc_10d = cap_config["min_roc_10d"] * 100

    # Filter universe by cap
    symbols = load_universe(cap=cap)
```

#### 6. Cron Schedule (9 Scans/Day)

Updated to run 3 caps × 3 time slots:

| Time (ET) | Large | Mid | Small |
|-----------|-------|-----|-------|
| 9:35-9:41 | 14:35 UTC | 14:38 UTC | 14:41 UTC |
| 12:30-12:36 | 17:30 UTC | 17:33 UTC | 17:36 UTC |
| 3:30-3:36 | 20:30 UTC | 20:33 UTC | 20:36 UTC |

```cron
# Market Open
35 14 * * 1-5 ./venv/bin/python main.py scan --type open --cap large
38 14 * * 1-5 ./venv/bin/python main.py scan --type open --cap mid
41 14 * * 1-5 ./venv/bin/python main.py scan --type open --cap small

# Midday
30 17 * * 1-5 ./venv/bin/python main.py scan --type midday --cap large
33 17 * * 1-5 ./venv/bin/python main.py scan --type midday --cap mid
36 17 * * 1-5 ./venv/bin/python main.py scan --type midday --cap small

# Pre-Close
30 20 * * 1-5 ./venv/bin/python main.py scan --type close --cap large
33 20 * * 1-5 ./venv/bin/python main.py scan --type close --cap mid
36 20 * * 1-5 ./venv/bin/python main.py scan --type close --cap small
```

#### 7. Runtime Configuration (`bot.py`)

Added `/settings` and `/set` commands for runtime config:

```
/settings - View current monitor settings
/set autoclose on|off - Enable/disable auto-close
/set threshold N - Set auto-close threshold (3-10)
/set alerts on|off - Enable/disable reversal alerts
```

Config stored in `data/runtime_config.json`.

### Files Modified

| File | Changes |
|------|---------|
| `config.py` | Added `CAP_CONFIG`, `get_cap_config()`, runtime config functions |
| `data/universe.json` | Restructured with cap classification (large/mid/small) |
| `main.py` | Added `--cap` and `--max-buys` CLI parameters |
| `executor.py` | Added `get_symbol_cap()`, per-cap position limit checks |
| `scanner.py` | Added cap filtering, cap-specific threshold application |
| `monitor.py` | Updated to use runtime config for auto-close settings |
| `bot.py` | Added `/settings` and `/set` commands |
| `README.md` | Added Per-Cap Configuration, Universe Classification sections |
| Crontab | Updated to 9 separate scans (3 caps × 3 times) |

### Updated Risk Management

| Rule | Old Value | New Value |
|------|-----------|-----------|
| Max Positions | 3 | **6** |
| Position Size | 10% | 10% |
| Max Exposure | 50% | **60%** |
| Per-Cap Max | N/A | **2** |
| Per-Scan Max | N/A | **2** (from config) |
| Trailing Stop | 5% | 5% |

### Cap-Specific Thresholds

| Filter | Large/Mid | Small |
|--------|-----------|-------|
| Gap Up | >= 1% | >= 3% |
| Volume Surge | >= 1.3x | >= 1.5x |
| ROC 10D | >= 3% | >= 5% |

---

## Session 8: Bug Fixes, Auto-Close & Trailing Stop Consolidation (January 6, 2026)

### Overview

Fixed database schema issues blocking trades, added `/error` command, implemented auto-close on strong reversal signals, and consolidated trailing stops to cover entire positions.

### Issues Fixed

**1. Database Schema Migration**

Trades were failing with:
```
✗ Failed to enter FCX: table trades has no column named spy_price
✗ Failed to enter LMT: table trades has no column named spy_price
```

**Root Cause:** SQLite's `CREATE TABLE IF NOT EXISTS` doesn't add new columns to existing tables.

**Fix:** Added `migrate_tables()` function to `db.py` that automatically adds missing columns:
- 25 new columns added to `trades` table (DQL training fields)
- Migration runs automatically on each database connection

**2. Partial Trailing Stops**

Positions had incomplete trailing stop coverage (e.g., 232 shares but only 165 protected).

**Root Cause:** Each entry only created a trailing stop for that entry's shares.

**Fix:** Modified `executor.py` to:
1. Cancel existing trailing stops when adding to a position
2. Place new trailing stop covering **entire position** (old + new shares)

### New Features

**1. `/error` Command**

New Telegram command to view recent errors from all log files:
- Scans `scan.log`, `jobs.log`, `monitor.log`
- Shows last 10 unique errors with source
- Truncates long error messages

**2. Auto-Close on Strong Reversal**

Monitor now automatically closes positions on strong reversal signals:

| Score | Action |
|-------|--------|
| 0-2 | Logged only |
| 3-4 | Telegram alert (manual close) |
| **5+** | **AUTO-CLOSE** + notification |

Configuration in `monitor.py`:
```python
AUTO_CLOSE_ENABLED = True   # Set False to disable
AUTO_CLOSE_THRESHOLD = 5    # Score threshold
```

### Files Modified

| File | Changes |
|------|---------|
| `db.py` | Added `migrate_tables()` function, called from `get_connection()` |
| `executor.py` | Consolidated trailing stops, improved max position check |
| `monitor.py` | Added auto-close logic, `AUTO_CLOSE_ENABLED`, `AUTO_CLOSE_THRESHOLD` |
| `bot.py` | Added `/error` command and handler |
| `README.md` | Updated exit flow, monitoring flow, added `/error` command |

### Code Changes Detail

**executor.py - Trailing Stop Consolidation:**
```python
# Before adding shares:
# 1. Cancel existing trailing stops for symbol
# 2. Buy new shares
# 3. Place trailing stop for ENTIRE position (old + new)
total_position_qty = int(existing_qty + filled_qty)
trailing_stop = TrailingStopOrderRequest(
    symbol=symbol,
    qty=total_position_qty,  # Full position
    ...
)
```

**monitor.py - Auto-Close:**
```python
if result["score"] >= AUTO_CLOSE_THRESHOLD and AUTO_CLOSE_ENABLED:
    close_result = close_position(symbol, reason=f"auto_reversal_score_{result['score']}")
    send_telegram_alert(..., auto_closed=True)
elif result["score"] >= 3:
    send_telegram_alert(..., auto_closed=False)  # Alert only
```

### Current State

After fixes, successful trade execution:
```
✓ Entered MU: 17 shares @ ~$331.99
  Trailing stop: 5.0%
```

Current positions:
- FCX: 232 shares (-1.5%)
- MU: 39 shares (-0.1%)

---

## Session 7: Universe Update & Test Scan Verification (January 6, 2026)

### Overview

Expanded stock universe and verified all systems ready for trading.

### Universe Changes

Updated `data/universe.json` from 184 to **211 stocks**:

**Added (27 symbols):**
- HIMS, CVNA, RDDT, WDC, STX, TEM, BBAI, SOUN, ARKQ, TSM
- ONON, SOFI, GOGO, NCNO, GLBE, NEM, GOLD, OXY, DVN, ZETA
- SMR, LEU, CEG, SYM, PL, VSAT, ONDS

**Fixed:**
- TSMC → TSM (correct Alpaca symbol)

**Removed:**
- GLLB (not available on Alpaca)

### Symbol Validation

All 211 symbols validated against Alpaca API - confirmed tradeable.

### Test Scan Results (January 6, 2026)

```
Universe: 211 stocks
Stage 1: 146 passed quick filter
Stage 2: 26 passed all filters

Market Context:
  SPY: $687.72 (+0.7%), Trend: sideways
  VIX: 23.6
  Market Breadth: 77% advancing

Top Candidates:
  KLAC: Score=20, ROC=10.64%, VolSurge=16.3x
  AMAT: Score=20, ROC=12.16%, VolSurge=17.0x
  CVX:  Score=20, ROC=10.94%, VolSurge=43.3x
  FCX:  Score=20, ROC=13.54%, VolSurge=18.5x
  LRCX: Score=19, ROC=18.25%, VolSurge=15.9x
```

### Database Verification

| Table | Records |
|-------|---------|
| market_snapshots | 2 |
| candidate_snapshots | 100 |
| trades | 0 |
| daily_performance | 0 |

All DQL training data collection working correctly.

### Files Modified

| File | Change |
|------|--------|
| `data/universe.json` | 184 → 211 symbols |

---

## Session 6: DQL Training Data Collection & Performance Metrics (January 6, 2026)

### Overview

Enhanced the momentum agent with comprehensive DQL (Deep Q-Learning) training data collection and performance metrics tracking.

### New Database Schema

Added 3 new tables for DQL training:

| Table | Purpose |
|-------|---------|
| `market_snapshots` | SPY/VIX context at each scan |
| `candidate_snapshots` | ALL candidates (traded + skipped) with outcomes |
| `daily_performance` | Daily equity, trades, SPY comparison |

Enhanced `trades` table with:
- Market context (SPY price, trend, VIX)
- Full signal data (ATR, RSI, ROC)
- Portfolio state (cash %, exposure)
- DQL fields (state_vector, reward, max_gain/drawdown)

### New Telegram Commands

| Command | Description |
|---------|-------------|
| `/metrics` | Baseline performance since inception |
| `/weekly` | Last 7 days report with SPY comparison |
| `/monthly` | Last 30 days with weekly breakdown |
| `/export` | Export trades & candidates to CSV |

### Background Jobs (jobs.py)

| Job | Schedule | Purpose |
|-----|----------|---------|
| `daily_snapshot` | 4:05 PM ET | Log daily performance |
| `update_outcomes` | 4:10 PM ET | Fill in price_Xd_later for candidates |
| `update_tracking` | Every 30min | Track max gain/drawdown for open positions |
| `cleanup` | Sunday midnight | Remove data older than 90 days |

### Scanner Enhancements

- Added market context fetching (SPY price, trend, SMA20, VIX proxy)
- Added market breadth calculation (% advancing)
- Logs ALL candidates to database (not just filtered ones)
- Added ATR-14 and RSI-14 calculations

### Files Modified

| File | Changes |
|------|---------|
| `db.py` | New schema, metrics functions, DQL helpers |
| `scanner.py` | Market context, candidate logging, ATR/RSI |
| `bot.py` | New /metrics, /weekly, /monthly, /export commands |
| `jobs.py` | NEW - Background tasks for DQL data |
| `crontab` | Added job schedules |

### Cron Schedule (UTC)

```
# Trading Scans
35 14 * * 1-5  scan --type open    # 9:35 AM ET
30 17 * * 1-5  scan --type midday  # 12:30 PM ET
30 20 * * 1-5  scan --type close   # 3:30 PM ET

# DQL Jobs
5 21 * * 1-5   daily_snapshot      # 4:05 PM ET
10 21 * * 1-5  update_outcomes     # 4:10 PM ET
*/30 14-21 * * 1-5  update_tracking
0 0 * * 0      cleanup             # Sunday midnight
```

---

## Session 5: Bug Fixes - Close Position & Error Handling (January 6, 2026)

### Problems Identified

1. **RDW Close Failed (Jan 2 17:30)**:
   - Error: `"insufficient qty available for order (requested: 1518, available: 0)"`
   - All shares were held by trailing stop order
   - 0.5 second delay after cancellation wasn't enough for Alpaca to process

2. **Cascading Failures**:
   - When close failed, agent still tried to buy (blocked by max positions)
   - Multiple "Would exceed max portfolio risk" errors

3. **/scan Command Issues**:
   - Errors not being logged with full traceback
   - Difficult to debug failures

### Fixes Applied

#### 1. Improved Position Close (`executor.py`)

Increased post-cancellation wait from 0.5s to up to 5s with verification loop:

```python
# Before: time.sleep(0.5)

# After: Wait up to 5s and verify shares are available
max_wait = 5  # seconds
wait_interval = 0.5
elapsed = 0

while elapsed < max_wait:
    time.sleep(wait_interval)
    elapsed += wait_interval
    try:
        updated_position = client.get_open_position(symbol)
        break  # Shares available
    except Exception:
        continue  # Still locked
```

#### 2. Better Error Logging (`bot.py`)

Added full traceback logging to /scan command:

```python
except Exception as e:
    import traceback
    error_details = traceback.format_exc()
    logger.error(f"Scan error: {e}\n{error_details}")
```

### Files Modified

| File | Change |
|------|--------|
| `executor.py` | Increased wait time after order cancellation, added verification loop |
| `bot.py` | Added traceback logging to /scan error handler |

---

## Session 4: Increase Max Portfolio Exposure (January 6, 2026)

### Problem Identified

Scan logs from Jan 2-5 showed repeated trade failures:

```
✗ Failed to enter UUUU: Would exceed max portfolio risk (30.0%)
✗ Failed to enter CVX: Would exceed max portfolio risk (30.0%)
✗ Failed to enter KTOS: Would exceed max portfolio risk (30.0%)
✗ Failed to enter SLB: Would exceed max portfolio risk (30.0%)
```

With 30% max exposure and 10% position sizing, the agent could only hold 2-3 positions before hitting the limit. Single positions with slight appreciation would block new entries entirely.

### Solution

Increased `max_portfolio_risk` from 30% to 50% to allow fuller deployment of capital while maintaining position diversity.

### Files Modified

| File | Line | Change |
|------|------|--------|
| `config.py` | 32 | `max_portfolio_risk`: `0.30` → `0.50` |
| `agent.py` | 36 | Agent prompt: `30%` → `50%` max exposure |
| `README.md` | 127 | Documentation updated |

### Updated Risk Management

| Rule | Old Value | New Value |
|------|-----------|-----------|
| Max Positions | 3 | 3 (unchanged) |
| Position Size | 10% | 10% (unchanged) |
| **Max Exposure** | **30%** | **50%** |
| Trailing Stop | 5% | 5% (unchanged) |

### Impact

- Agent can now deploy up to 50% of portfolio (~$66k of $132k)
- Enables 5 concurrent positions at 10% each
- Better capital utilization during strong momentum environments

---

## Session 3: Autonomous Agent Implementation (16:30-17:30 UTC)

### Objective

Transform the system from bot-driven to fully autonomous operation where Claude agent manages the entire portfolio autonomously with 3x daily scans.

### Architecture Before vs After

**Before:**
```
Scan → Bot (manual trigger) → Single candidate → Execute
```

**After:**
```
Cron → Scan → Get Positions + Reversal Scores → Claude Agent → Execute Closes/Buys → Telegram Summary
```

### Changes Made

#### 1. Enhanced Agent Prompt (`agent.py`)

Completely rewrote the system prompt for autonomous portfolio management:

- **Position Awareness**: Agent now sees all open positions with reversal scores
- **Decision Framework**:
  - CLOSE: Reversal score ≥5, P/L >+15%, or thesis breaking
  - HOLD: Momentum intact, reversal score <3
  - BUY: Score ≥12, momentum_breakout=True, have slot available
  - WATCH: Good setup but not ready (score 8-11)
  - SKIP: Weak setup, already holding, low conviction

- **New Function**: `get_portfolio_decision(account, positions, candidates, reversal_scores, scan_type)`
- **Watchlist Management**: Agent maintains pipeline of 3-5 quality setups

#### 2. Database Watchlist Support (`db.py`)

Added new table and functions:

```python
# New table
CREATE TABLE watchlist (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL UNIQUE,
    added_date TEXT,
    score INTEGER,
    reason TEXT
)

# New functions
get_watchlist() -> list[dict]
save_watchlist(watchlist: list[dict])
add_to_watchlist(symbol, score, reason)
remove_from_watchlist(symbol)
log_position_check(symbol, score, signals, pnl_pct, alert_sent)
```

#### 3. Autonomous Main Loop (`main.py`)

Rewrote `run_autonomous_scan()` with 5-step autonomous loop:

```python
def run_autonomous_scan(scan_type: str = "open", dry_run: bool = False):
    # [1/5] Fetch account and positions
    # [2/5] Calculate reversal scores for positions
    # [3/5] Run momentum scanner for new candidates
    # [4/5] Get Claude agent decision
    # [5/5] Execute decisions (closes first, then buys)
    # Send Telegram summary
```

- Supports `--type open|midday|close` flags
- Supports `--dry-run` for testing
- Sends structured Telegram summaries with emoji indicators

#### 4. Cron Schedule (3x Daily)

Updated `/tmp/newcron`:

| Scan | Time (ET) | Time (UTC) | Purpose |
|------|-----------|------------|---------|
| Open | 9:35 AM | 14:35 | Gap-up breakouts |
| Midday | 12:30 PM | 17:30 | Momentum continuation |
| Close | 3:30 PM | 20:30 | Pre-close review |

```cron
35 14 * * 1-5 cd /home/ubuntu/momentum-agent && ./venv/bin/python main.py scan --type open >> logs/scan.log 2>&1
30 17 * * 1-5 cd /home/ubuntu/momentum-agent && ./venv/bin/python main.py scan --type midday >> logs/scan.log 2>&1
30 20 * * 1-5 cd /home/ubuntu/momentum-agent && ./venv/bin/python main.py scan --type close >> logs/scan.log 2>&1
```

### Files Modified

| File | Change |
|------|--------|
| `agent.py` | Complete rewrite with autonomous portfolio prompt |
| `agent.py` | Added `get_portfolio_decision()` function |
| `agent.py` | Added watchlist integration in response handling |
| `db.py` | Added `watchlist` table |
| `db.py` | Added `position_checks` table |
| `db.py` | Added watchlist CRUD functions |
| `main.py` | Rewrote `run_autonomous_scan()` with 5-step loop |
| `main.py` | Added scan type and dry-run support |
| `main.py` | Added Telegram summary formatting |
| `/tmp/newcron` | Added 3x daily scan schedule |

### Agent Response Format

```json
{
  "market_assessment": "2-3 sentence market read",
  "position_actions": [
    {"symbol": "XXX", "action": "HOLD|CLOSE", "reversal_score": 0, "reasoning": "..."}
  ],
  "candidate_actions": [
    {"symbol": "XXX", "action": "BUY|WATCH|SKIP", "score": 15, "conviction": 0.85, "reasoning": "..."}
  ],
  "execution_plan": {
    "closes": ["SYM1"],
    "buys": ["SYM2"],
    "new_watchlist": ["SYM3", "SYM4"]
  },
  "portfolio_summary": "Brief summary of actions"
}
```

### Dry Run Test Results

```
======================================================================
MOMENTUM AGENT - Autonomous OPEN Scan
Time: 2026-01-02 17:15:00
Mode: DRY RUN
======================================================================

[1/5] Fetching account and positions...
  Account Equity: $XX,XXX.XX
  Buying Power: $XX,XXX.XX
  Current Positions: 0

[2/5] Calculating reversal scores for positions...
  (No positions to check)

[3/5] Running momentum scanner...
  Found 12 candidates:
    MU: Score=20, ROC=+36.2%, Vol=3.1x
    LUNR: Score=20, ROC=+68.2%, Vol=3.3x
    ...

[4/5] Getting Claude agent decision...
  Market Assessment: Strong momentum environment with multiple high-quality breakouts...
  Execution Plan:
    CLOSE: None
    BUY: ['MU', 'RDW']
    WATCHLIST: ['BA', 'ENPH']

[5/5] Executing decisions...
  [DRY RUN] Would buy: MU @ $308.76
  [DRY RUN] Would buy: RDW @ $8.64

======================================================================
Scan complete.
======================================================================
```

### Risk Management (Built into Agent)

| Rule | Value |
|------|-------|
| Max Positions | 3 |
| Position Size | 10% of portfolio |
| Max Exposure | 50% of portfolio |
| Trailing Stop | 5% (set on entry) |

### Prime Objective

> Grow capital with exceptional results through momentum trading.

Agent prioritizes:
1. Capital preservation - protect downside aggressively
2. Let winners run - don't cut early unless reversal signals
3. Quality over quantity - better to miss than force bad trades
4. Clear reasoning - every action must make sense tomorrow

---

## Session 2: Scanner Overhaul (15:15-16:00 UTC)

### Problem Identified

Scans returning 0 candidates despite 170 stocks in universe and many stocks up 5%+.

**Root Causes Found:**

1. **Stage 1 (Quick Filter)**: Compared partial-day volume to full-day volume
   ```python
   # OLD (broken)
   if curr_volume > prev_volume:  # Always fails early in day
   ```

2. **Stage 2 (Deep Analysis)**: `close_position` filter checked last 2 *completed* days
   - Stocks like LRCX (up 6%, RVOL 1.5x, ROC +16%) rejected because yesterday closed weak
   - Missed breakouts happening TODAY

### Solution Implemented

**Stage 1 Fix**: Time-normalized Relative Volume (RVOL)
```python
time_fraction = calculate_time_fraction()  # e.g., 0.22 at 10:30 AM
projected_volume = curr_volume / time_fraction
rvol = projected_volume / prev_volume
if rvol >= 1.2:  # 20%+ above normal pace
```

**Stage 2 Fix**: Replaced `close_position` with `momentum_breakout`
```python
# OLD: close_position >= 0.6 (backward-looking, missed breakouts)

# NEW: momentum_breakout (uses today's data)
gap_up = (today.open - yesterday.close) / yesterday.close
follow_through = today.close > today.open
breakout_5d = today.close > max(5_day_highs)
momentum_breakout = ((gap_up > 0.01 and follow_through) or breakout_5d) and volume_surge >= 1.3
```

### Changes Made

| File | Change |
|------|--------|
| `scanner.py` | Added `calculate_time_fraction()` function |
| `scanner.py` | Rewrote `quick_filter_snapshots()` with RVOL logic |
| `scanner.py` | Replaced `close_position` with `momentum_breakout` in `calculate_signals()` |
| `scanner.py` | Added new fields: `gap_up`, `follow_through`, `breakout_5d`, `intraday_strength` |
| `scanner.py` | Updated Stage 2 filter to use `momentum_breakout` |
| `scanner.py` | Removed `sma_aligned` requirement (catches breakouts before SMA alignment) |
| `README.md` | Added Stage 1 RVOL documentation |
| `README.md` | Updated Stage 2 filter documentation |

### Filter Comparison

**Stage 1 Quick Filter:**

| Filter | Old | New |
|--------|-----|-----|
| Price | > $5 | > $5 |
| Price Change | > 1% | > 0.5% |
| Volume | curr > prev (broken) | RVOL >= 1.2x |

**Stage 2 Deep Filter:**

| Filter | Old | New |
|--------|-----|-----|
| SMA Aligned | Required | Not required |
| Volume Surge | >= 1.3x | >= 1.3x (time-normalized) |
| Close Position | >= 0.6 | **Removed** |
| Momentum Breakout | N/A | **Required** (gap+follow OR 5D breakout) |
| ROC 10D | >= 3% | >= 3% |

### Test Results

Before fix: 0 candidates
After fix: **12 candidates** including MU, LUNR, LRCX, AMD, ENPH

```
MU:   Score=20, ROC=36.2%, VolSurge=3.1x, Gap=+3.4%, Breakout=True
LUNR: Score=20, ROC=68.2%, VolSurge=3.3x, Gap=+3.3%, Breakout=False
LRCX: Score=17, ROC=16.4%, VolSurge=1.5x, Gap=+3.9%, Breakout=True
AMD:  Score=15, ROC=11.5%, VolSurge=2.6x, Gap=+2.2%, Breakout=True
```

---

## Session 1 Summary

### Timeline (UTC)

| Time | Action |
|------|--------|
| ~03:45 | Reviewed implementation plan - found `monitor.py` and `/close` command already done |
| ~03:47 | Created `position-monitor.service` systemd unit file |
| ~03:47 | Created `position-monitor.timer` (initial version had syntax error) |
| ~03:48 | Fixed timer syntax, reloaded systemd, enabled and started timer |
| ~03:48 | Added `is_market_hours()` check to `monitor.py` with `--force` flag option |
| ~03:52 | Added `get_monitor_status()` function to `bot.py` |
| ~03:52 | Updated `/status` command to show Position Monitor status + next run time |
| ~03:56 | Restarted `momentum-agent.service` to apply bot changes |
| ~03:57 | Tested monitor script - successfully checked HON position (Score: 2/13) |
| ~04:00 | Created comprehensive `README.md` documentation |

### What Was Completed

1. **Position Monitor Timer** - Runs every 30 min on weekdays (Mon-Fri *:00,30)
2. **Market Hours Guard** - Script exits early outside 9:30 AM - 4:00 PM ET
3. **Bot Integration** - `/status` now shows monitor status and next scheduled check
4. **Documentation** - Full README with architecture, signals, logs, and workflows

### Files Modified/Created

| File | Change |
|------|--------|
| `position-monitor.service` | Created - systemd service unit |
| `position-monitor.timer` | Created - systemd timer (30 min intervals) |
| `monitor.py` | Added `is_market_hours()`, `--force` flag, pytz import |
| `bot.py` | Added `get_monitor_status()`, updated `/status` command |
| `README.md` | Created - full system documentation |

---

## Pre-Market Checklist

### Before Market Open (9:00 AM ET / 14:00 UTC)

```bash
# 1. Verify both services are running
sudo systemctl status momentum-agent
sudo systemctl status position-monitor.timer

# 2. Check timer schedule
sudo systemctl list-timers position-monitor.timer

# 3. Verify bot is responsive
# Send /status to Telegram bot - should show:
#   - Account info
#   - Current positions
#   - Position Monitor: Active
#   - Next check time
```

### At Market Open (9:30 AM ET / 14:30 UTC)

| Time (ET) | Expected Event | How to Verify |
|-----------|----------------|---------------|
| 9:30 | Market opens | Positions become tradeable |
| 10:00 | First monitor run | Check `logs/monitor.log` |
| 10:00 | Position checks run | Score calculated, logged to DB |

```bash
# Check if monitor ran
sudo journalctl -u position-monitor.service --since "today" -n 20

# Check monitor log
tail -20 /home/ubuntu/momentum-agent/logs/monitor.log

# Check database for position_checks
sqlite3 data/trades.db "SELECT * FROM position_checks ORDER BY check_time DESC LIMIT 5;"
```

### What to Expect from Monitor Run

**Alert Thresholds:**

| Score | Result |
|-------|--------|
| 0-2 | No alert, logged as OK |
| 3-4 | Telegram alert sent (weak reversal) |
| 5+ | Strong reversal alert sent |

**Telegram Alert Format (if triggered):**
```
⚠️ REVERSAL ALERT: SYMBOL

Score: X/13 (WEAK/STRONG)

Signals detected:
  • [list of triggered signals]

🔴 Current P/L: X.X%

Action: /close SYMBOL to exit
```

### Monitor Schedule (Weekdays Only)

| Time (ET) | Action |
|-----------|--------|
| 10:00 | First check |
| 10:30, 11:00, ... | Every 30 min |
| 16:00 | Last check |
| 16:30+ | Exits (outside market hours) |

---

## Troubleshooting Commands

```bash
# If no alerts received - check Telegram config
grep TELEGRAM /home/ubuntu/momentum-agent/.env

# If monitor not running - check timer
sudo systemctl status position-monitor.timer

# Force a manual run to test
./venv/bin/python monitor.py --force

# Check for Python errors
sudo journalctl -u position-monitor.service -n 50 --no-pager

# Restart services if needed
sudo systemctl restart momentum-agent
sudo systemctl restart position-monitor.timer
```

---

## Current State (January 6, 2026)

### Configuration
- **Max Portfolio Exposure**: 60%
- **Position Size**: 10%
- **Max Positions**: 6 total (2 per cap)
- **Per-Scan Max Buys**: 2 (from cap config)
- **Trailing Stop**: 5% (with 3-attempt retry)
- **Auto-Close**: Enabled (threshold: 5)
- **Skip-Buys When Healthy**: Enabled (threshold: 3)
- **Universe Size**: 211 stocks (108 large, 61 mid, 42 small)

### Cap-Specific Thresholds
| Filter | Large/Mid | Small |
|--------|-----------|-------|
| Gap Up | >= 1% | >= 3% |
| Volume Surge | >= 1.3x | >= 1.5x |
| ROC 10D | >= 3% | >= 5% |

### Open Positions
- FCX: 232 shares (large cap)
- MU: 39 shares (large cap)

### Services Running
- `momentum-agent.service`: Active (Telegram bot)
- `position-monitor.timer`: Active (every 30 min on weekdays)

### Cron Schedule
- 9 scans per day (3 caps × 3 time slots)
- Market Open: 9:35-9:41 AM ET
- Midday: 12:30-12:36 PM ET
- Pre-Close: 3:30-3:36 PM ET

### Active Features
- Per-cap scanning with separate thresholds
- Per-cap position limits (2 per cap, 6 total)
- Runtime configuration via bot (/settings, /set)
- Auto-close on strong reversal (score >= 5)
- Consolidated trailing stops (entire position)
- DQL training data collection (market_snapshots, candidate_snapshots)
- Performance metrics commands (/metrics, /weekly, /monthly, /export)
- Background jobs (daily_snapshot, update_outcomes, update_tracking, cleanup)
- **Self-learning loop** (poor signal tracking and agent awareness)
- **Skip-buy mode** (let winners run when all positions healthy)
- **Scan locking** (prevent concurrent scan interference)
- **Trailing stop retry** (3 attempts with alerts on failure)

---

## Future Development Ideas

- [ ] Add intraday momentum scanner (not just daily bars)
- [ ] Implement profit-taking rules (partial exits at targets)
- [ ] Add options flow integration
- [ ] Create web dashboard for monitoring
- [ ] Add backtesting framework for signal validation
- [ ] Implement sector rotation analysis
- [ ] Poor signal pattern ML analysis
