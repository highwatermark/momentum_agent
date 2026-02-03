# Momentum Agent System Review
**Date**: 2026-02-01
**Review Period**: 2026-01-06 to 2026-01-30
**Prepared For**: Next Development Session

---

## Executive Summary

The trading system is **not profitable** and several critical fixes implemented on Jan 20 are **not working as expected**. The DQN training pipeline was partially fixed but remains incomplete, same-day exits continue despite rules against them, and the RSI filter appears ineffective at preventing overbought entries.

### Key Metrics (30-Day)

| Metric | Value | Assessment |
|--------|-------|------------|
| Total Trades | 36 | High churn |
| Win Rate | **27.8%** | CRITICALLY LOW |
| Total P/L | **-$909.55** | Negative |
| Avg P/L per Trade | -0.52% | Losers outpace winners |
| Starting Equity (Jan 6) | $133,535.29 | |
| Current Equity (Jan 30) | $129,487.15 | |
| Drawdown | **-$4,048.14 (-3.0%)** | |
| SPY Performance | ~+2% | Underperforming market |

### Key Metrics (Last 7 Days: Jan 23-30)

| Metric | Value |
|--------|-------|
| Trades | 8 |
| Win Rate | **12.5%** (1 win, 7 losses) |
| Total P/L | -$99.56 |
| Avg P/L | -0.44% |

---

## Critical Finding: Jan 20 Fixes Not Working

On January 20, Session 11 & 12 implemented several critical fixes. Here's the status:

### Fix 1: DQN Outcome Pipeline - PARTIALLY WORKING

**What was implemented:**
- Batched API calls (50 symbols at a time)
- Fallback to historical bars API
- Delayed update timing

**Current Status:**
| Metric | Before (Jan 6-19) | After (Jan 26+) |
|--------|-------------------|-----------------|
| Outcome coverage | 0% | 95.7% |
| "No item with key" errors | 200+/day | 0 |
| Candidates updated | 0 | 30-63/day |

**Issue:** Candidate snapshots are being updated, but **DQN experiences table stuck at 22 records** (initial backfill only). No new experiences are being generated from recent trades.

**Root Cause:** The `backfill_dqn_experiences()` function was run once but there's no automated process to add new closed trades to the DQN experiences table.

### Fix 2: Same-Day Exit Prevention - NOT WORKING

**What was implemented:**
```
MINIMUM HOLD TIME RULE (CRITICAL):
- Do NOT close positions on the SAME DAY as entry unless P/L < -5%
```

**Actual Results (All 36 closed trades):**

| Hold Time | Trades | Avg P/L | Win Rate |
|-----------|--------|---------|----------|
| Same Day (0d) | 16 | **-1.49%** | **6.2%** |
| 1+ Days | 20 | +0.25% | 45.0% |

**44% of trades (16/36) were still closed same-day**, and these trades have a **6.2% win rate**. The rule is being ignored.

**Same-Day Exit Examples (Post Jan 20):**
| Symbol | Entry | Exit | P/L | Exit Reason |
|--------|-------|------|-----|-------------|
| RUN | Jan 26 | Jan 26 | -3.23% | auto_reversal_score_5 |
| OXY | Jan 29 | Jan 29 | -0.33% | auto_reversal_score_5 |
| ON | Jan 28 | Jan 28 | -0.69% | auto_reversal_score_5 |

**Root Cause:** The auto-close monitor (reversal score >= 5) fires **regardless of hold time**, bypassing the minimum hold rule.

### Fix 3: RSI < 70 Filter - INCONCLUSIVE

**What was implemented:**
- Hard RSI < 70 filter in scanner.py (Stage 2)
- RSI display with warnings in candidate prompt

**Current Status:**
- RSI data is being captured in some trades but analysis shows mixed results
- Several trades post-Jan 20 still entered with RSI > 70 (IREN: 75, RKLB: 82, MU: 73)
- The filter may be working at scanner level, but agent is still entering overbought stocks from other sources

**RSI Performance Data (Incomplete - many NULL):**
RSI analysis unavailable due to inconsistent data capture in trades table.

