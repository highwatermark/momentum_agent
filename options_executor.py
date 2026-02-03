"""
Options Executor - Place and manage options orders via Alpaca
"""
import json
import math
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest,
    LimitOrderRequest,
    GetOrdersRequest,
    GetOptionContractsRequest,
    ClosePositionRequest
)
from alpaca.trading.enums import (
    OrderSide,
    TimeInForce,
    OrderStatus,
    QueryOrderStatus,
    AssetClass,
    ContractType
)

from config import ALPACA_API_KEY, ALPACA_SECRET_KEY, OPTIONS_CONFIG, OPTIONS_SAFETY
from db import (
    log_options_trade,
    update_options_trade_exit,
    get_options_trade_by_contract,
    get_open_options_trades,
    get_options_performance,
    mark_flow_signal_executed,
)


@dataclass
class OptionsPosition:
    """Represents an options position"""
    symbol: str                    # Underlying symbol
    contract_symbol: str           # Full contract symbol (e.g., AAPL240315C00175000)
    option_type: str               # 'call' or 'put'
    strike: float
    expiration: str
    quantity: int
    avg_entry_price: float
    current_price: float
    market_value: float
    unrealized_pl: float
    unrealized_plpc: float


@dataclass
class PositionGreeks:
    """Greeks for a single position"""
    delta: float
    gamma: float
    theta: float  # Daily decay in $
    vega: float
    iv: float = 0.0  # Implied volatility

    def scale(self, quantity: int) -> 'PositionGreeks':
        """Scale Greeks by position size (100 shares per contract)"""
        return PositionGreeks(
            delta=self.delta * quantity * 100,  # Per 100 shares
            gamma=self.gamma * quantity * 100,
            theta=self.theta * quantity * 100,
            vega=self.vega * quantity * 100,
            iv=self.iv,
        )

    def to_dict(self) -> Dict:
        """Convert to dictionary for logging"""
        return {
            "delta": round(self.delta, 4),
            "gamma": round(self.gamma, 6),
            "theta": round(self.theta, 4),
            "vega": round(self.vega, 4),
            "iv": round(self.iv, 4),
        }


# Sector mapping for concentration checks
SECTOR_MAP = {
    # Tech
    "AAPL": "tech", "MSFT": "tech", "GOOGL": "tech", "GOOG": "tech", "META": "tech",
    "NVDA": "tech", "AMD": "tech", "INTC": "tech", "CRM": "tech", "ORCL": "tech",
    "ADBE": "tech", "NOW": "tech", "SNOW": "tech", "PLTR": "tech", "NET": "tech",
    "AVGO": "tech", "QCOM": "tech", "MU": "tech", "TSM": "tech", "ASML": "tech",

    # Finance
    "JPM": "finance", "BAC": "finance", "WFC": "finance", "GS": "finance", "MS": "finance",
    "C": "finance", "BLK": "finance", "SCHW": "finance", "V": "finance", "MA": "finance",
    "AXP": "finance", "COF": "finance", "USB": "finance", "PNC": "finance",

    # Healthcare
    "UNH": "healthcare", "JNJ": "healthcare", "PFE": "healthcare", "ABBV": "healthcare",
    "MRK": "healthcare", "LLY": "healthcare", "TMO": "healthcare", "ABT": "healthcare",
    "BMY": "healthcare", "AMGN": "healthcare", "GILD": "healthcare", "MRNA": "healthcare",

    # Energy
    "XOM": "energy", "CVX": "energy", "COP": "energy", "SLB": "energy", "EOG": "energy",
    "OXY": "energy", "MPC": "energy", "VLO": "energy", "PSX": "energy",

    # Consumer
    "AMZN": "consumer", "TSLA": "consumer", "HD": "consumer", "NKE": "consumer",
    "MCD": "consumer", "SBUX": "consumer", "TGT": "consumer", "WMT": "consumer",
    "COST": "consumer", "LOW": "consumer", "TJX": "consumer", "BKNG": "consumer",

    # Industrial
    "CAT": "industrial", "DE": "industrial", "BA": "industrial", "HON": "industrial",
    "UPS": "industrial", "RTX": "industrial", "LMT": "industrial", "GE": "industrial",
    "MMM": "industrial", "UNP": "industrial", "FDX": "industrial",

    # ETFs
    "SPY": "index", "QQQ": "index", "IWM": "index", "DIA": "index",
    "XLF": "finance_etf", "XLK": "tech_etf", "XLE": "energy_etf", "XLV": "healthcare_etf",
}


def get_trading_client() -> TradingClient:
    """Initialize Alpaca trading client"""
    return TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)


def get_account_info() -> Dict:
    """Get account information"""
    client = get_trading_client()
    account = client.get_account()
    return {
        "equity": float(account.equity),
        "cash": float(account.cash),
        "buying_power": float(account.buying_power),
        "options_buying_power": float(getattr(account, 'options_buying_power', account.buying_power)),
    }


def get_option_quote(contract_symbol: str) -> Dict:
    """
    Get current bid/ask for an option contract.

    Returns:
        Dict with bid, ask, mid, spread, spread_pct
    """
    try:
        from alpaca.data.historical.option import OptionHistoricalDataClient
        from alpaca.data.requests import OptionLatestQuoteRequest

        client = OptionHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)

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
                "bid_size": getattr(quote, 'bid_size', 0),
                "ask_size": getattr(quote, 'ask_size', 0),
            }
    except Exception as e:
        print(f"Error getting option quote for {contract_symbol}: {e}")

    return {"bid": 0, "ask": 0, "mid": 0, "spread": 0, "spread_pct": 100}


