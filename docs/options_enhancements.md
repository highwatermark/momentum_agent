# Options Flow System - Enhancements & Safety Fixes

This document addresses blindspots, edge cases, and recommended improvements for the options flow trading system.

---

## Critical Blindspots Identified

### 1. Options Liquidity & Execution
| Issue | Risk | Current State |
|-------|------|---------------|
| Wide bid-ask spreads | 20-50% loss on entry | Not checked |
| Market orders | Poor fills, slippage | Default order type |
| Illiquid contracts | Can't exit when needed | No OI minimum |

### 2. Greeks & Risk Exposure
| Issue | Risk | Current State |
|-------|------|---------------|
| No delta tracking | Unknown directional exposure | Not tracked |
| Theta decay | Silent P/L drain | Not modeled |
| Vega exposure | IV crush kills trades | Not tracked |
| Gamma near expiry | Wild P/L swings | Not warned |

### 3. Flow Signal Interpretation
| Issue | Risk | Current State |
|-------|------|---------------|
| Hedging vs directional | Misread intent | Not distinguished |
| Closing trades | False "unusual" signal | Partial filter |
| Multi-leg spreads | Legs misread as directional | Not detected |
| Market maker flow | Not informed flow | Not filtered |

### 4. Timing & Staleness
| Issue | Risk | Current State |
|-------|------|---------------|
| Signal delay | Smart money already moved | Not tracked |
| Delayed quotes | Bad execution decisions | Snapshot only |
| Pre-earnings flow | Already priced in | Not flagged |

### 5. Position Management Gaps
| Issue | Risk | Current State |
|-------|------|---------------|
| No rolling logic | Theta decay to zero | Not implemented |
| All-or-nothing exits | Miss partial profits | No scaling |
| Assignment risk | Unexpected stock position | Not tracked |
| No adjustments | Can't hedge losers | Not implemented |

### 6. System Risks
| Issue | Risk | Current State |
|-------|------|---------------|
| API rate limits | Scan failures | Unknown limits |
| No retry logic | Missed opportunities | Single attempt |
| Position drift | DB ≠ reality | No reconciliation |

---

## Priority Fixes

### HIGH Priority (Implement First)

#### 1. Limit Orders Instead of Market Orders

**File: `options_executor.py`**

```python
def get_option_quote(contract_symbol: str) -> Dict:
    """Get current bid/ask for an option contract"""
    from alpaca.data.historical.option import OptionHistoricalDataClient
    from alpaca.data.requests import OptionLatestQuoteRequest

    client = OptionHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)

    try:
        request = OptionLatestQuoteRequest(symbol_or_symbols=[contract_symbol])
        quotes = client.get_option_latest_quote(request)

        if contract_symbol in quotes:
            quote = quotes[contract_symbol]
            bid = float(quote.bid_price) if quote.bid_price else 0
            ask = float(quote.ask_price) if quote.ask_price else 0
            mid = (bid + ask) / 2 if bid and ask else 0
            spread = ask - bid if bid and ask else 0
            spread_pct = (spread / mid * 100) if mid > 0 else 100

            return {
                "bid": bid,
                "ask": ask,
                "mid": mid,
                "spread": spread,
                "spread_pct": spread_pct,
                "bid_size": quote.bid_size,
                "ask_size": quote.ask_size,
            }
    except Exception as e:
        print(f"Error getting option quote: {e}")

    return {"bid": 0, "ask": 0, "mid": 0, "spread": 0, "spread_pct": 100}


def place_options_order_smart(
    contract_symbol: str,
    quantity: int,
    side: str = "buy",
    max_spread_pct: float = 15.0,
    signal_data: Dict = None,
) -> Dict:
    """
    Place options order with smart limit pricing

    - Gets current quote
    - Checks spread width
    - Places limit order at favorable price
    """
    # Get current quote
    quote = get_option_quote(contract_symbol)

    # Check spread
    if quote["spread_pct"] > max_spread_pct:
        return {
            "success": False,
            "error": f"Spread too wide: {quote['spread_pct']:.1f}% (max {max_spread_pct}%)",
            "quote": quote,
        }

    # Calculate limit price
    if side.lower() == "buy":
        # Buy at mid or slightly above (willing to pay up a bit)
        limit_price = round(quote["mid"] * 1.02, 2)  # 2% above mid
        limit_price = min(limit_price, quote["ask"])  # But not above ask
    else:
        # Sell at mid or slightly below
        limit_price = round(quote["mid"] * 0.98, 2)  # 2% below mid
        limit_price = max(limit_price, quote["bid"])  # But not below bid

    # Place limit order
    return place_options_order(
        contract_symbol=contract_symbol,
        quantity=quantity,
        side=side,
        order_type="limit",
        limit_price=limit_price,
        signal_data=signal_data,
    )
```

