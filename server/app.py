from flask import Flask, jsonify, request
from flask_cors import CORS
from pykrx import stock
from datetime import datetime, timedelta
import traceback
import os

app = Flask(__name__)
CORS(app)

# ─────────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────────
def nearest_biz_day(date_str):
    d = datetime.strptime(date_str, '%Y%m%d')
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime('%Y%m%d')

def get_date():
    raw = request.args.get('date', '') or datetime.now().strftime('%Y%m%d')
    return nearest_biz_day(raw.replace('-', ''))

def safe_iloc(row, idx, default=0):
    try:
        v = row.iloc[idx]
        if v != v:   # NaN 체크
            return default
        return v
    except Exception:
        return default

# ─────────────────────────────────────────────
# 컬럼 인덱스 자동 감지
# ─────────────────────────────────────────────
def find_col(columns, candidates):
    for c in candidates:
        if c in columns:
            return list(columns).index(c)
    return None

def get_ohlcv_col_indices(df):
    cols = df.columns
    return {
        'close':  find_col(cols, ['종가', 'Close', 'close', '현재가']) or 3,
        'change': find_col(cols, ['등락률', '변동률', 'Change', 'change', '등락율']) or (len(cols)-1),
    }

def get_cap_col_indices(df):
    cols = df.columns
    return {
        'mktcap': find_col(cols, ['시가총액', 'Marcap', 'marcap', 'MktCap']) or 0,
        'shares': find_col(cols, ['상장주식수', 'Shares', 'shares', '상장주수']) or 3,
    }

def get_net_col_indices(df):
    cols = df.columns
    return {
        'frgn': find_col(cols, ['외국인', '외국인합계', 'Foreigners', 'foreigners', '외국인순매수']) or 1,
        'inst': find_col(cols, ['기관합계', '기관', 'Institutions', 'institutions', '기관순매수']) or 2,
    }

# ─────────────────────────────────────────────
# 라우트
# ─────────────────────────────────────────────
@app.route('/api/health')
def health():
    return jsonify({'status': 'ok', 'time': datetime.now().isoformat()})


@app.route('/api/debug')
def debug():
    try:
        date = get_date()
        ohlcv = stock.get_market_ohlcv_by_ticker(date, market='KOSPI')
        cap   = stock.get_market_cap_by_ticker(date, market='KOSPI')

        try:
            net = stock.get_market_net_purchases_of_equities_by_ticker(date, date, market='KOSPI')
            net_cols   = list(net.columns)
            net_sample = net.iloc[0].tolist() if len(net) > 0 else []
        except Exception as e:
            net_cols   = [f'ERROR: {e}']
            net_sample = []

        return jsonify({
            'date':         date,
            'ohlcv_cols':   list(ohlcv.columns),
            'cap_cols':     list(cap.columns),
            'net_cols':     net_cols,
            'ohlcv_sample': ohlcv.iloc[0].tolist() if len(ohlcv) > 0 else [],
            'cap_sample':   cap.iloc[0].tolist()   if len(cap)   > 0 else [],
            'net_sample':   net_sample,
            'ohlcv_len':    len(ohlcv),
            'cap_len':      len(cap),
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500


@app.route('/api/scan')
def scan():
    try:
        date      = get_date()
        threshold = float(request.args.get('threshold', 0.3))

        kospi_ohlcv  = stock.get_market_ohlcv_by_ticker(date, market='KOSPI')
        kosdaq_ohlcv = stock.get_market_ohlcv_by_ticker(date, market='KOSDAQ')

        if kospi_ohlcv.empty and kosdaq_ohlcv.empty:
            return jsonify({'error': f'{date} 휴장일이거나 데이터 없음'}), 404

        kospi_cap  = stock.get_market_cap_by_ticker(date, market='KOSPI')
        kosdaq_cap = stock.get_market_cap_by_ticker(date, market='KOSDAQ')

        try:
            kospi_net = stock.get_market_net_purchases_of_equities_by_ticker(date, date, market='KOSPI')
        except Exception:
            kospi_net = None

        try:
            kosdaq_net = stock.get_market_net_purchases_of_equities_by_ticker(date, date, market='KOSDAQ')
        except Exception:
            kosdaq_net = None

        ohlcv_idx = get_ohlcv_col_indices(kospi_ohlcv if not kospi_ohlcv.empty else kosdaq_ohlcv)
        cap_idx   = get_cap_col_indices(kospi_cap if not kospi_cap.empty else kosdaq_cap)

        net_idx = {'frgn': 1, 'inst': 2}
        for net_df in [kospi_net, kosdaq_net]:
            if net_df is not None and not net_df.empty:
                net_idx = get_net_col_indices(net_df)
                break

        result = []

        def build_rows(ohlcv, cap, net_df, market_name):
            for ticker in ohlcv.index:
                try:
                    row_o = ohlcv.loc[ticker]
                    close = int(safe_iloc(row_o, ohlcv_idx['close']))
                    if close <= 0:
                        continue

                    change_rate = float(safe_iloc(row_o, ohlcv_idx['change'], 0.0))

                    mkt_cap = 0
                    shares  = 0
                    if ticker in cap.index:
                        row_c   = cap.loc[ticker]
                        mkt_cap = int(safe_iloc(row_c, cap_idx['mktcap']))
                        shares  = int(safe_iloc(row_c, cap_idx['shares']))

                    frgn_net = 0
                    inst_net = 0
                    if net_df is not None and not net_df.empty and ticker in net_df.index:
                        row_n    = net_df.loc[ticker]
                        frgn_net = int(safe_iloc(row_n, net_idx['frgn']))
                        inst_net = int(safe_iloc(row_n, net_idx['inst']))

                    total_net = frgn_net + inst_net
                    ratio     = (total_net / mkt_cap * 100) if mkt_cap > 0 else 0.0

                    try:
                        name = stock.get_market_ticker_name(ticker)
                    except Exception:
                        name = ticker

                    result.append({
                        'isuCd':       ticker,
                        'isuNm':       name,
                        'market':      market_name,
                        'close':       close,
                        'changeRate':  round(change_rate, 2),
                        'floatShares': shares,
                        'floatMktCap': mkt_cap,
                        'foreignNet':  frgn_net,
                        'instNet':     inst_net,
                        'totalNet':    total_net,
                        'ratio':       round(ratio, 4),
                        'threshold':   round(mkt_cap * 0.003),
                        'isHit':       ratio >= threshold,
                    })
                except Exception:
                    pass

        build_rows(kospi_ohlcv,  kospi_cap,  kospi_net,  'KOSPI')
        build_rows(kosdaq_ohlcv, kosdaq_cap, kosdaq_net, 'KOSDAQ')

        result.sort(key=lambda x: x['ratio'], reverse=True)
        hit_count = sum(1 for r in result if r['isHit'])

        return jsonify({
            'date':         date,
            'threshold':    threshold,
            'total':        len(result),
            'hitCount':     hit_count,
            'data':         result,
            '_col_indices': {
                'ohlcv': ohlcv_idx,
                'cap':   cap_idx,
                'net':   net_idx,
            }
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
