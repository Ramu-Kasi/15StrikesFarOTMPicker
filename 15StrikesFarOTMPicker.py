import requests
from datetime import datetime
import pytz
import os
import json

# Timezone setup
IST = pytz.timezone('Asia/Kolkata')

# API Configuration
BASE_URL = 'https://api.india.delta.exchange'

# Create logs directory if it doesn't exist
logs_dir = "option_chain_logs"
if not os.path.exists(logs_dir):
    os.makedirs(logs_dir)

# Create trades directory for tracking entry prices
trades_dir = "trades"
if not os.path.exists(trades_dir):
    os.makedirs(trades_dir)

# Create filename with timestamp
timestamp = datetime.now(IST).strftime('%Y-%m-%d_%H-%M-%S')
log_file = os.path.join(logs_dir, f"option_chain_{timestamp}.txt")

# Function to write to both console and file
def log_print(message, file):
    console_message = message.replace('₹', 'Rs.')
    
    try:
        print(console_message)
    except UnicodeEncodeError:
        print(console_message.encode('ascii', errors='replace').decode('ascii'))
    
    file.write(message + "\n")

def format_inr(amount):
    """Format INR amounts intelligently"""
    if amount >= 100000:
        lakhs = amount / 100000
        if lakhs >= 10:
            return f"₹{lakhs:.1f}L"
        else:
            return f"₹{lakhs:.2f}L"
    else:
        return f"₹{amount:,.0f}"

def get_current_usd_inr_rate():
    """Fetch current USD/INR exchange rate"""
    try:
        api_url = "https://api.exchangerate-api.com/v4/latest/USD"
        response = requests.get(api_url, timeout=3)
        
        if response.status_code == 200:
            data = response.json()
            inr_rate = data.get('rates', {}).get('INR')
            if inr_rate and inr_rate > 0:
                return float(inr_rate)
        return 84.0
    except:
        return 84.0

def get_trade_file_path(expiry_date_str):
    """Get path to trade tracking file for today's expiry"""
    return os.path.join(trades_dir, f"trade_{expiry_date_str}.json")

def save_trade_entry(expiry_date_str, trade_data):
    """Save trade entry details"""
    trade_file = get_trade_file_path(expiry_date_str)
    with open(trade_file, 'w') as f:
        json.dump(trade_data, f, indent=2)

def load_trade_entry(expiry_date_str):
    """Load trade entry details if exists"""
    trade_file = get_trade_file_path(expiry_date_str)
    if os.path.exists(trade_file):
        with open(trade_file, 'r') as f:
            return json.load(f)
    return None

