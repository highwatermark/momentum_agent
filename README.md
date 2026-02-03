# Momentum Trading Agent

An automated momentum trading system that scans for high-momentum stocks, uses Claude AI for trade selection, executes trades via Alpaca, and monitors positions for reversal signals.

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        TELEGRAM BOT                              │
│                    (momentum-agent.service)                      │
│         Commands: /status /scan /execute /close /positions      │
└─────────────────────────┬───────────────────────────────────────┘
                          │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
┌───────────────┐ ┌───────────────┐ ┌───────────────┐
│   SCANNER     │ │    AGENT      │ │   EXECUTOR    │
│  scanner.py   │ │   agent.py    │ │  executor.py  │
│               │ │               │ │               │
│ - Fetch data  │ │ - Claude API  │ │ - Place orders│
│ - Calculate   │ │ - Reasoning   │ │ - Trailing    │
│   signals     │ │ - Decisions   │ │   stops       │
└───────────────┘ └───────────────┘ └───────────────┘
        │                                   │
        └───────────────┬───────────────────┘
                        ▼
              ┌───────────────────┐
              │     DATABASE      │
              │  data/trades.db   │
              └───────────────────┘
                        ▲
                        │
┌─────────────────────────────────────────────────────────────────┐
│                    POSITION MONITOR                              │
│               (position-monitor.timer)                           │
│            Runs every 30 min during market hours                 │
│         Detects reversals → Sends Telegram alerts                │
└─────────────────────────────────────────────────────────────────┘
```

## Components

| File | Description |
|------|-------------|
| `bot.py` | Telegram bot interface - all user interactions |
| `scanner.py` | Market scanner - fetches data & calculates momentum signals |
| `agent.py` | Claude AI integration for trade reasoning |
| `executor.py` | Alpaca order execution & position management |
| `monitor.py` | Position monitor for reversal signal detection |
| `db.py` | SQLite database for trade history, DQL training data, poor signals & metrics |
| `jobs.py` | Background jobs for DQL data collection & maintenance |
| `config.py` | Configuration parameters |
| `main.py` | CLI entry point for manual operations |

## Services

### 1. Telegram Bot (`momentum-agent.service`)

**Status:** Always running
**Restart:** Automatic on failure

```bash
# Control commands
sudo systemctl status momentum-agent
sudo systemctl restart momentum-agent
sudo journalctl -u momentum-agent -f  # View logs
```

### 2. Position Monitor (`position-monitor.timer`)

**Schedule:** Every 30 minutes on weekdays
**Market Hours Check:** Built-in (9:30 AM - 4:00 PM ET)

```bash
# Control commands
sudo systemctl status position-monitor.timer
sudo systemctl list-timers position-monitor.timer
sudo systemctl restart position-monitor.timer

# Manual run (bypass market hours check)
./venv/bin/python monitor.py --force
```

## Telegram Bot Commands

| Command | Description |
|---------|-------------|
| `/status` | Account overview + position monitor status |
| `/scan` | Run momentum scan for candidates |
| `/candidates` | View last scan results |
| `/execute SYMBOL` | Execute trade for symbol |
| `/close SYMBOL` | Close position manually |
| `/positions` | View current positions |
| `/orders` | View open orders |
| `/history` | Trade history |
| `/performance` | Signal performance stats |
| `/metrics` | Baseline performance since inception |
| `/weekly` | Last 7 days report with SPY comparison |
| `/monthly` | Last 30 days with weekly breakdown |
| `/export` | Export trades & candidates to CSV |
| `/error` | Show recent errors from logs |

## Configuration

### Environment Variables (`.env`)

```bash
# Alpaca API (Paper Trading)
ALPACA_API_KEY=your_key
ALPACA_SECRET_KEY=your_secret
ALPACA_BASE_URL=https://paper-api.alpaca.markets

# Anthropic API (Claude)
ANTHROPIC_API_KEY=your_key

