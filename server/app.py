from flask import Flask, jsonify, request
from flask_cors import CORS
from pykrx import stock
from datetime import datetime, timedelta
import traceback
import os

app = Flask(__name__)
CORS(app)

def get_nearest_business_day(date_str):
    d = datetime.strptime(date_str, '%Y%m%d')
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime('%Y%m%d')

def date_param():
    raw = request.args.get('date', '')
    if not raw:
        raw = datetime.now().strftime('%Y%m%d')
    raw = raw.replace('-', '')
    return get_nearest_business_day(raw)

@app.route('/api/health')
def health():
    return jsonify({'status': 'ok', 'time': datetime.now().isoformat()})

@app.route('/api/debug')
def debug():
    try:
        date = date_param()
        ohlcv = stock.get_market_ohlcv_by_ticker(date, market='KOSPI')
        cap   = stock.get_market_cap_by_ticker(date, market='KOSPI')
        net   = stock.get_market_net_purchases_of_equities_by_ticker(date, date, market='KOSPI')
        return jsonify({
            'date': date,
            'ohlcv_columns': list(ohlcv.columns),
            'cap_columns': list(cap.columns),
            'net_columns': list(net.columns) if net is not None else [],
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/scan')
def scan():
    try:
        date = date_param()
        threshold = float(request.args.get('threshold', 0.3))

        kospi_ohlcv  = stock.get_market_ohlcv_by_ticker(date, market='KOSPI')
        kosdaq_ohlcv = stock.get_market_ohlcv_by_ticker(date, market='KOSDAQ')
        kospi_cap    = stock.get_market_cap_by_ticker(date, market='KOSPI')
        kosdaq_cap   = stock.get_market_cap_by_ticker(date, market='KOSDAQ')

        if kospi_ohlcv.empty and kosdaq_ohlcv.empty:
            return jsonify({'error': f'{date} 는 휴장일이거나 데이터가 없습니다.'}), 404

        kospi_net  = stock.get_market_net_purchases_of_equities_by_ticker(date, date, market='KOSPI')
        kosdaq_net = stock.get_market_net_purchases_of_equities_by_ticker(date, date, market='KOSDAQ')

        kospi_names  = {t: stock.get_market_ticker_name(t) for t in kospi_ohlcv.index}
        kosdaq_names = {t: stock.get_market_ticker_name(t) for t in kosdaq_ohlcv.index}

        result = []

        def get_col(df, *candidates):
            for c in candidates:
                if c in df.columns:
                    return c
            return None

        def build_rows(ohlcv, cap, net_df, names, market_name):
            close_col  = get_col(ohlcv, '종가', 'Close', 'close')
            change_col = get_col(ohlcv, '등락률', 'Change', 'change', 'Returns')
            cap_col    = get_col(cap,   '시가총액', 'Mkt Cap', 'MarketCap')
            shrs_col   = get_col(cap,   '상장주식수', 'Shares', 'shares')

            for ticker in ohlcv.index:
                try:
                    close = int(ohlcv.loc[ticker, close_col]) if close_col else 0
                    if close <= 0:
                        continue
                    change_rate = float(ohlcv.loc[ticker, change_col]) if change_col else 0.0
                    mkt_cap   = int(cap.loc[ticker, cap_col])  if (ticker in cap.index and cap_col)  else 0
                    list_shrs = int(cap.loc[ticker, shrs_col]) if (ticker in cap.index and shrs_col) else 0

                    frgn_net = 0
                    inst_net = 0
                    if net_df is not None and ticker in net_df.index:
                        row = net_df.loc[ticker]
                        for col in ['외국인', '외국인합계', 'Foreigner', 'Foreign']:
                            if col in net_df.columns:
                                frgn_net = int(row[col])
                                break
                        for col in ['기관합계', '기관', 'Institution', 'Institutional']:
                            if col in net_df.columns:
                                inst_net = int(row[col])
                                break

                    total_net     = frgn_net + inst_net
                    float_mktcap  = mkt_cap
                    ratio         = (total_net / float_mktcap * 100) if float_mktcap > 0 else 0.0
                    threshold_amt = float_mktcap * 0.003

                    result.append({
                        'isuCd':       ticker,
                        'isuNm':       names.get(ticker, ticker),
                        'market':      market_name,
                        'close':       close,
                        'changeRate':  round(change_rate, 2),
                        'floatShares': list_shrs,
                        'floatMktCap': float_mktcap,
                        'foreignNet':  frgn_net,
                        'instNet':     inst_net,
                        'totalNet':    total_net,
                        'ratio':       round(ratio, 4),
                        'threshold':   round(threshold_amt),
                        'isHit':       ratio >= threshold,
                    })
                except Exception:
                    pass

        build_rows(kospi_ohlcv,  kospi_cap,  kospi_net,  kospi_names,  'KOSPI')
        build_rows(kosdaq_ohlcv, kosdaq_cap, kosdaq_net, kosdaq_names, 'KOSDAQ')

        result.sort(key=lambda x: x['ratio'], reverse=True)
        hit_count = sum(1 for r in result if r['isHit'])

        return jsonify({
            'date':      date,
            'threshold': threshold,
            'total':     len(result),
            'hitCount':  hit_count,
            'data':      result,
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
