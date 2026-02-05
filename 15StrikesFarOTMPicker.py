import requests
from datetime import datetime
import pytz
import os
import json
import traceback

# Timezone setup
IST = pytz.timezone('Asia/Kolkata')
BASE_URL = 'https://api.india.delta.exchange'

# Create directories
logs_dir = "option_chain_logs"
trades_dir = "trades"
for d in [logs_dir, trades_dir]:
    if not os.path.exists(d):
        os.makedirs(d)

timestamp = datetime.now(IST).strftime('%Y-%m-%d_%H-%M-%S')
log_file = os.path.join(logs_dir, f"option_chain_{timestamp}.txt")

def log_print(message, file):
    console_message = message.replace('₹', 'Rs.')
    try:
        print(console_message)
    except UnicodeEncodeError:
        print(console_message.encode('ascii', errors='replace').decode('ascii'))
    file.write(message + "\n")
    file.flush()  # Force write to disk

def format_inr(amount):
    if amount >= 100000:
        lakhs = amount / 100000
        return f"₹{lakhs:.2f}L" if lakhs < 10 else f"₹{lakhs:.1f}L"
    return f"₹{amount:,.0f}"

def get_current_usd_inr_rate():
    try:
        response = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=3)
        if response.status_code == 200:
            inr_rate = response.json().get('rates', {}).get('INR')
            if inr_rate and inr_rate > 0:
                return float(inr_rate)
        return 84.0
    except:
        return 84.0

def get_trade_file_path(expiry_date_str):
    return os.path.join(trades_dir, f"trade_{expiry_date_str}.json")

def save_trade_entry(expiry_date_str, trade_data):
    trade_file = get_trade_file_path(expiry_date_str)
    with open(trade_file, 'w') as f:
        json.dump(trade_data, f, indent=2)

def load_trade_entry(expiry_date_str):
    trade_file = get_trade_file_path(expiry_date_str)
    if os.path.exists(trade_file):
        with open(trade_file, 'r') as f:
            return json.load(f)
    return None

