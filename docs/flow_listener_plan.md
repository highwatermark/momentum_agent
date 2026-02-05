# Flow Listener Implementation Plan v2

## Executive Summary

Replace cron-based flow scanning with a real-time Claude-validated polling service. The service polls every 60 seconds and uses Claude AI as the primary decision-maker for trade execution, with **profit generation as the explicit goal**.

**Key Design Decisions:**
- Claude-centric validation (removes numeric scoring)
- Single batched Claude call per cycle (not per signal)
- Pre-fetched context (no tool use for Claude)
- Three-layer safety architecture
- 60-second cycle budget with ~45s buffer

---

## Performance Budget (60-Second Cycle)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    60-SECOND CYCLE BREAKDOWN                            │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  PHASE 1: Parallel Context Pre-fetch                       3-4s        │
│  ┌───────────────────────────────────────────────────────────────┐     │
│  │  Concurrent API calls (asyncio.gather):                       │     │
│  │  • UW API: flow-alerts?newer_than=...           ─┐            │     │
│  │  • Alpaca: get_account()                         │            │     │
│  │  • Alpaca: get_positions()                       ├─ parallel  │     │
│  │  • Alpaca: SPY + VIX quotes                      │            │     │
│  │  • Cache: symbol context (earnings, IV)         ─┘            │     │
│  └───────────────────────────────────────────────────────────────┘     │
│                                                                         │
│  PHASE 2: Pre-filter (in-memory)                           <0.1s       │
│  ┌───────────────────────────────────────────────────────────────┐     │
│  │  • Premium >= $100K                                           │     │
│  │  • Not in seen_signal_ids (dedup)                             │     │
│  │  • Basic sanity (valid symbol, reasonable strike)             │     │
│  │  → Typically 0-5 signals pass                                 │     │
│  └───────────────────────────────────────────────────────────────┘     │
│                                                                         │
│  PHASE 3: Claude Validation (SINGLE BATCHED CALL)          5-10s       │
│  ┌───────────────────────────────────────────────────────────────┐     │
│  │  ONE prompt with ALL passing signals + full context           │     │
│  │  Claude returns ranked recommendations for each signal        │     │
│  │  NO TOOL USE - all context pre-fetched                        │     │
│  └───────────────────────────────────────────────────────────────┘     │
│                                                                         │
│  PHASE 4: Safety Gate + Execution                          2-5s        │
│  ┌───────────────────────────────────────────────────────────────┐     │
│  │  For top EXECUTE recommendation:                              │     │
│  │  • Hard safety limits (position, Greeks, exposure)            │     │
│  │  • Find contract + liquidity check                            │     │
│  │  • Place order via options_executor                           │     │
│  │  • Send Telegram notification                                 │     │
│  └───────────────────────────────────────────────────────────────┘     │
│                                                                         │
│  ═══════════════════════════════════════════════════════════════════   │
│  TOTAL: 12-18 seconds                                                  │
│  BUFFER: 42-48 seconds (for retries, slow APIs, edge cases)            │
│  ═══════════════════════════════════════════════════════════════════   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Three-Layer Safety Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         THREE-LAYER SAFETY ARCHITECTURE                          │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  LAYER 1: PRE-CLAUDE FILTER (Flow Listener)                                     │
│  ┌────────────────────────────────────────────────────────────────────────┐     │
│  │  Quick filters before Claude API call:                                  │     │
│  │  • Premium >= $100K                                                     │     │
│  │  • Dedupe (not seen in current session)                                 │     │
│  │  • Valid symbol (exclude index options like SPXW)                       │     │
│  │  • Max 10 signals per cycle to Claude                                   │     │
│  └────────────────────────────────────────────────────────────────────────┘     │
│                                    │                                             │
│                                    ▼                                             │
│  LAYER 2: CLAUDE VALIDATION (with full portfolio context)                       │
│  ┌────────────────────────────────────────────────────────────────────────┐     │
│  │  Claude receives AND CONSIDERS in decision:                             │     │
│  │  • Current positions (symbols, P/L, Greeks)                             │     │
│  │  • Portfolio net delta, daily theta                                     │     │
│  │  • Options exposure % of portfolio                                      │     │
│  │  • Risk score from PortfolioManager                                     │     │
│  │  • Sector concentration                                                 │     │
│  │  • Earnings proximity for each signal                                   │     │
│  │  • Market context (VIX, SPY trend, sector performance)                  │     │
│  │                                                                         │     │
│  │  Claude factors these into conviction score.                            │     │
│  │  Example: If delta already +100, Claude may SKIP bullish signal.        │     │
│  │                                                                         │     │
│  │  Returns: EXECUTE (>=75%) / ALERT (50-74%) / SKIP (<50%)               │     │
│  └────────────────────────────────────────────────────────────────────────┘     │
│                                    │                                             │
│                        Claude says "EXECUTE"                                     │
│                                    │                                             │
│                                    ▼                                             │
│  LAYER 3: SAFETY GATE (Hard limits - override Claude if needed)                 │
│  ┌────────────────────────────────────────────────────────────────────────┐     │
│  │  Even if Claude says EXECUTE, these HARD LIMITS apply:                  │     │
│  │                                                                         │     │
│  │  Position Management:                                                   │     │
│  │  ├── Max 4 options positions (OPTIONS_CONFIG)                          │     │
│  │  ├── Max 10% portfolio in options                                      │     │
│  │  ├── Max 3 executions per day                                          │     │
│  │  └── No duplicate underlying positions                                  │     │
│  │                                                                         │     │
│  │  Risk Management:                                                       │     │
│  │  ├── Portfolio |delta| < 150 per $100K equity                          │     │
│  │  ├── Daily theta < 0.3% of portfolio                                   │     │
│  │  ├── Risk score < 50 (PortfolioManager)                                │     │
│  │  └── Sector concentration < 50%                                        │     │
│  │                                                                         │     │
│  │  Symbol Safety:                                                         │     │
│  │  ├── Earnings blackout (within 2 days)                                 │     │
│  │  └── Not on blocked symbols list                                        │     │
│  │                                                                         │     │
│  │  If ANY check fails → downgrade to ALERT only                          │     │
│  └────────────────────────────────────────────────────────────────────────┘     │
│                                    │                                             │
│                            All checks pass                                       │
│                                    │                                             │
│                                    ▼                                             │
│  LAYER 4: OPTIONS EXECUTOR (existing safety + execution)                        │
│  ┌────────────────────────────────────────────────────────────────────────┐     │
│  │  options_executor.py (UNCHANGED - keeps all existing checks):           │     │
│  │                                                                         │     │
│  │  • find_option_contract() - finds best matching contract               │     │
│  │  • check_option_liquidity() - spread < 15%, OI > 100, bid > $0.05     │     │
│  │  • calculate_options_position_size() - uses PositionSizer agent        │     │
│  │  • check_earnings_blackout() - redundant safety                        │     │
│  │  • can_add_position() - sector concentration                           │     │
│  │  • place_options_order_smart() - limit orders with buffer              │     │
│  │  • Log entry Greeks to database                                        │     │
│  └────────────────────────────────────────────────────────────────────────┘     │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Safety Check Distribution