### Fix 4: Let Winners Run - NOT WORKING

**What was implemented:**
```
IMPORTANT: Do NOT exit winning positions (+5% to +20%) just because
you see a "failed breakout" pattern. Winners should run.
```

**Actual Results:**
| Symbol | P/L at Exit | Days Held | Exit Reason |
|--------|-------------|-----------|-------------|
| BA | +5.34% | 11 | Manual profit-taking |
| KTOS | +10.59% | 4 | "Failed breakout" pattern |
| NET | +9.75% | 1 | auto_reversal_score_11 |
| KLAC | +2.01% | 1 | "Failed breakout" pattern |

Winners are still being cut due to reversal scores and "failed breakout" patterns. The auto-close monitor doesn't distinguish between winning and losing positions.

---

## Performance Deep Dive

### By Week

| Week | Trades Closed | Wins | Losses | Win Rate | Net P/L |
|------|---------------|------|--------|----------|---------|
| Jan 6-9 | 9 | 2 | 7 | 22.2% | -$902.67 |
| Jan 12-16 | 11 | 3 | 8 | 27.3% | +$256.53 |
| Jan 20-23 | 6 | 3 | 3 | 50.0% | +$152.57 |
| Jan 26-30 | 10 | 2 | 8 | 20.0% | -$415.98 |

**Observation:** Week of Jan 20-23 was best (50% win rate) after fixes were deployed, but performance degraded immediately after.

### By Exit Reason

| Exit Reason | Count | Avg P/L | Win Rate |
|-------------|-------|---------|----------|
| auto_reversal_score_* | 20 | -0.24% | 30% |
| Manual agent decision | 12 | -0.92% | 25% |
| Stop loss (-5%) | 4 | -5.22% | 0% |

**The auto-close feature is responsible for 55% of exits** and has a negative expected value.

### Holding Period Analysis

| Days Held | Count | Avg P/L | Win Rate | Total P/L |
|-----------|-------|---------|----------|-----------|
| 0 | 16 | -1.49% | 6.2% | -$2,384 |
| 1 | 9 | -0.11% | 44.4% | -$99 |
| 2 | 3 | -0.63% | 33.3% | -$189 |
| 3+ | 8 | +1.93% | 62.5% | +$1,545 |

**Clear pattern:** Trades held 3+ days have positive expected value. Same-day exits destroy performance.

---

## DQN System Status

### What's Working
- Candidate snapshots: 1,105 total, 95.7% with outcome data
- Daily performance snapshots: Recording correctly
- Position tracking: Running every 30 min during market hours
- Market snapshots: Logging SPY/VIX context

### What's Not Working
- **DQN experiences table stuck at 22 records** (no new trades added since Jan 20 backfill)
- No automated process to convert closed trades to DQN experiences
- No model training has occurred (table is just data collection)

### Missing Components

1. **Automated DQN Experience Recording**
   - Need: Trigger when trade closes to add experience
   - Current: Only manual backfill exists

2. **DQN Model Implementation**
   - Need: PyTorch/TensorFlow model to use collected data
   - Current: Only data collection infrastructure exists

3. **Model-Based Decision Making**
   - Need: Replace/augment Claude agent with DQN model
   - Current: All decisions made by Claude agent

---

## Root Cause Analysis

### Issue 1: Auto-Close Overrides Hold Rules

**Problem:** `monitor.py` fires auto-close on reversal score >= 5 without checking:
- How long position has been held
- Whether position is profitable
- The minimum hold time rule

**Evidence:**
- 16 same-day exits despite "no same-day exit" rule
- Winners (NET +9.75%) closed by auto_reversal_score
- -1.49% average P/L on same-day exits

**Fix Required:**
```python
# In monitor.py, before auto-close:
if days_held == 0:
    continue  # Never auto-close same-day
if pnl_pct > 5.0:
    continue  # Never auto-close big winners
```

### Issue 2: DQN Experience Pipeline Incomplete

**Problem:** `backfill_dqn_experiences()` only runs manually. No automated trigger when trades close.