#### 2. Bid-Ask Spread & Liquidity Check

**File: `options_executor.py`**

```python
# Add to OPTIONS_CONFIG
OPTIONS_SAFETY = {
    "max_spread_pct": 15.0,       # Max 15% bid-ask spread
    "min_open_interest": 100,     # Minimum OI for liquidity
    "min_volume": 10,             # Minimum daily volume
    "min_bid": 0.05,              # Minimum bid price (avoid pennies)
}


def check_option_liquidity(contract_symbol: str) -> Dict:
    """
    Check if option contract is liquid enough to trade

    Returns:
        Dict with 'liquid' bool and 'reasons' list if not liquid
    """
    quote = get_option_quote(contract_symbol)
    reasons = []

    # Check spread
    if quote["spread_pct"] > OPTIONS_SAFETY["max_spread_pct"]:
        reasons.append(f"Spread {quote['spread_pct']:.1f}% > {OPTIONS_SAFETY['max_spread_pct']}%")

    # Check bid exists
    if quote["bid"] < OPTIONS_SAFETY["min_bid"]:
        reasons.append(f"Bid ${quote['bid']:.2f} too low")

    # Check size
    min_size = OPTIONS_SAFETY.get("min_bid_size", 10)
    if quote.get("bid_size", 0) < min_size:
        reasons.append(f"Bid size {quote.get('bid_size', 0)} < {min_size}")

    return {
        "liquid": len(reasons) == 0,
        "reasons": reasons,
        "quote": quote,
    }


def check_contract_liquidity(
    underlying: str,
    option_type: str,
    strike: float,
    expiration: str,
) -> Dict:
    """Check liquidity before finding/trading contract"""
    # Get contract info from Alpaca
    contract = find_option_contract(
        underlying=underlying,
        option_type=option_type,
        target_strike=strike,
        target_expiration=expiration,
    )

    if not contract:
        return {"liquid": False, "reasons": ["Contract not found"]}

    return check_option_liquidity(contract["symbol"])
```

#### 3. Position Reconciliation

**File: `options_executor.py`**

```python
def reconcile_options_positions() -> Dict:
    """
    Compare database positions with actual Alpaca positions

    Returns:
        Dict with mismatches and actions needed
    """
    from db import get_open_options_trades, update_options_trade_exit

    # Get actual positions from Alpaca
    actual_positions = get_options_positions()
    actual_contracts = {p.contract_symbol: p for p in actual_positions}

    # Get DB positions
    db_trades = get_open_options_trades()
    db_contracts = {t["contract_symbol"]: t for t in db_trades}

    mismatches = {
        "in_db_not_alpaca": [],      # DB says open, but not in Alpaca
        "in_alpaca_not_db": [],      # In Alpaca, but not in DB
        "quantity_mismatch": [],      # Different quantities
    }

    # Check DB positions against Alpaca
    for contract, db_trade in db_contracts.items():
        if contract not in actual_contracts:
            mismatches["in_db_not_alpaca"].append({
                "contract": contract,
                "db_qty": db_trade["quantity"],
                "action": "Mark as closed in DB (likely filled exit order)",
            })
            # Auto-fix: mark as closed
            update_options_trade_exit(
                contract_symbol=contract,
                exit_price=0,  # Unknown
                exit_reason="reconciliation_closed",
            )
        else:
            actual = actual_contracts[contract]
            if actual.quantity != db_trade["quantity"]:
                mismatches["quantity_mismatch"].append({
                    "contract": contract,
                    "db_qty": db_trade["quantity"],
                    "actual_qty": actual.quantity,
                })

    # Check Alpaca positions against DB
    for contract, actual in actual_contracts.items():
        if contract not in db_contracts:
            mismatches["in_alpaca_not_db"].append({
                "contract": contract,
                "actual_qty": actual.quantity,
                "action": "Add to DB (manual trade or missed log)",
            })

    return {
        "synced": all(len(v) == 0 for v in mismatches.values()),
        "mismatches": mismatches,
        "actual_count": len(actual_positions),
        "db_count": len(db_trades),
    }
```

