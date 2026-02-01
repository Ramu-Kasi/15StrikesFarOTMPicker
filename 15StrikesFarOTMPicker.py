import requests
from datetime import datetime
import os

# API Configuration
# IMPORTANT: Using PRODUCTION API to get REAL bid/ask spreads
# This is READ-ONLY - we're just observing market data, not placing any orders
BASE_URL = 'https://api.india.delta.exchange'  # Production API

# Create logs directory if it doesn't exist
logs_dir = "option_chain_logs"
if not os.path.exists(logs_dir):
    os.makedirs(logs_dir)

# Create filename with timestamp
timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
log_file = os.path.join(logs_dir, f"option_chain_{timestamp}.txt")

# Function to write to both console and file
def log_print(message, file):
    # For console: Replace special characters that cp1252 can't handle
    # For file: Keep original message with Unicode characters
    console_message = message.replace('₹', 'Rs.')
    
    try:
        print(console_message)
    except UnicodeEncodeError:
        # Fallback: Convert any remaining Unicode to ASCII
        print(console_message.encode('ascii', errors='replace').decode('ascii'))
    
    # Write full Unicode version to file (file is UTF-8, supports ₹)
    file.write(message + "\n")

def format_inr(amount):
    """
    Format INR amounts intelligently:
    - >= 1 lakh (100,000): Show as ₹1.6L, ₹3L, ₹4.5L
    - < 1 lakh: Show as ₹1,400 or ₹14,500 with commas
    """
    if amount >= 100000:
        lakhs = amount / 100000
        if lakhs >= 10:
            return f"₹{lakhs:.1f}L"
        else:
            return f"₹{lakhs:.2f}L"
    else:
        return f"₹{amount:,.0f}"

def get_current_usd_inr_rate():
    """
    Fetch current USD/INR exchange rate from a free API
    Falls back to 84.0 if API fails
    """
    try:
        # Try free forex API
        api_url = "https://api.exchangerate-api.com/v4/latest/USD"
        response = requests.get(api_url, timeout=3)
        
        if response.status_code == 200:
            data = response.json()
            inr_rate = data.get('rates', {}).get('INR')
            if inr_rate and inr_rate > 0:
                return float(inr_rate)
        
        # If API fails, return default
        return 84.0
        
    except:
        # Fallback to approximate rate
        return 84.0