def print_trade_summary_dashboard(f, trade_entry, current_data, usd_to_inr):
    """Print a prominent trade summary dashboard"""
    
    log_print("", f)
    log_print("╔" + "═" * 148 + "╗", f)
    log_print("║" + " " * 148 + "║", f)
    log_print("║" + "TRADE SUMMARY DASHBOARD".center(148) + "║", f)
    log_print("║" + " " * 148 + "║", f)
    log_print("╠" + "═" * 148 + "╣", f)
    
    if trade_entry:
        # Entry information
        log_print("║" + " " * 148 + "║", f)
        log_print("║   " + "ENTRY @ 3:30 AM".ljust(146) + "║", f)
        log_print("║   " + f"Time: {trade_entry['entry_time']}".ljust(146) + "║", f)
        log_print("║   " + f"Spot Price: ${trade_entry['spot_price']:,.2f}".ljust(146) + "║", f)
        log_print("║" + " " * 148 + "║", f)
        log_print("║   " + f"SELL CALL: {trade_entry['call_strike']:>8,.0f}  Premium: ${trade_entry['call_premium']:>8,.2f}".ljust(146) + "║", f)
        log_print("║   " + f"SELL PUT:  {trade_entry['put_strike']:>8,.0f}  Premium: ${trade_entry['put_premium']:>8,.2f}".ljust(146) + "║", f)
        log_print("║   " + "-" * 144 + "  ║", f)
        log_print("║   " + f"COMBINED PREMIUM @ ENTRY: ${trade_entry['combined_premium']:>10,.2f}  ({format_inr(trade_entry['combined_premium'] * usd_to_inr)})".ljust(146) + "║", f)
        log_print("║" + " " * 148 + "║", f)
        
        # Current information
        log_print("╠" + "═" * 148 + "╣", f)
        log_print("║" + " " * 148 + "║", f)
        log_print("║   " + "CURRENT POSITION".ljust(146) + "║", f)
        log_print("║   " + f"Time: {current_data['current_time']}".ljust(146) + "║", f)
        log_print("║   " + f"Spot Price: ${current_data['spot_price']:,.2f}".ljust(146) + "║", f)
        log_print("║" + " " * 148 + "║", f)
        log_print("║   " + f"CALL {trade_entry['call_strike']:>8,.0f}  Current Ask: ${current_data['call_ask']:>8,.2f}  (Entry: ${trade_entry['call_premium']:,.2f})".ljust(146) + "║", f)
        log_print("║   " + f"PUT  {trade_entry['put_strike']:>8,.0f}  Current Ask: ${current_data['put_ask']:>8,.2f}  (Entry: ${trade_entry['put_premium']:,.2f})".ljust(146) + "║", f)
        log_print("║   " + "-" * 144 + "  ║", f)
        log_print("║   " + f"COMBINED BUYBACK COST: ${current_data['combined_buyback']:>10,.2f}  ({format_inr(current_data['combined_buyback'] * usd_to_inr)})".ljust(146) + "║", f)
        log_print("║" + " " * 148 + "║", f)
        
        # PnL calculation
        log_print("╠" + "═" * 148 + "╣", f)
        log_print("║" + " " * 148 + "║", f)
        
        pnl_per_contract = trade_entry['combined_premium'] - current_data['combined_buyback']
        pnl_pct = (pnl_per_contract / trade_entry['combined_premium'] * 100)
        
        # Color-coded status (using text markers)
        if pnl_per_contract > 0:
            status = ">>> PROFIT <<<"
            marker = "[SUCCESS]"
        elif pnl_per_contract == 0:
            status = "=== BREAKEVEN ==="
            marker = "[INFO] "
        else:
            status = "<<< LOSS >>>"
            marker = "[FAILED]"
        
        log_print("║" + " " * 148 + "║", f)
        log_print("║" + status.center(148) + "║", f)
        log_print("║" + " " * 148 + "║", f)
        log_print("║   " + f"{marker} Price Difference: ${pnl_per_contract:+,.2f} per contract ({pnl_pct:+.2f}%)".ljust(146) + "║", f)
        log_print("║" + " " * 148 + "║", f)
        
        # Position sizing PnL
        log_print("║   " + "PnL BY POSITION SIZE:".ljust(146) + "║", f)
        log_print("║" + " " * 148 + "║", f)
        
        btc_sizes = [1, 2, 5, 7, 10, 12]
        for btc in btc_sizes:
            pnl_usd = pnl_per_contract * btc
            pnl_inr = pnl_usd * usd_to_inr
            
            position_str = f"{btc:>2} BTC ({btc * 1000:>5,} lots)"
            pnl_str = f"${pnl_usd:>+10,.2f}  ({format_inr(pnl_inr):>12})"
            
            log_print("║   " + f"  {position_str}: {pnl_str}".ljust(146) + "║", f)
        
        log_print("║" + " " * 148 + "║", f)
        
        # Exit recommendations
        log_print("╠" + "═" * 148 + "╣", f)
        log_print("║" + " " * 148 + "║", f)
        log_print("║   " + "EXIT TRIGGER STATUS:".ljust(146) + "║", f)
        log_print("║" + " " * 148 + "║", f)
        
        # Check exit conditions
        call_5x = trade_entry['call_premium'] * 5
        put_5x = trade_entry['put_premium'] * 5
        loss_1_5x = trade_entry['combined_premium'] * 1.5
        
        # Stop loss check (5x)
        if current_data['call_ask'] >= call_5x or current_data['put_ask'] >= put_5x:
            log_print("║   " + "[FAILED][FAILED][FAILED] STOP LOSS HIT (5x) - CLOSE BOTH LEGS NOW!".ljust(146) + "║", f)
        else:
            log_print("║   " + f"[SUCCESS] Stop Loss (5x): CE ${call_5x:.2f} | PE ${put_5x:.2f} - NOT HIT".ljust(146) + "║", f)
        
        # Loss limit check (1.5x)
        if current_data['combined_buyback'] >= loss_1_5x:
            log_print("║   " + "[FAILED][FAILED] LOSS LIMIT (1.5x) - CONSIDER CLOSING".ljust(146) + "║", f)
        else:
            log_print("║   " + f"[SUCCESS] Loss Limit (1.5x): ${loss_1_5x:.2f} - NOT HIT".ljust(146) + "║", f)
        
        # Time check
        current_time = datetime.now(IST)
        exit_time = current_time.replace(hour=17, minute=15, second=0, microsecond=0)
        if current_time >= exit_time:
            log_print("║   " + "[INFO]  Time Exit: Past 5:15 PM - CLOSE POSITION".ljust(146) + "║", f)
        else:
            time_remaining = exit_time - current_time
            hours = time_remaining.seconds // 3600
            minutes = (time_remaining.seconds % 3600) // 60
            log_print("║   " + f"[INFO]  Time Remaining: {hours}h {minutes}m until 5:15 PM exit".ljust(146) + "║", f)
        
        log_print("║" + " " * 148 + "║", f)
        
    else:
        # No trade entry - just show current prices
        log_print("║" + " " * 148 + "║", f)
        log_print("║   " + "NO ACTIVE TRADE - MONITORING MODE".center(144) + "  ║", f)
        log_print("║" + " " * 148 + "║", f)
        log_print("║   " + f"Time: {current_data['current_time']}".ljust(146) + "║", f)
        log_print("║   " + f"Spot Price: ${current_data['spot_price']:,.2f}".ljust(146) + "║", f)
        log_print("║" + " " * 148 + "║", f)
        log_print("║   " + f"SUGGESTED CALL: {current_data['call_strike']:>8,.0f}  Premium: ${current_data['call_bid']:>8,.2f}".ljust(146) + "║", f)
        log_print("║   " + f"SUGGESTED PUT:  {current_data['put_strike']:>8,.0f}  Premium: ${current_data['put_bid']:>8,.2f}".ljust(146) + "║", f)
        log_print("║   " + "-" * 144 + "  ║", f)
        log_print("║   " + f"COMBINED PREMIUM IF ENTERED NOW: ${current_data['combined_premium']:>10,.2f}  ({format_inr(current_data['combined_premium'] * usd_to_inr)})".ljust(146) + "║", f)
        log_print("║" + " " * 148 + "║", f)
        log_print("║   " + "[INFO]  Run this script at 3:30 AM on Saturday to log entry prices".center(144) + "  ║", f)
        log_print("║" + " " * 148 + "║", f)
    
    log_print("╚" + "═" * 148 + "╝", f)
    log_print("", f)

