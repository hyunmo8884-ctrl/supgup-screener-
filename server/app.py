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

def krx_fetch(url, payload):
    headers = {
        'User-Agent': 'Mozilla/5.0',
        'Referer': 'http://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd',
    }
    otp_url = 'http://data.krx.co.kr/comm/fileDn/GenerateOTP/generate.cmd'
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(otp_url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        otp = r.read().decode()

    data2 = urllib.parse.urlencode({'code': otp}).encode()
    req2 = urllib.request.Request(url, data=data2, headers=headers)
    with urllib.request.urlopen(req2, timeout=30) as r:
        return json.loads(r.read().decode())

def get_ohlcv(date, market):
    mktId = 'STK' if market == 'KOSPI' else 'KSQ'
    payload = {
        'bld': 'dbms/MDC/STAT/standard/MDCSTAT01501',
        'mktId': mktId,
        'trdDd': date,
        'share': '1',
        'money': '1',
        'csvxls_isNo': 'false',
    }
    raw = krx_fetch('http://data.krx.co.kr/comm/fileDn/download_csv.cmd', payload)
    rows = raw.get('OutBlock_1', [])
    result = {}
    for r in rows:
        ticker = r.get('ISU_SRT_CD', '')
        if not ticker:
            continue
        try:
            close = int(str(r.get('TDD_CLSPRC', '0')).replace(',', ''))
            change_rate = float(str(r.get('FLUC_RT', '0')).replace(',', ''))
            name = r.get('ISU_ABBRV', ticker)
            result[ticker] = {'close': close, 'changeRate': change_rate, 'name': name}
        except Exception:
            pass
    return result

def get_marcap(date, market):
    mktId = 'STK' if market == 'KOSPI' else 'KSQ'
    payload = {
        'bld': 'dbms/MDC/STAT/standard/MDCSTAT01501',
        'mktId': mktId,
        'trdDd': date,
        'share': '1',
        'money': '1',
        'csvxls_isNo': 'false',
    }
    raw = krx_fetch('http://data.krx.co.kr/comm/fileDn/download_csv.cmd', payload)
    rows = raw.get('OutBlock_1', [])
    result = {}
    for r in rows:
        ticker = r.get('ISU_SRT_CD', '')
        if not ticker:
            continue
        try:
            mktcap = int(str(r.get('MKTCAP', '0')).replace(',', ''))
            shares = int(str(r.get('LIST_SHRS', '0')).replace(',', ''))
            result[ticker] = {'mktcap': mktcap, 'shares': shares}
        except Exception:
            pass
    return result

def get_netbuy(date, market):
    mktId = 'STK' if market == 'KOSPI' else 'KSQ'
    payload = {
        'bld': 'dbms/MDC/STAT/standard/MDCSTAT02303',
        'mktId': mktId,
        'trdDd': date,
        'invstTpCd': '4000',
        'csvxls_isNo': 'false',
    }
    try:
        raw = krx_fetch('http://data.krx.co.kr/comm/fileDn/download_csv.cmd', payload)
        rows = raw.get('OutBlock_1', [])
        result = {}
        for r in rows:
            ticker = r.get('ISU_SRT_CD', '')
            if not ticker:
                continue
            try:
                frgn = int(str(r.get('FRGN_NETBID_TRDVOL', '0')).replace(',', ''))
                inst = int(str(r.get('ORGN_NETBID_TRDVOL', '0')).replace(',', ''))
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
        date = get_date()
        payload = {
            'bld': 'dbms/MDC/STAT/standard/MDCSTAT01501',
            'mktId': 'STK',
            'trdDd': date,
            'share': '1',
            'money': '1',
            'csvxls_isNo': 'false',
        }
        raw = krx_fetch('http://data.krx.co.kr/comm/fileDn/download_csv.cmd', payload)
        rows = raw.get('OutBlock_1', [])
        return jsonify({
            'date': date,
            'row_count': len(rows),
            'sample_keys': list(rows[0].keys()) if rows else [],
            'sample_row': rows[0] if rows else {},
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500

@app.route('/api/scan')
def scan():
    try:
        date      = get_date()
        threshold = float(request.args.get('threshold', 0.3))

        kospi_price  = get_ohlcv(date, 'KOSPI')
        kosdaq_price = get_ohlcv(date, 'KOSDAQ')

        if not kospi_price and not kosdaq_price:
            return jsonify({'error': f'{date} 휴장일이거나 데이터 없음'}), 404

        kospi_cap  = get_marcap(date, 'KOSPI')
        kosdaq_cap = get_marcap(date, 'KOSDAQ')
        kospi_net  = get_netbuy(date, 'KOSPI')
        kosdaq_net = get_netbuy(date, 'KOSDAQ')

        result = []

        def build_rows(price_map, cap_map, net_map, market_name):
            for ticker, p in price_map.items():
                try:
                    close = p['close']
                    if close <= 0:
                        continue

                    cap_info = cap_map.get(ticker, {})
                    mkt_cap  = cap_info.get('mktcap', 0)
                    shares   = cap_info.get('shares', 0)

                    net_info = net_map.get(ticker, {})
                    frgn_net = net_info.get('frgn', 0)
                    inst_net = net_info.get('inst', 0)

                    total_net = frgn_net + inst_net
                    ratio     = (total_net / mkt_cap * 100) if mkt_cap > 0 else 0.0

                    result.append({
                        'isuCd':       ticker,
                        'isuNm':       p.get('name', ticker),
                        'market':      market_name,
                        'close':       close,
                        'changeRate':  round(p['changeRate'], 2),
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

        build_rows(kospi_price,  kospi_cap,  kospi_net,  'KOSPI')
        build_rows(kosdaq_price, kosdaq_cap, kosdaq_net, 'KOSDAQ')

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
