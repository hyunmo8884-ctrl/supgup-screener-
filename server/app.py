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

        kospi = fdr.StockListing('KOSPI')
        kosdaq = fdr.StockListing('KOSDAQ')

        return jsonify({
            'date': date,
            'kospi_count': len(kospi),
            'kosdaq_count': len(kosdaq),
            'kospi_columns': list(kospi.columns),
            'kospi_sample': kospi.head(2).to_dict(orient='records'),
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500

@app.route('/api/scan')
def scan():
    try:
        import FinanceDataReader as fdr
        date = get_date()
        threshold = float(request.args.get('threshold', 0.3))

        result = []

        for market in ['KOSPI', 'KOSDAQ']:
            try:
                listing = fdr.StockListing(market)

                def gcol(candidates):
                    for c in candidates:
                        for col in listing.columns:
                            if col.upper() == c.upper() or c.upper() in col.upper():
                                return col
                    return None

                code_col   = gcol(['CODE', 'SYMBOL', 'ISU_SRT_CD', 'TICKER'])
                name_col   = gcol(['NAME', 'ISU_ABBRV', 'CORP_NAME', 'COMPANY'])
                close_col  = gcol(['CLOSE', 'TDD_CLSPRC', 'PRICE', '종가'])
                change_col = gcol(['CHANGES', 'CHANGE', 'FLUC_RT', 'CHG', '등락률'])
                cap_col    = gcol(['MARCAP', 'MKTCAP', 'CAP', '시가총액'])

                for _, row in listing.iterrows():
                    try:
                        ticker = str(row[code_col]).zfill(6) if code_col else ''
                        if not ticker or ticker == 'nan':
                            continue

                        name  = str(row[name_col]) if name_col else ticker
                        close = int(float(str(row[close_col]).replace(',',''))) if close_col else 0
                        if close <= 0:
                            continue

                        change_rate = float(str(row[change_col]).replace(',','').replace('%','')) if change_col else 0.0
                        mkt_cap     = int(float(str(row[cap_col]).replace(',',''))) if cap_col else 0

                        frgn_net = 0
                        inst_net = 0

                        total_net = frgn_net + inst_net
                        ratio     = (total_net / mkt_cap * 100) if mkt_cap > 0 else 0.0

                        result.append({
                            'isuCd':       ticker,
                            'isuNm':       name,
                            'market':      market,
                            'close':       close,
                            'changeRate':  round(change_rate, 2),
                            'floatShares': 0,
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
            except Exception:
                pass

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