**Add to `bot.py`:**
```python
@admin_only
async def cmd_reconcile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reconcile options positions between DB and Alpaca"""
    from options_executor import reconcile_options_positions

    result = reconcile_options_positions()

    if result["synced"]:
        await update.message.reply_text(
            f"Options positions synced\n"
            f"Alpaca: {result['actual_count']} | DB: {result['db_count']}"
        )
    else:
        msg = "*Position Mismatches Found*\n\n"

        for mtype, items in result["mismatches"].items():
            if items:
                msg += f"*{mtype}:*\n"
                for item in items:
                    msg += f"  - {item['contract']}: {item.get('action', '')}\n"
                msg += "\n"

        await update.message.reply_text(msg, parse_mode="Markdown")
```

---

### MEDIUM Priority

#### 4. Greeks Tracking

**File: `options_executor.py`**

```python
from dataclasses import dataclass
from typing import Optional
import math


@dataclass
class PositionGreeks:
    """Greeks for a single position"""
    delta: float
    gamma: float
    theta: float  # Daily decay in $
    vega: float

    def __mul__(self, quantity: int):
        """Scale Greeks by position size"""
        return PositionGreeks(
            delta=self.delta * quantity * 100,  # Per 100 shares
            gamma=self.gamma * quantity * 100,
            theta=self.theta * quantity * 100,
            vega=self.vega * quantity * 100,
        )


def estimate_greeks(
    option_type: str,
    underlying_price: float,
    strike: float,
    days_to_exp: int,
    iv: float,
    risk_free_rate: float = 0.05,
) -> PositionGreeks:
    """
    Estimate option Greeks using Black-Scholes approximation

    Note: For production, use Alpaca's Greeks if available or a proper options library
    """
    if days_to_exp <= 0:
        return PositionGreeks(delta=0, gamma=0, theta=0, vega=0)

    T = days_to_exp / 365
    S = underlying_price
    K = strike
    r = risk_free_rate
    sigma = iv

    # Simplified Black-Scholes Greeks
    try:
        d1 = (math.log(S/K) + (r + sigma**2/2)*T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)

        # Normal CDF approximation
        def norm_cdf(x):
            return (1 + math.erf(x / math.sqrt(2))) / 2

        def norm_pdf(x):
            return math.exp(-x**2/2) / math.sqrt(2 * math.pi)

        if option_type.lower() == "call":
            delta = norm_cdf(d1)
        else:
            delta = norm_cdf(d1) - 1

        gamma = norm_pdf(d1) / (S * sigma * math.sqrt(T))
        theta = -(S * norm_pdf(d1) * sigma) / (2 * math.sqrt(T)) / 365  # Per day
        vega = S * norm_pdf(d1) * math.sqrt(T) / 100  # Per 1% IV change

        return PositionGreeks(
            delta=round(delta, 4),
            gamma=round(gamma, 6),
            theta=round(theta, 4),
            vega=round(vega, 4),
        )
    except Exception as e:
        print(f"Greeks calculation error: {e}")
        return PositionGreeks(delta=0, gamma=0, theta=0, vega=0)


def get_portfolio_greeks() -> Dict:
    """
    Calculate aggregate Greeks across all options positions
    """
    positions = get_options_positions()

    if not positions:
        return {
            "net_delta": 0,
            "total_gamma": 0,
            "daily_theta": 0,
            "total_vega": 0,
            "positions": [],
        }

    # Get current prices and IV for each underlying
    from flow_scanner import UnusualWhalesClient
    uw_client = UnusualWhalesClient()

    position_greeks = []
    totals = {"delta": 0, "gamma": 0, "theta": 0, "vega": 0}

    for pos in positions:
        try:
            # Get IV from UW
            iv_data = uw_client.get_iv_rank(pos.symbol)
            iv = (iv_data.get("iv", 30) or 30) / 100  # Convert to decimal

            # Calculate DTE
            from datetime import datetime
            exp_date = datetime.strptime(pos.expiration, "%Y-%m-%d")
            dte = max(1, (exp_date - datetime.now()).days)

            # Estimate Greeks
            greeks = estimate_greeks(
                option_type=pos.option_type,
                underlying_price=pos.current_price,
                strike=pos.strike,
                days_to_exp=dte,
                iv=iv,
            )

            # Scale by quantity
            scaled = greeks * pos.quantity

            position_greeks.append({
                "symbol": pos.symbol,
                "contract": pos.contract_symbol,
                "quantity": pos.quantity,
                "delta": scaled.delta,
                "gamma": scaled.gamma,
                "theta": scaled.theta,
                "vega": scaled.vega,
            })

            totals["delta"] += scaled.delta
            totals["gamma"] += scaled.gamma
            totals["theta"] += scaled.theta
            totals["vega"] += scaled.vega

        except Exception as e:
            print(f"Error calculating Greeks for {pos.symbol}: {e}")

    return {
        "net_delta": round(totals["delta"], 1),
        "total_gamma": round(totals["gamma"], 2),
        "daily_theta": round(totals["theta"], 2),  # Daily $ decay
        "total_vega": round(totals["vega"], 2),
        "positions": position_greeks,
    }
```

