from flask import Flask, jsonify, request
from flask_cors import CORS
from datetime import datetime, timedelta
import traceback
import os

app = Flask(__name__)
CORS(app)

def nearest_biz_day(date_str):
    d = datetime.strptime(date_str, '%Y%m%d')
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime('%Y%m%d')

def get_date():
    raw = request.args.get('date', '') or datetime.now().strftime('%Y%m%d')
    return nearest_biz_day(raw.replace('-', ''))

@app.route('/api/health')
def health():
    return jsonify({'status': 'ok', 'time': datetime.now().isoformat()})

@app.route('/api/debug')
def debug():
    try:
        import FinanceDataReader as fdr
        date = get_date()
        df = fdr.DataReader('005930', date, date)
        return jsonify({
            'date': date,
            'columns': list(df.columns),
            'sample': df.to_dict(orient='records'),
            'row_count': len(df),
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500

@app.route('/api/scan')
def scan():
    try:
        import FinanceDataReader as fdr
        date      = get_date()
        threshold = float(request.args.get('threshold', 0.3))

        kospi_df  = fdr.StockListing('KOSPI')
        kosdaq_df = fdr.StockListing('KOSDAQ')

        if kospi_df.empty and kosdaq_df.empty:
            return jsonify({'error': f'{date} 데이터 없음'}), 404

        result = []

        def build_rows(df, market_name):
            for _, row in df.iterrows():
                try:
                    ticker = str(row.get('Code', '')).zfill(6)
                    if not ticker or ticker == '000000':
                        continue
                    name        = str(row.get('Name', ticker))
                    close       = int(float(row.get('Close', 0) or 0))
                    if close <= 0:
                        continue
                    change_rate = float(row.get('ChagesRatio', 0) or 0)
                    mkt_cap     = int(float(row.get('Marcap', 0) or 0))
                    shares      = int(float(row.get('Stocks', 0) or 0))

                    frgn_net = 0
                    inst_net = 0
                    try:
                        stock_df = fdr.DataReader(ticker, date, date)
                        if not stock_df.empty:
                            cols = [c.lower() for c in stock_df.columns]
                            for cname in ['foreignnetvolume', 'foreign_net', 'foreignnet']:
                                matches = [i for i, c in enumerate(cols) if cname in c]
                                if matches:
                                    frgn_net = int(stock_df.iloc[-1, matches[0]])
                                    break
                            for cname in ['institutionnetvolume', 'institution_net', 'instnet']:
                                matches = [i for i, c in enumerate(cols) if cname in c]
                                if matches:
                                    inst_net = int(stock_df.iloc[-1, matches[0]])
                                    break
                    except Exception:
                        pass

                    total_net = frgn_net + inst_net
                    ratio     = (total_net / mkt_cap * 100) if mkt_cap > 0 else 0.0

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

        build_rows(kospi_df,  'KOSPI')
        build_rows(kosdaq_df, 'KOSDAQ')

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
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
