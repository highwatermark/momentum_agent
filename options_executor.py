"""
Options Executor - Place and manage options orders via Alpaca
"""
import json
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import List, Dict, Optional

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

from config import ALPACA_API_KEY, ALPACA_SECRET_KEY, OPTIONS_CONFIG
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

    # Check position limits
    current_positions = get_options_positions()
    if len(current_positions) >= OPTIONS_CONFIG["max_options_positions"]:
        return {
            "success": False,
            "error": f"Max options positions ({OPTIONS_CONFIG['max_options_positions']}) reached"
        }

    # Check if already have position in this underlying
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

    # Place the order
    order_result = place_options_order(
        contract_symbol=contract['symbol'],
        quantity=quantity,
        side="buy",
        order_type="market"
    )

    if not order_result.get("success"):
        return order_result

    # Log to database
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
            flow_signal_id=None  # Would need signal ID from DB if logged
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
        "thesis": enriched_signal.thesis[:200] if enriched_signal.thesis else None
    }


def close_options_position(
    contract_symbol: str,
    reason: str = "manual",
    quantity: int = None
) -> Dict:
    """
    Close an options position.

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

        # Update database
        try:
            trade = get_options_trade_by_contract(contract_symbol, status='open')
            if trade:
                update_options_trade_exit(
                    trade_id=trade['id'],
                    exit_price=exit_price,
                    exit_reason=reason
                )
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
            "order_id": order_result.get('order_id')
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
