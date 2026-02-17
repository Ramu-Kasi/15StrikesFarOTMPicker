"""
=====================================================================
  BTC SHORT STRANGLE - DAILY OBSERVER / LIVE TRADING
=====================================================================

Runs DAILY at 3:30 AM IST via GitHub Actions.
DRY_RUN = True  -> Simulates trades, monitors prices, logs to Excel
DRY_RUN = False -> Places REAL orders on Delta Exchange (Saturdays only)

Strategy: Post US-session IV Crush Short Strangle (Far OTM CE + PE)
Position Size: 10 lots each leg (configurable)
"""

import requests
import time
import hmac
import hashlib
from datetime import datetime, timedelta
import pytz
import os
import json
import traceback

# =====================================================================
# CONFIGURATION
# =====================================================================

DRY_RUN = True  # True = simulate only, False = REAL ORDERS

API_KEY = os.environ.get('DELTA_API_KEY', '')
API_SECRET = os.environ.get('DELTA_API_SECRET', '')
BASE_URL = 'https://api.india.delta.exchange'

IST = pytz.timezone('Asia/Kolkata')

# Trading Parameters
POSITION_SIZE_LOTS = 1000
POSITION_SIZE_BTC = POSITION_SIZE_LOTS / 1000

# Stop Loss
SL_COMBINED_MULTIPLIER = 2.5
HARD_MAX_LOSS_INR = 50000

# Profit / Early Exit
EARLY_EXIT_PREMIUM = 5.0

# Time Exit
EXIT_HOUR = 17
EXIT_MINUTE = 15

# Filters
MAX_SPREAD_PCT = 30.0
MIN_PREMIUM_USD = 5.0

# Monitoring
MONITOR_INTERVAL = 30

# Excel Tracker
TRACKER_FILE = "trade_tracker.xlsx"

# =====================================================================
# LOGGING
# =====================================================================

logs_dir = "live_trading_logs"
option_logs_dir = "option_chain_logs"
for d in [logs_dir, option_logs_dir]:
    if not os.path.exists(d):
        os.makedirs(d)

timestamp = datetime.now(IST).strftime('%Y-%m-%d_%H-%M-%S')
log_file = os.path.join(logs_dir, f"live_trade_{timestamp}.txt")

def log_print(message, file_handle=None):
    console_message = message.replace('\u20b9', 'Rs.')
    try:
        print(console_message)
    except UnicodeEncodeError:
        print(console_message.encode('ascii', errors='replace').decode('ascii'))
    if file_handle:
        file_handle.write(message + "\n")
        file_handle.flush()

def format_inr(amount):
    if amount >= 100000:
        lakhs = amount / 100000
        return f"\u20b9{lakhs:.2f}L" if lakhs < 10 else f"\u20b9{lakhs:.1f}L"
    return f"\u20b9{amount:,.0f}"

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

# =====================================================================
# DELTA EXCHANGE API FUNCTIONS
# =====================================================================