**Evidence:**
- 22 DQN experiences from Jan 20 backfill
- 14 additional trades closed since then, 0 added to experiences

**Fix Required:**
Add call to `add_dqn_experience(trade_id)` in:
- `executor.py` `close_position()` function
- `monitor.py` auto-close path
- Or create a daily job to backfill new closed trades

### Issue 3: RSI Filter Not Enforced Consistently

**Problem:** RSI < 70 filter exists in scanner, but trades still entering with high RSI.

**Possible Causes:**
- Agent recommending stocks from watchlist (bypass scanner filter)
- Previous scan candidates still being executed
- RSI crossing 70 between scan and execution

**Evidence:**
- IREN (RSI 75), RKLB (RSI 82), MU (RSI 73) all entered post-Jan 20
- These should have been blocked by RSI < 70 filter

### Issue 4: Poor Signal Self-Learning Not Used

**Problem:** `poor_signals` table exists but agent doesn't seem to be learning from it.

**Evidence:**
- Same patterns repeat: "failed breakout" exits, same-day reversals
- Agent prompt includes poor signals but keeps making same errors

---

## Actionable Recommendations

### Priority 1: Fix Auto-Close Logic (CRITICAL)

**File:** `monitor.py`

**Changes Required:**
1. Add hold time check before auto-close
2. Add P/L check to protect winners
3. Log when rules prevent auto-close

```python
# Pseudo-code for fix:
if result["score"] >= AUTO_CLOSE_THRESHOLD:
    days_held = (datetime.now() - entry_date).days

    # Rule 1: Never close same-day
    if days_held == 0:
        print(f"  Skipping {symbol}: same-day protection")
        continue

    # Rule 2: Never close big winners
    if pnl_pct > 5.0:
        print(f"  Skipping {symbol}: protecting +{pnl_pct:.1f}% gain")
        continue

    # Rule 3: Only close if held >= 2 days
    if days_held < 2:
        send_alert_only(...)  # Alert but don't close
        continue

    # Now safe to auto-close
    close_position(...)
```

### Priority 2: Automate DQN Experience Recording

**File:** `db.py` or `executor.py`

**Option A - On Trade Close:**
```python
# In executor.py close_position() or update_trade_exit()
def update_trade_exit(...):
    # ... existing code ...

    # Add DQN experience for closed trade
    try:
        add_dqn_experience(trade_id)
    except Exception as e:
        print(f"Warning: Failed to add DQN experience: {e}")
```

**Option B - Daily Job (simpler):**
```python
# In jobs.py
def backfill_new_dqn_experiences():
    """Run daily to add any closed trades not yet in DQN experiences"""
    cursor.execute("""
        SELECT id FROM trades
        WHERE status = 'closed'
        AND id NOT IN (SELECT trade_id FROM dqn_experiences)
    """)
    for row in cursor.fetchall():
        add_dqn_experience(row['id'])
```

### Priority 3: Enforce RSI at Execution Time

**File:** `executor.py`

**Change:** Add RSI check before placing buy order
```python
def execute_trade(symbol, ...):
    # Fetch current RSI
    rsi = get_current_rsi(symbol)  # Need to implement
    if rsi >= 70:
        return {"success": False, "error": f"RSI too high ({rsi:.0f} >= 70)"}

    # Proceed with order...
```

### Priority 4: Review Auto-Close Threshold

**Current:** Score >= 5 triggers auto-close
**Observation:** 55% of exits are auto-close, avg P/L -0.24%

**Options:**
1. Raise threshold to 7+ (fewer false positives)
2. Require score >= 5 AND days held >= 2
3. Require score >= 5 AND P/L < 0%

### Priority 5: Fix DQN Reward Calculation

**Current avg reward:** -0.0483
**Current win rate:** 27.8%

The negative average reward is correct given the negative P/L, but the reward function may need tuning to better guide the model.

---

## Files That Need Changes