**Add to `/options` command in `bot.py`:**
```python
# In cmd_options, add Greeks summary:
greeks = get_portfolio_greeks()

msg += f"\n*Portfolio Greeks:*\n"
msg += f"|- Net Delta: {greeks['net_delta']:+.0f} shares equivalent\n"
msg += f"|- Daily Theta: ${greeks['daily_theta']:+.2f}/day\n"
msg += f"|- Gamma: {greeks['total_gamma']:.2f}\n"
msg += f"|- Vega: {greeks['total_vega']:.2f}\n"

# Warnings
if abs(greeks['net_delta']) > 500:
    msg += f"\n High delta exposure ({greeks['net_delta']:+.0f})"
if greeks['daily_theta'] < -50:
    msg += f"\n High theta decay (${greeks['daily_theta']:.0f}/day)"
```

#### 5. DTE Alerts & Roll Suggestions

**File: `options_executor.py`**

```python
def check_expiration_risk() -> List[Dict]:
    """
    Check positions approaching expiration

    Returns list of positions needing attention with suggested actions
    """
    positions = get_options_positions()
    alerts = []

    for pos in positions:
        try:
            exp_date = datetime.strptime(pos.expiration, "%Y-%m-%d")
            dte = (exp_date - datetime.now()).days

            alert = None

            if dte <= 0:
                alert = {
                    "position": pos,
                    "dte": dte,
                    "severity": "CRITICAL",
                    "message": "EXPIRED - Close immediately",
                    "action": "close",
                }
            elif dte <= 3:
                alert = {
                    "position": pos,
                    "dte": dte,
                    "severity": "HIGH",
                    "message": f"Expiring in {dte} days - Consider closing or rolling",
                    "action": "close_or_roll",
                }
            elif dte <= 7:
                alert = {
                    "position": pos,
                    "dte": dte,
                    "severity": "MEDIUM",
                    "message": f"{dte} DTE - Monitor theta decay",
                    "action": "monitor",
                }

            # Check if ITM (assignment risk for American options)
            if alert and pos.option_type == "call" and pos.current_price > pos.strike:
                alert["message"] += " ITM - Assignment risk"
            elif alert and pos.option_type == "put" and pos.current_price < pos.strike:
                alert["message"] += " ITM - Assignment risk"

            if alert:
                alerts.append(alert)

        except Exception as e:
            print(f"Error checking expiration for {pos.contract_symbol}: {e}")

    return sorted(alerts, key=lambda x: x["dte"])


def suggest_roll(position: OptionsPosition) -> Dict:
    """
    Suggest a roll for an expiring position
    """
    # Find same strike, later expiration
    new_exp_min = 21  # At least 3 weeks out
    new_exp_max = 45  # Not too far

    new_contract = find_option_contract(
        underlying=position.symbol,
        option_type=position.option_type,
        target_strike=position.strike,
        min_dte=new_exp_min,
        max_dte=new_exp_max,
    )

    if not new_contract:
        return {"can_roll": False, "reason": "No suitable contract found"}

    # Get quotes for both
    old_quote = get_option_quote(position.contract_symbol)
    new_quote = get_option_quote(new_contract["symbol"])

    roll_cost = new_quote["mid"] - old_quote["mid"]

    return {
        "can_roll": True,
        "current_contract": position.contract_symbol,
        "new_contract": new_contract["symbol"],
        "new_expiration": new_contract["expiration"],
        "roll_cost": roll_cost,  # Positive = debit, Negative = credit
        "current_value": old_quote["mid"],
        "new_value": new_quote["mid"],
    }
```

