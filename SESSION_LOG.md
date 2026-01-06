# Session Log - January 2, 2026

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