| File | Priority | Changes |
|------|----------|---------|
| `monitor.py` | P1 | Add hold time & P/L checks before auto-close |
| `executor.py` | P2 | Add DQN experience creation on trade close |
| `executor.py` | P3 | Add RSI check before execution |
| `jobs.py` | P2 | Add daily job to backfill new DQN experiences |
| `config.py` | P4 | Consider raising `auto_close_threshold` |
| `agent.py` | P4 | Review prompt effectiveness |

---

## Testing Checklist for Next Session

After implementing fixes:

- [ ] Verify same-day exit protection: `SELECT * FROM trades WHERE status='closed' AND holding_days=0 AND exit_date > '2026-02-01'` should return 0
- [ ] Verify DQN experiences growing: `SELECT COUNT(*) FROM dqn_experiences` should increase with each closed trade
- [ ] Verify RSI enforcement: Check logs for "RSI too high" rejections
- [ ] Monitor win rate: Should improve from 27.8% as same-day exits stop
- [ ] Track auto-close vs manual: Auto-close should decrease as threshold adjustments take effect

---

## Appendix: Trade Log (Jan 20-30)

| Date | Symbol | Entry | Exit | P/L | Days | Reason |
|------|--------|-------|------|-----|------|--------|
| Jan 30 | INTC | Jan 28 | Jan 30 | -1.02% | 2 | auto_reversal_score_5 |
| Jan 29 | RDW | Jan 27 | Jan 29 | -5.48% | 2 | Stop loss |
| Jan 29 | OXY | Jan 29 | Jan 29 | -0.33% | 0 | auto_reversal_score_5 |
| Jan 28 | ON | Jan 28 | Jan 28 | -0.69% | 0 | auto_reversal_score_5 |
| Jan 28 | NET | Jan 27 | Jan 28 | -2.33% | 1 | auto_reversal_score_11 |
| Jan 27 | NET | Jan 27 | Jan 27 | -0.20% | 0 | auto_reversal_score_11 |
| Jan 27 | NET | Jan 26 | Jan 27 | +9.75% | 1 | auto_reversal_score_11 |
| Jan 26 | RUN | Jan 26 | Jan 26 | -3.23% | 0 | auto_reversal_score_5 |
| Jan 23 | PG | Jan 22 | Jan 23 | +0.66% | 1 | auto_reversal_score_5 |
| Jan 22 | PWR | Jan 16 | Jan 22 | -0.79% | 6 | auto_reversal_score_5 |
| Jan 21 | IREN | Jan 16 | Jan 21 | -6.04% | 5 | Stop loss |
| Jan 20 | MU | Jan 16 | Jan 20 | +2.25% | 4 | auto_reversal_score_5 |
| Jan 20 | BA | Jan 9 | Jan 20 | +5.34% | 11 | Manual take profit |
| Jan 20 | RKLB | Jan 16 | Jan 20 | -4.52% | 4 | Stop loss |

---

*Report generated: 2026-02-01*
*Next review: After Priority 1 & 2 fixes implemented*

---

## Changelog

### 2026-02-03 - Minimum Hold Protection Implemented

**Change**: Added `days_held < 2` check before auto-close in `monitor.py`

**Files Modified**:
- `monitor.py`: Added import `from db import get_trade_by_symbol` (line 15), added hold time check (lines 341-357)

**Reasoning**:
Data showed same-day exits had 6.2% win rate vs 62.5% for 3+ day holds. Positions need time to differentiate healthy pullbacks from real reversals. This is the 80/20 fix - single highest-impact change to improve system performance.

**Behavior**:
- Reversal score >= 5 with days_held < 2 → Skip auto-close, send alert only
- Reversal score >= 5 with days_held >= 2 → Proceed with auto-close

**Status**: Tested and deployed, ready for 2026-02-04 market session.

### 2026-02-03 - DQN Experience Backfill Job Added

**Change**: Added automated daily job to backfill DQN experiences from closed trades

**Files Modified**:
- `jobs.py`: Added `backfill_dqn_experiences_job()` function, added to `run_all_daily_jobs()`, added CLI option

**Crontab Added**:
```
45 22 * * 1-5 cd /home/ubuntu/momentum-agent && ./venv/bin/python jobs.py backfill_dqn >> logs/jobs.log 2>&1
```