**Add `/expirations` command to `bot.py`:**
```python
@admin_only
async def cmd_expirations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check expiring options positions"""
    from options_executor import check_expiration_risk, suggest_roll

    alerts = check_expiration_risk()

    if not alerts:
        await update.message.reply_text("No expiration concerns. All positions have adequate time.")
        return

    msg = "*Expiration Alerts*\n\n"

    for alert in alerts:
        pos = alert["position"]
        severity_emoji = {"CRITICAL": "RED", "HIGH": "ORANGE", "MEDIUM": "YELLOW"}.get(alert["severity"], "WHITE")

        msg += f"{severity_emoji} *{pos.symbol}* {pos.option_type.upper()} ${pos.strike}\n"
        msg += f"   DTE: {alert['dte']} | {alert['message']}\n"

        if alert["action"] in ["close_or_roll", "close"]:
            roll = suggest_roll(pos)
            if roll["can_roll"]:
                cost_str = f"${roll['roll_cost']:.2f} debit" if roll["roll_cost"] > 0 else f"${abs(roll['roll_cost']):.2f} credit"
                msg += f"   Roll to {roll['new_expiration']}: {cost_str}\n"

        msg += "\n"

    msg += "Use `/closeoption CONTRACT` to close or contact for roll execution."

    await update.message.reply_text(msg, parse_mode="Markdown")
```

#### 6. Sector Concentration Check

**File: `options_executor.py`**