| Check | Layer 2 (Claude) | Layer 3 (Gate) | Layer 4 (Executor) |
|-------|------------------|----------------|-------------------|
| Position count | ✓ Informed | ✓ Hard block >= 4 | ✓ Double-check |
| Portfolio delta | ✓ Informed | ✓ Hard block > 150 | - |
| Daily theta | ✓ Informed | ✓ Hard block > 0.3% | - |
| Options exposure | ✓ Informed | ✓ Hard block >= 10% | ✓ Double-check |
| Risk score | ✓ Informed | ✓ Hard block > 50 | - |
| Sector concentration | ✓ Informed | ✓ Hard block > 50% | ✓ Double-check |
| Earnings proximity | ✓ Informed | ✓ Hard block <= 2d | ✓ Double-check |
| Duplicate position | ✓ Informed | ✓ Hard block | ✓ Double-check |
| Contract liquidity | - | - | ✓ Hard block |
| Position sizing | - | - | ✓ PositionSizer agent |

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    CLAUDE-CENTRIC FLOW LISTENER                         │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│   ┌─────────────┐                                                       │
│   │  UW API     │                                                       │
│   │  (polling)  │                                                       │
│   └──────┬──────┘                                                       │
│          │                                                              │
│          ▼                                                              │
│   ┌─────────────────────────────────────────────────────────────────┐  │
│   │  FLOW LISTENER SERVICE (flow_listener.py)                        │  │
│   │                                                                  │  │
│   │  1. Pre-fetch context (parallel)                                 │  │
│   │     • Market: SPY, VIX, sector performance                       │  │
│   │     • Portfolio: positions, Greeks, exposure, risk score         │  │
│   │     • Symbols: earnings dates, IV rank (cached)                  │  │
│   │                                                                  │  │
│   │  2. Pre-filter signals (Layer 1)                                 │  │
│   │     • Premium >= $100K                                           │  │
│   │     • Dedupe against seen_ids                                    │  │
│   │                                                                  │  │
│   │  3. Claude validation (Layer 2 - single batched call)            │  │
│   │     • All signals + all context in one prompt                    │  │
│   │     • Returns: EXECUTE / ALERT / SKIP per signal                 │  │
│   │                                                                  │  │
│   │  4. Safety Gate (Layer 3)                                        │  │
│   │     • Hard limits check                                          │  │
│   │                                                                  │  │
│   │  5. Execute via options_executor (Layer 4)                       │  │
│   └─────────────────────────────────────────────────────────────────┘  │
│          │                                                              │
│          ├─────────────┬─────────────┐                                 │
│          ▼             ▼             ▼                                  │
│   ┌───────────┐ ┌───────────┐ ┌───────────┐                            │
│   │  EXECUTE  │ │   ALERT   │ │   SKIP    │                            │
│   │ conviction│ │ conviction│ │ conviction│                            │
│   │   >= 75%  │ │  50-74%   │ │   < 50%   │                            │
│   └─────┬─────┘ └─────┬─────┘ └─────┬─────┘                            │
│         │             │             │                                   │
│         ▼             ▼             ▼                                   │
│   ┌───────────┐ ┌───────────┐ ┌───────────┐                            │
│   │ options_  │ │ Telegram  │ │ Log only  │                            │
│   │ executor  │ │ Alert     │ │ (DB)      │                            │
│   │     ↓     │ └───────────┘ └───────────┘                            │
│   │ Telegram  │                                                         │
│   │ + Log     │                                                         │
│   └───────────┘                                                         │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Claude Prompt Design (Profit-Focused)

