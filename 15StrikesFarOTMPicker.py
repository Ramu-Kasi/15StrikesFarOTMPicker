"""
=====================================================================
  BTC SHORT STRANGLE - DAILY OBSERVER  v4 (FINAL)
=====================================================================

Two-phase approach:
  Phase 1 ENTRY  (3:30 AM IST / 22:00 UTC prev day):
      - Fetch spot, scan option chain, pick best strikes
      - Save entry snapshot to active_trade.json
      - Exit immediately (~15 seconds)

  Phase 2 EXIT   (5:15 PM IST / 11:45 UTC same day):
      - Load active_trade.json
      - Validate entry date == today
      - Check intraday candles for SL breach
      - Fetch exit prices, calculate P&L, write to tracker
      - Delete active_trade.json

FIXES in v4 (definitive):
  FIX 1 — resolution '1m' (was '1'):
      Delta candle API requires the STRING '1m'. Sending integer 1
      returns HTTP 400 bad_schema. This was causing ALL candle fetch
      failures since day one. Now correctly uses '1m'.

  FIX 2 — Candle endpoint uses 'symbol' directly (no product_id lookup):
      The old code tried to look up product_id via ticker endpoint and
      pass it to the candle endpoint. The candle endpoint accepts 'symbol'
      directly. Removed the unnecessary lookup entirely.

  FIX 3 — Zero-exit guard:
      If both legs return $0 at 5:15 PM (expired contracts are removed
      from Delta's ticker endpoint), script now fetches BTC spot and
      calculates intrinsic value. Previously recorded phantom full-profit.

  FIX 4 — Safety fallback SL check:
      If candle fetch fails, script now tries live price fetch. If that
      also fails (expired), falls back to settlement spot intrinsic check.
      A failed candle fetch can NEVER silently become a phantom profit.

  FIX 5 — Whole number formatting:
      All P&L columns (USD, INR, Cumulative) now display as whole numbers.
      No more $37.6000 — shows clean $38 / ₹3,420 instead.

ENVIRONMENT VARIABLES (set in GitHub Actions):
  PHASE            : 'ENTRY' or 'EXIT'
  DELTA_API_KEY    : Your Delta Exchange API key
  DELTA_API_SECRET : Your Delta Exchange API secret
  DRY_RUN          : Set to 'false' to place real orders (default: true)
=====================================================================
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
# CONFIGURATION — Edit these as needed
# =====================================================================

DRY_RUN = os.environ.get('DRY_RUN', 'true').lower() != 'false'

API_KEY    = os.environ.get('DELTA_API_KEY', '')
API_SECRET = os.environ.get('DELTA_API_SECRET', '')
BASE_URL   = 'https://api.india.delta.exchange'

PHASE = os.environ.get('PHASE', 'ENTRY').upper().strip()

IST = pytz.timezone('Asia/Kolkata')

POSITION_SIZE_LOTS = 1000
POSITION_SIZE_BTC  = POSITION_SIZE_LOTS / 1000   # 1.0 BTC

SL_COMBINED_MULTIPLIER = 2.5
HARD_MAX_LOSS_INR      = 10_000
EARLY_EXIT_PREMIUM     = 5.0

EXIT_HOUR   = 17
EXIT_MINUTE = 15

MAX_SPREAD_PCT  = 30.0
MIN_PREMIUM_USD = 5.0
MONITOR_INTERVAL = 30

TRACKER_FILE      = "trade_tracker.xlsx"
ACTIVE_TRADE_FILE = "active_trade.json"

# =====================================================================
# LOGGING SETUP
# =====================================================================

logs_dir = "live_trading_logs"
os.makedirs(logs_dir, exist_ok=True)

timestamp = datetime.now(IST).strftime('%Y-%m-%d_%H-%M-%S')
log_file  = os.path.join(logs_dir, f"trade_{PHASE}_{timestamp}.txt")

def log_print(message, fh=None):
    safe = message.replace('\u20b9', 'Rs.')
    try:
        print(safe)
    except UnicodeEncodeError:
        print(safe.encode('ascii', errors='replace').decode('ascii'))
    if fh:
        fh.write(message + "\n")
        fh.flush()

def fmt_inr(amount):
    if abs(amount) >= 100_000:
        return f"\u20b9{amount / 100_000:.2f}L"
    return f"\u20b9{amount:,.0f}"

def get_usd_inr():
    try:
        r = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
        rate = r.json().get('rates', {}).get('INR') if r.status_code == 200 else None
        return float(rate) if rate else 84.0
    except Exception:
        return 84.0

# =====================================================================
# DELTA EXCHANGE API HELPERS
# =====================================================================

def _signature(method, endpoint, payload=""):
    ts  = str(int(time.time()))
    msg = method + ts + endpoint + payload
    sig = hmac.new(API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return sig, ts

def _headers(method, endpoint, payload=""):
    sig, ts = _signature(method, endpoint, payload)
    return {
        'api-key':      API_KEY,
        'timestamp':    ts,
        'signature':    sig,
        'Content-Type': 'application/json'
    }

def get_wallet_balance():
    try:
        ep = '/v2/wallet/balances'
        r  = requests.get(BASE_URL + ep, headers=_headers('GET', ep), timeout=10)
        if r.status_code == 200:
            for b in r.json().get('result', []):
                if b.get('asset_symbol') == 'USDT':
                    return {
                        'success':           True,
                        'balance':           float(b.get('balance', 0)),
                        'available_balance': float(b.get('available_balance', 0))
                    }
        return {'success': False, 'error': f"HTTP {r.status_code}"}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def place_order(product_id, size, side, order_type='market_order', limit_price=None):
    try:
        ep   = '/v2/orders'
        body = {
            'product_id': product_id,
            'size':       size,
            'side':       side,
            'order_type': order_type
        }
        if order_type == 'limit_order' and limit_price:
            body['limit_price'] = str(limit_price)
        payload = json.dumps(body)
        r = requests.post(
            BASE_URL + ep,
            headers=_headers('POST', ep, payload),
            data=payload,
            timeout=10
        )
        if r.status_code in (200, 201):
            return {'success': True, 'data': r.json()}
        return {'success': False, 'error': f"HTTP {r.status_code}: {r.text}"}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def get_positions():
    try:
        ep = '/v2/positions'
        r  = requests.get(BASE_URL + ep, headers=_headers('GET', ep), timeout=10)
        if r.status_code == 200:
            return {'success': True, 'positions': r.json().get('result', [])}
        return {'success': False, 'error': f"HTTP {r.status_code}"}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def close_position(product_id, size):
    try:
        pos = get_positions()
        if not pos['success']:
            return {'success': False, 'error': 'Could not fetch positions'}
        target = next(
            (p for p in pos['positions'] if p.get('product_id') == product_id), None
        )
        if not target or int(target.get('size', 0)) == 0:
            return {'success': True, 'already_closed': True}
        side = 'buy' if int(target['size']) > 0 else 'sell'
        return place_order(product_id=product_id, size=abs(size), side=side)
    except Exception as e:
        return {'success': False, 'error': str(e)}

def get_current_premium(symbol):
    try:
        r = requests.get(f"{BASE_URL}/v2/tickers/{symbol}", timeout=10)
        if r.status_code == 200:
            q = r.json().get('result', {}).get('quotes', {})
            return {
                'success': True,
                'bid':     float(q.get('best_bid', 0) or 0),
                'ask':     float(q.get('best_ask', 0) or 0)
            }
        return {'success': False, 'error': f"HTTP {r.status_code}"}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def get_btc_spot():
    try:
        r = requests.get(f"{BASE_URL}/v2/tickers/BTCUSD", timeout=10)
        if r.status_code == 200:
            return float(r.json()['result']['spot_price'])
        return None
    except Exception:
        return None

# =====================================================================
# FIX 1 + FIX 2: get_intraday_worst_combined
# =====================================================================

def get_intraday_worst_combined(call_symbol, put_symbol, entry_time_str,
                                sl_level, hard_cap_level, fh=None):
    try:
        now_ist     = datetime.now(IST)
        parts       = entry_time_str.split(':')
        entry_dt    = now_ist.replace(
            hour=int(parts[0]), minute=int(parts[1]),
            second=0, microsecond=0
        )
        exit_dt = now_ist.replace(
            hour=EXIT_HOUR, minute=EXIT_MINUTE,
            second=0, microsecond=0
        )

        def fetch_candles(symbol):
            params = {
                'resolution': '1m',
                'symbol':     symbol,
                'start':      int(entry_dt.timestamp()),
                'end':        int(exit_dt.timestamp())
            }
            r = requests.get(
                f"{BASE_URL}/v2/history/candles",
                params=params,
                timeout=15
            )
            if r.status_code != 200:
                log_print(
                    f"  [DEBUG] Candle fetch HTTP {r.status_code} "
                    f"for {symbol}: {r.text[:400]}", fh
                )
                return None

            candles = r.json().get('result', [])
            if not candles:
                log_print(
                    f"  [DEBUG] Zero candles for {symbol} between "
                    f"{entry_dt.strftime('%H:%M')} - {exit_dt.strftime('%H:%M')} IST", fh
                )
                return None

            result = {}
            for c in candles:
                ts = c.get('time')
                if ts:
                    result[int(ts)] = float(c.get('close', 0) or 0)
            return result

        log_print("  Fetching intraday 1m candles for SL check...", fh)
        call_candles = fetch_candles(call_symbol)
        put_candles  = fetch_candles(put_symbol)

        if not call_candles or not put_candles:
            log_print("  [WARN] Candle fetch failed for one or both legs.", fh)
            return None

        common_ts = sorted(set(call_candles.keys()) & set(put_candles.keys()))
        if not common_ts:
            log_print("  [WARN] No overlapping candle timestamps found.", fh)
            return None

        worst_combined = 0.0
        worst_ts       = None
        for ts in common_ts:
            combined = call_candles[ts] + put_candles[ts]
            if combined > worst_combined:
                worst_combined = combined
                worst_ts       = ts

        worst_time_str = (
            datetime.fromtimestamp(worst_ts, tz=IST).strftime('%H:%M')
            if worst_ts else '?'
        )

        log_print(
            f"  Intraday scan: {len(common_ts)} candles | "
            f"Peak combined: ${worst_combined:.2f} at {worst_time_str} IST | "
            f"SL level: ${sl_level:.2f}", fh
        )

        return {
            'worst_combined':     worst_combined,
            'worst_time':         worst_time_str,
            'candle_count':       len(common_ts),
            'sl_breached':        worst_combined >= sl_level,
            'hard_cap_breached':  worst_combined >= hard_cap_level,
        }

    except Exception as e:
        log_print(f"  [WARN] Intraday SL check exception: {e}", fh)
        log_print(f"  [DEBUG] {traceback.format_exc()}", fh)
        return None


def _close_both_legs(fh, call_pid, put_pid, reason):
    log_print(f"  Closing both legs — {reason}...", fh)
    if DRY_RUN:
        log_print("  [DRY RUN] Simulated close.", fh)
        return
    for name, pid in [("Call", call_pid), ("Put", put_pid)]:
        res = close_position(pid, POSITION_SIZE_LOTS)
        if res.get('already_closed'):
            log_print(f"  {name}: already closed", fh)
        elif res['success']:
            log_print(f"  {name}: closed OK", fh)
        else:
            log_print(f"  {name}: ERROR — {res.get('error')}", fh)

# =====================================================================
# LIVE MONITORING
# =====================================================================

def monitor_live(fh, call_sym, put_sym, call_pid, put_pid,
                 entry_call_bid, entry_put_bid, entry_combined, usd_inr):

    log_print("\n" + "=" * 100, fh)
    log_print("LIVE MONITORING STARTED", fh)
    log_print(
        f"  Entry CE ${entry_call_bid:.2f} | PE ${entry_put_bid:.2f} | "
        f"Combined ${entry_combined:.2f}", fh
    )
    log_print(
        f"  SL: {SL_COMBINED_MULTIPLIER}x >= ${entry_combined * SL_COMBINED_MULTIPLIER:.2f} | "
        f"Hard cap: Rs.{HARD_MAX_LOSS_INR:,} | "
        f"Early exit: < ${EARLY_EXIT_PREMIUM:.0f} | "
        f"Time exit: {EXIT_HOUR}:{EXIT_MINUTE:02d}", fh
    )
    log_print("=" * 100 + "\n", fh)

    result = {
        'exit_ce': 0, 'exit_pe': 0, 'exit_combined': 0,
        'exit_reason': 'Unknown', 'exit_time': ''
    }

    while True:
        try:
            now      = datetime.now(IST)
            time_str = now.strftime('%H:%M:%S')

            if now.hour > EXIT_HOUR or (now.hour == EXIT_HOUR and now.minute >= EXIT_MINUTE):
                log_print(f"\n[{time_str}] TIME EXIT triggered", fh)
                cd = get_current_premium(call_sym)
                pd = get_current_premium(put_sym)
                result.update({
                    'exit_ce':      cd['ask'] if cd['success'] else 0,
                    'exit_pe':      pd['ask'] if pd['success'] else 0,
                    'exit_reason':  'Time Exit (5:15 PM)',
                    'exit_time':    time_str
                })
                result['exit_combined'] = result['exit_ce'] + result['exit_pe']
                _close_both_legs(fh, call_pid, put_pid, "Time Exit")
                break

            cd = get_current_premium(call_sym)
            pd = get_current_premium(put_sym)

            if not cd['success'] or not pd['success']:
                log_print(f"[{time_str}] Price fetch failed — retrying...", fh)
                time.sleep(MONITOR_INTERVAL)
                continue

            pos_res = get_positions()
            if pos_res['success']:
                has_call = any(
                    p.get('product_id') == call_pid and int(p.get('size', 0)) != 0
                    for p in pos_res['positions']
                )
                has_put = any(
                    p.get('product_id') == put_pid and int(p.get('size', 0)) != 0
                    for p in pos_res['positions']
                )
                if not has_call and not has_put:
                    log_print(f"\n[{time_str}] Manual exit detected", fh)
                    result.update({
                        'exit_ce':      cd['ask'],
                        'exit_pe':      pd['ask'],
                        'exit_combined': cd['ask'] + pd['ask'],
                        'exit_reason':  'Manual Exit',
                        'exit_time':    time_str
                    })
                    break

            cur_ce       = cd['ask']
            cur_pe       = pd['ask']
            cur_combined = cur_ce + cur_pe
            pnl_usd      = (entry_combined - cur_combined) * POSITION_SIZE_BTC
            pnl_inr      = pnl_usd * usd_inr

            log_print(
                f"[{time_str}] CE ${cur_ce:.2f} | PE ${cur_pe:.2f} | "
                f"Combined ${cur_combined:.2f} | "
                f"P&L ${pnl_usd:+.2f} ({fmt_inr(pnl_inr)})", fh
            )

            if cur_combined >= entry_combined * SL_COMBINED_MULTIPLIER:
                log_print(f"\n[{time_str}] SL HIT: combined >= {SL_COMBINED_MULTIPLIER}x", fh)
                result.update({
                    'exit_ce':       cur_ce,
                    'exit_pe':       cur_pe,
                    'exit_combined': cur_combined,
                    'exit_reason':   f"SL — Combined {SL_COMBINED_MULTIPLIER}x",
                    'exit_time':     time_str
                })
                _close_both_legs(fh, call_pid, put_pid, "Combined 2.5x SL")
                break

            loss_inr = (cur_combined - entry_combined) * POSITION_SIZE_BTC * usd_inr
            if loss_inr >= HARD_MAX_LOSS_INR:
                log_print(f"\n[{time_str}] HARD CAP HIT: Rs.{loss_inr:,.0f}", fh)
                result.update({
                    'exit_ce':       cur_ce,
                    'exit_pe':       cur_pe,
                    'exit_combined': cur_combined,
                    'exit_reason':   f"Hard Cap Rs.{HARD_MAX_LOSS_INR:,}",
                    'exit_time':     time_str
                })
                _close_both_legs(fh, call_pid, put_pid, "Hard Cap")
                break

            if cur_combined < EARLY_EXIT_PREMIUM:
                log_print(
                    f"\n[{time_str}] EARLY EXIT: "
                    f"premium ${cur_combined:.2f} < ${EARLY_EXIT_PREMIUM}", fh
                )
                result.update({
                    'exit_ce':       cur_ce,
                    'exit_pe':       cur_pe,
                    'exit_combined': cur_combined,
                    'exit_reason':   'Early Exit — Premium decayed',
                    'exit_time':     time_str
                })
                _close_both_legs(fh, call_pid, put_pid, "Early Exit")
                break

            time.sleep(MONITOR_INTERVAL)

        except KeyboardInterrupt:
            result.update({
                'exit_reason': 'Interrupted',
                'exit_time':   datetime.now(IST).strftime('%H:%M:%S')
            })
            break
        except Exception as e:
            log_print(f"[ERROR] {e}", fh)
            time.sleep(MONITOR_INTERVAL)

    log_print("\nMONITORING ENDED\n", fh)
    return result

# =====================================================================
# EXCEL TRACKER — FIX 5: All P&L columns use whole number formatting
# =====================================================================

def append_to_tracker(trade):
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    HEADERS = [
        "Date", "Day", "Entry Time", "Exit Time",
        "BTC Spot ($)", "ATM Strike ($)", "Call Strike ($)", "Put Strike ($)",
        "CE Dist", "PE Dist",
        "Entry CE ($)", "Entry PE ($)", "Entry Combined ($)",
        "Exit CE ($)", "Exit PE ($)", "Exit Combined ($)",
        "P&L (USD)", "P&L (INR)", "P&L %",
        "Exit Reason", "Duration", "Mode", "Cum P&L (INR)"
    ]

    H_FONT   = Font(name='Arial', bold=True, color='FFFFFF', size=10)
    H_FILL   = PatternFill('solid', fgColor='1a1a2e')
    H_ALIGN  = Alignment(horizontal='center', vertical='center', wrap_text=True)
    D_FONT   = Font(name='Arial', size=9)
    D_ALIGN  = Alignment(horizontal='center', vertical='center')
    G_FONT   = Font(name='Arial', size=9, bold=True, color='006100')
    R_FONT   = Font(name='Arial', size=9, bold=True, color='9C0006')
    G_FILL   = PatternFill('solid', fgColor='C6EFCE')
    R_FILL   = PatternFill('solid', fgColor='FFC7CE')
    SAT_FILL = PatternFill('solid', fgColor='DAEEF3')
    BORDER   = Border(
        left=Side(style='thin', color='CCCCCC'),
        right=Side(style='thin', color='CCCCCC'),
        top=Side(style='thin', color='CCCCCC'),
        bottom=Side(style='thin', color='CCCCCC')
    )
    COL_W = {
        'A':12,'B':11,'C':10,'D':10,'E':14,'F':14,'G':14,'H':14,
        'I':8,'J':8,'K':13,'L':13,'M':16,'N':13,'O':13,'P':16,
        'Q':13,'R':13,'S':10,'T':28,'U':11,'V':10,'W':16
    }

    is_new = not os.path.exists(TRACKER_FILE)
    if is_new:
        wb = Workbook()
        ws = wb.active
        ws.title = "Trade Tracker"
        ws.append(HEADERS)
        for ci in range(1, len(HEADERS) + 1):
            cell = ws.cell(row=1, column=ci)
            cell.font      = H_FONT
            cell.fill      = H_FILL
            cell.alignment = H_ALIGN
            cell.border    = BORDER
        ws.freeze_panes = 'A2'
        ws.auto_filter.ref = f"A1:{chr(64 + len(HEADERS))}1"
        for col, w in COL_W.items():
            ws.column_dimensions[col].width = w
    else:
        wb = load_workbook(TRACKER_FILE)
        ws = wb["Trade Tracker"]

    entry_combined = trade.get('entry_combined', 0)
    pnl_usd        = trade.get('pnl_usd', 0)
    pnl_inr        = trade.get('pnl_inr', 0)
    total_prem     = entry_combined * POSITION_SIZE_BTC
    pnl_pct        = (pnl_usd / total_prem * 100) if total_prem else 0

    row = [
        trade.get('date',''),        trade.get('day',''),
        trade.get('entry_time',''),  trade.get('exit_time',''),
        trade.get('btc_spot', 0),    trade.get('atm_strike', 0),
        trade.get('call_strike', 0), trade.get('put_strike', 0),
        trade.get('ce_dist', 0),     trade.get('pe_dist', 0),
        trade.get('entry_ce', 0),    trade.get('entry_pe', 0), entry_combined,
        trade.get('exit_ce', 0),     trade.get('exit_pe', 0),  trade.get('exit_combined', 0),
        round(pnl_usd),              round(pnl_inr),           round(pnl_pct, 1),
        trade.get('exit_reason',''), trade.get('duration','-'),
        trade.get('mode','DRY RUN'), 0
    ]
    ws.append(row)
    nr = ws.max_row

    is_sat    = trade.get('day','') == 'Saturday'
    is_profit = pnl_inr >= 0

    # Apply base formatting to all cells in the row
    for ci in range(1, len(HEADERS) + 1):
        cell           = ws.cell(row=nr, column=ci)
        cell.font      = D_FONT
        cell.alignment = D_ALIGN
        cell.border    = BORDER
        if is_sat:
            cell.fill = SAT_FILL

    # Dollar columns — whole numbers, no decimals
    for col_idx, fmt in [
        (5,  '$#,##0'),   # BTC Spot
        (6,  '$#,##0'),   # ATM Strike
        (7,  '$#,##0'),   # Call Strike
        (8,  '$#,##0'),   # Put Strike
        (11, '$#,##0'),   # Entry CE
        (12, '$#,##0'),   # Entry PE
        (13, '$#,##0'),   # Entry Combined
        (14, '$#,##0'),   # Exit CE
        (15, '$#,##0'),   # Exit PE
        (16, '$#,##0'),   # Exit Combined
    ]:
        ws.cell(row=nr, column=col_idx).number_format = fmt

    # P&L columns — coloured green/red + whole numbers (FIX 5)
    for col_idx in (17, 18, 19):
        c      = ws.cell(row=nr, column=col_idx)
        c.font = G_FONT if is_profit else R_FONT
        c.fill = G_FILL if is_profit else R_FILL

    # FIX 5: Whole numbers only — no .0000 or .xx decimals
    ws.cell(row=nr, column=17).number_format = '$#,##0;-$#,##0'          # P&L USD
    ws.cell(row=nr, column=18).number_format = '\u20b9#,##0;-\u20b9#,##0'  # P&L INR
    ws.cell(row=nr, column=19).number_format = '0.0%;-0.0%'               # P&L %

    # Cumulative P&L — whole number INR (FIX 5)
    cum_cell               = ws.cell(row=nr, column=23)
    cum_cell.value         = f'=R{nr}' if nr == 2 else f'=W{nr-1}+R{nr}'
    cum_cell.number_format = '\u20b9#,##0;-\u20b9#,##0'                   # FIX 5
    cum_cell.font          = Font(name='Arial', size=9, bold=True)

    wb.save(TRACKER_FILE)
    print(f"[TRACKER] Appended row {nr} to {TRACKER_FILE}")

# =====================================================================
# HELPER: duration string
# =====================================================================

def calc_duration(entry_time_str, exit_time_str, entry_date, exit_date):
    try:
        efmt     = '%d-%m-%Y %H:%M'
        entry_dt = datetime.strptime(f"{entry_date} {entry_time_str[:5]}", efmt)
        exit_dt  = datetime.strptime(f"{exit_date} {exit_time_str[:5]}", efmt)
        secs     = max(0, int((exit_dt - entry_dt).total_seconds()))
        return f"{secs // 3600}h {(secs % 3600) // 60}m"
    except Exception:
        return "-"

# =====================================================================
# MAIN
# =====================================================================

with open(log_file, 'w', encoding='utf-8') as f:
    try:
        now_ist     = datetime.now(IST)
        today_str   = now_ist.strftime('%d-%m-%Y')
        today_day   = now_ist.strftime('%A')
        is_saturday = now_ist.weekday() == 5
        usd_inr     = get_usd_inr()

        SEP = "=" * 120
        log_print(SEP, f)
        log_print(
            f"  BTC SHORT STRANGLE v4 — {'DRY RUN' if DRY_RUN else 'LIVE'} "
            f"— {today_day} — Phase: {PHASE}", f
        )
        log_print(SEP, f)
        log_print(f"  Timestamp : {now_ist.strftime('%d-%m-%Y %H:%M:%S IST')}", f)
        log_print(f"  Position  : {POSITION_SIZE_LOTS} lots / leg  ({POSITION_SIZE_BTC} BTC)", f)
        log_print(f"  USD/INR   : {usd_inr:.2f}", f)
        log_print(SEP + "\n", f)

        # ╔══════════════════════════════════════════════════════════════╗
        # ║  PHASE: ENTRY  (3:30 AM IST)                                ║
        # ╚══════════════════════════════════════════════════════════════╝
        if PHASE == "ENTRY":

            cutoff          = now_ist.replace(hour=17, minute=30, second=0, microsecond=0)
            target_expiry   = now_ist if now_ist < cutoff else now_ist + timedelta(days=1)
            expiry_date_str = target_expiry.strftime('%d-%m-%Y')
            log_print(f"Target expiry: {expiry_date_str}\n", f)

            # Fetch BTC spot
            r = requests.get(f"{BASE_URL}/v2/tickers/BTCUSD", timeout=10)
            if r.status_code != 200:
                raise Exception(f"Spot fetch failed: HTTP {r.status_code}")
            spot_price = float(r.json()['result']['spot_price'])
            log_print(f"BTC Spot: ${spot_price:,.2f}\n", f)

            # Fetch option chain
            params = {
                'contract_types':           'call_options,put_options',
                'underlying_asset_symbols': 'BTC',
                'expiry_date':              expiry_date_str
            }
            r = requests.get(f"{BASE_URL}/v2/tickers", params=params, timeout=15)
            if r.status_code != 200:
                raise Exception(f"Option chain fetch failed: HTTP {r.status_code}")
            options = r.json()['result']
            if not options:
                log_print(f"[SKIP] No options for expiry {expiry_date_str}", f)
                raise SystemExit(0)

            calls = sorted(
                [o for o in options if o['contract_type'] == 'call_options'],
                key=lambda x: float(x['strike_price'])
            )
            puts = sorted(
                [o for o in options if o['contract_type'] == 'put_options'],
                key=lambda x: float(x['strike_price'])
            )

            all_strikes  = sorted(set(float(o['strike_price']) for o in options))
            atm_strike   = min(all_strikes, key=lambda x: abs(x - spot_price))
            atm_index    = all_strikes.index(atm_strike)
            calls_by_str = {float(c['strike_price']): c for c in calls}
            puts_by_str  = {float(p['strike_price']): p for p in puts}

            max_ce = len(all_strikes) - atm_index - 1
            max_pe = atm_index

            log_print(
                f"ATM: ${atm_strike:,.0f}  |  "
                f"Strikes available: +{max_ce} calls / -{max_pe} puts\n", f
            )

            if max_ce < 13 or max_pe < 13:
                log_print(
                    f"[WARNING] Need 13 strikes each side. "
                    f"Have: CE {max_ce}, PE {max_pe}\n", f
                )

            def run_strike_scan(range_start, range_end, label, fh):
                best = None
                bi   = float('inf')

                log_print(f"DELTA-NEUTRALITY SCAN ({label}):", fh)
                log_print("-" * 120, fh)

                for ce_d in range(range_start, min(range_end + 1, max_ce + 1)):
                    for pe_d in range(range_start, min(range_end + 1, max_pe + 1)):
                        cs = all_strikes[atm_index + ce_d]
                        ps = all_strikes[atm_index - pe_d]
                        co = calls_by_str.get(cs, {})
                        po = puts_by_str.get(ps, {})
                        cq = co.get('quotes', {})
                        pq = po.get('quotes', {})

                        cb = float(cq.get('best_bid', 0) or 0)
                        ca = float(cq.get('best_ask', 0) or 0)
                        pb = float(pq.get('best_bid', 0) or 0)
                        pa = float(pq.get('best_ask', 0) or 0)

                        if cb < MIN_PREMIUM_USD or pb < MIN_PREMIUM_USD:
                            log_print(
                                f"  CE +{ce_d} ${cs:,.0f} bid ${cb:.2f} | "
                                f"PE -{pe_d} ${ps:,.0f} bid ${pb:.2f}  "
                                f"→ SKIP (below ${MIN_PREMIUM_USD} min)", fh
                            )
                            continue

                        cs_pct = ((ca - cb) / ca * 100) if ca > 0 else 100
                        ps_pct = ((pa - pb) / pa * 100) if pa > 0 else 100
                        wide   = cs_pct > MAX_SPREAD_PCT or ps_pct > MAX_SPREAD_PCT

                        imb     = abs(cb - pb)
                        imb_pct = imb / max(cb, pb) * 100

                        flag = "  [WIDE SPREAD — skipped]" if wide else ""
                        log_print(
                            f"  CE +{ce_d} ${cs:,.0f} bid ${cb:.2f} | "
                            f"PE -{pe_d} ${ps:,.0f} bid ${pb:.2f}  "
                            f"→ Imbalance ${imb:.2f} ({imb_pct:.1f}%){flag}", fh
                        )

                        if not wide and imb < bi:
                            bi   = imb
                            best = {
                                'call_strike':     cs,
                                'put_strike':      ps,
                                'ce_dist':         ce_d,
                                'pe_dist':         pe_d,
                                'call_symbol':     co.get('symbol'),
                                'put_symbol':      po.get('symbol'),
                                'call_product_id': co.get('product_id') or co.get('id'),
                                'put_product_id':  po.get('product_id') or po.get('id'),
                                'call_bid':        cb,
                                'call_ask':        ca,
                                'put_bid':         pb,
                                'put_ask':         pa,
                                'combined_premium': cb + pb,
                                'scan_label':      label
                            }
                            log_print(
                                f"    *** BEST SO FAR: CE +{ce_d}, PE -{pe_d} "
                                f"imbalance ${imb:.2f} ({imb_pct:.1f}%)", fh
                            )

                log_print("-" * 120 + "\n", fh)
                return best

            best_combo = run_strike_scan(13, 15, "PRIMARY — 13-15 strikes OTM", f)

            if not best_combo:
                log_print(
                    "[INFO] Primary scan (13-15) found no valid pair — "
                    "trying fallback (10-12)...\n", f
                )
                best_combo = run_strike_scan(10, 12, "FALLBACK — 10-12 strikes OTM", f)
                if best_combo:
                    log_print("[FALLBACK] Valid pair found at closer strikes.\n", f)
                else:
                    log_print("[INFO] Fallback scan also empty. Skipping today.\n", f)

            if not best_combo:
                log_print("[SKIP] No valid strike pair found today.", f)
                raise SystemExit(0)

            selected_ce = best_combo['call_strike']
            selected_pe = best_combo['put_strike']
            combined    = best_combo['combined_premium']

            log_print(SEP, f)
            log_print(f"SELECTED TRADE  [{best_combo['scan_label']}]", f)
            log_print(SEP, f)
            log_print(
                f"  SELL CE : {best_combo['call_symbol']}  "
                f"Strike ${selected_ce:,.0f}  (+{best_combo['ce_dist']} from ATM)  "
                f"Bid ${best_combo['call_bid']:.2f}", f
            )
            log_print(
                f"  SELL PE : {best_combo['put_symbol']}  "
                f"Strike ${selected_pe:,.0f}  (-{best_combo['pe_dist']} from ATM)  "
                f"Bid ${best_combo['put_bid']:.2f}", f
            )
            log_print(
                f"  Combined: ${combined:.2f}  |  "
                f"Total: ${combined * POSITION_SIZE_BTC:.2f}  "
                f"({fmt_inr(combined * POSITION_SIZE_BTC * usd_inr)})", f
            )
            log_print(
                f"  SL: {SL_COMBINED_MULTIPLIER}x >= ${combined * SL_COMBINED_MULTIPLIER:.2f}  |  "
                f"Hard cap: Rs.{HARD_MAX_LOSS_INR:,}  |  "
                f"Early exit: < ${EARLY_EXIT_PREMIUM:.0f}  |  "
                f"Time exit: {EXIT_HOUR}:{EXIT_MINUTE:02d}", f
            )
            log_print(SEP + "\n", f)

            # Save active_trade.json
            active_trade = {
                'date':            today_str,
                'day':             today_day,
                'entry_time':      now_ist.strftime('%H:%M'),
                'btc_spot':        spot_price,
                'atm_strike':      atm_strike,
                'usd_to_inr':      usd_inr,
                'call_strike':     best_combo['call_strike'],
                'put_strike':      best_combo['put_strike'],
                'ce_dist':         best_combo['ce_dist'],
                'pe_dist':         best_combo['pe_dist'],
                'call_symbol':     best_combo['call_symbol'],
                'put_symbol':      best_combo['put_symbol'],
                'call_product_id': best_combo['call_product_id'],
                'put_product_id':  best_combo['put_product_id'],
                'entry_ce':        best_combo['call_bid'],
                'entry_pe':        best_combo['put_bid'],
                'entry_combined':  combined
            }
            with open(ACTIVE_TRADE_FILE, 'w') as tf:
                json.dump(active_trade, tf, indent=2)
            log_print(f"[ENTRY] Saved → {ACTIVE_TRADE_FILE}", f)

            if DRY_RUN:
                log_print("[DRY RUN] No orders placed. EXIT phase runs at 5:15 PM IST.\n", f)
            elif is_saturday:
                log_print("PLACING LIVE ORDERS...\n", f)
                bal = get_wallet_balance()
                if bal['success']:
                    log_print(f"  Wallet: ${bal['available_balance']:.2f} USDT available", f)

                co = place_order(best_combo['call_product_id'], POSITION_SIZE_LOTS, 'sell')
                if not co['success']:
                    raise Exception(f"Call order failed: {co.get('error')}")
                log_print(f"  Call placed. ID: {co['data'].get('result',{}).get('id','N/A')}", f)

                po = place_order(best_combo['put_product_id'], POSITION_SIZE_LOTS, 'sell')
                if not po['success']:
                    log_print("  Put FAILED — rolling back call...", f)
                    close_position(best_combo['call_product_id'], POSITION_SIZE_LOTS)
                    raise Exception("Put order failed — rolled back")
                log_print(f"  Put placed. ID: {po['data'].get('result',{}).get('id','N/A')}", f)
                log_print("  BOTH LEGS LIVE\n", f)

                time.sleep(5)
                exit_data = monitor_live(
                    fh=f,
                    call_sym=best_combo['call_symbol'],
                    put_sym=best_combo['put_symbol'],
                    call_pid=best_combo['call_product_id'],
                    put_pid=best_combo['put_product_id'],
                    entry_call_bid=best_combo['call_bid'],
                    entry_put_bid=best_combo['put_bid'],
                    entry_combined=combined,
                    usd_inr=usd_inr
                )
                exit_combined = exit_data['exit_ce'] + exit_data['exit_pe']
                pnl_usd  = (combined - exit_combined) * POSITION_SIZE_BTC
                pnl_inr  = pnl_usd * usd_inr
                dur_str  = calc_duration(
                    now_ist.strftime('%H:%M'), exit_data['exit_time'],
                    today_str, today_str
                )
                log_print(
                    f"\nFINAL P&L: ${pnl_usd:+.2f}  ({fmt_inr(pnl_inr)})  "
                    f"— {exit_data['exit_reason']}\n", f
                )
                append_to_tracker({
                    'date': today_str, 'day': today_day,
                    'entry_time': now_ist.strftime('%H:%M'),
                    'exit_time':  exit_data['exit_time'],
                    'btc_spot':   spot_price, 'atm_strike': atm_strike,
                    'call_strike': best_combo['call_strike'],
                    'put_strike':  best_combo['put_strike'],
                    'ce_dist':    best_combo['ce_dist'],
                    'pe_dist':    best_combo['pe_dist'],
                    'entry_ce':   best_combo['call_bid'],
                    'entry_pe':   best_combo['put_bid'],
                    'entry_combined': combined,
                    'exit_ce':    exit_data['exit_ce'],
                    'exit_pe':    exit_data['exit_pe'],
                    'exit_combined': exit_combined,
                    'pnl_usd': pnl_usd, 'pnl_inr': pnl_inr,
                    'exit_reason': exit_data['exit_reason'],
                    'duration': dur_str, 'mode': 'LIVE'
                })
            else:
                log_print(
                    f"[INFO] {today_day} — live orders only on Saturdays. "
                    f"Entry snapshot saved, exit will run at 5:15 PM.\n", f
                )

            # Full option chain display
            log_print("=" * 160, f)
            log_print("FULL OPTION CHAIN", f)
            log_print("=" * 160 + "\n", f)

            si  = max(0, atm_index - 15)
            ei  = min(len(all_strikes), atm_index + 16)
            sel = all_strikes[si:ei]

            log_print(f"{'CALL (CE)':<77} | {'PUT (PE)':<77}", f)
            log_print("=" * 160, f)
            log_print(
                f"{'Symbol':<22} | {'Strike':>12} | {'Bid':>10} | "
                f"{'Ask':>10} | {'IV':>8} || "
                f"{'Symbol':<22} | {'Bid':>10} | {'Ask':>10} | {'IV':>8}", f
            )
            log_print("-" * 160, f)

            for strike in sel:
                cd  = calls_by_str.get(strike, {})
                pd_ = puts_by_str.get(strike, {})
                cq  = cd.get('quotes', {})
                pq  = pd_.get('quotes', {})

                c_sym = (cd.get('symbol') or '-')[:22]
                c_b   = f"${float(cq['best_bid']):,.2f}" if cq.get('best_bid') else '-'
                c_a   = f"${float(cq['best_ask']):,.2f}" if cq.get('best_ask') else '-'
                c_iv  = str(cq.get('ask_iv', '-'))

                p_sym = (pd_.get('symbol') or '-')[:22]
                p_b   = f"${float(pq['best_bid']):,.2f}" if pq.get('best_bid') else '-'
                p_a   = f"${float(pq['best_ask']):,.2f}" if pq.get('best_ask') else '-'
                p_iv  = str(pq.get('ask_iv', '-'))

                marker = ""
                if strike == atm_strike:    marker = "  <- ATM"
                elif strike == selected_ce: marker = "  <- CE SELECTED"
                elif strike == selected_pe: marker = "  <- PE SELECTED"

                log_print(
                    f"{c_sym:<22} | ${strike:>11,.0f} | {c_b:>10} | "
                    f"{c_a:>10} | {c_iv:>8} || "
                    f"{p_sym:<22} | {p_b:>10} | {p_a:>10} | {p_iv:>8}{marker}", f
                )

            log_print("=" * 160 + "\n", f)

        # ╔══════════════════════════════════════════════════════════════╗
        # ║  PHASE: EXIT  (5:15 PM IST)                                 ║
        # ╚══════════════════════════════════════════════════════════════╝
        elif PHASE == "EXIT":

            if not os.path.exists(ACTIVE_TRADE_FILE):
                log_print("[EXIT] No active_trade.json — nothing to exit.\n", f)
                raise SystemExit(0)

            with open(ACTIVE_TRADE_FILE, 'r') as tf:
                entry = json.load(tf)

            entry_date = entry.get('date', '')
            if entry_date != today_str:
                log_print(
                    f"[EXIT] STALE FILE — entry {entry_date} != today {today_str}. "
                    f"Deleting.", f
                )
                os.remove(ACTIVE_TRADE_FILE)
                raise SystemExit(0)

            log_print(f"Entry date     : {entry['date']} ({entry['day']})", f)
            log_print(f"Entry time     : {entry['entry_time']}", f)
            log_print(f"Entry CE       : {entry['call_symbol']}  bid ${entry['entry_ce']:.2f}", f)
            log_print(f"Entry PE       : {entry['put_symbol']}  bid ${entry['entry_pe']:.2f}", f)
            log_print(f"Entry combined : ${entry['entry_combined']:.2f}\n", f)

            entry_combined = entry['entry_combined']
            saved_usd_inr  = entry.get('usd_to_inr', usd_inr)
            sl_level       = entry_combined * SL_COMBINED_MULTIPLIER
            hard_cap_level = (
                HARD_MAX_LOSS_INR / saved_usd_inr / POSITION_SIZE_BTC + entry_combined
            )

            # STEP 1: Intraday candle SL check
            log_print("\nSTEP 1 — Intraday SL check (1m candles)...", f)
            intraday = get_intraday_worst_combined(
                call_symbol=entry['call_symbol'],
                put_symbol=entry['put_symbol'],
                entry_time_str=entry['entry_time'],
                sl_level=sl_level,
                hard_cap_level=hard_cap_level,
                fh=f
            )

            sl_breached       = False
            hard_cap_breached = False
            exit_combined     = None
            exit_ce           = None
            exit_pe           = None
            exit_time_str     = None
            exit_reason       = None

            if intraday:
                worst = intraday['worst_combined']
                wt    = intraday['worst_time']

                if intraday['sl_breached']:
                    sl_breached   = True
                    exit_combined = sl_level
                    exit_time_str = wt
                    exit_reason   = (
                        f"SL — Combined {SL_COMBINED_MULTIPLIER}x "
                        f"(intraday @ {wt})"
                    )
                    ratio  = entry['entry_ce'] / entry_combined if entry_combined else 0.5
                    exit_ce = round(exit_combined * ratio, 2)
                    exit_pe = round(exit_combined * (1 - ratio), 2)
                    log_print(
                        f"  *** SL BREACHED at {wt} — "
                        f"peak ${worst:.2f} >= SL ${sl_level:.2f}", f
                    )

                elif intraday['hard_cap_breached']:
                    hard_cap_breached = True
                    exit_combined = hard_cap_level
                    exit_time_str = wt
                    exit_reason   = (
                        f"Hard Cap Rs.{HARD_MAX_LOSS_INR:,} (intraday @ {wt})"
                    )
                    ratio  = entry['entry_ce'] / entry_combined if entry_combined else 0.5
                    exit_ce = round(exit_combined * ratio, 2)
                    exit_pe = round(exit_combined * (1 - ratio), 2)
                    log_print(
                        f"  *** HARD CAP BREACHED at {wt} — "
                        f"peak ${worst:.2f} >= cap ${hard_cap_level:.2f}", f
                    )

                else:
                    log_print(
                        f"  No SL breach. "
                        f"Peak ${worst:.2f} < SL ${sl_level:.2f}", f
                    )

            else:
                # FIX 4: Safety fallback
                log_print(
                    "\n  [SAFETY FALLBACK] Candle fetch failed — "
                    "trying live price check...", f
                )
                cd_now = get_current_premium(entry['call_symbol'])
                pd_now = get_current_premium(entry['put_symbol'])

                if cd_now['success'] and pd_now['success']:
                    spot_combined = cd_now['ask'] + pd_now['ask']
                    log_print(
                        f"  [SAFETY] Live combined: ${spot_combined:.2f} | "
                        f"SL: ${sl_level:.2f}", f
                    )
                    if spot_combined >= sl_level:
                        sl_breached   = True
                        exit_combined = spot_combined
                        exit_ce       = cd_now['ask']
                        exit_pe       = pd_now['ask']
                        exit_time_str = now_ist.strftime('%H:%M')
                        exit_reason   = (
                            f"SL — Combined {SL_COMBINED_MULTIPLIER}x "
                            f"(live price fallback @ {exit_time_str})"
                        )
                        log_print("  [SAFETY] SL confirmed via live price.", f)
                    else:
                        log_print(
                            f"  [SAFETY] ${spot_combined:.2f} < SL ${sl_level:.2f} "
                            f"— proceeding to Step 2.", f
                        )
                else:
                    log_print(
                        "  [SAFETY] Live price unavailable — "
                        "options likely expired. Trying spot intrinsic...", f
                    )
                    current_spot = get_btc_spot()
                    if current_spot:
                        put_strike  = entry['put_strike']
                        call_strike = entry['call_strike']
                        pe_intrinsic = max(0.0, put_strike  - current_spot)
                        ce_intrinsic = max(0.0, current_spot - call_strike)
                        est_combined = pe_intrinsic + ce_intrinsic

                        log_print(
                            f"  [SAFETY] BTC spot: ${current_spot:,.2f} | "
                            f"PE intrinsic: ${pe_intrinsic:.2f} | "
                            f"CE intrinsic: ${ce_intrinsic:.2f} | "
                            f"Est. combined: ${est_combined:.2f}", f
                        )

                        if est_combined >= sl_level:
                            sl_breached   = True
                            exit_combined = est_combined
                            exit_ce       = ce_intrinsic
                            exit_pe       = pe_intrinsic
                            exit_time_str = now_ist.strftime('%H:%M')
                            exit_reason   = (
                                f"SL — Combined {SL_COMBINED_MULTIPLIER}x "
                                f"(estimated from settlement spot)"
                            )
                            log_print(
                                "  [SAFETY] Estimated SL breach. "
                                "[WARNING] Verify manually against actual prices.", f
                            )
                        else:
                            log_print(
                                f"  [SAFETY] Est. ${est_combined:.2f} < SL ${sl_level:.2f} "
                                f"— proceeding to Step 2.", f
                            )
                            if pe_intrinsic > 0 or ce_intrinsic > 0:
                                log_print(
                                    "  [WARNING] Option had intrinsic value at expiry — "
                                    "verify if intraday SL should have triggered.", f
                                )
                    else:
                        log_print(
                            "  [SAFETY] Cannot fetch spot either — "
                            "proceeding to Step 2. P&L may be unreliable.", f
                        )
                        log_print("  [WARNING] Verify this trade manually.", f)

            # STEP 2: Live 5:15 PM price fetch
            if not sl_breached and not hard_cap_breached:
                log_print("\nSTEP 2 — Fetching live exit prices at 5:15 PM...", f)
                cd = get_current_premium(entry['call_symbol'])
                pd = get_current_premium(entry['put_symbol'])

                if not cd['success'] or not pd['success']:
                    log_print("  First attempt failed — retrying in 10s...", f)
                    time.sleep(10)
                    cd = get_current_premium(entry['call_symbol'])
                    pd = get_current_premium(entry['put_symbol'])

                exit_ce       = cd['ask'] if cd['success'] else 0.0
                exit_pe       = pd['ask'] if pd['success'] else 0.0
                exit_combined = exit_ce + exit_pe
                exit_time_str = now_ist.strftime('%H:%M')

                # FIX 3: Zero-value guard
                if exit_combined == 0.0:
                    log_print(
                        "  [WARN] Both legs $0 — options expired. "
                        "Calculating intrinsic value from BTC spot...", f
                    )
                    settlement_spot = get_btc_spot()
                    if settlement_spot:
                        put_strike  = entry['put_strike']
                        call_strike = entry['call_strike']
                        exit_pe     = max(0.0, put_strike  - settlement_spot)
                        exit_ce     = max(0.0, settlement_spot - call_strike)
                        exit_combined = exit_ce + exit_pe

                        log_print(
                            f"  [FIX] Settlement spot: ${settlement_spot:,.2f} | "
                            f"CE intrinsic: ${exit_ce:.2f} | "
                            f"PE intrinsic: ${exit_pe:.2f} | "
                            f"Combined: ${exit_combined:.2f}", f
                        )

                        if exit_combined == 0.0:
                            exit_reason = (
                                "Time Exit — Options Expired OTM (full premium kept)"
                            )
                            log_print(
                                "  [FIX] Both strikes OTM at expiry — "
                                "full premium kept. WIN.", f
                            )
                        else:
                            log_print(
                                f"  [WARNING] Options had intrinsic value at expiry "
                                f"(${exit_combined:.2f}) — verify if intraday SL "
                                f"should have triggered.", f
                            )
                    else:
                        log_print("  [WARN] Cannot fetch settlement spot.", f)

                if not exit_reason:
                    exit_reason = (
                        "Early Exit — Premium decayed"
                        if exit_combined < EARLY_EXIT_PREMIUM
                        else "Time Exit (5:15 PM)"
                    )

            # Final P&L
            pnl_usd = (entry_combined - exit_combined) * POSITION_SIZE_BTC
            pnl_inr = pnl_usd * saved_usd_inr
            dur_str = calc_duration(
                entry['entry_time'], exit_time_str,
                entry['date'], today_str
            )

            log_print(SEP, f)
            log_print("EXIT SUMMARY", f)
            log_print(SEP, f)
            if intraday:
                log_print(
                    f"  Intraday peak  : ${intraday['worst_combined']:.2f} "
                    f"at {intraday['worst_time']} IST "
                    f"({intraday['candle_count']} candles)", f
                )
            log_print(f"  SL level       : ${sl_level:.2f}", f)
            log_print(f"  Hard cap level : ${hard_cap_level:.2f}", f)
            log_print(f"  Exit CE        : ${exit_ce:.2f}", f)
            log_print(f"  Exit PE        : ${exit_pe:.2f}", f)
            log_print(f"  Exit combined  : ${exit_combined:.2f}", f)
            log_print(f"  P&L            : ${pnl_usd:+.2f}  ({fmt_inr(pnl_inr)})", f)
            log_print(f"  Exit reason    : {exit_reason}", f)
            log_print(f"  Duration       : {dur_str}", f)
            log_print(SEP + "\n", f)

            append_to_tracker({
                'date':           entry['date'],
                'day':            entry['day'],
                'entry_time':     entry['entry_time'],
                'exit_time':      exit_time_str,
                'btc_spot':       entry['btc_spot'],
                'atm_strike':     entry['atm_strike'],
                'call_strike':    entry['call_strike'],
                'put_strike':     entry['put_strike'],
                'ce_dist':        entry['ce_dist'],
                'pe_dist':        entry['pe_dist'],
                'entry_ce':       entry['entry_ce'],
                'entry_pe':       entry['entry_pe'],
                'entry_combined': entry_combined,
                'exit_ce':        exit_ce,
                'exit_pe':        exit_pe,
                'exit_combined':  exit_combined,
                'pnl_usd':        pnl_usd,
                'pnl_inr':        pnl_inr,
                'exit_reason':    exit_reason,
                'duration':       dur_str,
                'mode':           'DRY RUN' if DRY_RUN else 'LIVE'
            })
            log_print(f"[TRACKER] Row written to {TRACKER_FILE}\n", f)

            os.remove(ACTIVE_TRADE_FILE)
            log_print(f"[CLEANUP] {ACTIVE_TRADE_FILE} deleted.\n", f)

        else:
            log_print(f"[ERROR] Unknown PHASE='{PHASE}'. Must be ENTRY or EXIT.", f)

        log_print(f"Done. Log: {log_file}", f)

    except SystemExit as e:
        log_print(f"\n[EXIT] Script exited cleanly (code {e.code}).", f)
    except Exception as e:
        log_print(f"\n[FATAL ERROR] {e}", f)
        log_print(traceback.format_exc(), f)

print(f"\n[SUCCESS] Log: {log_file}")
