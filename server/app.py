from flask import Flask, jsonify, request
from flask_cors import CORS
from datetime import datetime, timedelta
import traceback
import os
import urllib.request
import urllib.parse
import json

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

def get_netbuy_krx(date, market):
    mktId = 'STK' if market == 'KOSPI' else 'KSQ'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'http://data.krx.co.kr/',
        'Content-Type': 'application/x-www-form-urlencoded',
    }
    otp_payload = urllib.parse.urlencode({
        'locale': 'ko_KR',
        'mktId': mktId,
        'trdDd': date,
        'money': '1',
        'csvxls_isNo': 'false',
        'name': 'fileDown',
        'filetype': 'csv',
        'url': 'dbms/MDC/STAT/standard/MDCSTAT02303',
    }).encode()

    try:
        otp_req = urllib.request.Request(
            'http://data.krx.co.kr/comm/fileDn/GenerateOTP/generate.cmd',
            data=otp_payload, headers=headers
        )
        with urllib.request.urlopen(otp_req, timeout=15) as r:
            otp = r.read().decode().strip()

        if not otp:
            return {}

        down_payload = urllib.parse.urlencode({'code': otp}).encode()
        down_req = urllib.request.Request(
            'http://data.krx.co.kr/comm/fileDn/download_csv.cmd',
            data=down_payload, headers=headers
        )
        with urllib.request.urlopen(down_req, timeout=15) as r:
            raw_bytes = r.read()
            try:
                content = raw_bytes.decode('utf-8-sig')
            except Exception:
                content = raw_bytes.decode('euc-kr', errors='replace')

        lines = content.strip().split('\n')
        if len(lines) < 2:
            return {}

        headers_row = [h.strip().strip('"') for h in lines[0].split(',')]
        result = {}

        code_idx = next((i for i, h in enumerate(headers_row) if '종목코드' in h or 'Code' in h or 'ISU_SRT_CD' in h), 0)
        frgn_idx = next((i for i, h in enumerate(headers_row) if '외국인' in h and '순매수' in h), 8)
        inst_idx = next((i for i, h in enumerate(headers_row) if '기관' in h and '순매수' in h), 12)

        for line in lines[1:]:
            cols = [c.strip().strip('"') for c in line.split(',')]
            if len(cols) <= max(code_idx, frgn_idx, inst_idx):
                continue
            try:
                ticker = cols[code_idx].zfill(6)
                frgn = int(cols[frgn_idx].replace(',', '') or 0)
                inst = int(cols[inst_idx].replace(',', '') or 0)
                result[ticker] = {'frgn': frgn, 'inst': inst}
            except Exception:
                pass

        return result

    except Exception:
        return {}


@app.route('/api/health')
def health():
    return jsonify({'status': 'ok', 'time': datetime.now().isoformat()})


@app.route('/api/debug')
def debug():
    try:
        import FinanceDataReader as fdr
        date = get_date()
        kospi = fdr.StockListing('KOSPI')
        netbuy = get_netbuy_krx(date, 'KOSPI')
        return jsonify({
            'date': date,
            'kospi_count': len(kospi),
            'kospi_columns': list(kospi.columns),
            'netbuy_count': len(netbuy),
            'netbuy_sample': dict(list(netbuy.items())[:3]) if netbuy else {},
            'kospi_sample': kospi.head(2).to_dict(orient='records'),
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

        kospi_net  = get_netbuy_krx(date, 'KOSPI')
        kosdaq_net = get_netbuy_krx(date, 'KOSDAQ')

        result = []

        def build_rows(df, net_map, market_name):
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
                    net_info    = net_map.get(ticker, {})
                    frgn_net    = net_info.get('frgn', 0)
                    inst_net    = net_info.get('inst', 0)
                    total_net   = frgn_net + inst_net
                    ratio       = (total_net / mkt_cap * 100) if mkt_cap > 0 else 0.0
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

        build_rows(kospi_df,  kospi_net,  'KOSPI')
        build_rows(kosdaq_df, kosdaq_net, 'KOSDAQ')

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