# Telegram Bot
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_ADMIN_ID=your_telegram_user_id
```

### Trading Parameters (`config.py`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `min_price` | $5.00 | Minimum stock price |
| `min_avg_volume` | 500,000 | Minimum average daily volume |
| `min_volume_surge` | 1.3x | Today's volume vs 20D average |
| `min_sma_alignment` | True | Require 7 > 20 > 30 SMA |
| `min_close_position` | 0.6 | Where price closes in daily range |
| `min_roc_10d` | 3% | Minimum 10-day rate of change |
| `min_gap_up` | 1% | Minimum gap up for breakout |
| `max_positions` | 6 | Maximum total concurrent positions |
| `position_size_pct` | 10% | Portfolio allocation per trade |
| `max_portfolio_risk` | 60% | Maximum deployed capital |
| `trailing_stop_pct` | 5% | Trailing stop percentage |

### Per-Cap Configuration (`CAP_CONFIG`)

Different thresholds and limits per market cap category:

| Parameter | Large | Mid | Small |
|-----------|-------|-----|-------|
| `max_positions` | 2 | 2 | 2 |
| `max_buys_per_scan` | 2 | 2 | 2 |
| `min_volume_surge` | 1.3x | 1.3x | 1.5x |
| `min_gap_up` | 1% | 1% | 3% |
| `min_roc_10d` | 3% | 3% | 5% |

**Small caps require higher thresholds** to filter out noise and ensure stronger momentum signals.

### Universe Classification (`data/universe.json`)

| Category | Count | Market Cap |
|----------|-------|------------|
| Large | 108 | > $10B |
| Mid | 61 | $2B - $10B |
| Small | 42 | < $2B |

## Quick Filter (Stage 1)

The scanner uses a **two-stage filtering process**. Stage 1 uses real-time snapshots with time-normalized Relative Volume (RVOL):

| Filter | Threshold | Purpose |
|--------|-----------|---------|
| Price | > $5 | Liquidity requirement |
| Price Change | > 0.5% | Buyers in control |
| **RVOL** | >= 1.2x | Institutional interest |

**RVOL Calculation:**
```
projected_volume = current_volume / time_fraction
rvol = projected_volume / previous_day_volume
```

This normalizes volume by time-of-day, allowing early detection of unusual activity. A stock with 20M volume 1 hour into trading vs 80M full-day yesterday has RVOL = (20M / 0.15) / 80M = **1.67x** - strong institutional interest.

## Momentum Signals (Stage 2)

Candidates passing Stage 1 get deep analysis with 30-day OHLCV bars.

### Stage 2 Filter Requirements

Thresholds vary by market cap (see Per-Cap Configuration above):

| Filter | Large/Mid | Small | Description |
|--------|-----------|-------|-------------|
| **Gap Up** | >= 1% | >= 3% | Gap from yesterday's close |
| **Volume Surge** | >= 1.3x | >= 1.5x | vs 20-day average |
| **ROC 10D** | >= 3% | >= 5% | 10-day rate of change |

**Momentum Breakout** captures today's price action:
- **Gap + Follow-through**: Opened above threshold AND trading above today's open
- **5-Day Breakout**: Current price exceeding the 5-day high

### Composite Score (0-20)

| Signal | Points | Criteria |
|--------|--------|----------|
| SMA Aligned | +5 | 7-day > 20-day > 30-day SMA |
| Volume Surge | +2/+3 | >1.3x / >1.5x vs 20D avg |
| Momentum Breakout | +3 | Gap+follow-through OR 5D breakout |
| Intraday Strength | +1/+2 | >0.5 / >0.7 in today's range |
| 10D ROC | +2/+4 | >5% / >10% rate of change |
| Near 52W High | +3 | Within 5% of 52-week high |

## Reversal Signals (Monitor)

The monitor calculates a **reversal score** (0-13) for position alerts:

| Signal | Points | Criteria |
|--------|--------|----------|
| SMA Bearish Cross | +3 | 7-day < 20-day SMA |
| Close in Lower 30% | +2 | Price closes in lower 30% of daily range |
| Distribution Volume | +3 | Red day + volume > 1.5x average |
| RSI Breakdown | +2 | RSI was >70, now <60 |
| Failed Breakout | +3 | Hit 5-day high but closing red |

**Alert Thresholds & Actions:**

| Score | Severity | Action |
|-------|----------|--------|
| 0-2 | None | No action, logged only |
| 3-4 | Weak | Telegram alert sent, manual close recommended |
| **5+** | **Strong** | **AUTO-CLOSE** - Position automatically closed + logged as poor signal |

**Minimum Hold Protection** (added 2026-02-03):
- Positions held < 2 days are **protected from auto-close**
- Alerts still sent, but no automatic exit
- Allows positions to survive temporary whipsaws/pullbacks
- Reasoning: Same-day exits had 6.2% win rate vs 62.5% for 3+ day holds

The auto-close feature can be configured via Telegram bot commands (`/set autoclose on|off`, `/set threshold N`).

## Self-Learning Loop

The system tracks "poor signals" - trades that looked good at entry but triggered reversal exits - to improve signal quality over time.

### How It Works

1. **Detection**: When a position is auto-closed due to reversal (score >= 5), it's logged as a poor signal
2. **Pattern Analysis**: Entry signals that led to the poor trade are recorded for pattern recognition
3. **Agent Awareness**: The agent receives poor signal patterns when making decisions
4. **Weekly Review**: Poor signal summaries included in weekly/monthly reports

### Poor Signals Table

| Column | Description |
|--------|-------------|
| symbol | Stock symbol |
| entry_signals | JSON of original entry signals |
| exit_reason | Why position was closed |
| reversal_score | Score that triggered the close |
| reversal_signals | Which reversal signals fired |
| pnl_pct | Profit/loss at exit |
| holding_days | How long position was held |

### Skip-Buy Logic

When all open positions are "healthy" (reversal score < threshold), the system:
- **Logs scan results** for record-keeping
- **Skips new buys** to let winners run without interference
- Configurable via `skip_buys_when_healthy` and `healthy_threshold` in config

This prevents over-trading during strong momentum runs.

## Logs

| Log File | Description |
|----------|-------------|
| `logs/monitor.log` | Position monitor output |
| `journalctl -u momentum-agent` | Bot service logs |
| `journalctl -u position-monitor` | Monitor service logs |

View logs:
```bash
# Bot logs (live)
sudo journalctl -u momentum-agent -f