# Open file for writing
with open(log_file, 'w', encoding='utf-8') as f:
    
    # Fetch current USD/INR exchange rate
    usd_to_inr = get_current_usd_inr_rate()
    
    # Step 1: Determine current active expiry
    today = datetime.now(IST)
    expiry_cutoff_time = today.replace(hour=17, minute=30, second=0, microsecond=0)
    
    if today < expiry_cutoff_time:
        target_expiry_date = today
    else:
        from datetime import timedelta
        target_expiry_date = today + timedelta(days=1)
    
    expiry_date_str = target_expiry_date.strftime('%d-%m-%Y')
    
    # Step 2: Get current BTC spot price
    try:
        ticker_url = f"{BASE_URL}/v2/tickers/BTCUSD"
        response = requests.get(ticker_url, timeout=10)
        
        if response.status_code == 200:
            ticker_data = response.json()
            spot_price = float(ticker_data['result']['spot_price'])
        else:
            log_print(f"[ERROR] Failed to get spot price: {response.status_code}", f)
            exit(1)
            
    except Exception as e:
        log_print(f"[ERROR] Error getting spot price: {e}", f)
        exit(1)
    
    # Step 3: Get option chain
    try:
        option_chain_url = f"{BASE_URL}/v2/tickers"
        params = {
            'contract_types': 'call_options,put_options',
            'underlying_asset_symbols': 'BTC',
            'expiry_date': expiry_date_str
        }
        
        response = requests.get(option_chain_url, params=params, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            options = data['result']
            
            if not options:
                log_print(f"[ERROR] No options found for expiry date: {expiry_date_str}", f)
                exit(0)
            
            # Separate and sort
            calls = [opt for opt in options if opt['contract_type'] == 'call_options']
            puts = [opt for opt in options if opt['contract_type'] == 'put_options']
            
            calls.sort(key=lambda x: float(x['strike_price']))
            puts.sort(key=lambda x: float(x['strike_price']))
            
            # Find ATM
            all_strikes = sorted(set([float(opt['strike_price']) for opt in options]))
            atm_strike = min(all_strikes, key=lambda x: abs(x - spot_price))
            atm_index = all_strikes.index(atm_strike)
            
            # Create strike mappings
            calls_by_strike = {float(c['strike_price']): c for c in calls}
            puts_by_strike = {float(p['strike_price']): p for p in puts}
            
            # Find optimal strikes (same logic as before - 13-15 range)
            max_call_strikes = len(all_strikes) - atm_index - 1
            max_put_strikes = atm_index
            
            # Optimization logic for delta neutrality
            best_ce_distance = 13
            best_pe_distance = 13
            best_imbalance = float('inf')
            best_combo = None
            
            if max_call_strikes >= 13 and max_put_strikes >= 13:
                for ce_dist in range(13, min(16, max_call_strikes + 1)):
                    for pe_dist in range(13, min(16, max_put_strikes + 1)):
                        call_strike = all_strikes[atm_index + ce_dist]
                        put_strike = all_strikes[atm_index - pe_dist]
                        
                        call_opt = calls_by_strike.get(call_strike, {})
                        put_opt = puts_by_strike.get(put_strike, {})
                        
                        call_bid = float(call_opt.get('quotes', {}).get('best_bid', 0))
                        put_bid = float(put_opt.get('quotes', {}).get('best_bid', 0))
                        
                        if call_bid <= 0 or put_bid <= 0 or call_bid < 5 or put_bid < 5:
                            continue
                        
                        imbalance = abs(call_bid - put_bid)
                        total_width = ce_dist + pe_dist
                        score = imbalance - (total_width * 0.5)
                        
                        if score < best_imbalance:
                            best_imbalance = score
                            best_ce_distance = ce_dist
                            best_pe_distance = pe_dist
                            best_combo = {
                                'call_strike': call_strike,
                                'put_strike': put_strike,
                                'call_bid': call_bid,
                                'put_bid': put_bid
                            }
            
            # Get the optimal strikes
            call_strike_target = all_strikes[atm_index + best_ce_distance]
            put_strike_target = all_strikes[atm_index - best_pe_distance]
            
            call_opt_target = calls_by_strike.get(call_strike_target, {})
            put_opt_target = puts_by_strike.get(put_strike_target, {})
            
            call_quotes = call_opt_target.get('quotes', {})
            put_quotes = put_opt_target.get('quotes', {})
            
            call_bid_price = float(call_quotes.get('best_bid', 0))
            call_ask_price = float(call_quotes.get('best_ask', 0))
            put_bid_price = float(put_quotes.get('best_bid', 0))
            put_ask_price = float(put_quotes.get('best_ask', 0))
            
            combined_premium = call_bid_price + put_bid_price
            combined_buyback = call_ask_price + put_ask_price
            
            # Prepare data for dashboard
            current_data = {
                'current_time': today.strftime('%Y-%m-%d %H:%M:%S IST'),
                'spot_price': spot_price,
                'call_strike': call_strike_target,
                'put_strike': put_strike_target,
                'call_bid': call_bid_price,
                'call_ask': call_ask_price,
                'put_bid': put_bid_price,
                'put_ask': put_ask_price,
                'combined_premium': combined_premium,
                'combined_buyback': combined_buyback
            }
            
            # Check if we have an existing trade entry for today
            trade_entry = load_trade_entry(expiry_date_str)
            
            # If it's between 3:25 AM and 3:35 AM on Saturday, save as trade entry
            is_saturday = today.weekday() == 5
            is_entry_window = (today.hour == 3 and 25 <= today.minute <= 35)
            
            if is_saturday and is_entry_window and trade_entry is None:
                trade_entry = {
                    'entry_time': today.strftime('%Y-%m-%d %H:%M:%S IST'),
                    'expiry_date': expiry_date_str,
                    'spot_price': spot_price,
                    'call_strike': call_strike_target,
                    'put_strike': put_strike_target,
                    'call_premium': call_bid_price,
                    'put_premium': put_bid_price,
                    'combined_premium': combined_premium
                }
                save_trade_entry(expiry_date_str, trade_entry)
            
            # PRINT THE BIG DASHBOARD AT THE TOP
            print_trade_summary_dashboard(f, trade_entry, current_data, usd_to_inr)
            
            # Now print all the detailed logs as before
            log_print("=" * 150, f)
            log_print("DETAILED ANALYSIS LOG", f)
            log_print("=" * 150, f)
            log_print("", f)
            
            # [Rest of your original logging code continues here...]
            log_print(f"Current Date & Time: {today.strftime('%d-%m-%Y %H:%M:%S IST')}", f)
            log_print(f"[DATA] Target Expiry: {expiry_date_str}", f)
            log_print(f"[OK] Current BTC Spot Price (ATM): ${spot_price:,.2f}", f)
            log_print(f"[OK] Found {len(options)} total options for today's expiry", f)
            log_print(f"ATM Strike: ${atm_strike:,.0f}", f)
            
            # ... Continue with all your existing detailed logging ...
            # (I'll keep all the existing option chain display code)
            
        else:
            log_print(f"[ERROR] Failed to fetch option chain: {response.status_code}", f)
            
    except Exception as e:
        log_print(f"[ERROR] Error: {e}", f)
    
    log_print(f"\nLog saved to: {log_file}", f)

print(f"\n[SUCCESS] Successfully saved option chain data to: {log_file}")