def generate_signature(method, endpoint, payload=""):
    ts = str(int(time.time()))
    signature_data = method + ts + endpoint + payload
    signature = hmac.new(
        API_SECRET.encode('utf-8'),
        signature_data.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return signature, ts

def get_headers(method, endpoint, payload=""):
    signature, ts = generate_signature(method, endpoint, payload)
    return {
        'api-key': API_KEY, 'timestamp': ts,
        'signature': signature, 'Content-Type': 'application/json'
    }

def get_wallet_balance():
    try:
        endpoint = '/v2/wallet/balances'
        headers = get_headers('GET', endpoint)
        response = requests.get(BASE_URL + endpoint, headers=headers, timeout=10)
        if response.status_code == 200:
            for balance in response.json().get('result', []):
                if balance.get('asset_symbol') == 'USDT':
                    return {
                        'success': True,
                        'balance': float(balance.get('balance', 0)),
                        'available_balance': float(balance.get('available_balance', 0))
                    }
        return {'success': False, 'error': f"Status {response.status_code}"}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def calculate_required_margin(product_id, size):
    try:
        endpoint = '/v2/orders/margin'
        payload_dict = {'product_id': product_id, 'size': size, 'side': 'sell'}
        payload = json.dumps(payload_dict)
        headers = get_headers('POST', endpoint, payload)
        response = requests.post(BASE_URL + endpoint, headers=headers, data=payload, timeout=10)
        if response.status_code == 200:
            result = response.json().get('result', {})
            margin = result.get('margin') or result.get('required_margin') or result.get('initial_margin')
            if margin is not None:
                return {'success': True, 'margin': float(margin), 'estimated': False}
        return {'success': True, 'margin': size * 0.5, 'estimated': True}
    except Exception as e:
        return {'success': True, 'margin': size * 0.5, 'estimated': True, 'error': str(e)}

def place_order(product_id, size, side, order_type='market_order', limit_price=None):
    try:
        endpoint = '/v2/orders'
        payload_dict = {'product_id': product_id, 'size': size, 'side': side, 'order_type': order_type}
        if order_type == 'limit_order' and limit_price:
            payload_dict['limit_price'] = str(limit_price)
        payload = json.dumps(payload_dict)
        headers = get_headers('POST', endpoint, payload)
        response = requests.post(BASE_URL + endpoint, headers=headers, data=payload, timeout=10)
        if response.status_code in [200, 201]:
            return {'success': True, 'data': response.json()}
        return {'success': False, 'error': f"Status {response.status_code}: {response.text}"}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def get_positions():
    try:
        endpoint = '/v2/positions'
        headers = get_headers('GET', endpoint)
        response = requests.get(BASE_URL + endpoint, headers=headers, timeout=10)
        if response.status_code == 200:
            return {'success': True, 'positions': response.json().get('result', [])}
        return {'success': False, 'error': f"Status {response.status_code}"}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def close_position(product_id, size):
    try:
        positions = get_positions()
        if not positions['success']:
            return {'success': False, 'error': 'Failed to get positions'}
        target = next((p for p in positions['positions'] if p.get('product_id') == product_id), None)
        if not target:
            return {'success': True, 'already_closed': True}
        current_size = int(target.get('size', 0))
        if current_size == 0:
            return {'success': True, 'already_closed': True}
        close_side = 'buy' if current_size > 0 else 'sell'
        return place_order(product_id=product_id, size=abs(size), side=close_side, order_type='market_order')
    except Exception as e:
        return {'success': False, 'error': str(e)}

def get_current_premium(symbol):
    try:
        response = requests.get(f"{BASE_URL}/v2/tickers/{symbol}", timeout=10)
        if response.status_code == 200:
            quotes = response.json().get('result', {}).get('quotes', {})
            return {'success': True, 'bid': float(quotes.get('best_bid', 0)), 'ask': float(quotes.get('best_ask', 0))}
        return {'success': False}
    except:
        return {'success': False}

# =====================================================================
# EXCEL TRACKER
# =====================================================================

def append_to_tracker(trade_result):
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    headers = [
        "Date", "Day", "Entry Time", "Exit Time", "BTC Spot", "ATM Strike",
        "Call Strike", "Put Strike", "CE Dist", "PE Dist",
        "Entry CE ($)", "Entry PE ($)", "Entry Combined ($)",
        "Exit CE ($)", "Exit PE ($)", "Exit Combined ($)",
        "P&L (USD)", "P&L (INR)", "P&L %", "Exit Reason", "Duration", "Mode", "Cum P&L (INR)"
    ]

    header_font = Font(name='Arial', bold=True, color='FFFFFF', size=10)
    header_fill = PatternFill('solid', fgColor='1a1a2e')
    header_align = Alignment(horizontal='center', vertical='center', wrap_text=True)
    data_font = Font(name='Arial', size=9)
    data_align = Alignment(horizontal='center', vertical='center')
    green_font = Font(name='Arial', size=9, bold=True, color='006100')
    red_font = Font(name='Arial', size=9, bold=True, color='9C0006')
    green_fill = PatternFill('solid', fgColor='C6EFCE')
    red_fill = PatternFill('solid', fgColor='FFC7CE')
    saturday_fill = PatternFill('solid', fgColor='DAEEF3')
    thin_border = Border(
        left=Side(style='thin', color='CCCCCC'), right=Side(style='thin', color='CCCCCC'),
        top=Side(style='thin', color='CCCCCC'), bottom=Side(style='thin', color='CCCCCC')
    )

    col_widths = {
        'A': 12, 'B': 11, 'C': 10, 'D': 10, 'E': 13, 'F': 13,
        'G': 13, 'H': 13, 'I': 8, 'J': 8,
        'K': 12, 'L': 12, 'M': 15,
        'N': 12, 'O': 12, 'P': 15,
        'Q': 12, 'R': 12, 'S': 10, 'T': 28, 'U': 11, 'V': 10, 'W': 15
    }

    is_new = not os.path.exists(TRACKER_FILE)

    if is_new:
        wb = Workbook()
        ws = wb.active
        ws.title = "Trade Tracker"
        ws.append(headers)
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_align
            cell.border = thin_border
        ws.freeze_panes = 'A2'
        ws.auto_filter.ref = f"A1:W1"
        for col_letter, width in col_widths.items():
            ws.column_dimensions[col_letter].width = width
    else:
        wb = load_workbook(TRACKER_FILE)
        ws = wb["Trade Tracker"]

    tr = trade_result
    pnl_usd = tr.get('pnl_usd', 0)
    pnl_inr = tr.get('pnl_inr', 0)
    entry_combined = tr.get('entry_combined', 0)
    total_premium_usd = entry_combined * POSITION_SIZE_BTC
    pnl_pct = (pnl_usd / total_premium_usd * 100) if total_premium_usd > 0 else 0

    row_data = [
        tr.get('date', ''), tr.get('day', ''),
        tr.get('entry_time', ''), tr.get('exit_time', ''),
        tr.get('btc_spot', 0), tr.get('atm_strike', 0),
        tr.get('call_strike', 0), tr.get('put_strike', 0),
        tr.get('ce_dist', 0), tr.get('pe_dist', 0),
        tr.get('entry_ce', 0), tr.get('entry_pe', 0), entry_combined,
        tr.get('exit_ce', 0), tr.get('exit_pe', 0), tr.get('exit_combined', 0),
        round(pnl_usd, 4), round(pnl_inr, 2), round(pnl_pct, 1),
        tr.get('exit_reason', ''), tr.get('duration', ''),
        tr.get('mode', 'DRY RUN'), 0
    ]

    ws.append(row_data)
    new_row = ws.max_row

    is_saturday = tr.get('day', '') == 'Saturday'
    is_profit = pnl_inr >= 0

    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=new_row, column=col_idx)
        cell.font = data_font
        cell.alignment = data_align
        cell.border = thin_border
        if is_saturday:
            cell.fill = saturday_fill

    # P&L USD (col 17) - green/red font + background
    pnl_usd_cell = ws.cell(row=new_row, column=17)
    pnl_usd_cell.font = green_font if is_profit else red_font
    pnl_usd_cell.fill = green_fill if is_profit else red_fill
    pnl_usd_cell.number_format = '$#,##0.0000;-$#,##0.0000'

    # P&L INR (col 18) - green/red font + background
    pnl_inr_cell = ws.cell(row=new_row, column=18)
    pnl_inr_cell.font = green_font if is_profit else red_font
    pnl_inr_cell.fill = green_fill if is_profit else red_fill
    pnl_inr_cell.number_format = '\u20b9#,##0.00;-\u20b9#,##0.00'

    # P&L % (col 19) - green/red
    pnl_pct_cell = ws.cell(row=new_row, column=19)
    pnl_pct_cell.font = green_font if is_profit else red_font
    pnl_pct_cell.fill = green_fill if is_profit else red_fill

    # Cum P&L INR (col 23) - formula
    if new_row == 2:
        ws.cell(row=new_row, column=23).value = f'=R{new_row}'
    else:
        ws.cell(row=new_row, column=23).value = f'=W{new_row - 1}+R{new_row}'

    cum_cell = ws.cell(row=new_row, column=23)
    cum_cell.number_format = '\u20b9#,##0.00;-\u20b9#,##0.00'
    cum_cell.font = Font(name='Arial', size=9, bold=True)

    # Dollar formatting for price columns
    for col_idx in [5, 6, 7, 8, 11, 12, 13, 14, 15, 16]:
        ws.cell(row=new_row, column=col_idx).number_format = '$#,##0.00'

    wb.save(TRACKER_FILE)
    print(f"[TRACKER] Row appended to {TRACKER_FILE}")