### System Prompt

```
You are an autonomous options flow trading agent. Your PRIMARY OBJECTIVE is to
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

You will receive flow signals with market and portfolio context. Analyze each
signal and provide a clear recommendation with profit-focused thesis.
```

### Per-Cycle Prompt Template

```
CURRENT MARKET CONTEXT:
- SPY: ${spy_price} ({spy_change}%), Trend: {spy_trend}
- VIX: {vix} ({vix_level})
- Sector Performance: {sector_summary}
- Time: {time} ET

PORTFOLIO CONTEXT:
- Equity: ${equity}
- Current Options Positions: {position_count}/{max_positions}
{position_details}
- Net Delta: {net_delta} ({delta_assessment})
- Daily Theta: ${daily_theta}
- Options Exposure: {exposure_pct}% of portfolio
- Risk Score: {risk_score}/100 ({risk_assessment})
- Available for new position: ~${available_capital}

SIGNALS TO ANALYZE:
{signals_formatted}

For each signal, provide JSON:
{
  "signal_id": "string",
  "symbol": "string",
  "recommendation": "EXECUTE|ALERT|SKIP",
  "conviction": 0-100,
  "thesis": "Profit-focused reasoning",
  "risk_factors": ["list"],
  "suggested_contracts": 1-5,
  "profit_target": "50% or specific",
  "stop_loss": "50% or specific"
}

Return JSON array ranked by execution priority. Focus on PROFIT POTENTIAL.
```

---

## Configuration

```python
FLOW_LISTENER_CONFIG = {
    # Polling
    "poll_interval_seconds": 60,

    # Pre-filter thresholds (Layer 1)
    "min_premium": 100000,            # $100K minimum
    "max_signals_per_cycle": 10,      # Max signals to Claude
    "excluded_symbols": ["SPXW", "SPX", "NDX"],  # Index options

    # Claude decision thresholds (Layer 2)
    "min_conviction_execute": 75,     # Auto-execute threshold
    "min_conviction_alert": 50,       # Alert threshold

    # Market hours (ET)
    "market_open_hour": 9,
    "market_open_minute": 30,
    "market_close_hour": 16,
    "market_close_minute": 0,

    # Safety limits (Layer 3)
    "max_executions_per_day": 3,
    "max_delta_per_100k": 150,
    "max_theta_pct": 0.003,           # 0.3% daily
    "max_risk_score": 50,
    "max_sector_concentration": 0.50,

    # Operational
    "enable_auto_execute": True,      # Master switch
    "max_cycle_time_seconds": 55,     # Hard timeout

    # Circuit breaker
    "max_consecutive_errors": 5,
    "circuit_breaker_cooldown_seconds": 300,
}
```