```python
# Sector mapping (expand as needed)
SECTOR_MAP = {
    # Tech
    "AAPL": "tech", "MSFT": "tech", "GOOGL": "tech", "GOOG": "tech", "META": "tech",
    "NVDA": "tech", "AMD": "tech", "INTC": "tech", "CRM": "tech", "ORCL": "tech",
    "ADBE": "tech", "NOW": "tech", "SNOW": "tech", "PLTR": "tech", "NET": "tech",

    # Finance
    "JPM": "finance", "BAC": "finance", "WFC": "finance", "GS": "finance", "MS": "finance",
    "C": "finance", "BLK": "finance", "SCHW": "finance", "V": "finance", "MA": "finance",

    # Healthcare
    "UNH": "healthcare", "JNJ": "healthcare", "PFE": "healthcare", "ABBV": "healthcare",
    "MRK": "healthcare", "LLY": "healthcare", "TMO": "healthcare", "ABT": "healthcare",

    # Energy
    "XOM": "energy", "CVX": "energy", "COP": "energy", "SLB": "energy", "EOG": "energy",

    # Consumer
    "AMZN": "consumer", "TSLA": "consumer", "HD": "consumer", "NKE": "consumer",
    "MCD": "consumer", "SBUX": "consumer", "TGT": "consumer", "WMT": "consumer",

    # Industrial
    "CAT": "industrial", "DE": "industrial", "BA": "industrial", "HON": "industrial",
    "UPS": "industrial", "RTX": "industrial", "LMT": "industrial", "GE": "industrial",

    # ETFs
    "SPY": "index", "QQQ": "index", "IWM": "index", "DIA": "index",
}


def get_sector(symbol: str) -> str:
    """Get sector for a symbol"""
    return SECTOR_MAP.get(symbol.upper(), "other")


def check_sector_concentration() -> Dict:
    """
    Check sector concentration across options positions
    """
    positions = get_options_positions()

    if not positions:
        return {"concentrated": False, "sectors": {}}

    sector_values = {}
    total_value = 0

    for pos in positions:
        sector = get_sector(pos.symbol)
        value = abs(pos.market_value)
        sector_values[sector] = sector_values.get(sector, 0) + value
        total_value += value

    # Calculate percentages
    sector_pcts = {
        sector: (value / total_value * 100) if total_value > 0 else 0
        for sector, value in sector_values.items()
    }

    # Check if any sector > 50%
    max_sector = max(sector_pcts.items(), key=lambda x: x[1]) if sector_pcts else ("none", 0)
    concentrated = max_sector[1] > 50

    return {
        "concentrated": concentrated,
        "max_sector": max_sector[0],
        "max_sector_pct": max_sector[1],
        "sectors": sector_pcts,
        "warning": f"{max_sector[1]:.0f}% in {max_sector[0]}" if concentrated else None,
    }


def can_add_position(symbol: str, max_sector_pct: float = 50.0) -> Tuple[bool, str]:
    """
    Check if adding a position would violate sector concentration limits
    """
    sector = get_sector(symbol)
    concentration = check_sector_concentration()

    current_pct = concentration["sectors"].get(sector, 0)

    # Estimate new position would add ~25% to sector (rough)
    estimated_new_pct = current_pct + 25

    if estimated_new_pct > max_sector_pct:
        return False, f"Would exceed {max_sector_pct}% in {sector} sector (currently {current_pct:.0f}%)"

    return True, "OK"
```

---

### LOW Priority (Nice to Have)

#### 7. Signal Outcome Tracking (Learning Loop)

**File: `db.py`**

```sql
-- Add to init_options_tables()
CREATE TABLE IF NOT EXISTS flow_signal_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER,
    symbol TEXT,
    signal_score INTEGER,

    -- Signal characteristics
    was_sweep INTEGER,
    was_ask_side INTEGER,
    was_floor INTEGER,
    was_opening INTEGER,
    premium_tier TEXT,  -- 'high' (100K+), 'very_high' (250K+)
    vol_oi_tier TEXT,   -- 'high' (>1), 'very_high' (>3)

    -- Outcome
    entry_price REAL,
    max_price REAL,
    min_price REAL,
    exit_price REAL,
    max_gain_pct REAL,
    max_loss_pct REAL,
    actual_pnl_pct REAL,
    holding_days INTEGER,
    exit_reason TEXT,

    -- Win/loss flags for analysis
    was_winner INTEGER,  -- 1 if profit, 0 if loss
    hit_target INTEGER,  -- 1 if hit 50% profit target
    hit_stop INTEGER,    -- 1 if hit 50% stop loss

    created_at TEXT,
    FOREIGN KEY (signal_id) REFERENCES flow_signals(id)
);

CREATE INDEX IF NOT EXISTS idx_outcomes_score ON flow_signal_outcomes(signal_score);
CREATE INDEX IF NOT EXISTS idx_outcomes_winner ON flow_signal_outcomes(was_winner);
```