# =====================================================================
# POSITION MONITORING
# =====================================================================

def monitor_positions(f, entry_call_symbol, entry_put_symbol, entry_call_bid, entry_put_bid,
                     entry_combined_premium, call_product_id, put_product_id, usd_to_inr):

    exit_data = {'exit_ce': 0, 'exit_pe': 0, 'exit_combined': 0, 'exit_reason': 'Unknown', 'exit_time': ''}

    log_print("\n" + "=" * 100, f)
    log_print("POSITION MONITORING STARTED", f)
    log_print("=" * 100, f)
    log_print(f"Entry CE: ${entry_call_bid:.2f} | Entry PE: ${entry_put_bid:.2f} | Combined: ${entry_combined_premium:.2f}", f)
    log_print(f"", f)
    log_print(f"Exit Rules:", f)
    log_print(f"  SL1 (Combined {SL_COMBINED_MULTIPLIER}x): Buyback >= ${entry_combined_premium * SL_COMBINED_MULTIPLIER:.2f}", f)
    log_print(f"  SL2 (Hard Cap): Loss >= Rs.{HARD_MAX_LOSS_INR:,} (~${HARD_MAX_LOSS_INR / usd_to_inr:.2f})", f)
    log_print(f"  Early Exit: Combined premium < ${EARLY_EXIT_PREMIUM:.0f}", f)
    log_print(f"  Time Exit: {EXIT_HOUR}:{EXIT_MINUTE:02d} IST", f)
    log_print("=" * 100 + "\n", f)

    while True:
        try:
            now_ist = datetime.now(IST)
            time_str = now_ist.strftime('%H:%M:%S')

            # ─── TIME EXIT ───
            if now_ist.hour >= EXIT_HOUR and now_ist.minute >= EXIT_MINUTE:
                log_print(f"\n[{time_str}] TIME EXIT: {EXIT_HOUR}:{EXIT_MINUTE:02d} IST", f)
                cd = get_current_premium(entry_call_symbol)
                pd_ = get_current_premium(entry_put_symbol)
                exit_data['exit_ce'] = cd['ask'] if cd['success'] else 0
                exit_data['exit_pe'] = pd_['ask'] if pd_['success'] else 0
                exit_data['exit_combined'] = exit_data['exit_ce'] + exit_data['exit_pe']
                exit_data['exit_reason'] = 'Time Exit (5:15 PM)'
                exit_data['exit_time'] = time_str
                _close_both_legs(f, call_product_id, put_product_id, "Time Exit")
                break

            # ─── GET PRICES ───
            call_data = get_current_premium(entry_call_symbol)
            put_data = get_current_premium(entry_put_symbol)

            if not call_data['success'] or not put_data['success']:
                log_print(f"[{time_str}] [WARN] Failed to get premiums, retrying...", f)
                time.sleep(MONITOR_INTERVAL)
                continue

            # ─── MANUAL CLOSE DETECTION (live only) ───
            if not DRY_RUN:
                pos_result = get_positions()
                if pos_result['success']:
                    has_call = any(p.get('product_id') == call_product_id and int(p.get('size', 0)) != 0 for p in pos_result['positions'])
                    has_put = any(p.get('product_id') == put_product_id and int(p.get('size', 0)) != 0 for p in pos_result['positions'])
                    if not has_call and not has_put:
                        log_print(f"\n[{time_str}] MANUAL EXIT DETECTED", f)
                        exit_data.update({
                            'exit_ce': call_data['ask'], 'exit_pe': put_data['ask'],
                            'exit_combined': call_data['ask'] + put_data['ask'],
                            'exit_reason': 'Manual Exit', 'exit_time': time_str
                        })
                        break

            cur_ce = call_data['ask']
            cur_pe = put_data['ask']
            cur_combined = cur_ce + cur_pe

            pnl = (entry_combined_premium - cur_combined) * POSITION_SIZE_BTC
            pnl_inr = pnl * usd_to_inr

            log_print(
                f"[{time_str}] CE: ${cur_ce:.2f} | PE: ${cur_pe:.2f} | "
                f"Combined: ${cur_combined:.2f} | P&L: ${pnl:+.4f} (Rs.{pnl_inr:+,.2f})", f
            )

            # ─── SL1: COMBINED 2.5x ───
            if cur_combined >= (entry_combined_premium * SL_COMBINED_MULTIPLIER):
                log_print(f"\n[{time_str}] SL1 HIT: Combined {SL_COMBINED_MULTIPLIER}x (${cur_combined:.2f} >= ${entry_combined_premium * SL_COMBINED_MULTIPLIER:.2f})", f)
                exit_data.update({
                    'exit_ce': cur_ce, 'exit_pe': cur_pe, 'exit_combined': cur_combined,
                    'exit_reason': f'SL - Combined {SL_COMBINED_MULTIPLIER}x', 'exit_time': time_str
                })
                _close_both_legs(f, call_product_id, put_product_id, "Combined 2.5x SL")
                break

            # ─── SL2: HARD CAP ───
            loss_usd = (cur_combined - entry_combined_premium) * POSITION_SIZE_BTC
            loss_inr = loss_usd * usd_to_inr
            if loss_inr >= HARD_MAX_LOSS_INR:
                log_print(f"\n[{time_str}] HARD CAP HIT: Loss Rs.{loss_inr:,.0f} >= Rs.{HARD_MAX_LOSS_INR:,}", f)
                exit_data.update({
                    'exit_ce': cur_ce, 'exit_pe': cur_pe, 'exit_combined': cur_combined,
                    'exit_reason': f'Hard Cap Rs.{HARD_MAX_LOSS_INR:,}', 'exit_time': time_str
                })
                _close_both_legs(f, call_product_id, put_product_id, f"Hard Cap Rs.{HARD_MAX_LOSS_INR:,}")
                break

            # ─── EARLY EXIT: PREMIUM < $5 ───
            if cur_combined < EARLY_EXIT_PREMIUM:
                log_print(f"\n[{time_str}] EARLY EXIT: Premium decayed to ${cur_combined:.2f}", f)
                exit_data.update({
                    'exit_ce': cur_ce, 'exit_pe': cur_pe, 'exit_combined': cur_combined,
                    'exit_reason': 'Early Exit - Premium decayed', 'exit_time': time_str
                })
                _close_both_legs(f, call_product_id, put_product_id, "Early Exit")
                break

            time.sleep(MONITOR_INTERVAL)

        except KeyboardInterrupt:
            log_print(f"\n[WARN] Monitoring interrupted", f)
            exit_data['exit_reason'] = 'Interrupted'
            exit_data['exit_time'] = datetime.now(IST).strftime('%H:%M:%S')
            break
        except Exception as e:
            log_print(f"\n[ERROR] Monitoring: {e}", f)
            time.sleep(MONITOR_INTERVAL)

    log_print("\n" + "=" * 100, f)
    log_print("POSITION MONITORING ENDED", f)
    log_print("=" * 100 + "\n", f)
    return exit_data

