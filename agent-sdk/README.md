# AI-Native Options Flow Trading System

A redesign of the momentum-agent options flow system using the Claude Agent SDK for true AI-native operation.

## Overview

This implementation replaces the current polling-based, stateless AI integration with a multi-agent architecture where Claude agents have:

- **Persistent context** - Memory of signals, decisions, and market evolution
- **Autonomous coordination** - Subagents work in parallel
- **Dynamic tool access** - Agents fetch additional context as needed
- **Self-directed loops** - Context → Action → Verify happens automatically

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        ORCHESTRATOR AGENT                                    │
│                   (High-level decision making)                               │
│                                                                              │
│    "You are the lead options trader. Coordinate specialized agents to        │
│    monitor flow, manage positions, and execute trades profitably."           │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │
         ┌────────────────────────┼────────────────────────────────┐
         │                        │                                │
         ▼                        ▼                                ▼
┌─────────────────┐     ┌─────────────────┐            ┌─────────────────┐
│ FLOW_SCANNER    │     │ POSITION_MANAGER│            │  RISK_MANAGER   │
│ SUBAGENT        │     │ SUBAGENT        │            │  SUBAGENT       │
│                 │     │                 │            │                 │
│ Tools:          │     │ Tools:          │            │ Tools:          │
│ - uw_flow_scan  │     │ - get_positions │            │ - portfolio_    │
│ - stock_quote   │     │ - get_quote     │            │   greeks        │
│ - earnings_check│     │ - calculate_dte │            │ - sector_       │
│ - iv_rank       │     │ - greeks        │            │   concentration │
└────────┬────────┘     └────────┬────────┘            └────────┬────────┘
         │                       │                              │
         └───────────────────────┴──────────────────────────────┘
                                 │
                                 ▼
                     ┌─────────────────────┐
                     │   EXECUTOR SUBAGENT │
                     │                     │
                     │ Tools:              │
                     │ - find_contract     │
                     │ - check_liquidity   │
                     │ - place_order       │
                     │ - close_position    │
                     │ - execute_roll      │
                     │                     │
                     │ SAFETY GATES:       │
                     │ - Max 3 trades/day  │
                     │ - Spread < 15%      │
                     │ - Earnings blackout │
                     └─────────────────────┘
```

## Current vs AI-Native Flow

### Before (Current Implementation)

```
1. Timer fires every 60s
2. Python fetches UW API
3. Python scores signals
4. Python calls Claude for validation (single turn, stateless)
5. Claude returns JSON
6. Python parses JSON
7. Python checks safety gates
8. Python executes trade
9. Python sends Telegram
10. Context lost, repeat from step 1
```

### After (AI-Native)

```
1. Orchestrator agent runs with persistent context
2. Agent decides when to check flow (adaptive, not fixed timer)
3. Agent delegates to flow-scanner subagent
4. Flow-scanner uses tools, returns ranked signals
5. Orchestrator evaluates with FULL context:
   - Previous signals seen today
   - Current positions and their evolution
   - Market conditions (SPY, VIX)
   - Trading history and outcomes
6. If interested, delegates to risk-manager for approval
7. If approved, delegates to executor with specific instructions
8. Executor verifies liquidity, places order, reports result
9. Orchestrator updates state, sends Telegram notification
10. Context PRESERVED, agent continues monitoring
```

## File Structure

```
agent-sdk/
├── README.md                 # This file
├── requirements.txt          # Dependencies
├── agent_config.py           # Agent configuration
├── main.py                   # Entry point
├── agents/
│   ├── __init__.py
│   ├── definitions.py        # All agent prompt definitions
│   ├── orchestrator.py       # Main orchestrator logic
│   └── hooks.py              # PreToolUse/PostToolUse hooks
└── tools/
    ├── __init__.py
    ├── alpaca_mcp.py         # Alpaca trading tools
    ├── unusual_whales_mcp.py # UW flow scanning tools
    └── telegram_mcp.py       # Notification tools
```

## Installation

```bash
# Install dependencies
cd agent-sdk
pip install -r requirements.txt

# Set environment variables (same as parent project)
# ALPACA_API_KEY, ALPACA_SECRET_KEY, UW_API_KEY, ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN

# Run in shadow mode (logs decisions, doesn't execute)
python main.py --shadow

# Run live
python main.py
```

## State Persistence

The orchestrator maintains state across cycles and sessions via `trading_state.json`:

```
/home/ubuntu/momentum-agent/data/trading_state.json
```

### State Contents

| Field | Description |
|-------|-------------|
| `session_id` | Current session identifier |
| `trading_date` | Current trading date (resets daily counters) |
| `executions_today` | Number of trades executed today (max 3) |
| `positions` | Active positions synced from Alpaca |
| `signals` | Signals seen today (last 50) |
| `trades` | Trades executed today |
| `portfolio` | Portfolio summary (value, Greeks, risk score) |
| `market` | Market context (SPY, VIX, market hours) |
| `circuit_breaker` | Circuit breaker state |
| `recent_decisions` | Last 20 decisions for context |

### State Lifecycle

1. **Startup**: Load existing state or create new
2. **Each Cycle**: Sync positions/portfolio from Alpaca, update state
3. **After Cycle**: Save state to disk
4. **New Day**: Reset daily counters, preserve positions
5. **Session Resume**: Load previous state with new session ID

### Prompt Injection

Each cycle, the state is formatted and injected into the orchestrator's prompt:

```
============================================================
CURRENT TRADING STATE
============================================================
Session: orch-20260205-abc12345
Date: 2026-02-05

EXECUTION STATUS:
  Trades Today: 1/3
  Signals Seen: 12
  Executions Remaining: 2

PORTFOLIO:
  Total Value: $125,000.00
  Options Exposure: $4,500.00
  Net Delta: 45.2
  Risk Score: 35/100

ACTIVE POSITIONS (2):
  🟢 SPY CALL $500 exp 2026-02-21
     Entry: $3.50 | Current: $4.20 | P/L: +20.0%
  ...
============================================================
```

## Migration Roadmap

### Phase 1: Foundation (1-2 weeks)
- [x] Create MCP tool wrappers for existing functions
- [x] Implement basic orchestrator with subagents
- [x] Add session/state persistence
- [ ] Test single-agent flow

### Phase 2: Subagent Architecture (2-3 weeks)
- [x] Implement FlowScanner subagent
- [x] Implement PositionManager subagent
- [x] Implement RiskManager subagent
- [x] Implement Executor subagent
- [x] Orchestrator coordination logic

### Phase 3: Context & Memory (1-2 weeks)
- [x] Session resume/fork functionality
- [x] Signal history in context window
- [ ] Trade outcome feedback loop
- [ ] Compaction strategy for long sessions

### Phase 4: Production (2 weeks)
- [ ] Parallel paper trading vs current system
- [ ] Performance benchmarking
- [ ] Error handling and recovery
- [ ] Cost optimization

## Key Differences from Current System

| Aspect | Current | AI-Native |
|--------|---------|-----------|
| Context | Lost every cycle | Persistent across session |
| Decision timing | Fixed 60s polling | Agent-determined |
| Tool access | None (pre-formatted data) | Dynamic via MCP |
| Coordination | Manual Python orchestration | Agent delegation |
| Learning | Post-hoc analysis | In-context adaptation |
| Parallelism | Sequential | Subagents in parallel |

## Safety Guarantees

The AI-native system maintains all existing safety gates:

1. **Max 3 executions per day** - Enforced via PreToolUse hook
2. **Spread < 15%** - Checked before every order
3. **Earnings blackout** - 2 days before earnings
4. **Position limits** - Max 4 options positions
5. **Risk score < 50** - Portfolio-level check
6. **Sector concentration < 50%** - Diversification enforcement

These are implemented as hooks that CANNOT be bypassed by the agent.

## Monitoring

```bash
# View agent logs (logs stored in parent project's logs directory)
tail -f /home/ubuntu/momentum-agent/logs/agent-sdk/agent_$(date +%Y%m%d).log

# Follow via journalctl
sudo journalctl -u agent-sdk -f

# Check service status
sudo systemctl status agent-sdk

# Restart service
sudo systemctl restart agent-sdk
```