# Monitor logs
tail -f logs/monitor.log

# Last monitor run
sudo journalctl -u position-monitor -n 50
```

## Database Schema

Located at `data/trades.db`:

### trades
| Column | Type | Description |
|--------|------|-------------|
| symbol | TEXT | Stock symbol |
| entry_date | TEXT | Entry timestamp |
| entry_price | REAL | Entry price |
| quantity | INTEGER | Number of shares |
| signals | TEXT | JSON of entry signals |
| exit_date | TEXT | Exit timestamp |
| exit_price | REAL | Exit price |
| exit_reason | TEXT | Why position was closed |
| pnl_amount | REAL | Profit/loss in dollars |
| pnl_pct | REAL | Profit/loss percentage |
| status | TEXT | 'open' or 'closed' |

### scans
| Column | Type | Description |
|--------|------|-------------|
| scan_date | TEXT | When scan was run |
| candidates | TEXT | JSON of candidates found |
| decision | TEXT | JSON of agent decision |
| executed_symbol | TEXT | Symbol that was traded |

### position_checks
| Column | Type | Description |
|--------|------|-------------|
| check_time | TEXT | When check was run |
| symbol | TEXT | Stock symbol |
| score | INTEGER | Reversal score (0-13) |
| signals | TEXT | JSON of detected signals |
| pnl_pct | REAL | Current P/L percentage |
| alert_sent | INTEGER | 1 if alert was sent |

### market_snapshots (DQL Training)
| Column | Type | Description |
|--------|------|-------------|
| scan_id | INTEGER | Links to candidate_snapshots |
| timestamp | TEXT | When snapshot was taken |
| spy_price | REAL | SPY price at scan time |
| spy_change_1d | REAL | SPY daily change % |
| spy_trend | TEXT | up/down/sideways |
| vix_level | REAL | VIX proxy level |
| market_breadth | REAL | % advancing stocks |
| scan_type | TEXT | open/midday/close |

### candidate_snapshots (DQL Training)
| Column | Type | Description |
|--------|------|-------------|
| scan_id | INTEGER | Links to market_snapshots |
| symbol | TEXT | Stock symbol |
| price | REAL | Price at scan time |
| composite_score | INTEGER | Momentum score (0-20) |
| roc_5/roc_10 | REAL | Rate of change |
| rsi_14 | REAL | RSI indicator |
| atr_14 | REAL | Average true range |
| action | TEXT | candidate/filtered_out/skipped |
| price_Xd_later | REAL | Outcome prices (1/5/10 day) |

### daily_performance
| Column | Type | Description |
|--------|------|-------------|
| date | TEXT | Trading date |
| starting_equity | REAL | Equity at start |
| ending_equity | REAL | Equity at end |
| trades_opened | INTEGER | Trades opened today |
| trades_closed | INTEGER | Trades closed today |
| win_count | INTEGER | Winning trades |
| loss_count | INTEGER | Losing trades |
| spy_change | REAL | SPY daily change % |

### poor_signals (Self-Learning)
| Column | Type | Description |
|--------|------|-------------|
| symbol | TEXT | Stock symbol |
| trade_id | INTEGER | Links to trades table |
| entry_date | TEXT | Original entry date |
| exit_date | TEXT | When closed due to reversal |
| entry_price | REAL | Entry price |
| exit_price | REAL | Exit price |
| pnl_pct | REAL | Profit/loss percentage |
| holding_days | INTEGER | Days held before reversal |
| reversal_score | INTEGER | Score that triggered close |
| reversal_signals | TEXT | JSON of reversal signals |
| entry_signals | TEXT | JSON of original entry signals |
| reviewed | INTEGER | 0=unreviewed, 1=reviewed |

## Stock Universe

Located at `data/universe.json`:
- 211 liquid US stocks
- Sources: S&P 500, NASDAQ 100, high-volume mid-caps
- All symbols validated against Alpaca API
- Updated as needed

## Manual CLI Usage

```bash
# Run momentum scan (dry run - no trades)
./venv/bin/python main.py scan --dry-run