# Open file for writing
with open(log_file, 'w', encoding='utf-8') as f:
    log_print("=" * 150, f)
    log_print("BTC ATM Options Chain - Scheduled Run", f)
    log_print("[DATA] PRODUCTION DATA (Real Market Bid/Ask Prices)", f)
    log_print("WARNING:  OBSERVATION MODE ONLY - No Orders Being Placed", f)
    log_print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}", f)
    log_print("=" * 150, f)
    log_print("", f)
    
    # Fetch current USD/INR exchange rate
    log_print("[RATE] Fetching current USD/INR exchange rate...", f)
    usd_to_inr = get_current_usd_inr_rate()
    log_print(f"   Current Rate: $1 USD = ₹{usd_to_inr:.2f} INR", f)
    log_print("", f)
    
    # Step 1: Determine current active expiry based on time (5:30 PM cutoff)
    today = datetime.now()
    log_print(f"Current Date & Time: {today.strftime('%d-%m-%Y %H:%M:%S IST')}", f)
    log_print("", f)
    
    # Check if current time is before or after 5:30 PM (17:30)
    expiry_cutoff_time = today.replace(hour=17, minute=30, second=0, microsecond=0)
    
    if today < expiry_cutoff_time:
        # Before 5:30 PM - use today's expiry
        target_expiry_date = today
        log_print(f"[TIME] Time is BEFORE 5:30 PM - Looking for TODAY'S expiry ({target_expiry_date.strftime('%d-%m-%Y')})", f)
    else:
        # After 5:30 PM - use tomorrow's expiry
        from datetime import timedelta
        target_expiry_date = today + timedelta(days=1)
        log_print(f"[TIME] Time is AFTER 5:30 PM - Looking for TOMORROW'S expiry ({target_expiry_date.strftime('%d-%m-%Y')})", f)
    
    expiry_date_str = target_expiry_date.strftime('%d-%m-%Y')
    log_print(f"[DATE] Target Expiry: {expiry_date_str}", f)
    log_print("", f)
    
    # Step 2: Get current BTC spot price (ATM price)
    try:
        # Get BTC spot price from ticker
        ticker_url = f"{BASE_URL}/v2/tickers/BTCUSD"
        response = requests.get(ticker_url, timeout=10)
        
        if response.status_code == 200:
            ticker_data = response.json()
            spot_price = float(ticker_data['result']['spot_price'])
            log_print(f"[OK] Current BTC Spot Price (ATM): ${spot_price:,.2f}", f)
            log_print("", f)
        else:
            log_print(f"[ERROR] Failed to get spot price: {response.status_code}", f)
            log_print(f"Response: {response.text}", f)
            exit(1)
            
    except Exception as e:
        log_print(f"[ERROR] Error getting spot price: {e}", f)
        exit(1)
    
    # Step 3: Get option chain for the target expiry
    try:
        # Fetch both call and put options for BTC expiring on target date
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
                log_print(f"   This could mean:", f)
                log_print(f"   - No daily options listed for this date", f)
                log_print(f"   - Weekend/holiday (no trading)", f)
                log_print(f"   - Expiry might be weekly/monthly only", f)
                log_print("", f)
                
                # Try to show available expiries
                log_print("Checking what expiries are available...", f)
                params_all = {
                    'contract_types': 'call_options,put_options',
                    'underlying_asset_symbols': 'BTC'
                }
                response_all = requests.get(option_chain_url, params=params_all, timeout=15)
                if response_all.status_code == 200:
                    all_opts = response_all.json()['result']
                    expiry_set = set()
                    for opt in all_opts[:50]:  # Check first 50 options
                        symbol = opt.get('symbol', '')
                        if symbol:
                            parts = symbol.split('-')
                            if len(parts) >= 4:
                                expiry_set.add(parts[-1])
                    if expiry_set:
                        log_print(f"   Available expiries in format DDMMYY: {', '.join(sorted(expiry_set))}", f)
                
                exit(0)
            
            log_print(f"[OK] Found {len(options)} total options for today's expiry", f)
            log_print("", f)
            
            # Step 4: Separate calls and puts, sort by strike price
            calls = [opt for opt in options if opt['contract_type'] == 'call_options']
            puts = [opt for opt in options if opt['contract_type'] == 'put_options']
            
            # Sort by strike price
            calls.sort(key=lambda x: float(x['strike_price']))
            puts.sort(key=lambda x: float(x['strike_price']))
            
            # Step 5: Find ATM strike (closest to spot price)
            all_strikes = sorted(set([float(opt['strike_price']) for opt in options]))
            
            # Find the strike closest to spot price
            atm_strike = min(all_strikes, key=lambda x: abs(x - spot_price))
            atm_index = all_strikes.index(atm_strike)
            
            log_print(f"ATM Strike: ${atm_strike:,.0f} (closest to spot: ${spot_price:,.2f})", f)
            log_print("=" * 150, f)
            log_print("", f)
            
            # Step 6: Get up to 15 strikes on either side for display
            # Always show what's available (up to 15 on each side)
            start_index = max(0, atm_index - 15)
            end_index = min(len(all_strikes), atm_index + 16)  # +16 to include ATM + 15 above
            
            selected_strikes = all_strikes[start_index:end_index]
            
            strikes_below = atm_index - start_index
            strikes_above = end_index - atm_index - 1
            
            log_print(f"Displaying strikes: {strikes_below} below ATM, ATM, {strikes_above} above ATM", f)
            log_print(f"Strike range: ${selected_strikes[0]:,.0f} to ${selected_strikes[-1]:,.0f}", f)
            log_print("", f)
            
            # Step 7: Display the option chain
            log_print("=" * 150, f)
            log_print(f"{'CALL OPTIONS (CE)':<75} | {'PUT OPTIONS (PE)':<75}", f)
            log_print("=" * 150, f)
            log_print(f"{'Symbol':<20} | {'Strike':<10} | {'Bid':<10} | {'Ask':<10} | {'IV':<10} | {'Symbol':<20} | {'Strike':<10} | {'Bid':<10} | {'Ask':<10} | {'IV':<10}", f)
            log_print("-" * 150, f)
            
            # Create a mapping of strike to options
            calls_by_strike = {float(c['strike_price']): c for c in calls}
            puts_by_strike = {float(p['strike_price']): p for p in puts}
            
            for strike in selected_strikes:
                call_opt = calls_by_strike.get(strike, {})
                put_opt = puts_by_strike.get(strike, {})
                
                # Format call data
                call_symbol = call_opt.get('symbol', '-')[:20]
                call_quotes = call_opt.get('quotes', {})
                call_bid = f"${float(call_quotes.get('best_bid', 0)):,.2f}" if call_quotes.get('best_bid') else '-'
                call_ask = f"${float(call_quotes.get('best_ask', 0)):,.2f}" if call_quotes.get('best_ask') else '-'
                call_iv = call_quotes.get('ask_iv', '-')
                
                # Format put data  
                put_symbol = put_opt.get('symbol', '-')[:20]
                put_quotes = put_opt.get('quotes', {})
                put_bid = f"${float(put_quotes.get('best_bid', 0)):,.2f}" if put_quotes.get('best_bid') else '-'
                put_ask = f"${float(put_quotes.get('best_ask', 0)):,.2f}" if put_quotes.get('best_ask') else '-'
                put_iv = put_quotes.get('ask_iv', '-')
                
                # Highlight ATM row
                strike_marker = " <- ATM" if strike == atm_strike else ""
                
                log_print(f"{call_symbol:<20} | ${strike:<9,.0f} | {call_bid:<10} | {call_ask:<10} | {call_iv:<10} | {put_symbol:<20} | ${strike:<9,.0f} | {put_bid:<10} | {put_ask:<10} | {put_iv:<10}{strike_marker}", f)
            
            log_print("=" * 150, f)
            log_print("", f)
            
            # Step 8: Summary
            log_print(f"[DATA] Summary:", f)
            log_print(f"  - Total strikes displayed: {len(selected_strikes)}", f)
            log_print(f"  - Call options available: {len([s for s in selected_strikes if s in calls_by_strike])}", f)
            log_print(f"  - Put options available: {len([s for s in selected_strikes if s in puts_by_strike])}", f)
            log_print(f"  - ATM Strike: ${atm_strike:,.0f}", f)
            log_print(f"  - Current Spot: ${spot_price:,.2f}", f)
            
            # Step 9: Short Strangle Analysis - Saturday Early Morning Setup
            log_print("", f)
            log_print("=" * 150, f)
            log_print("[CHART] SHORT STRANGLE ANALYSIS - SATURDAY EARLY MORNING SETUP", f)
            log_print("Trading Window: Saturday 3:30 AM -> Saturday 5:15 PM (SAME DAY EXPIRY)", f)
            log_print("Strategy: Capture intraday theta decay during 'Sideways Saturday' (~14 hours)", f)
            log_print("=" * 150, f)
            
            # Check if today is Saturday
            is_saturday = today.weekday() == 5  # 5 = Saturday
            if is_saturday:
                log_print("[SUCCESS] TODAY IS SATURDAY - TRADE DAY!", f)
            else:
                day_name = today.strftime('%A')
                log_print(f"[INFO]  Today is {day_name} - Observation only", f)
            log_print("", f)
            
            # Determine how many strikes are available on each side
            max_call_strikes = len(all_strikes) - atm_index - 1
            max_put_strikes = atm_index
            
            log_print(f"CE Strikes available above ATM: {max_call_strikes}", f)
            log_print(f"PE Strikes available below ATM: {max_put_strikes}", f)
            log_print("", f)
            
            # Try 15, 13 strikes as per requirement (sufficient width to avoid whipsaws)
            # Base strategy: 13 strikes with +2 adjustment for delta neutrality (13-15 range)
            target_strikes = None
            for strike_distance in [15, 13]:
                if strike_distance <= max_call_strikes and strike_distance <= max_put_strikes:
                    target_strikes = strike_distance
                    break
            
            if target_strikes is None:
                log_print("[FAILED] INSUFFICIENT STRIKES - Need minimum 13 strikes on each side for strategy", f)
                log_print(f"   Available: {min(max_call_strikes, max_put_strikes)} strikes", f)
                log_print("   WARNING:  DO NOT TRADE - Wait for options with more strikes available", f)
                sufficient_width = False
            elif target_strikes >= 13:
                log_print(f"[SUCCESS] Initial strike distance: {target_strikes} on either side", f)
                log_print("   Base: 13 strikes, will adjust within 13-15 range for delta neutrality...", f)
                sufficient_width = True
            else:
                log_print(f"WARNING:  Only {target_strikes} strikes available - BELOW MINIMUM", f)
                log_print("   [FAILED] DO NOT TRADE - Insufficient width for strategy", f)
                sufficient_width = False
            
            log_print("=" * 150, f)
            log_print("", f)
            
            if sufficient_width and target_strikes >= 13:
                # Intelligent strike selection for delta neutrality
                # STRICT RULE: Base distance 13 strikes, allow +2 adjustment for balance
                
                best_ce_distance = target_strikes
                best_pe_distance = target_strikes
                best_imbalance = float('inf')
                best_combo = None
                
                # Define the base distance and adjustment range
                base_strike_dist = 13  # Base symmetric distance
                max_adjustment = 2     # Can adjust +2 from base (not below)
                
                min_strike_dist = base_strike_dist      # 13 (minimum)
                max_strike_dist = base_strike_dist + max_adjustment  # 13 + 2 = 15
                
                log_print("[SEARCH] OPTIMIZING STRIKES FOR DELTA NEUTRALITY:", f)
                log_print(f"   Base Distance: {base_strike_dist} strikes", f)
                log_print(f"   Allowed Range: {min_strike_dist} to {max_strike_dist} strikes", f)
                log_print("", f)
                
                # Try combinations within 13-15 range
                # Ensure we never exceed available strikes
                for ce_dist in range(max(min_strike_dist, 1), min(max_strike_dist + 1, max_call_strikes + 1)):
                    for pe_dist in range(max(min_strike_dist, 1), min(max_strike_dist + 1, max_put_strikes + 1)):
                        call_strike = all_strikes[atm_index + ce_dist]
                        put_strike = all_strikes[atm_index - pe_dist]
                        
                        call_opt = calls_by_strike.get(call_strike, {})
                        put_opt = puts_by_strike.get(put_strike, {})
                        
                        call_bid = float(call_opt.get('quotes', {}).get('best_bid', 0))
                        put_bid = float(put_opt.get('quotes', {}).get('best_bid', 0))
                        
                        # Skip if either bid is 0 (no liquidity)
                        if call_bid <= 0 or put_bid <= 0:
                            continue
                        
                        # Skip if premiums are too low (< $5 each)
                        if call_bid < 5 or put_bid < 5:
                            continue
                        
                        # Calculate premium imbalance (we want this close to 0)
                        imbalance = abs(call_bid - put_bid)
                        imbalance_pct = (imbalance / max(call_bid, put_bid) * 100)
                        
                        # Prefer wider strikes, but prioritize balance
                        # Score: lower is better (imbalance penalty + width bonus)
                        total_width = ce_dist + pe_dist
                        width_bonus = total_width * 0.5  # Bonus for wider strikes
                        score = imbalance - width_bonus  # Lower imbalance + wider = better score
                        
                        if score < best_imbalance:
                            best_imbalance = score
                            best_ce_distance = ce_dist
                            best_pe_distance = pe_dist
                            best_combo = {
                                'call_strike': call_strike,
                                'put_strike': put_strike,
                                'call_bid': call_bid,
                                'put_bid': put_bid,
                                'imbalance': imbalance,
                                'imbalance_pct': imbalance_pct
                            }
                
                if best_combo and best_combo['call_bid'] >= 5 and best_combo['put_bid'] >= 5:
                    call_strike_target = best_combo['call_strike']
                    put_strike_target = best_combo['put_strike']
                    
                    # Safety check - ensure we're within adjustment limits
                    if best_ce_distance > max_strike_dist or best_pe_distance > max_strike_dist:
                        log_print(f"[FAILED] ERROR: Strike selection exceeded {max_strike_dist} limit!", f)
                        log_print(f"   CE: {best_ce_distance}, PE: {best_pe_distance}", f)
                        log_print(f"   Falling back to symmetric base: {base_strike_dist}", f)
                        best_ce_distance = base_strike_dist
                        best_pe_distance = base_strike_dist
                        call_strike_target = all_strikes[atm_index + base_strike_dist]
                        put_strike_target = all_strikes[atm_index - base_strike_dist]
                    
                    log_print(f"[SUCCESS] OPTIMIZED STRIKES SELECTED:", f)
                    log_print(f"   CE: +{best_ce_distance} strikes from ATM (${call_strike_target:,.0f})", f)
                    log_print(f"   PE: -{best_pe_distance} strikes from ATM (${put_strike_target:,.0f})", f)
                    log_print(f"   [SUCCESS] Within {min_strike_dist}-{max_strike_dist} strike range", f)
                    log_print(f"   Premium Balance: CE ${best_combo['call_bid']:.2f} | PE ${best_combo['put_bid']:.2f}", f)
                    log_print(f"   Imbalance: ${best_combo['imbalance']:.2f} ({best_combo['imbalance_pct']:.1f}%)", f)
                    
                    if best_combo['imbalance_pct'] < 20:
                        log_print(f"   [SUCCESS] Well balanced (delta-neutral)", f)
                    elif best_combo['imbalance_pct'] < 40:
                        log_print(f"   WARNING:  Moderate imbalance", f)
                    else:
                        log_print(f"   WARNING:  High imbalance - but acceptable premiums", f)
                else:
                    # No good combination found - use symmetric strikes
                    log_print(f"WARNING:  No optimal combination found with sufficient premium", f)
                    log_print(f"   Using symmetric strikes: +/-{target_strikes}", f)
                    best_ce_distance = target_strikes
                    best_pe_distance = target_strikes
                    call_strike_target = all_strikes[atm_index + target_strikes]
                    put_strike_target = all_strikes[atm_index - target_strikes]
                
                log_print("", f)
                
                call_opt_target = calls_by_strike.get(call_strike_target, {})
                put_opt_target = puts_by_strike.get(put_strike_target, {})
                
                # Get bid prices (since we're selling) and ask prices (for spread analysis)
                call_quotes = call_opt_target.get('quotes', {})
                put_quotes = put_opt_target.get('quotes', {})
                
                call_bid_price = float(call_quotes.get('best_bid', 0))
                call_ask_price = float(call_quotes.get('best_ask', 0))
                call_bid_size = call_quotes.get('bid_size', 0)
                call_ask_size = call_quotes.get('ask_size', 0)
                
                put_bid_price = float(put_quotes.get('best_bid', 0))
                put_ask_price = float(put_quotes.get('best_ask', 0))
                put_bid_size = put_quotes.get('bid_size', 0)
                put_ask_size = put_quotes.get('ask_size', 0)
                
                # Calculate spreads
                call_spread = call_ask_price - call_bid_price if call_bid_price > 0 and call_ask_price > 0 else 0
                put_spread = put_ask_price - put_bid_price if put_bid_price > 0 and put_ask_price > 0 else 0
                call_spread_pct = (call_spread / call_ask_price * 100) if call_ask_price > 0 else 0
                put_spread_pct = (put_spread / put_ask_price * 100) if put_ask_price > 0 else 0
                
                total_premium = call_bid_price + put_bid_price
                range_width = call_strike_target - put_strike_target
                range_width_pct = (range_width / spot_price * 100)
                
                # Display trade details
                log_print("[TARGET] TRADE SETUP:", f)
                log_print("", f)
                log_print(f"SELL CALL: {call_opt_target.get('symbol', 'N/A')}", f)
                log_print(f"  Strike: ${call_strike_target:,.0f} (+{best_ce_distance} strikes, {((call_strike_target - spot_price) / spot_price * 100):.2f}% above spot)", f)
                log_print(f"  Bid Price: ${call_bid_price:,.2f} <- YOU RECEIVE THIS", f)
                log_print(f"  Ask Price: ${call_ask_price:,.2f}", f)
                log_print(f"  Spread: ${call_spread:,.2f} ({call_spread_pct:.2f}%)", f)
                log_print(f"  Bid Size: {call_bid_size} contracts", f)
                log_print("", f)
                log_print(f"SELL PUT: {put_opt_target.get('symbol', 'N/A')}", f)
                log_print(f"  Strike: ${put_strike_target:,.0f} (-{best_pe_distance} strikes, {((spot_price - put_strike_target) / spot_price * 100):.2f}% below spot)", f)
                log_print(f"  Bid Price: ${put_bid_price:,.2f} <- YOU RECEIVE THIS", f)
                log_print(f"  Ask Price: ${put_ask_price:,.2f}", f)
                log_print(f"  Spread: ${put_spread:,.2f} ({put_spread_pct:.2f}%)", f)
                log_print(f"  Bid Size: {put_bid_size} contracts", f)
                log_print("", f)
                log_print("=" * 150, f)
                log_print("", f)
                log_print("[MONEY] PREMIUM & MARGIN:", f)
                log_print(f"  Total Premium Collected: ${total_premium:,.2f} per contract", f)
                log_print(f"  Premium Balance: CE ${call_bid_price:.2f} | PE ${put_bid_price:.2f} (Delta ${abs(call_bid_price - put_bid_price):.2f})", f)
                log_print(f"  Safe Range: ${put_strike_target:,.0f} - ${call_strike_target:,.0f}", f)
                log_print(f"  Range Width: ${range_width:,.0f} ({range_width_pct:.2f}% of spot price)", f)
                log_print("", f)
                log_print(f"  [DATA] MARGIN REQUIREMENT (200x Leverage):", f)
                log_print(f"", f)
                log_print(f"     WARNING:  IMPORTANT: Margin is approximately FIXED at ~$0.88/contract", f)
                log_print(f"         regardless of strike selection (ATM or far OTM)", f)
                log_print(f"", f)
                log_print(f"     Note: 1 BTC = 1,000 lots (contracts)", f)
                log_print(f"         Margin per BTC = $880 USD ({format_inr(880 * usd_to_inr)})", f)
                log_print(f"", f)
                
                # Calculate position sizes based on combined premium
                log_print(f"  [MONEY] POSITION SIZING & PROFIT/LOSS ANALYSIS:", f)
                log_print(f"", f)
                log_print(f"     Combined Premium per 1 BTC: ${total_premium:.2f} USD ({format_inr(total_premium * usd_to_inr)})", f)
                log_print(f"     (This is the total for selling CE + PE for 1 BTC position)", f)
                log_print(f"", f)
                
                # Calculate for different BTC position sizes
                btc_sizes = [1, 2, 5, 7, 10, 12]
                
                log_print(f"  {'Position':<15} {'Margin (USD)':<15} {'Margin (INR)':<15} {'Premium':<20} {'Max Profit':<20} {'Loss@1.5x':<20} {'Loss@5xSL':<20}", f)
                log_print(f"  {'-'*130}", f)
                
                for btc in btc_sizes:
                    lots = btc * 1000
                    margin_usd = btc * 880
                    margin_inr = margin_usd * usd_to_inr
                    
                    # Total premium for this position
                    # total_premium is already per contract, multiply by lots
                    premium_usd = total_premium * btc  # Scale by BTC, not lots
                    premium_inr = premium_usd * usd_to_inr
                    
                    # Max profit (premium - minimal buyback ~$0.10 per BTC)
                    max_profit_usd = premium_usd - (btc * 0.10)
                    max_profit_inr = max_profit_usd * usd_to_inr
                    
                    # Loss at 1.5x (premium x 1.5)
                    loss_1_5x_usd = premium_usd * 1.5
                    loss_1_5x_inr = loss_1_5x_usd * usd_to_inr
                    
                    # Loss at 5x SL (premium x 3, since other leg decays)
                    loss_5x_usd = premium_usd * 3
                    loss_5x_inr = loss_5x_usd * usd_to_inr
                    
                    # Format output with intelligent INR formatting
                    position_str = f"{btc} BTC ({lots:,} lots)"
                    margin_usd_str = f"${margin_usd:,.0f}"
                    margin_inr_str = format_inr(margin_inr)
                    premium_str = f"${premium_usd:,.0f} ({format_inr(premium_inr)})"
                    profit_str = f"${max_profit_usd:,.0f} ({format_inr(max_profit_inr)})"
                    loss_1_5x_str = f"-${loss_1_5x_usd:,.0f} ({format_inr(loss_1_5x_inr)})"
                    loss_5x_str = f"-${loss_5x_usd:,.0f} ({format_inr(loss_5x_inr)})"
                    
                    log_print(f"  {position_str:<15} {margin_usd_str:<15} {margin_inr_str:<15} {premium_str:<20} {profit_str:<20} {loss_1_5x_str:<20} {loss_5x_str:<20}", f)
                
                log_print(f"", f)
                log_print(f"  [NOTE] Exit Conditions:", f)
                log_print(f"     1. Stop Loss (5x): If CE >= ${call_bid_price * 5:.2f} OR PE >= ${put_bid_price * 5:.2f} -> Close both (Expected loss: 3x premium)", f)
                log_print(f"     2. Loss Limit (1.5x): If buyback cost >= ${total_premium * 1.5:.2f} -> Close both (Loss: 1.5x premium)", f)
                log_print(f"     3. Time Exit: 5:15 PM IST -> Close both (Target: Full premium decay)", f)
                log_print(f"", f)
                log_print(f"  [INFO] Recommended Position Sizing (with {format_inr(1000000)} capital):", f)
                log_print(f"     Conservative: 5 BTC (5,000 lots) - 40% capital utilization", f)
                log_print(f"     Moderate: 7 BTC (7,000 lots) - 56% capital utilization", f)
                log_print(f"     Aggressive: 10 BTC (10,000 lots) - 80% capital utilization", f)
                log_print(f"     Maximum: 12 BTC (12,000 lots) - 96% capital utilization", f)
                log_print("", f)
                log_print("[DATA] LIQUIDITY & SPREAD CHECK:", f)
                
                # Call spread check
                if call_spread_pct < 3:
                    log_print(f"  [SUCCESS] Call Spread: EXCELLENT ({call_spread_pct:.2f}%)", f)
                    call_ok = True
                elif call_spread_pct < 5:
                    log_print(f"  [SUCCESS] Call Spread: GOOD ({call_spread_pct:.2f}%)", f)
                    call_ok = True
                elif call_spread_pct < 10:
                    log_print(f"  WARNING:  Call Spread: ACCEPTABLE ({call_spread_pct:.2f}%)", f)
                    call_ok = True
                else:
                    log_print(f"  [FAILED] Call Spread: TOO WIDE ({call_spread_pct:.2f}%)", f)
                    call_ok = False
                
                # Put spread check
                if put_spread_pct < 3:
                    log_print(f"  [SUCCESS] Put Spread: EXCELLENT ({put_spread_pct:.2f}%)", f)
                    put_ok = True
                elif put_spread_pct < 5:
                    log_print(f"  [SUCCESS] Put Spread: GOOD ({put_spread_pct:.2f}%)", f)
                    put_ok = True
                elif put_spread_pct < 10:
                    log_print(f"  WARNING:  Put Spread: ACCEPTABLE ({put_spread_pct:.2f}%)", f)
                    put_ok = True
                else:
                    log_print(f"  [FAILED] Put Spread: TOO WIDE ({put_spread_pct:.2f}%)", f)
                    put_ok = False
                
                # Bid availability check
                if call_bid_price == 0:
                    log_print(f"  [FAILED] Call: NO BID AVAILABLE", f)
                    call_ok = False
                if put_bid_price == 0:
                    log_print(f"  [FAILED] Put: NO BID AVAILABLE", f)
                    put_ok = False
                
                log_print("", f)
                log_print("=" * 150, f)
                log_print("", f)
                
                # Overall trade quality assessment
                log_print("[DICE] TRADE QUALITY ASSESSMENT:", f)
                log_print("", f)
                
                if call_ok and put_ok and sufficient_width:
                    if call_spread_pct < 3 and put_spread_pct < 3:
                        log_print("  [SUCCESS][SUCCESS][SUCCESS] EXCELLENT - Ready to trade!", f)
                        trade_quality = "EXCELLENT"
                    elif call_spread_pct < 5 and put_spread_pct < 5:
                        log_print("  [SUCCESS][SUCCESS] GOOD - Ready to trade", f)
                        trade_quality = "GOOD"
                    else:
                        log_print("  [SUCCESS] ACCEPTABLE - Can trade with caution", f)
                        trade_quality = "ACCEPTABLE"
                else:
                    log_print("  [FAILED] POOR - DO NOT TRADE", f)
                    trade_quality = "POOR"
                    if not call_ok:
                        log_print("     Reason: Call option has issues", f)
                    if not put_ok:
                        log_print("     Reason: Put option has issues", f)
                    if not sufficient_width:
                        log_print("     Reason: Insufficient strike width", f)
                
                # Saturday specific recommendation
                log_print("", f)
                if is_saturday and trade_quality in ["EXCELLENT", "GOOD", "ACCEPTABLE"]:
                    log_print("[START] SATURDAY TRADE RECOMMENDATION: GO AHEAD!", f)
                    log_print(f"   Premium: ${total_premium:,.2f}", f)
                    log_print(f"   Safe Range: ${put_strike_target:,.0f} - ${call_strike_target:,.0f}", f)
                    log_print(f"   Trade Duration: 3:30 AM - 5:15 PM (Same day expiry - ~14 hours)", f)
                    log_print(f"   Expected Outcome: Capture theta decay on 'Sideways Saturday'", f)
                elif is_saturday:
                    log_print("[STOP] SATURDAY TRADE RECOMMENDATION: SKIP THIS ONE", f)
                    log_print("   Wait for better liquidity or wider strikes", f)
                elif trade_quality in ["EXCELLENT", "GOOD"]:
                    log_print(f"[INFO]  Good setup observed on {day_name}", f)
                    log_print("   Continue monitoring for Saturday execution", f)
            else:
                log_print("[STOP] TRADE SETUP NOT POSSIBLE", f)
                log_print("   Reason: Insufficient strikes available for strategy", f)
                log_print("   Minimum required: 13 strikes on each side", f)
            
        else:
            log_print(f"[ERROR] Failed to fetch option chain: {response.status_code}", f)
            log_print(f"Response: {response.text}", f)
            
    except Exception as e:
        log_print(f"[ERROR] Error: {e}", f)
    
    log_print("", f)
    log_print("=" * 150, f)
    log_print(f"Log saved to: {log_file}", f)
    log_print("=" * 150, f)

print(f"\n[SUCCESS] Successfully saved option chain data to: {log_file}")