def _close_both_legs(f, call_product_id, put_product_id, reason):
    log_print(f"Closing BOTH legs ({reason})...", f)
    if DRY_RUN:
        log_print(f"  [DRY RUN] Positions closed ({reason})", f)
        return
    call_close = close_position(call_product_id, POSITION_SIZE_LOTS)
    put_close = close_position(put_product_id, POSITION_SIZE_LOTS)
    for name, result in [("Call", call_close), ("Put", put_close)]:
        if result.get('already_closed'):
            log_print(f"  {name}: Already closed", f)
        elif result['success']:
            log_print(f"  {name}: Closed successfully", f)
        else:
            log_print(f"  {name}: ERROR - {result.get('error')}", f)

# =====================================================================
# MAIN EXECUTION
# =====================================================================

with open(log_file, 'w', encoding='utf-8') as f:
    try:
        mode_label = "DRY RUN (SIMULATION)" if DRY_RUN else "LIVE TRADING"
        today = datetime.now(IST)
        is_saturday = today.weekday() == 5
        entry_time_str = today.strftime('%H:%M')

        log_print("=" * 120, f)
        log_print(f"  BTC SHORT STRANGLE - {mode_label} - {today.strftime('%A')}", f)
        log_print("=" * 120, f)
        log_print(f"Timestamp: {today.strftime('%d-%m-%Y %H:%M:%S IST')}", f)
        log_print(f"Position: {POSITION_SIZE_LOTS} lots/leg ({POSITION_SIZE_BTC} BTC)", f)
        log_print("=" * 120 + "\n", f)

        # ─── USD/INR ───
        usd_to_inr = get_current_usd_inr_rate()
        log_print(f"USD/INR: {usd_to_inr:.2f}\n", f)

        # ─── WALLET (live only) ───
        available_balance = 0.0
        if API_KEY and API_SECRET and not DRY_RUN:
            bal = get_wallet_balance()
            if bal['success']:
                available_balance = bal['available_balance']
                log_print(f"Balance: ${available_balance:.2f}\n", f)

        # ─── EXPIRY ───
        expiry_cutoff = today.replace(hour=17, minute=30, second=0, microsecond=0)
        target_expiry = today if today < expiry_cutoff else today + timedelta(days=1)
        expiry_date_str = target_expiry.strftime('%d-%m-%Y')
        log_print(f"Expiry: {expiry_date_str}\n", f)

        # ─── SPOT ───
        resp = requests.get(f"{BASE_URL}/v2/tickers/BTCUSD", timeout=10)
        if resp.status_code != 200:
            raise Exception(f"Spot price failed: {resp.status_code}")
        spot_price = float(resp.json()['result']['spot_price'])
        log_print(f"BTC Spot: ${spot_price:,.2f}\n", f)

        # ─── OPTION CHAIN ───
        params = {'contract_types': 'call_options,put_options', 'underlying_asset_symbols': 'BTC', 'expiry_date': expiry_date_str}
        resp = requests.get(f"{BASE_URL}/v2/tickers", params=params, timeout=15)
        if resp.status_code != 200:
            raise Exception(f"Options failed: {resp.status_code}")
        options = resp.json()['result']
        if not options:
            log_print(f"[ERROR] No options for {expiry_date_str}\n", f)
            exit(0)

        calls = sorted([o for o in options if o['contract_type'] == 'call_options'], key=lambda x: float(x['strike_price']))
        puts = sorted([o for o in options if o['contract_type'] == 'put_options'], key=lambda x: float(x['strike_price']))

        all_strikes = sorted(set(float(o['strike_price']) for o in options))
        atm_strike = min(all_strikes, key=lambda x: abs(x - spot_price))
        atm_index = all_strikes.index(atm_strike)

        calls_by_strike = {float(c['strike_price']): c for c in calls}
        puts_by_strike = {float(p['strike_price']): p for p in puts}

        max_ce = len(all_strikes) - atm_index - 1
        max_pe = atm_index
        log_print(f"ATM: ${atm_strike:,.0f} | Strikes: {max_ce} above, {max_pe} below\n", f)

        has_sufficient = max_ce >= 13 and max_pe >= 13
        if not has_sufficient:
            log_print(f"[WARNING] Need 13 each side. Have: {max_ce} above, {max_pe} below\n", f)

        # ═════════════════════════════════════════════════════════
        # STRIKE SELECTION
        # ═════════════════════════════════════════════════════════

        best_combo = None
        call_strike_target = None
        put_strike_target = None

        if has_sufficient:
            log_print("DELTA NEUTRALITY OPTIMIZATION (13-15 strikes):", f)
            log_print("-" * 120, f)

            best_imbalance = float('inf')
            selection_reason = ""

            for ce_d in range(13, min(16, max_ce + 1)):
                for pe_d in range(13, min(16, max_pe + 1)):
                    cs = all_strikes[atm_index + ce_d]
                    ps = all_strikes[atm_index - pe_d]
                    co = calls_by_strike.get(cs, {})
                    po = puts_by_strike.get(ps, {})
                    cq = co.get('quotes', {})
                    pq = po.get('quotes', {})
                    cb = float(cq.get('best_bid', 0))
                    ca = float(cq.get('best_ask', 0))
                    pb = float(pq.get('best_bid', 0))
                    pa = float(pq.get('best_ask', 0))

                    if cb < MIN_PREMIUM_USD or pb < MIN_PREMIUM_USD:
                        log_print(f"  CE +{ce_d} (${cs:,.0f}) | PE -{pe_d} (${ps:,.0f}) -> SKIP (< ${MIN_PREMIUM_USD})", f)
                        continue

                    cs_pct = ((ca - cb) / ca * 100) if ca > 0 else 100
                    ps_pct = ((pa - pb) / pa * 100) if pa > 0 else 100
                    spread_ok = cs_pct <= MAX_SPREAD_PCT and ps_pct <= MAX_SPREAD_PCT
                    flag = "" if spread_ok else " [WIDE]"

                    imb = abs(cb - pb)
                    imb_pct = (imb / max(cb, pb) * 100)
                    log_print(f"  CE +{ce_d} (${cs:,.0f}, ${cb:.2f}) | PE -{pe_d} (${ps:,.0f}, ${pb:.2f}) -> Imb: ${imb:.2f} ({imb_pct:.1f}%){flag}", f)

                    if imb < best_imbalance and spread_ok:
                        best_imbalance = imb
                        tag = f"Sym ({ce_d})" if ce_d == pe_d else f"Asym (CE+{ce_d}, PE-{pe_d})"
                        selection_reason = f"{tag}, Imb: ${imb:.2f} ({imb_pct:.1f}%)"
                        best_combo = {
                            'call_strike': cs, 'put_strike': ps,
                            'ce_dist': ce_d, 'pe_dist': pe_d,
                            'call_symbol': co.get('symbol'), 'put_symbol': po.get('symbol'),
                            'call_product_id': co.get('id'), 'put_product_id': po.get('id'),
                            'call_bid': cb, 'call_ask': ca, 'put_bid': pb, 'put_ask': pa,
                            'combined_premium': cb + pb
                        }
                        log_print(f"    -> *** BEST: {selection_reason}", f)

            log_print("-" * 120 + "\n", f)

        # ═════════════════════════════════════════════════════════
        # TRADE + MONITOR + TRACK
        # ═════════════════════════════════════════════════════════

        if best_combo:
            call_strike_target = best_combo['call_strike']
            put_strike_target = best_combo['put_strike']
            combined_premium = best_combo['combined_premium']

            log_print("=" * 120, f)
            log_print("TRADE SETUP", f)
            log_print("=" * 120, f)
            log_print(f"SELL CE: {best_combo['call_symbol']} | ${best_combo['call_strike']:,.0f} (+{best_combo['ce_dist']}) | Bid: ${best_combo['call_bid']:.2f}", f)
            log_print(f"SELL PE: {best_combo['put_symbol']} | ${best_combo['put_strike']:,.0f} (-{best_combo['pe_dist']}) | Bid: ${best_combo['put_bid']:.2f}", f)
            log_print(f"Combined: ${combined_premium:.2f} | Premium: ${combined_premium * POSITION_SIZE_BTC:.4f} ({format_inr(combined_premium * POSITION_SIZE_BTC * usd_to_inr)})", f)
            log_print(f"SL1: {SL_COMBINED_MULTIPLIER}x >= ${combined_premium * SL_COMBINED_MULTIPLIER:.2f} | SL2: Rs.{HARD_MAX_LOSS_INR:,} | Early: < ${EARLY_EXIT_PREMIUM:.0f} | Time: {EXIT_HOUR}:{EXIT_MINUTE:02d}", f)
            log_print("=" * 120 + "\n", f)

            orders_placed = False

            if DRY_RUN:
                log_print("[DRY RUN] Simulating orders...", f)
                log_print(f"  SELL {POSITION_SIZE_LOTS} lots {best_combo['call_symbol']}", f)
                log_print(f"  SELL {POSITION_SIZE_LOTS} lots {best_combo['put_symbol']}", f)
                log_print(f"  Starting monitoring...\n", f)
                orders_placed = True

            elif is_saturday:
                log_print("PLACING LIVE ORDERS...\n", f)
                cm = calculate_required_margin(best_combo['call_product_id'], POSITION_SIZE_LOTS)
                pm = calculate_required_margin(best_combo['put_product_id'], POSITION_SIZE_LOTS)
                total_margin = cm['margin'] + pm['margin']

                if available_balance < total_margin:
                    log_print(f"[ERROR] MARGIN: Need ${total_margin:.2f}, Have ${available_balance:.2f}\n", f)
                else:
                    co = place_order(best_combo['call_product_id'], POSITION_SIZE_LOTS, 'sell')
                    if not co['success']:
                        raise Exception(f"Call order failed: {co.get('error')}")
                    log_print(f"  Call placed. ID: {co['data'].get('result', {}).get('id', 'N/A')}", f)

                    po = place_order(best_combo['put_product_id'], POSITION_SIZE_LOTS, 'sell')
                    if not po['success']:
                        log_print(f"  Put FAILED. Rolling back Call...", f)
                        rb = close_position(best_combo['call_product_id'], POSITION_SIZE_LOTS)
                        if not (rb['success'] or rb.get('already_closed')):
                            log_print(f"  [CRITICAL] ROLLBACK FAILED! Close manually!", f)
                        raise Exception("Put order failed")

                    log_print(f"  Put placed. ID: {po['data'].get('result', {}).get('id', 'N/A')}", f)
                    log_print(f"  BOTH ORDERS PLACED\n", f)
                    time.sleep(5)
                    orders_placed = True

            if orders_placed:
                exit_data = monitor_positions(
                    f=f,
                    entry_call_symbol=best_combo['call_symbol'],
                    entry_put_symbol=best_combo['put_symbol'],
                    entry_call_bid=best_combo['call_bid'],
                    entry_put_bid=best_combo['put_bid'],
                    entry_combined_premium=combined_premium,
                    call_product_id=best_combo['call_product_id'],
                    put_product_id=best_combo['put_product_id'],
                    usd_to_inr=usd_to_inr
                )

                # ─── FINAL P&L ───
                exit_combined = exit_data.get('exit_combined', 0)
                pnl_usd = (combined_premium - exit_combined) * POSITION_SIZE_BTC
                pnl_inr = pnl_usd * usd_to_inr

                try:
                    ep = exit_data.get('exit_time', '').split(':')
                    exit_dt = today.replace(hour=int(ep[0]), minute=int(ep[1]), second=int(ep[2]))
                    dur_s = int((exit_dt - today).total_seconds())
                    duration_str = f"{dur_s // 3600}h {(dur_s % 3600) // 60}m"
                except:
                    duration_str = "-"

                log_print(f"FINAL P&L: ${pnl_usd:+.4f} (Rs.{pnl_inr:+,.2f}) | {exit_data['exit_reason']}\n", f)

                # ─── EXCEL TRACKER ───
                try:
                    append_to_tracker({
                        'date': today.strftime('%d-%m-%Y'),
                        'day': today.strftime('%A'),
                        'entry_time': entry_time_str,
                        'exit_time': exit_data.get('exit_time', ''),
                        'btc_spot': spot_price,
                        'atm_strike': atm_strike,
                        'call_strike': best_combo['call_strike'],
                        'put_strike': best_combo['put_strike'],
                        'ce_dist': best_combo['ce_dist'],
                        'pe_dist': best_combo['pe_dist'],
                        'entry_ce': best_combo['call_bid'],
                        'entry_pe': best_combo['put_bid'],
                        'entry_combined': combined_premium,
                        'exit_ce': exit_data.get('exit_ce', 0),
                        'exit_pe': exit_data.get('exit_pe', 0),
                        'exit_combined': exit_combined,
                        'pnl_usd': pnl_usd,
                        'pnl_inr': pnl_inr,
                        'exit_reason': exit_data.get('exit_reason', ''),
                        'duration': duration_str,
                        'mode': 'DRY RUN' if DRY_RUN else 'LIVE'
                    })
                    log_print(f"[TRACKER] Trade logged to {TRACKER_FILE}\n", f)
                except Exception as e:
                    log_print(f"[ERROR] Tracker: {e}\n", f)
                    log_print(traceback.format_exc(), f)
        else:
            log_print("[INFO] No valid strikes found. Skipping today.\n", f)

        # ═════════════════════════════════════════════════════════
        # FULL OPTION CHAIN (ALWAYS)
        # ═════════════════════════════════════════════════════════

        log_print("=" * 160, f)
        log_print("FULL OPTION CHAIN", f)
        log_print("=" * 160 + "\n", f)

        si = max(0, atm_index - 15)
        ei = min(len(all_strikes), atm_index + 16)
        sel = all_strikes[si:ei]

        log_print(f"Range: ${sel[0]:,.0f} - ${sel[-1]:,.0f}\n", f)
        log_print("=" * 160, f)
        log_print(f"{'CALL OPTIONS (CE)':<77} | {'PUT OPTIONS (PE)':<77}", f)
        log_print("=" * 160, f)
        log_print(f"{'Symbol':<22} | {'Strike':<12} | {'Bid':<12} | {'Ask':<12} | {'IV':<10} | {'Symbol':<22} | {'Strike':<12} | {'Bid':<12} | {'Ask':<12} | {'IV':<10}", f)
        log_print("-" * 160, f)

        for strike in sel:
            cd = calls_by_strike.get(strike, {})
            pd_ = puts_by_strike.get(strike, {})
            c_sym = cd.get('symbol', '-')[:22]
            c_q = cd.get('quotes', {})
            c_b = f"${float(c_q.get('best_bid', 0)):,.2f}" if c_q.get('best_bid') else '-'
            c_a = f"${float(c_q.get('best_ask', 0)):,.2f}" if c_q.get('best_ask') else '-'
            c_iv = c_q.get('ask_iv', '-')
            p_sym = pd_.get('symbol', '-')[:22]
            p_q = pd_.get('quotes', {})
            p_b = f"${float(p_q.get('best_bid', 0)):,.2f}" if p_q.get('best_bid') else '-'
            p_a = f"${float(p_q.get('best_ask', 0)):,.2f}" if p_q.get('best_ask') else '-'
            p_iv = p_q.get('ask_iv', '-')
            m = ""
            if strike == atm_strike: m = " <- ATM"
            elif call_strike_target and (strike == call_strike_target or strike == put_strike_target): m = " <- SELECTED"
            log_print(f"{c_sym:<22} | ${strike:>11,.0f} | {c_b:<12} | {c_a:<12} | {c_iv:<10} | {p_sym:<22} | ${strike:>11,.0f} | {p_b:<12} | {p_a:<12} | {p_iv:<10}{m}", f)

        log_print("=" * 160 + "\n", f)
        log_print(f"Session complete. Log: {log_file}", f)

    except Exception as e:
        log_print(f"\n[ERROR] {str(e)}", f)
        log_print(traceback.format_exc(), f)

print(f"\n[SUCCESS] Log: {log_file}")