```python
def get_signal_factor_performance() -> Dict:
    """
    Analyze which signal factors correlate with winning trades
    """
    conn = get_connection()
    cursor = conn.cursor()

    factors = ["was_sweep", "was_ask_side", "was_floor", "was_opening"]
    results = {}

    for factor in factors:
        cursor.execute(f"""
            SELECT
                {factor} as factor_value,
                COUNT(*) as total,
                SUM(was_winner) as wins,
                AVG(actual_pnl_pct) as avg_pnl
            FROM flow_signal_outcomes
            WHERE {factor} IS NOT NULL
            GROUP BY {factor}
        """)

        rows = cursor.fetchall()
        results[factor] = [
            {
                "present": bool(row["factor_value"]),
                "total": row["total"],
                "wins": row["wins"],
                "win_rate": (row["wins"] / row["total"] * 100) if row["total"] > 0 else 0,
                "avg_pnl": row["avg_pnl"] or 0,
            }
            for row in rows
        ]

    conn.close()
    return results


def get_score_tier_performance() -> Dict:
    """
    Analyze win rate by signal score tier
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            CASE
                WHEN signal_score >= 15 THEN 'elite (15+)'
                WHEN signal_score >= 12 THEN 'high (12-14)'
                WHEN signal_score >= 10 THEN 'medium (10-11)'
                ELSE 'low (8-9)'
            END as tier,
            COUNT(*) as total,
            SUM(was_winner) as wins,
            AVG(actual_pnl_pct) as avg_pnl,
            AVG(max_gain_pct) as avg_max_gain,
            AVG(max_loss_pct) as avg_max_loss
        FROM flow_signal_outcomes
        GROUP BY tier
        ORDER BY signal_score DESC
    """)

    rows = cursor.fetchall()
    conn.close()

    return [
        {
            "tier": row["tier"],
            "total": row["total"],
            "win_rate": (row["wins"] / row["total"] * 100) if row["total"] > 0 else 0,
            "avg_pnl": row["avg_pnl"] or 0,
            "avg_max_gain": row["avg_max_gain"] or 0,
            "avg_max_loss": row["avg_max_loss"] or 0,
        }
        for row in rows
    ]
```

#### 8. Earnings Blackout

**File: `options_executor.py`**

```python
def check_earnings_blackout(symbol: str, blackout_days: int = 2) -> Tuple[bool, Optional[str]]:
    """
    Check if symbol is in earnings blackout period

    Returns:
        (is_blocked, earnings_date or None)
    """
    from flow_scanner import UnusualWhalesClient

    client = UnusualWhalesClient()
    earnings = client.get_earnings(symbol)

    if not earnings or not earnings.get("next_earnings_date"):
        return False, None

    try:
        earnings_date = datetime.strptime(earnings["next_earnings_date"][:10], "%Y-%m-%d")
        days_to_earnings = (earnings_date - datetime.now()).days

        if 0 <= days_to_earnings <= blackout_days:
            return True, earnings["next_earnings_date"][:10]
    except:
        pass

    return False, None
```

**Add check to `execute_flow_trade()`:**
```python
# Add at start of execute_flow_trade()
blocked, earnings_date = check_earnings_blackout(signal.symbol)
if blocked:
    return {
        "success": False,
        "error": f"Earnings blackout: {signal.symbol} reports on {earnings_date}",
        "symbol": signal.symbol,
    }
```

---

## Configuration Summary

**Add to `config.py`:**

```python
# Options Safety Limits
OPTIONS_SAFETY = {
    # Liquidity
    "max_spread_pct": 15.0,
    "min_open_interest": 100,
    "min_volume": 10,
    "min_bid": 0.05,

    # Concentration
    "max_single_sector_pct": 50.0,
    "max_single_underlying_pct": 30.0,

    # Time
    "earnings_blackout_days": 2,
    "roll_alert_dte": 7,
    "critical_dte": 3,

    # Greeks
    "max_portfolio_delta": 500,
    "max_daily_theta": -100,  # Max $100/day decay

    # Execution
    "use_limit_orders": True,
    "limit_price_buffer_pct": 2.0,  # % above mid for buys
}
```

---

## Testing Checklist

- [ ] Limit orders execute with reasonable fills
- [ ] Spread check rejects illiquid options
- [ ] Position reconciliation catches mismatches
- [ ] Greeks calculation produces reasonable values
- [ ] DTE alerts fire at correct thresholds
- [ ] Sector concentration blocks over-concentration
- [ ] Earnings blackout prevents pre-earnings trades
- [ ] Signal outcome tracking logs correctly

---

## Bot Commands Summary

| Command | Purpose |
|---------|---------|
| `/flow` | Scan for flow signals |
| `/analyze` | Generate Claude theses |
| `/options` | View positions + Greeks |
| `/buyoption SYMBOL` | Execute trade |
| `/closeoption CONTRACT` | Close position |
| `/expirations` | Check DTE alerts |
| `/reconcile` | Sync DB with Alpaca |
| `/flowperf` | Signal factor performance |
