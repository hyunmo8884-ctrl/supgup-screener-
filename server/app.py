from flask import Flask, jsonify, request
from flask_cors import CORS
from datetime import datetime, timedelta
import traceback
import os
import urllib.request
import urllib.parse

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
        date = get_date()
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'http://data.krx.co.kr/',
            'Content-Type': 'application/x-www-form-urlencoded',
        }
        otp_payload = urllib.parse.urlencode({
            'locale': 'ko_KR',
            'mktId': 'STK',
            'trdDd': date,
            'money': '1',
            'csvxls_isNo': 'false',
            'name': 'fileDown',
            'filetype': 'csv',
            'url': 'dbms/MDC/STAT/standard/MDCSTAT02303',
        }).encode()

        otp_req = urllib.request.Request(
            'http://data.krx.co.kr/comm/fileDn/GenerateOTP/generate.cmd',
            data=otp_payload, headers=headers
        )
        with urllib.request.urlopen(otp_req, timeout=15) as r:
            otp = r.read().decode().strip()

        down_payload = urllib.parse.urlencode({'code': otp}).encode()
        down_req = urllib.request.Request(
            'http://data.krx.co.kr/comm/fileDn/download_csv.cmd',
            data=down_payload, headers=headers
        )
        with urllib.request.urlopen(down_req, timeout=15) as r:
            raw_bytes = r.read()

        for enc in ['utf-8-sig', 'euc-kr', 'cp949']:
            try:
                content = raw_bytes.decode(enc)
                break
            except Exception:
                content = raw_bytes.decode('utf-8', errors='replace')

        lines = content.strip().split('\n')

        return jsonify({
            'date': date,
            'otp': otp,
            'total_lines': len(lines),
            'header_line': lines[0] if lines else '',
            'first_data_line': lines[1] if len(lines) > 1 else '',
            'raw_first_500': content[:500],
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
                    result.append({
                        'isuCd':       ticker,
                        'isuNm':       name,
                        'market':      market_name,
                        'close':       close,
                        'changeRate':  round(change_rate, 2),
                        'floatShares': shares,
                        'floatMktCap': mkt_cap,
                        'foreignNet':  0,
                        'instNet':     0,
                        'totalNet':    0,
                        'ratio':       0.0,
                        'threshold':   round(mkt_cap * 0.003),
                        'isHit':       False,
                    })
                except Exception:
                    pass

        build_rows(kospi_df,  'KOSPI')
        build_rows(kosdaq_df, 'KOSDAQ')
        result.sort(key=lambda x: x['changeRate'], reverse=True)

        return jsonify({
            'date':      date,
            'threshold': threshold,
            'total':     len(result),
            'hitCount':  0,
            'data':      result,
            'notice':    '수급 데이터 연결 작업 중',
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