# Run momentum scan (live execution)
./venv/bin/python main.py scan

# Check positions
./venv/bin/python main.py positions

# View trade history
./venv/bin/python main.py history

# Run position monitor manually
./venv/bin/python monitor.py --force
```

## Installation

```bash
# Clone and setup
cd /home/ubuntu/momentum-agent
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Install systemd services
sudo cp momentum-agent.service /etc/systemd/system/
sudo cp position-monitor.service /etc/systemd/system/
sudo cp position-monitor.timer /etc/systemd/system/

# Enable and start services
sudo systemctl daemon-reload
sudo systemctl enable momentum-agent
sudo systemctl start momentum-agent
sudo systemctl enable position-monitor.timer
sudo systemctl start position-monitor.timer
```

## Workflow

### Entry Flow
1. User triggers `/scan` or scheduled scan runs
2. Scanner fetches snapshots for ~150 stocks
3. Quick filter: price >$5, up >1%, volume increasing
4. Deep analysis: 30-day bars, calculate all signals
5. Filter by minimum criteria, return top 10
6. Claude AI analyzes candidates with trade history
7. Returns TRADE/SKIP/WATCH decisions with reasoning
8. User executes with `/execute SYMBOL`
9. Market order + trailing stop placed via Alpaca

### Exit Flow
1. **Trailing Stop:** 5% trailing stop triggers automatically (covers entire position)
2. **Auto-Close:** Monitor detects strong reversal (score >= 5), auto-closes position + logs as poor signal
3. **Alert-based:** Monitor detects weak reversal (score 3-4), user decides
4. **Manual:** User sends `/close SYMBOL`

### Self-Learning Flow
1. Position closed due to reversal → logged to `poor_signals` table
2. Entry signals that led to reversal are recorded
3. Agent receives poor signal patterns in next scan prompt
4. Weekly reports summarize poor signals with recommendations
5. User reviews and adjusts agent prompt if patterns emerge

### Trailing Stop Behavior
- When adding to an existing position, any existing trailing stops are cancelled
- A new trailing stop is placed covering the **entire position** (old + new shares)
- This ensures consistent protection across the full holding

### Monitoring Flow (every 30 min on weekdays)
1. Timer triggers `position-monitor.service`
2. Script checks if within market hours (9:30 AM - 4:00 PM ET)
3. Fetches all stock positions (excludes options)
4. For each position:
   - Fetch 30-day historical bars
   - Calculate reversal signals (score 0-13)
   - Log to database
   - **Score >= 5:** Auto-close position, log as poor signal, send notification
   - **Score 3-4:** Send Telegram alert, user decides
5. User can respond with `/close SYMBOL` for manual exit

### Scan Locking
- File-based lock prevents concurrent scans from interfering
- Only one scan can run at a time across all processes
- Lock automatically released when scan completes

### Skip-Buy Mode
When `skip_buys_when_healthy` is enabled and all positions have reversal score < `healthy_threshold`:
1. Scan runs normally, candidates found
2. Results logged to database for tracking
3. **No new buys executed** - lets winners run
4. Telegram summary indicates "SKIP MODE"

## Background Jobs (DQL Training)

Background jobs run via cron to collect training data for future DQL model development.

### Job Schedule (UTC)

| Job | Schedule | Time (ET) | Purpose |
|-----|----------|-----------|---------|
| `daily_snapshot` | 21:05 M-F | 4:05 PM | Log daily performance & equity |
| `update_outcomes` | 22:30 M-F | 5:30 PM | Fill in price_Xd_later for candidates |
| `backfill_dqn` | 22:45 M-F | 5:45 PM | Add closed trades to DQN experiences (added 2026-02-03) |
| `update_tracking` | */30 14-21 M-F | Every 30min | Track max gain/drawdown for open positions |
| `cleanup` | 0:00 Sunday | Midnight | Remove data older than 90 days |

### Running Jobs Manually

```bash
# Run all daily jobs
./venv/bin/python jobs.py all

# Run individual jobs
./venv/bin/python jobs.py daily_snapshot
./venv/bin/python jobs.py update_outcomes
./venv/bin/python jobs.py backfill_dqn
./venv/bin/python jobs.py update_tracking
./venv/bin/python jobs.py cleanup
```