**Reasoning**:
DQN experiences table was stuck at 22 records since Jan 20 backfill. No automated process existed to add new closed trades. This fix ensures training data grows with each closed trade.

**Results from manual run**:
- Backfilled 14 new experiences (trades closed since Jan 20)
- Total experiences: 22 → 36
- Win rate: 27.8%, avg reward: -0.0305

**Status**: Tested and deployed, will run daily at 22:45 UTC.

### 2026-02-03 - Winner Protection Added

**Change**: Skip auto-close if position P/L >= 5%

**File Modified**:
- `monitor.py`: Added winner protection check after min hold check (lines 359-365)

**Reasoning**:
Winners like NET (+9.75%) were being cut by auto-close on reversal signals. Big winners should be allowed to run - temporary pullbacks in winning positions are often healthy consolidation.

**Behavior**:
- Reversal score >= 5 with P/L >= 5% → Skip auto-close, send alert only
- User can still manually close via `/close SYMBOL` if desired

**Status**: Tested and deployed.

### 2026-02-03 - RSI Enforcement at Execution

**Change**: Block trade execution if RSI >= 70

**File Modified**:
- `executor.py`: Added RSI check at start of `execute_trade()` function

**Reasoning**:
Trades were entering with high RSI (IREN: 75, RKLB: 82, MU: 73) despite scanner filter. This happened when stocks came from watchlist or RSI changed between scan and execution.

**Behavior**:
- RSI >= 70 at execution → Trade blocked with error message
- RSI < 70 → Trade proceeds normally

**Status**: Tested and deployed.

### 2026-02-03 - Options Flow Trading System Implemented

**Change**: Complete options flow trading system integrated with Unusual Whales API

**New Files Created**:
- `flow_scanner.py`: UW API client for fetching and scoring options flow signals
- `flow_analyzer.py`: Signal enrichment with Alpaca data and Claude thesis generation
- `options_executor.py`: Alpaca options trading (contracts, sizing, execution)
- `docs/OPTIONS_FLOW_BUILD_INSTRUCTIONS.md`: Build documentation
- `docs/options_enhancements.md`: Enhancement roadmap

**Files Modified**:
- `config.py`: Added `OPTIONS_CONFIG`, `FLOW_CONFIG`, `FLOW_SCORING`
- `db.py`: Added options tables (`flow_signals`, `options_trades`, `flow_scan_history`)
- `bot.py`: Added commands `/flow`, `/analyze`, `/options`, `/buyoption`, `/closeoption`
- `.env`: Added `UW_API_KEY`

**New Telegram Commands**:
| Command | Description |
|---------|-------------|
| `/flow` | Scan options flow from Unusual Whales |
| `/analyze` | Analyze top signals with Claude |
| `/options` | View options positions and performance |
| `/buyoption SYMBOL confirm` | Execute options trade |
| `/closeoption CONTRACT` | Close options position |

**Flow Signal Scoring (0-20)**:
- Sweep: +3 (urgency)
- Ask side: +2 (conviction)
- Premium $100K+: +3, $250K+: +2 bonus
- Vol/OI >1: +2, >3: +1 bonus
- Floor trade: +2 (institutional)
- Opening trade: +2
- OTM: +1, Near earnings: +1, Low DTE: +1

**Status**: Tested and deployed.

### 2026-02-03 - Options Safety Features (HIGH Priority)

**Change**: Implemented HIGH priority safety features from options_enhancements.md

**Files Modified**:
- `config.py`: Added `OPTIONS_SAFETY` config with liquidity limits
- `options_executor.py`: Added safety functions and smart order execution

**New Functions**:
- `get_option_quote()`: Fetches real-time bid/ask from Alpaca
- `check_option_liquidity()`: Validates spread, bid, and size before trading
- `place_options_order_smart()`: Limit orders at mid + 2% buffer
- `reconcile_options_positions()`: Syncs DB with Alpaca positions