---

## Database Changes

### New Table: flow_listener_state

```sql
CREATE TABLE IF NOT EXISTS flow_listener_state (
    id INTEGER PRIMARY KEY,
    last_check_time TEXT,
    daily_execution_count INTEGER DEFAULT 0,
    last_reset_date TEXT,
    seen_signal_ids TEXT,  -- JSON array
    updated_at TEXT
);
```

### Modify flow_signals Table

```sql
ALTER TABLE flow_signals ADD COLUMN action_taken TEXT;
-- Values: 'executed', 'alert_sent', 'blocked', 'skipped'

ALTER TABLE flow_signals ADD COLUMN claude_analysis TEXT;
-- JSON blob with thesis, conviction, risk_factors
```

---

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `flow_listener.py` | **CREATE** | Main service (~450 lines) |
| `config.py` | MODIFY | Add FLOW_LISTENER_CONFIG |
| `options_agent.py` | MODIFY | Add FlowValidator class |
| `db.py` | MODIFY | Add listener state functions |
| `flow-listener.service` | **CREATE** | Systemd unit file |

### Files to Deprecate

| File | Action | Notes |
|------|--------|-------|
| `flow_scanner.py` | KEEP (slim) | Keep: UnusualWhalesClient, FlowSignal, parse_flow_alert |
| `flow_analyzer.py` | DEPRECATE | Logic moves to FlowValidator in options_agent.py |
| `flow_job.py` | KEEP (partial) | Keep: exit checks, DTE alerts. Remove: run_full_flow_job |

---

## Crontab Changes

```bash
# REMOVE - replaced by flow_listener service
# 0 15 * * 1-5 ... flow_job.py full
# 0 19 * * 1-5 ... flow_job.py full

# KEEP - position management
*/30 14-20 * * 1-5 cd /home/ubuntu/momentum-agent && ./venv/bin/python flow_job.py exits >> logs/flow.log 2>&1

# KEEP - expiration warnings
30 14 * * 1-5 cd /home/ubuntu/momentum-agent && ./venv/bin/python flow_job.py dte >> logs/flow.log 2>&1
```

---

## Error Handling & Resilience

### Circuit Breaker

```python
class CircuitBreaker:
    """Pause auto-execution after repeated errors"""

    def __init__(self):
        self.consecutive_errors = 0
        self.is_open = False
        self.last_error_time = None

    def record_error(self):
        self.consecutive_errors += 1
        if self.consecutive_errors >= config["max_consecutive_errors"]:
            self.is_open = True
            send_telegram("🔴 Circuit breaker OPEN - auto-execution paused")

    def can_execute(self) -> bool:
        if not self.is_open:
            return True
        # Check cooldown expired
        if (now - self.last_error_time).seconds > config["circuit_breaker_cooldown"]:
            self.is_open = False
            return True
        return False
```

### Timeout Handling

```python
async def poll_cycle_with_timeout(self):
    try:
        async with asyncio.timeout(config["max_cycle_time_seconds"]):
            await self._poll_cycle()
    except asyncio.TimeoutError:
        logger.warning("Cycle timeout - skipping to next")
```

---

## Rollout Plan

| Day | Configuration | Purpose |
|-----|---------------|---------|
| 1 | `enable_auto_execute=False` | Alerts only, validate Claude |
| 2-3 | `max_executions_per_day=1` | Single execution, monitor |
| 4-5 | `max_executions_per_day=2` | Gradual increase |
| 6+ | `max_executions_per_day=3` | Full operation |

---

## Service Management

```bash
# Install
sudo cp flow-listener.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable flow-listener

# Control
sudo systemctl start flow-listener
sudo systemctl stop flow-listener
sudo systemctl status flow-listener

# Logs
tail -f logs/flow_listener.log
journalctl -u flow-listener -f
```

---

## Success Metrics

| Metric | Target |
|--------|--------|
| Cycle completion rate | > 99% |
| Signal detection latency | < 60s |
| Execution success rate | > 95% |
| Win rate | > 40% |
| Profit factor | > 1.5 |