# Ensure file is created even if there's an error
with open(log_file, 'w', encoding='utf-8') as f:
    try:
        usd_to_inr = get_current_usd_inr_rate()
        today = datetime.now(IST)
        expiry_cutoff_time = today.replace(hour=17, minute=30, second=0, microsecond=0)
        
        if today < expiry_cutoff_time:
            target_expiry_date = today
        else:
            from datetime import timedelta
            target_expiry_date = today + timedelta(days=1)
        
        expiry_date_str = target_expiry_date.strftime('%d-%m-%Y')
        
        log_print("=" * 160, f)
        log_print(f"BTC SHORT STRANGLE - {today.strftime('%d-%m-%Y %H:%M:%S IST')}", f)
        log_print("=" * 160, f)
        log_print("", f)
        
        # Get spot price
        ticker_url = f"{BASE_URL}/v2/tickers/BTCUSD"
        response = requests.get(ticker_url, timeout=10)
        if response.status_code != 200:
            log_print(f"[ERROR] Failed to get spot price: {response.status_code}", f)
            log_print(f"Response: {response.text}", f)
            raise Exception("Failed to get spot price")
        
        spot_price = float(response.json()['result']['spot_price'])
        
        # Get options
        option_chain_url = f"{BASE_URL}/v2/tickers"
        params = {
            'contract_types': 'call_options,put_options',
            'underlying_asset_symbols': 'BTC',
            'expiry_date': expiry_date_str
        }
        
        response = requests.get(option_chain_url, params=params, timeout=15)
        if response.status_code != 200:
            log_print(f"[ERROR] Failed to get options: {response.status_code}", f)
            log_print(f"Response: {response.text}", f)
            raise Exception("Failed to get options")
        
        options = response.json()['result']
        if not options:
            log_print(f"[ERROR] No options for {expiry_date_str}", f)
            log_print("", f)
            log_print(f"Log saved to: {log_file}", f)
            print(f"\n[INFO] No options available for {expiry_date_str}")
            exit(0)
        
        # Process options
        calls = [opt for opt in options if opt['contract_type'] == 'call_options']
        puts = [opt for opt in options if opt['contract_type'] == 'put_options']
        
        calls.sort(key=lambda x: float(x['strike_price']))
        puts.sort(key=lambda x: float(x['strike_price']))
        
        all_strikes = sorted(set([float(opt['strike_price']) for opt in options]))
        atm_strike = min(all_strikes, key=lambda x: abs(x - spot_price))
        atm_index = all_strikes.index(atm_strike)
        
        calls_by_strike = {float(c['strike_price']): c for c in calls}
        puts_by_strike = {float(p['strike_price']): p for p in puts}
        
        # Find optimal strikes (13-15 range)
        max_call_strikes = len(all_strikes) - atm_index - 1
        max_put_strikes = atm_index
        
        best_ce_distance = 13
        best_pe_distance = 13
        selection_reason = "Symmetric positioning (13 strikes)"
        
        if max_call_strikes >= 13 and max_put_strikes >= 13:
            best_imbalance = float('inf')
            for ce_dist in range(13, min(16, max_call_strikes + 1)):
                for pe_dist in range(13, min(16, max_put_strikes + 1)):
                    call_strike = all_strikes[atm_index + ce_dist]
                    put_strike = all_strikes[atm_index - pe_dist]
                    
                    call_opt = calls_by_strike.get(call_strike, {})
                    put_opt = puts_by_strike.get(put_strike, {})
                    
                    call_bid = float(call_opt.get('quotes', {}).get('best_bid', 0))
                    put_bid = float(put_opt.get('quotes', {}).get('best_bid', 0))
                    
                    if call_bid < 5 or put_bid < 5:
                        continue
                    
                    imbalance = abs(call_bid - put_bid)
                    total_width = ce_dist + pe_dist
                    score = imbalance - (total_width * 0.5)
                    
                    if score < best_imbalance:
                        best_imbalance = score
                        best_ce_distance = ce_dist
                        best_pe_distance = pe_dist
                        
                        # Determine reason
                        if ce_dist == pe_dist:
                            if imbalance <= 5:
                                selection_reason = f"Symmetric + Delta balanced (Δ${imbalance:.2f})"
                            else:
                                selection_reason = f"Symmetric positioning ({ce_dist} strikes)"
                        else:
                            if imbalance <= 5:
                                selection_reason = f"Asymmetric for delta neutrality (CE +{ce_dist}, PE -{pe_dist}, Δ${imbalance:.2f})"
                            else:
                                selection_reason = f"Asymmetric positioning (CE +{ce_dist}, PE -{pe_dist})"
        
        call_strike_target = all_strikes[atm_index + best_ce_distance]
        put_strike_target = all_strikes[atm_index - best_pe_distance]
        
        call_opt = calls_by_strike.get(call_strike_target, {})
        put_opt = puts_by_strike.get(put_strike_target, {})
        
        call_quotes = call_opt.get('quotes', {})
        put_quotes = put_opt.get('quotes', {})
        
        call_bid = float(call_quotes.get('best_bid', 0))
        call_ask = float(call_quotes.get('best_ask', 0))
        put_bid = float(put_quotes.get('best_bid', 0))
        put_ask = float(put_quotes.get('best_ask', 0))
        
        # Check for trade entry (3:25-3:35 AM on Saturday)
        is_saturday = today.weekday() == 5
        is_entry_window = (today.hour == 3 and 25 <= today.minute <= 35)
        
        trade_entry = load_trade_entry(expiry_date_str)
        
        if is_saturday and is_entry_window and trade_entry is None:
            trade_entry = {
                'entry_time': today.strftime('%Y-%m-%d %H:%M:%S IST'),
                'expiry_date': expiry_date_str,
                'spot_price': spot_price,
                'call_strike': call_strike_target,
                'put_strike': put_strike_target,
                'call_premium': call_bid,
                'put_premium': put_bid,
                'combined_premium': call_bid + put_bid,
                'ce_distance': best_ce_distance,
                'pe_distance': best_pe_distance,
                'selection_reason': selection_reason
            }
            save_trade_entry(expiry_date_str, trade_entry)
        
        # ═══════════════════════════════════════════════════════════════════
        # SUMMARY
        # ═══════════════════════════════════════════════════════════════════
        
        log_print(f"Spot: ${spot_price:,.2f} | ATM: ${atm_strike:,.0f} | Expiry: {expiry_date_str} | USD/INR: {usd_to_inr:.2f}", f)
        log_print("", f)
        
        # STRIKE SELECTION INFO
        log_print("STRIKE SELECTION:", f)
        log_print("-" * 160, f)
        if trade_entry and 'ce_distance' in trade_entry and 'pe_distance' in trade_entry:
            log_print(f"Selected Strikes: CE +{trade_entry['ce_distance']} strikes from ATM (${int(trade_entry['call_strike']):,}), PE -{trade_entry['pe_distance']} strikes from ATM (${int(trade_entry['put_strike']):,})", f)
            log_print(f"Reason: {trade_entry.get('selection_reason', 'N/A')}", f)
        else:
            log_print(f"Selected Strikes: CE +{best_ce_distance} strikes from ATM (${int(call_strike_target):,}), PE -{best_pe_distance} strikes from ATM (${int(put_strike_target):,})", f)
            log_print(f"Reason: {selection_reason}", f)
        log_print("-" * 160, f)
        log_print("", f)
        
        current_time_str = today.strftime('%H:%M:%S')
        
        if trade_entry:
            # Active trade - show entry vs current
            call_entry = trade_entry['call_premium']
            put_entry = trade_entry['put_premium']
            combined_entry = trade_entry['combined_premium']
            
            # Current buyback prices
            call_current = call_ask
            put_current = put_ask
            combined_current = call_current + put_current
            
            # PnL calculation (per 1 BTC)
            pnl_usd = combined_entry - combined_current
            pnl_inr = pnl_usd * usd_to_inr
            
            # Table header
            log_print("-" * 160, f)
            log_print(f"{'Position':<10} | {'Strikes':<12} | {'Premium @ 3:30':<18} | {'Combined Premium':<20} | {'Current Premium @ ' + current_time_str:<28} | {'Current Combined Premium':<30} | {'PnL':<30}", f)
            log_print("-" * 160, f)
            
            # CALL row with combined values
            log_print(f"{'CALL':<10} | {int(trade_entry['call_strike']):>12,} | ${call_entry:>16.2f} | ${combined_entry:>18.2f} | ${call_current:>26.2f} | ${combined_current:>28.2f} | ${pnl_usd:>+10.2f} ({format_inr(pnl_inr)})", f)
            
            # PUT row without combined values
            log_print(f"{'PUT':<10} | {int(trade_entry['put_strike']):>12,} | ${put_entry:>16.2f} | {'':<20} | ${put_current:>26.2f} | {'':<30} | {'':<30}", f)
            
            log_print("-" * 160, f)
            log_print("", f)
            
            # Position sizing breakdown
            log_print("POSITION SIZE BREAKDOWN:", f)
            log_print("-" * 160, f)
            log_print(f"{'Size':<22} | {'Margin':<12} | {'Entry Premium':<20} | {'Current Cost':<20} | {'PnL (USD)':<20} | {'PnL (INR)':<25}", f)
            log_print("-" * 160, f)
            
            btc_sizes = [1, 2, 5, 7, 10, 12]
            for btc in btc_sizes:
                margin = btc * 880
                entry_prem = combined_entry * btc
                current_cost = combined_current * btc
                pnl_btc_usd = pnl_usd * btc
                pnl_btc_inr = pnl_btc_usd * usd_to_inr
                
                size_str = f"{btc} BTC ({btc * 1000:,} lots)"
                
                log_print(f"{size_str:<22} | ${margin:>11,} | ${entry_prem:>18.2f} | ${current_cost:>18.2f} | ${pnl_btc_usd:>+18.2f} | {format_inr(pnl_btc_inr):<25}", f)
            
            log_print("-" * 160, f)
            log_print("", f)
            
            # Exit trigger status
            log_print("EXIT TRIGGER STATUS:", f)
            log_print("-" * 160, f)
            
            call_5x = call_entry * 5
            put_5x = put_entry * 5
            loss_1_5x = combined_entry * 1.5
            
            if call_current >= call_5x or put_current >= put_5x:
                log_print("[✗✗✗] STOP LOSS HIT (5x) - CLOSE BOTH LEGS IMMEDIATELY!", f)
            else:
                log_print(f"[✓] Stop Loss (5x): CE ${call_5x:.2f} | PE ${put_5x:.2f} - NOT HIT", f)
            
            if combined_current >= loss_1_5x:
                log_print("[✗✗] LOSS LIMIT (1.5x) - CONSIDER CLOSING", f)
            else:
                log_print(f"[✓] Loss Limit (1.5x): ${loss_1_5x:.2f} - NOT HIT", f)
            
            exit_time = today.replace(hour=17, minute=15, second=0, microsecond=0)
            if today >= exit_time:
                log_print("[!] Time Exit: Past 5:15 PM - CLOSE POSITION NOW", f)
            else:
                time_remaining = exit_time - today
                hours = time_remaining.seconds // 3600
                minutes = (time_remaining.seconds % 3600) // 60
                log_print(f"[✓] Time Remaining: {hours}h {minutes}m until 5:15 PM exit", f)
            
            log_print("-" * 160, f)
            log_print("", f)
            
        else:
            # No active trade - show current setup
            combined_premium = call_bid + put_bid
            
            # Table header
            log_print("-" * 160, f)
            log_print(f"{'Position':<10} | {'Strikes':<12} | {'Premium @ 3:30':<18} | {'Combined Premium':<20} | {'Current Premium @ ' + current_time_str:<28} | {'Current Combined Premium':<30} | {'PnL':<15}", f)
            log_print("-" * 160, f)
            
            # CALL row with combined values
            log_print(f"{'CALL':<10} | {int(call_strike_target):>12,} | {'-':<18} | {'-':<20} | ${call_bid:>26.2f} | ${combined_premium:>28.2f} | {'-':<15}", f)
            
            # PUT row without combined values
            log_print(f"{'PUT':<10} | {int(put_strike_target):>12,} | {'-':<18} | {'':<20} | ${put_bid:>26.2f} | {'':<30} | {'':<15}", f)
            
            log_print("-" * 160, f)
            log_print("", f)
            
            # Potential returns
            log_print("POTENTIAL RETURNS (if entered at current premiums):", f)
            log_print("-" * 160, f)
            log_print(f"{'Size':<22} | {'Margin':<12} | {'Premium Collected':<35} | {'Max Profit':<35} | {'Loss @ 5x SL':<35}", f)
            log_print("-" * 160, f)
            
            btc_sizes = [1, 2, 5, 7, 10, 12]
            for btc in btc_sizes:
                margin = btc * 880
                premium = combined_premium * btc
                max_profit = premium - (btc * 0.10)
                loss_5x = premium * 3
                
                size_str = f"{btc} BTC ({btc * 1000:,} lots)"
                premium_str = f"${premium:,.2f} ({format_inr(premium * usd_to_inr)})"
                profit_str = f"${max_profit:,.2f} ({format_inr(max_profit * usd_to_inr)})"
                loss_str = f"-${loss_5x:,.2f} ({format_inr(loss_5x * usd_to_inr)})"
                
                log_print(f"{size_str:<22} | ${margin:>11,} | {premium_str:<35} | {profit_str:<35} | {loss_str:<35}", f)
            
            log_print("-" * 160, f)
            log_print("", f)
            
            # Status
            log_print("STATUS:", f)
            log_print("-" * 160, f)
            
            if is_saturday:
                log_print("[INFO] TODAY IS SATURDAY - Ready to trade at 3:30 AM", f)
            else:
                log_print(f"[INFO] Today is {today.strftime('%A')} - Monitoring only", f)
            
            log_print("[INFO] Run this script between 3:25-3:35 AM on Saturday to lock in entry prices", f)
            
            log_print("-" * 160, f)
            log_print("", f)
        
        log_print("", f)
        
        # ═══════════════════════════════════════════════════════════════════
        # FULL OPTION CHAIN
        # ═══════════════════════════════════════════════════════════════════
        
        log_print("=" * 160, f)
        log_print("FULL OPTION CHAIN", f)
        log_print("=" * 160, f)
        log_print("", f)
        
        # Display ATM +/- 15 strikes
        start_index = max(0, atm_index - 15)
        end_index = min(len(all_strikes), atm_index + 16)
        selected_strikes = all_strikes[start_index:end_index]
        
        strikes_below = atm_index - start_index
        strikes_above = end_index - atm_index - 1
        
        log_print(f"Displaying strikes: {strikes_below} below ATM, ATM, {strikes_above} above ATM", f)
        log_print(f"Strike range: ${selected_strikes[0]:,.0f} to ${selected_strikes[-1]:,.0f}", f)
        log_print("", f)
        
        log_print("=" * 160, f)
        log_print(f"{'CALL OPTIONS (CE)':<77} | {'PUT OPTIONS (PE)':<77}", f)
        log_print("=" * 160, f)
        log_print(f"{'Symbol':<22} | {'Strike':<12} | {'Bid':<12} | {'Ask':<12} | {'IV':<10} | {'Symbol':<22} | {'Strike':<12} | {'Bid':<12} | {'Ask':<12} | {'IV':<10}", f)
        log_print("-" * 160, f)
        
        for strike in selected_strikes:
            call_opt_display = calls_by_strike.get(strike, {})
            put_opt_display = puts_by_strike.get(strike, {})
            
            # Format call data
            call_symbol = call_opt_display.get('symbol', '-')[:22]
            call_quotes_display = call_opt_display.get('quotes', {})
            call_bid_display = f"${float(call_quotes_display.get('best_bid', 0)):,.2f}" if call_quotes_display.get('best_bid') else '-'
            call_ask_display = f"${float(call_quotes_display.get('best_ask', 0)):,.2f}" if call_quotes_display.get('best_ask') else '-'
            call_iv = call_quotes_display.get('ask_iv', '-')
            
            # Format put data  
            put_symbol = put_opt_display.get('symbol', '-')[:22]
            put_quotes_display = put_opt_display.get('quotes', {})
            put_bid_display = f"${float(put_quotes_display.get('best_bid', 0)):,.2f}" if put_quotes_display.get('best_bid') else '-'
            put_ask_display = f"${float(put_quotes_display.get('best_ask', 0)):,.2f}" if put_quotes_display.get('best_ask') else '-'
            put_iv = put_quotes_display.get('ask_iv', '-')
            
            # Highlight ATM and selected strikes
            marker = ""
            if strike == atm_strike:
                marker = " <- ATM"
            elif strike == call_strike_target or strike == put_strike_target:
                marker = " <- SELECTED"
            
            log_print(f"{call_symbol:<22} | ${strike:>11,.0f} | {call_bid_display:<12} | {call_ask_display:<12} | {call_iv:<10} | {put_symbol:<22} | ${strike:>11,.0f} | {put_bid_display:<12} | {put_ask_display:<12} | {put_iv:<10}{marker}", f)
        
        log_print("=" * 160, f)
        log_print("", f)
        log_print(f"Log saved to: {log_file}", f)
        
    except Exception as e:
        log_print("", f)
        log_print(f"[ERROR] {str(e)}", f)
        log_print("", f)
        log_print("TRACEBACK:", f)
        log_print(traceback.format_exc(), f)
        log_print("", f)
        log_print(f"Log saved to: {log_file}", f)
        print(f"\n[ERROR] Script failed: {str(e)}")
        print(traceback.format_exc())

print(f"\n[SUCCESS] Saved to: {log_file}")