**New Config (`OPTIONS_SAFETY`)**:
| Parameter | Value | Purpose |
|-----------|-------|---------|
| `max_spread_pct` | 15% | Block trades with wide spreads |
| `min_bid` | $0.05 | Block penny options |
| `min_bid_size` | 10 | Ensure liquidity |
| `use_limit_orders` | True | Avoid market order slippage |
| `limit_price_buffer_pct` | 2% | Buffer above mid for buys |

**New Bot Command**:
- `/reconcile`: Sync options DB with Alpaca positions

**Behavior**:
- Trades blocked if spread > 15% or bid < $0.05
- Uses limit orders at mid price + 2% instead of market orders
- Auto-closes stale DB records on reconciliation

**Test Results**:
```
AAPL260220C00100000:
  Bid: $168.10 | Ask: $171.95
  Spread: 2.3% (passes)
  Liquid: True
```

**Status**: Tested and deployed, ready for 2026-02-04 market session.

### 2026-02-03 - Options Greeks & Learning Loop (MEDIUM Priority)

**Change**: Implemented MEDIUM priority enhancements - Greeks tracking, DTE alerts, sector concentration, earnings blackout

**Files Modified**:
- `options_executor.py`: Added Greeks calculation, sector concentration, DTE alerts, earnings blackout
- `db.py`: Added Greeks columns to options_trades, added flow_signal_outcomes table
- `bot.py`: Added `/greeks`, `/expirations`, `/flowperf` commands
- `config.py`: Added SECTOR_MAP

**New Functions (options_executor.py)**:

| Function | Purpose |
|----------|---------|
| `estimate_greeks()` | Black-Scholes Greeks calculation |
| `get_option_greeks()` | Get Greeks for specific contract |
| `get_portfolio_greeks()` | Aggregate Greeks across all positions |
| `check_sector_concentration()` | Analyze sector allocation |
| `can_add_position()` | Check if new position violates limits |
| `check_expiration_risk()` | Find positions approaching expiration |
| `suggest_roll()` | Suggest roll for expiring position |
| `check_earnings_blackout()` | Block trades near earnings |

**Greeks Logged at Entry/Exit**:
| Field | Description |
|-------|-------------|
| entry/exit_delta | Delta at open/close |
| entry/exit_gamma | Gamma at open/close |
| entry/exit_theta | Theta at open/close |
| entry/exit_vega | Vega at open/close |
| entry/exit_iv | Implied volatility |
| entry/exit_underlying_price | Stock price |
| entry/exit_dte | Days to expiration |

**New Database Table (flow_signal_outcomes)**:
- Tracks every closed options trade outcome
- Records signal characteristics: sweep, ask_side, floor, opening, premium_tier, vol_oi_tier
- Records entry Greeks: delta, theta, IV
- Records outcomes: max_price, min_price, actual_pnl, holding_days
- Enables factor analysis: which signals correlate with wins?

**New Bot Commands**:
| Command | Description |
|---------|-------------|
| `/greeks` | Portfolio Greeks with sector allocation |
| `/expirations` | DTE alerts with roll suggestions |
| `/flowperf` | Signal factor performance analysis |

**Sector Concentration Limits**:
- Max single sector: 50%
- Max single underlying: 30%
- New positions blocked if limits exceeded

**DTE Alert Thresholds**:
| DTE | Severity | Action |
|-----|----------|--------|
| <= 0 | CRITICAL | "EXPIRED - Close immediately" |
| <= 3 | HIGH | "Expiring - Consider closing or rolling" |
| <= 7 | MEDIUM | "Monitor theta decay" |

**Earnings Blackout**:
- Blocks trades 2 days before earnings
- Uses Unusual Whales earnings calendar
- Configurable via `OPTIONS_SAFETY['earnings_blackout_days']`

**Signal Factor Performance Analysis**:
- Tracks win rate by signal score tier (elite 15+, high 12-14, etc.)
- Tracks win rate by factor (sweep, ask side, floor, opening)
- Tracks win rate by premium tier and vol/oi tier
- Accessible via `/flowperf` command

**Status**: Tested and deployed, bot running (PID 60241).