def check_option_liquidity(contract_symbol: str) -> Dict:
    """
    Check if option contract is liquid enough to trade.

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
        reasons.append(f"Bid ${quote['bid']:.2f} too low (min ${OPTIONS_SAFETY['min_bid']})")

    # Check size
    min_size = OPTIONS_SAFETY.get("min_bid_size", 10)
    if quote.get("bid_size", 0) < min_size:
        reasons.append(f"Bid size {quote.get('bid_size', 0)} < {min_size}")

    return {
        "liquid": len(reasons) == 0,
        "reasons": reasons,
        "quote": quote,
    }


def place_options_order_smart(
    contract_symbol: str,
    quantity: int,
    side: str = "buy",
    max_spread_pct: float = None,
    signal_data: Dict = None,
) -> Dict:
    """
    Place options order with smart limit pricing.

    - Gets current quote
    - Checks spread width
    - Places limit order at favorable price

    Args:
        contract_symbol: Full OCC contract symbol
        quantity: Number of contracts
        side: 'buy' or 'sell'
        max_spread_pct: Maximum allowed spread (default from OPTIONS_SAFETY)
        signal_data: Optional signal data for logging

    Returns:
        Order result dict
    """
    max_spread_pct = max_spread_pct or OPTIONS_SAFETY["max_spread_pct"]

    # Get current quote
    quote = get_option_quote(contract_symbol)

    # Check spread
    if quote["spread_pct"] > max_spread_pct:
        return {
            "success": False,
            "error": f"Spread too wide: {quote['spread_pct']:.1f}% (max {max_spread_pct}%)",
            "quote": quote,
        }

    # Check minimum bid
    if quote["bid"] < OPTIONS_SAFETY["min_bid"]:
        return {
            "success": False,
            "error": f"Bid too low: ${quote['bid']:.2f} (min ${OPTIONS_SAFETY['min_bid']})",
            "quote": quote,
        }

    # Calculate limit price
    buffer_pct = OPTIONS_SAFETY.get("limit_price_buffer_pct", 2.0) / 100

    if side.lower() == "buy":
        # Buy at mid or slightly above (willing to pay up a bit)
        limit_price = round(quote["mid"] * (1 + buffer_pct), 2)
        limit_price = min(limit_price, quote["ask"])  # But not above ask
    else:
        # Sell at mid or slightly below
        limit_price = round(quote["mid"] * (1 - buffer_pct), 2)
        limit_price = max(limit_price, quote["bid"])  # But not below bid

    print(f"  Quote: bid=${quote['bid']:.2f} ask=${quote['ask']:.2f} (spread {quote['spread_pct']:.1f}%)")
    print(f"  Limit order at ${limit_price:.2f}")

    # Place limit order
    return place_options_order(
        contract_symbol=contract_symbol,
        quantity=quantity,
        side=side,
        order_type="limit",
        limit_price=limit_price,
        signal_data=signal_data,
    )


def reconcile_options_positions() -> Dict:
    """
    Compare database positions with actual Alpaca positions.

    Returns:
        Dict with mismatches and actions needed
    """
    from db import get_open_options_trades

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
            try:
                update_options_trade_exit(
                    trade_id=db_trade["id"],
                    exit_price=0,  # Unknown
                    exit_reason="reconciliation_closed"
                )
                print(f"  Auto-closed DB record for {contract}")
            except Exception as e:
                print(f"  Could not auto-close {contract}: {e}")
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


# ============== GREEKS CALCULATIONS ==============

def estimate_greeks(
    option_type: str,
    underlying_price: float,
    strike: float,
    days_to_exp: int,
    iv: float,
    risk_free_rate: float = 0.05,
) -> PositionGreeks:
    """
    Estimate option Greeks using Black-Scholes approximation.

    Args:
        option_type: 'call' or 'put'
        underlying_price: Current price of underlying
        strike: Strike price
        days_to_exp: Days to expiration
        iv: Implied volatility (as decimal, e.g., 0.30 for 30%)
        risk_free_rate: Risk-free interest rate (default 5%)

    Returns:
        PositionGreeks with delta, gamma, theta, vega
    """
    if days_to_exp <= 0:
        return PositionGreeks(delta=0, gamma=0, theta=0, vega=0, iv=iv)

    T = days_to_exp / 365
    S = underlying_price
    K = strike
    r = risk_free_rate
    sigma = iv if iv > 0 else 0.30  # Default to 30% IV

    try:
        d1 = (math.log(S / K) + (r + sigma ** 2 / 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)

        # Normal CDF approximation
        def norm_cdf(x):
            return (1 + math.erf(x / math.sqrt(2))) / 2

        def norm_pdf(x):
            return math.exp(-x ** 2 / 2) / math.sqrt(2 * math.pi)

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
            iv=round(sigma, 4),
        )
    except Exception as e:
        print(f"Greeks calculation error: {e}")
        return PositionGreeks(delta=0, gamma=0, theta=0, vega=0, iv=iv)


def get_option_greeks(contract_symbol: str, underlying_price: float = None) -> PositionGreeks:
    """
    Get Greeks for a specific option contract.

    Args:
        contract_symbol: OCC contract symbol
        underlying_price: Optional underlying price (will fetch if not provided)

    Returns:
        PositionGreeks
    """
    # Parse contract to get details
    contract_info = parse_contract_symbol(contract_symbol)

    if not contract_info.get('expiration'):
        return PositionGreeks(delta=0, gamma=0, theta=0, vega=0)

    # Calculate DTE
    try:
        exp_date = datetime.strptime(contract_info['expiration'], "%Y-%m-%d")
        dte = max(1, (exp_date - datetime.now()).days)
    except Exception:
        dte = 30  # Default

    # Get underlying price if not provided
    if underlying_price is None:
        try:
            from alpaca.data.historical.stock import StockHistoricalDataClient
            from alpaca.data.requests import StockLatestQuoteRequest

            client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
            request = StockLatestQuoteRequest(symbol_or_symbols=[contract_info['underlying']])
            quotes = client.get_stock_latest_quote(request)

            if contract_info['underlying'] in quotes:
                quote = quotes[contract_info['underlying']]
                underlying_price = (float(quote.bid_price) + float(quote.ask_price)) / 2
        except Exception as e:
            print(f"Error fetching underlying price: {e}")
            underlying_price = contract_info.get('strike', 100)  # Fallback

    # Estimate IV from option price (simplified)
    option_quote = get_option_quote(contract_symbol)
    option_mid = option_quote.get('mid', 0)

    # Simple IV approximation: higher option price relative to underlying = higher IV
    # This is a rough estimate; real systems would use Newton-Raphson to solve for IV
    moneyness = underlying_price / contract_info.get('strike', underlying_price) if contract_info.get('strike') else 1
    base_iv = 0.30  # 30% base

    if option_mid > 0 and underlying_price > 0:
        # Rough IV estimate based on option price
        price_ratio = option_mid / underlying_price
        estimated_iv = base_iv * (1 + price_ratio * 10)  # Scale up based on option price
        estimated_iv = min(max(estimated_iv, 0.10), 2.0)  # Cap between 10% and 200%
    else:
        estimated_iv = base_iv

    return estimate_greeks(
        option_type=contract_info.get('option_type', 'call'),
        underlying_price=underlying_price,
        strike=contract_info.get('strike', underlying_price),
        days_to_exp=dte,
        iv=estimated_iv,
    )


def get_portfolio_greeks() -> Dict:
    """
    Calculate aggregate Greeks across all options positions.

    Returns:
        Dict with net_delta, total_gamma, daily_theta, total_vega, and per-position Greeks
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

    position_greeks = []
    totals = {"delta": 0, "gamma": 0, "theta": 0, "vega": 0}

    for pos in positions:
        try:
            # Get Greeks for this position
            greeks = get_option_greeks(pos.contract_symbol, pos.current_price)

            # Scale by quantity
            scaled = greeks.scale(pos.quantity)

            position_greeks.append({
                "symbol": pos.symbol,
                "contract": pos.contract_symbol,
                "quantity": pos.quantity,
                "option_type": pos.option_type,
                "strike": pos.strike,
                "expiration": pos.expiration,
                "delta": round(scaled.delta, 1),
                "gamma": round(scaled.gamma, 4),
                "theta": round(scaled.theta, 2),
                "vega": round(scaled.vega, 2),
                "iv": round(greeks.iv * 100, 1),  # As percentage
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


# ============== SECTOR CONCENTRATION ==============

def get_sector(symbol: str) -> str:
    """Get sector for a symbol"""
    return SECTOR_MAP.get(symbol.upper(), "other")


def check_sector_concentration() -> Dict:
    """
    Check sector concentration across options positions.

    Returns:
        Dict with concentration analysis
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
    concentrated = max_sector[1] > OPTIONS_SAFETY.get("max_single_sector_pct", 50.0)

    return {
        "concentrated": concentrated,
        "max_sector": max_sector[0],
        "max_sector_pct": round(max_sector[1], 1),
        "sectors": {k: round(v, 1) for k, v in sector_pcts.items()},
        "warning": f"{max_sector[1]:.0f}% in {max_sector[0]}" if concentrated else None,
    }


def can_add_position(symbol: str, estimated_value: float = None) -> Tuple[bool, str]:
    """
    Check if adding a position would violate concentration limits.

    Args:
        symbol: Symbol to add
        estimated_value: Estimated position value (optional)

    Returns:
        (can_add: bool, reason: str)
    """
    sector = get_sector(symbol)
    concentration = check_sector_concentration()
    positions = get_options_positions()

    max_sector_pct = OPTIONS_SAFETY.get("max_single_sector_pct", 50.0)
    max_underlying_pct = OPTIONS_SAFETY.get("max_single_underlying_pct", 30.0)

    current_sector_pct = concentration["sectors"].get(sector, 0)

    # Check if adding would exceed sector limit (rough estimate: assume adds 15-25%)
    if current_sector_pct > max_sector_pct * 0.8:  # Already at 80% of limit
        return False, f"Sector {sector} already at {current_sector_pct:.0f}% (limit {max_sector_pct:.0f}%)"

    # Check if already have position in this underlying
    for pos in positions:
        if pos.symbol == symbol:
            return False, f"Already have position in {symbol}"

    # Check underlying concentration
    total_value = sum(abs(p.market_value) for p in positions)
    underlying_value = sum(abs(p.market_value) for p in positions if p.symbol == symbol)
    underlying_pct = (underlying_value / total_value * 100) if total_value > 0 else 0

    if underlying_pct > max_underlying_pct:
        return False, f"Would exceed {max_underlying_pct:.0f}% in {symbol} (currently {underlying_pct:.0f}%)"

    return True, "OK"


# ============== DTE ALERTS & ROLL SUGGESTIONS ==============

def check_expiration_risk() -> List[Dict]:
    """
    Check positions approaching expiration.

    Returns:
        List of positions needing attention with suggested actions
    """
    positions = get_options_positions()
    alerts = []

    roll_alert_dte = OPTIONS_SAFETY.get("roll_alert_dte", 7)
    critical_dte = OPTIONS_SAFETY.get("critical_dte", 3)

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
            elif dte <= critical_dte:
                alert = {
                    "position": pos,
                    "dte": dte,
                    "severity": "HIGH",
                    "message": f"Expiring in {dte} days - Consider closing or rolling",
                    "action": "close_or_roll",
                }
            elif dte <= roll_alert_dte:
                alert = {
                    "position": pos,
                    "dte": dte,
                    "severity": "MEDIUM",
                    "message": f"{dte} DTE - Monitor theta decay",
                    "action": "monitor",
                }

            # Check if ITM (assignment risk for American options)
            if alert:
                if pos.option_type == "call" and pos.current_price > pos.strike:
                    alert["message"] += " | ITM - Assignment risk"
                    alert["itm"] = True
                elif pos.option_type == "put" and pos.current_price < pos.strike:
                    alert["message"] += " | ITM - Assignment risk"
                    alert["itm"] = True

            if alert:
                alerts.append(alert)

        except Exception as e:
            print(f"Error checking expiration for {pos.contract_symbol}: {e}")

    return sorted(alerts, key=lambda x: x["dte"])


def suggest_roll(position: OptionsPosition) -> Dict:
    """
    Suggest a roll for an expiring position.

    Args:
        position: The expiring position

    Returns:
        Roll suggestion with new contract and cost
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
        "new_dte": new_contract.get("dte", 30),
        "roll_cost": round(roll_cost, 2),  # Positive = debit, Negative = credit
        "current_value": round(old_quote["mid"], 2),
        "new_value": round(new_quote["mid"], 2),
    }


# ============== EARNINGS BLACKOUT ==============

def check_earnings_blackout(symbol: str, blackout_days: int = None) -> Tuple[bool, Optional[str]]:
    """
    Check if symbol is in earnings blackout period.

    Args:
        symbol: Stock symbol
        blackout_days: Days before earnings to block (default from config)

    Returns:
        (is_blocked, earnings_date or None)
    """
    blackout_days = blackout_days or OPTIONS_SAFETY.get("earnings_blackout_days", 2)

    try:
        from flow_scanner import UnusualWhalesClient

        client = UnusualWhalesClient()
        earnings = client.get_earnings(symbol)

        if not earnings or not earnings.get("next_earnings_date"):
            return False, None

        earnings_date_str = earnings["next_earnings_date"][:10]
        earnings_date = datetime.strptime(earnings_date_str, "%Y-%m-%d")
        days_to_earnings = (earnings_date - datetime.now()).days

        if 0 <= days_to_earnings <= blackout_days:
            return True, earnings_date_str

    except Exception as e:
        print(f"Error checking earnings for {symbol}: {e}")

    return False, None


def get_options_positions() -> List[OptionsPosition]:
    """Get all current options positions"""
    client = get_trading_client()

    try:
        positions = client.get_all_positions()
        options_positions = []

        for p in positions:
            # Check if this is an options position by looking at asset class
            asset_class = getattr(p, 'asset_class', None)
            if asset_class and str(asset_class).lower() == 'us_option':
                # Parse contract symbol to extract details
                contract_info = parse_contract_symbol(p.symbol)

                options_positions.append(OptionsPosition(
                    symbol=contract_info.get('underlying', p.symbol[:4]),
                    contract_symbol=p.symbol,
                    option_type=contract_info.get('option_type', 'call'),
                    strike=contract_info.get('strike', 0),
                    expiration=contract_info.get('expiration', ''),
                    quantity=int(float(p.qty)),
                    avg_entry_price=float(p.avg_entry_price),
                    current_price=float(p.current_price),
                    market_value=float(p.market_value),
                    unrealized_pl=float(p.unrealized_pl),
                    unrealized_plpc=float(p.unrealized_plpc)
                ))

        return options_positions
    except Exception as e:
        print(f"Error getting options positions: {e}")
        return []


def parse_contract_symbol(contract_symbol: str) -> Dict:
    """
    Parse OCC contract symbol format.
    Format: AAPL240315C00175000
    - Underlying: AAPL (1-6 chars)
    - Expiration: YYMMDD (240315 = Mar 15, 2024)
    - Type: C or P
    - Strike: 8 digits (00175000 = $175.00)
    """
    try:
        # Find where the date portion starts (6 digits before C or P)
        for i in range(len(contract_symbol)):
            if contract_symbol[i:i+1] in ['C', 'P'] and i >= 6:
                underlying = contract_symbol[:i-6]
                exp_str = contract_symbol[i-6:i]
                opt_type = 'call' if contract_symbol[i] == 'C' else 'put'
                strike_str = contract_symbol[i+1:]

                # Parse expiration YYMMDD
                exp_date = f"20{exp_str[:2]}-{exp_str[2:4]}-{exp_str[4:6]}"

                # Parse strike (8 digits, divide by 1000)
                strike = float(strike_str) / 1000

                return {
                    'underlying': underlying,
                    'expiration': exp_date,
                    'option_type': opt_type,
                    'strike': strike
                }
    except Exception:
        pass

    return {'underlying': contract_symbol[:4], 'option_type': 'call', 'strike': 0, 'expiration': ''}


def find_option_contract(
    underlying: str,
    option_type: str,
    target_strike: float = None,
    target_expiration: str = None,
    min_dte: int = None,
    max_dte: int = None,
    otm_pct: float = 0.05
) -> Optional[Dict]:
    """
    Find an option contract matching the criteria.

    Args:
        underlying: Underlying stock symbol
        option_type: 'call' or 'put'
        target_strike: Specific strike price (if known)
        target_expiration: Specific expiration date YYYY-MM-DD (if known)
        min_dte: Minimum days to expiration
        max_dte: Maximum days to expiration
        otm_pct: OTM percentage for strike selection (default 5%)

    Returns:
        Contract info dict or None
    """
    client = get_trading_client()

    min_dte = min_dte or OPTIONS_CONFIG["min_days_to_exp"]
    max_dte = max_dte or OPTIONS_CONFIG["max_days_to_exp"]

    try:
        # Build request
        today = datetime.now().date()
        exp_start = today + timedelta(days=min_dte)
        exp_end = today + timedelta(days=max_dte)

        contract_type = ContractType.CALL if option_type.lower() == 'call' else ContractType.PUT

        request = GetOptionContractsRequest(
            underlying_symbols=[underlying],
            status='active',
            type=contract_type,
            expiration_date_gte=exp_start.isoformat(),
            expiration_date_lte=exp_end.isoformat(),
        )

        # Add strike filters if we have a target
        if target_strike:
            request.strike_price_gte = str(target_strike - 1)
            request.strike_price_lte = str(target_strike + 1)

        contracts = client.get_option_contracts(request)

        if not contracts or not contracts.option_contracts:
            print(f"No contracts found for {underlying}")
            return None

        # Filter and sort contracts
        valid_contracts = []
        for c in contracts.option_contracts:
            # Check if tradable
            if not getattr(c, 'tradable', True):
                continue

            contract_info = {
                'symbol': c.symbol,
                'underlying': underlying,
                'option_type': option_type,
                'strike': float(c.strike_price),
                'expiration': str(c.expiration_date),
                'dte': (c.expiration_date - today).days if hasattr(c.expiration_date, 'days') else 0,
            }

            # Calculate DTE
            try:
                if isinstance(c.expiration_date, str):
                    exp_dt = datetime.strptime(c.expiration_date, "%Y-%m-%d").date()
                else:
                    exp_dt = c.expiration_date
                contract_info['dte'] = (exp_dt - today).days
            except Exception:
                contract_info['dte'] = 30

            valid_contracts.append(contract_info)

        if not valid_contracts:
            return None

        # If we have a target strike, find closest match
        if target_strike:
            valid_contracts.sort(key=lambda x: abs(x['strike'] - target_strike))
            return valid_contracts[0]

        # If we have a target expiration, filter by it
        if target_expiration:
            exp_contracts = [c for c in valid_contracts if c['expiration'] == target_expiration]
            if exp_contracts:
                valid_contracts = exp_contracts

        # Sort by DTE (prefer 30-45 days)
        valid_contracts.sort(key=lambda x: abs(x['dte'] - 35))

        return valid_contracts[0]

    except Exception as e:
        print(f"Error finding option contract: {e}")
        return None


def calculate_options_position_size(
    account_equity: float,
    option_price: float,
    conviction: float = 0.5
) -> int:
    """
    Calculate number of contracts based on config limits.

    Args:
        account_equity: Total account equity
        option_price: Price per contract (premium * 100)
        conviction: Signal conviction (0-1), used to scale size

    Returns:
        Number of contracts to buy
    """
    # Base position value from config
    max_position_value = OPTIONS_CONFIG["max_position_value"]
    pct_position_value = account_equity * OPTIONS_CONFIG["position_size_pct"]

    # Use smaller of max_position_value or percentage
    position_value = min(max_position_value, pct_position_value)

    # Scale by conviction (min 50% of calculated size)
    conviction_scale = 0.5 + (conviction * 0.5)
    adjusted_value = position_value * conviction_scale

    # Calculate contracts (option_price is per share, multiply by 100 for full contract)
    contract_cost = option_price * 100
    if contract_cost <= 0:
        return 1

    contracts = int(adjusted_value / contract_cost)

    # Apply limits
    contracts = max(OPTIONS_CONFIG["default_contracts"], contracts)
    contracts = min(OPTIONS_CONFIG["max_contracts_per_trade"], contracts)

    # Check premium limits
    if option_price < OPTIONS_CONFIG["min_premium"] / 100:
        print(f"  Warning: Option premium ${option_price:.2f} below minimum ${OPTIONS_CONFIG['min_premium']/100:.2f}")
    if option_price > OPTIONS_CONFIG["max_premium"] / 100:
        print(f"  Warning: Option premium ${option_price:.2f} above maximum ${OPTIONS_CONFIG['max_premium']/100:.2f}")

    return contracts


def place_options_order(
    contract_symbol: str,
    quantity: int,
    side: str = "buy",
    order_type: str = "market",
    limit_price: float = None,
    signal_data: Dict = None
) -> Dict:
    """
    Place an options order.

    Args:
        contract_symbol: Full OCC contract symbol
        quantity: Number of contracts
        side: 'buy' or 'sell'
        order_type: 'market' or 'limit'
        limit_price: Limit price if order_type is 'limit'
        signal_data: Optional signal data for logging

    Returns:
        Order result dict
    """
    client = get_trading_client()

    try:
        order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL

        if order_type.lower() == "limit" and limit_price:
            order_request = LimitOrderRequest(
                symbol=contract_symbol,
                qty=quantity,
                side=order_side,
                time_in_force=TimeInForce.DAY,
                limit_price=limit_price
            )
        else:
            order_request = MarketOrderRequest(
                symbol=contract_symbol,
                qty=quantity,
                side=order_side,
                time_in_force=TimeInForce.DAY
            )

        order = client.submit_order(order_request)

        # Wait for fill
        import time
        max_wait = 30
        elapsed = 0
        fill_price = None

        while elapsed < max_wait:
            order_status = client.get_order_by_id(order.id)
            if order_status.status == OrderStatus.FILLED:
                fill_price = float(order_status.filled_avg_price or 0)
                break
            elif order_status.status in [OrderStatus.CANCELED, OrderStatus.EXPIRED, OrderStatus.REJECTED]:
                return {
                    "success": False,
                    "error": f"Order {order_status.status.value}",
                    "order_id": str(order.id)
                }
            time.sleep(0.5)
            elapsed += 0.5

        return {
            "success": True,
            "order_id": str(order.id),
            "contract_symbol": contract_symbol,
            "quantity": quantity,
            "side": side,
            "fill_price": fill_price,
            "status": order_status.status.value if 'order_status' in dir() else "submitted"
        }

    except Exception as e:
        print(f"Error placing options order: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def execute_flow_trade(enriched_signal) -> Dict:
    """
    Execute an options trade based on an enriched flow signal.

    Args:
        enriched_signal: EnrichedFlowSignal with thesis and recommendation

    Returns:
        Execution result dict
    """
    signal = enriched_signal.signal

    print(f"[{datetime.now()}] Executing options trade for {signal.symbol}...")

    # Validate recommendation
    if enriched_signal.recommendation != "BUY":
        return {
            "success": False,
            "error": f"Recommendation is {enriched_signal.recommendation}, not BUY"
        }

    # Check earnings blackout
    blocked, earnings_date = check_earnings_blackout(signal.symbol)
    if blocked:
        return {
            "success": False,
            "error": f"Earnings blackout: {signal.symbol} reports on {earnings_date}",
            "symbol": signal.symbol,
        }

    # Check sector concentration
    can_add, concentration_reason = can_add_position(signal.symbol)
    if not can_add:
        return {
            "success": False,
            "error": f"Concentration limit: {concentration_reason}",
            "symbol": signal.symbol,
        }

    # Check position limits
    current_positions = get_options_positions()
    if len(current_positions) >= OPTIONS_CONFIG["max_options_positions"]:
        return {
            "success": False,
            "error": f"Max options positions ({OPTIONS_CONFIG['max_options_positions']}) reached"
        }

    # Check if already have position in this underlying (redundant with can_add_position but explicit)
    for pos in current_positions:
        if pos.symbol == signal.symbol:
            return {
                "success": False,
                "error": f"Already have options position in {signal.symbol}"
            }

    # Get account info
    account = get_account_info()

    # Check portfolio options exposure
    options_value = sum(pos.market_value for pos in current_positions)
    max_options_exposure = account["equity"] * OPTIONS_CONFIG["max_portfolio_risk_options"]

    if options_value >= max_options_exposure:
        return {
            "success": False,
            "error": f"Max options exposure ({OPTIONS_CONFIG['max_portfolio_risk_options']*100:.0f}%) reached"
        }

    # Find the contract
    # First try to find exact match from signal
    contract = find_option_contract(
        underlying=signal.symbol,
        option_type=signal.option_type,
        target_strike=signal.strike,
        target_expiration=signal.expiration[:10] if signal.expiration else None,
        min_dte=OPTIONS_CONFIG["min_days_to_exp"],
        max_dte=OPTIONS_CONFIG["max_days_to_exp"]
    )

    if not contract:
        return {
            "success": False,
            "error": f"Could not find matching contract for {signal.symbol}"
        }

    # Estimate option price (use signal premium / size as rough estimate)
    estimated_price = (signal.premium / signal.size / 100) if signal.size > 0 else 1.0

    # Calculate position size
    quantity = calculate_options_position_size(
        account_equity=account["equity"],
        option_price=estimated_price,
        conviction=enriched_signal.conviction
    )

    print(f"  Contract: {contract['symbol']}")
    print(f"  Strike: ${contract['strike']} | Exp: {contract['expiration']}")
    print(f"  Quantity: {quantity} contracts")
    print(f"  Est. Cost: ${quantity * estimated_price * 100:,.2f}")

    # Check liquidity before trading
    liquidity = check_option_liquidity(contract['symbol'])
    if not liquidity["liquid"]:
        return {
            "success": False,
            "error": f"Liquidity check failed: {', '.join(liquidity['reasons'])}",
            "quote": liquidity.get("quote"),
        }

    # Place the order using smart limit orders
    if OPTIONS_SAFETY.get("use_limit_orders", True):
        order_result = place_options_order_smart(
            contract_symbol=contract['symbol'],
            quantity=quantity,
            side="buy",
        )
    else:
        order_result = place_options_order(
            contract_symbol=contract['symbol'],
            quantity=quantity,
            side="buy",
            order_type="market"
        )

    if not order_result.get("success"):
        return order_result

    # Get Greeks at entry for logging
    entry_greeks = None
    underlying_price = None
    dte = None
    try:
        # Get underlying price
        from alpaca.data.historical.stock import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestQuoteRequest

        stock_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
        quote_request = StockLatestQuoteRequest(symbol_or_symbols=[signal.symbol])
        quotes = stock_client.get_stock_latest_quote(quote_request)
        if signal.symbol in quotes:
            q = quotes[signal.symbol]
            underlying_price = (float(q.bid_price) + float(q.ask_price)) / 2

        # Calculate DTE
        exp_date = datetime.strptime(contract['expiration'], "%Y-%m-%d")
        dte = max(1, (exp_date - datetime.now()).days)

        # Get Greeks
        greeks = get_option_greeks(contract['symbol'], underlying_price)
        entry_greeks = greeks.to_dict()
        print(f"  Entry Greeks: Delta={entry_greeks['delta']:.3f}, Theta={entry_greeks['theta']:.3f}, IV={entry_greeks['iv']*100:.1f}%")
    except Exception as e:
        print(f"  Warning: Could not calculate entry Greeks: {e}")

    # Log to database with Greeks
    try:
        trade_id = log_options_trade(
            contract_symbol=contract['symbol'],
            underlying=signal.symbol,
            option_type=signal.option_type,
            strike=contract['strike'],
            expiration=contract['expiration'],
            quantity=quantity,
            entry_price=order_result.get('fill_price', estimated_price),
            signal_score=signal.score,
            signal_data=json.dumps({
                'premium': signal.premium,
                'vol_oi_ratio': signal.vol_oi_ratio,
                'is_sweep': signal.is_sweep,
                'is_floor': signal.is_floor,
                'sentiment': signal.sentiment,
                'score_breakdown': signal.score_breakdown
            }),
            thesis=enriched_signal.thesis,
            flow_signal_id=getattr(signal, 'db_id', None),
            entry_greeks=entry_greeks,
            underlying_price=underlying_price,
            dte=dte,
        )

        # Mark signal as executed if we have the ID
        if hasattr(signal, 'db_id') and signal.db_id:
            mark_flow_signal_executed(signal.db_id)

    except Exception as e:
        print(f"  Warning: Could not log trade to database: {e}")
        trade_id = None

    return {
        "success": True,
        "trade_id": trade_id,
        "contract_symbol": contract['symbol'],
        "underlying": signal.symbol,
        "option_type": signal.option_type,
        "strike": contract['strike'],
        "expiration": contract['expiration'],
        "quantity": quantity,
        "fill_price": order_result.get('fill_price'),
        "estimated_cost": quantity * estimated_price * 100,
        "order_id": order_result.get('order_id'),
        "thesis": enriched_signal.thesis[:200] if enriched_signal.thesis else None,
        "entry_greeks": entry_greeks,
    }


def close_options_position(
    contract_symbol: str,
    reason: str = "manual",
    quantity: int = None
) -> Dict:
    """
    Close an options position with Greeks logging.

    Args:
        contract_symbol: Full OCC contract symbol
        reason: Exit reason for logging
        quantity: Number of contracts to close (default: all)

    Returns:
        Close result dict
    """
    client = get_trading_client()

    try:
        # Get position details
        position = None
        for p in client.get_all_positions():
            if p.symbol == contract_symbol:
                position = p
                break

        if not position:
            return {
                "success": False,
                "error": f"No position found for {contract_symbol}"
            }

        qty = quantity or int(float(position.qty))
        entry_price = float(position.avg_entry_price)
        current_price = float(position.current_price)

        # Get exit Greeks before closing
        exit_greeks = None
        underlying_price = None
        dte = None
        try:
            contract_info = parse_contract_symbol(contract_symbol)

            # Get underlying price
            from alpaca.data.historical.stock import StockHistoricalDataClient
            from alpaca.data.requests import StockLatestQuoteRequest

            stock_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
            quote_request = StockLatestQuoteRequest(symbol_or_symbols=[contract_info['underlying']])
            quotes = stock_client.get_stock_latest_quote(quote_request)
            if contract_info['underlying'] in quotes:
                q = quotes[contract_info['underlying']]
                underlying_price = (float(q.bid_price) + float(q.ask_price)) / 2

            # Calculate DTE
            if contract_info.get('expiration'):
                exp_date = datetime.strptime(contract_info['expiration'], "%Y-%m-%d")
                dte = max(0, (exp_date - datetime.now()).days)

            # Get Greeks
            greeks = get_option_greeks(contract_symbol, underlying_price)
            exit_greeks = greeks.to_dict()
            print(f"  Exit Greeks: Delta={exit_greeks['delta']:.3f}, Theta={exit_greeks['theta']:.3f}, IV={exit_greeks['iv']*100:.1f}%")
        except Exception as e:
            print(f"  Warning: Could not calculate exit Greeks: {e}")

        # Cancel any open orders for this contract
        orders = client.get_orders(GetOrdersRequest(
            status=QueryOrderStatus.OPEN,
            symbols=[contract_symbol]
        ))
        for o in orders:
            try:
                client.cancel_order_by_id(o.id)
            except Exception:
                pass

        # Place sell order
        order_result = place_options_order(
            contract_symbol=contract_symbol,
            quantity=qty,
            side="sell",
            order_type="market"
        )

        if not order_result.get("success"):
            return order_result

        exit_price = order_result.get('fill_price', current_price)

        # Calculate P/L
        pnl = (exit_price - entry_price) * qty * 100
        pnl_pct = ((exit_price - entry_price) / entry_price) if entry_price > 0 else 0

        # Update database with exit Greeks
        try:
            trade = get_options_trade_by_contract(contract_symbol, status='open')
            if trade:
                update_options_trade_exit(
                    trade_id=trade['id'],
                    exit_price=exit_price,
                    exit_reason=reason,
                    exit_greeks=exit_greeks,
                    underlying_price=underlying_price,
                    dte=dte,
                )

                # Log signal outcome for learning if we have signal data
                try:
                    from db import log_signal_outcome, get_options_trade_with_greeks

                    full_trade = get_options_trade_with_greeks(trade['id'])
                    if full_trade and full_trade.get('signal_data'):
                        import json
                        signal_factors = json.loads(full_trade['signal_data'])
                        signal_factors['option_type'] = full_trade.get('option_type')
                        signal_factors['dte'] = full_trade.get('entry_dte', 0)

                        # Calculate holding days
                        holding_days = 0
                        if full_trade.get('entry_date'):
                            entry_dt = datetime.fromisoformat(full_trade['entry_date'][:10])
                            holding_days = (datetime.now() - entry_dt).days

                        log_signal_outcome(
                            signal_id=full_trade.get('flow_signal_id'),
                            trade_id=trade['id'],
                            symbol=full_trade.get('underlying'),
                            signal_score=full_trade.get('signal_score'),
                            signal_factors=signal_factors,
                            entry_greeks={
                                'delta': full_trade.get('entry_delta'),
                                'theta': full_trade.get('entry_theta'),
                                'iv': full_trade.get('entry_iv'),
                            },
                            outcome={
                                'entry_price': full_trade.get('entry_price'),
                                'exit_price': exit_price,
                                'max_price': exit_price,  # Would need tracking during hold
                                'min_price': exit_price,  # Would need tracking during hold
                                'max_gain_pct': max(pnl_pct * 100, 0),
                                'max_loss_pct': min(pnl_pct * 100, 0),
                                'actual_pnl_pct': pnl_pct * 100,
                                'holding_days': holding_days,
                                'exit_reason': reason,
                            }
                        )
                except Exception as e:
                    print(f"  Warning: Could not log signal outcome: {e}")

        except Exception as e:
            print(f"  Warning: Could not update trade record: {e}")

        return {
            "success": True,
            "contract_symbol": contract_symbol,
            "quantity": qty,
            "exit_price": exit_price,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "reason": reason,
            "order_id": order_result.get('order_id'),
            "exit_greeks": exit_greeks,
        }

    except Exception as e:
        print(f"Error closing options position: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def check_options_exits() -> List[Dict]:
    """
    Check all options positions for profit target or stop loss.

    Returns:
        List of exit results
    """
    positions = get_options_positions()
    results = []

    profit_target = OPTIONS_CONFIG["profit_target_pct"]
    stop_loss = OPTIONS_CONFIG["stop_loss_pct"]

    for pos in positions:
        pnl_pct = pos.unrealized_plpc

        should_exit = False
        reason = None

        # Check profit target
        if pnl_pct >= profit_target:
            should_exit = True
            reason = f"profit_target_{pnl_pct*100:.0f}pct"
            print(f"  {pos.contract_symbol}: Hit profit target ({pnl_pct*100:.1f}% >= {profit_target*100:.0f}%)")

        # Check stop loss
        elif pnl_pct <= -stop_loss:
            should_exit = True
            reason = f"stop_loss_{abs(pnl_pct)*100:.0f}pct"
            print(f"  {pos.contract_symbol}: Hit stop loss ({pnl_pct*100:.1f}% <= -{stop_loss*100:.0f}%)")

        # Check DTE (close if < 3 days)
        if pos.expiration:
            try:
                exp_date = datetime.strptime(pos.expiration, "%Y-%m-%d").date()
                dte = (exp_date - datetime.now().date()).days
                if dte <= 3:
                    should_exit = True
                    reason = f"expiration_close_dte_{dte}"
                    print(f"  {pos.contract_symbol}: Close to expiration ({dte} DTE)")
            except Exception:
                pass

        if should_exit:
            result = close_options_position(pos.contract_symbol, reason=reason)
            result['position'] = {
                'symbol': pos.symbol,
                'contract_symbol': pos.contract_symbol,
                'unrealized_pl': pos.unrealized_pl,
                'unrealized_plpc': pos.unrealized_plpc
            }
            results.append(result)

    return results


def get_options_summary() -> Dict:
    """
    Get summary of options positions for display.

    Returns:
        Summary dict with positions and totals
    """
    positions = get_options_positions()
    account = get_account_info()

    total_value = sum(pos.market_value for pos in positions)
    total_pnl = sum(pos.unrealized_pl for pos in positions)

    return {
        "count": len(positions),
        "positions": [
            {
                "symbol": pos.symbol,
                "contract_symbol": pos.contract_symbol,
                "option_type": pos.option_type,
                "strike": pos.strike,
                "expiration": pos.expiration,
                "quantity": pos.quantity,
                "avg_entry_price": pos.avg_entry_price,
                "current_price": pos.current_price,
                "market_value": pos.market_value,
                "unrealized_pl": pos.unrealized_pl,
                "unrealized_plpc": pos.unrealized_plpc
            }
            for pos in positions
        ],
        "total_value": total_value,
        "total_pnl": total_pnl,
        "pnl_pct": (total_pnl / total_value * 100) if total_value > 0 else 0,
        "portfolio_pct": (total_value / account["equity"] * 100) if account["equity"] > 0 else 0
    }


if __name__ == "__main__":
    print("Testing Options Executor\n")

    # Test account info
    account = get_account_info()
    print(f"Account equity: ${account['equity']:,.2f}")
    print(f"Options buying power: ${account['options_buying_power']:,.2f}")

    # Test positions
    print("\nOptions Positions:")
    positions = get_options_positions()
    if positions:
        for pos in positions:
            emoji = "+" if pos.unrealized_pl >= 0 else ""
            print(f"  {pos.symbol} {pos.option_type.upper()} ${pos.strike} exp {pos.expiration}")
            print(f"    {pos.quantity}x @ ${pos.avg_entry_price:.2f} -> ${pos.current_price:.2f}")
            print(f"    P/L: {emoji}${pos.unrealized_pl:.2f} ({pos.unrealized_plpc*100:+.1f}%)")
    else:
        print("  No options positions")

    # Test contract lookup
    print("\nTesting contract lookup for AAPL calls...")
    contract = find_option_contract(
        underlying="AAPL",
        option_type="call",
        min_dte=14,
        max_dte=45
    )
    if contract:
        print(f"  Found: {contract['symbol']}")
        print(f"  Strike: ${contract['strike']} | Exp: {contract['expiration']} ({contract['dte']} DTE)")
    else:
        print("  No contracts found")

    # Test summary
    print("\nOptions Summary:")
    summary = get_options_summary()
    print(f"  Positions: {summary['count']}")
    print(f"  Total Value: ${summary['total_value']:,.2f}")
    print(f"  Total P/L: ${summary['total_pnl']:,.2f} ({summary['pnl_pct']:.1f}%)")
    print(f"  Portfolio %: {summary['portfolio_pct']:.1f}%")

    # Test performance
    print("\nAll-Time Performance:")
    perf = get_options_performance()
    print(f"  Total Trades: {perf['total_trades']} ({perf['open_trades']} open)")
    print(f"  Win Rate: {perf['win_rate']:.1f}%")
    print(f"  Avg Win: +{perf['avg_win']:.1f}% | Avg Loss: {perf['avg_loss']:.1f}%")
    print(f"  Total P/L: ${perf['total_pnl']:,.2f}")
