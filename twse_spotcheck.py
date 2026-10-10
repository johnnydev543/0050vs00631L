#!/usr/bin/env python3
"""Fetch public TWSE spot checks and split notices, retaining original evidence."""
import concurrent.futures
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = Path(__file__).resolve().parent
RAW = BASE / 'raw'
RAW.mkdir(exist_ok=True)
normalized = json.loads((BASE / 'normalized.json').read_text())
checks = [('0050', '20141001'), ('00631L', '20141001'),
          ('0050', '20261001'), ('00631L', '20261001'),
          ('0050', '20250601'), ('00631L', '20260301')]
notices = [
    ('0050', 'https://www.twse.com.tw/staticFiles/news/news/tsecnews/8a8216d696b406fc0196ce27c2e90063.pdf'),
    ('00631L', 'https://wwwc.twse.com.tw/staticFiles/news/news/tsecnews/8a8216d69c37647f019c994d0e2700d1.pdf'),
]

def fetch(url):
    req = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urlopen(req, timeout=25) as response:
        return response.read(), response.status, response.headers.get('Content-Type')

def prices(task):
    stock, month = task
    url = 'https://www.twse.com.tw/exchangeReport/STOCK_DAY?' + urlencode(
        {'response': 'json', 'date': month, 'stockNo': stock})
    result = {'stock_id': stock, 'month': month, 'url': url, 'status': 'UNAVAILABLE',
              'mismatches': [], 'compared_rows': 0, 'compared_fields': 0}
    try:
        raw, status, content_type = fetch(url)
        filename = f'twse_stock_day_{stock}_{month}.json'
        (RAW / filename).write_bytes(raw)
        result.update(http_status=status, content_type=content_type,
                      raw_file='raw/' + filename, sha256=hashlib.sha256(raw).hexdigest())
        data = json.loads(raw)
        result.update(provider_status=data.get('stat'), fields=data.get('fields'),
                      returned_rows=len(data.get('data', [])))
        if data.get('stat') != 'OK':
            result['reason'] = 'TWSE returned a non-OK status.'
            return result
        fields = data['fields']
        columns = {'open': '開盤價', 'max': '最高價', 'min': '最低價', 'close': '收盤價'}
        seen = set()
        for row in data['data']:
            yy, mm, dd = map(int, row[fields.index('日期')].split('/'))
            date = f'{yy + 1911:04d}-{mm:02d}-{dd:02d}'
            if not normalized['start'] <= date <= normalized['end']:
                continue
            seen.add(date)
            expected = normalized['prices'][stock].get(date)
            if expected is None:
                result['mismatches'].append({'date': date, 'field': 'row',
                                             'reason': 'TWSE row absent from normalized prices'})
                continue
            result['compared_rows'] += 1
            for key, title in columns.items():
                actual = float(row[fields.index(title)].replace(',', ''))
                result['compared_fields'] += 1
                if abs(actual - float(expected[key])) > 1e-8:
                    result['mismatches'].append({'date': date, 'field': key,
                                                'twse': actual, 'finmind': expected[key]})
        month_prefix = f'{month[:4]}-{month[4:6]}'
        missing = sorted(d for d in normalized['prices'][stock] if
                         d.startswith(month_prefix) and d not in seen)
        result['normalized_rows_absent_from_twse'] = missing
        result['status'] = 'PASS' if result['compared_rows'] and not missing and not result['mismatches'] else 'FAIL'
    except Exception as exc:
        result['reason'] = f'{type(exc).__name__}: {exc}'
    return result

def notice(task):
    stock, url = task
    result = {'stock_id': stock, 'url': url, 'status': 'UNAVAILABLE'}
    try:
        raw, status, content_type = fetch(url)
        if not raw.startswith(b'%PDF'):
            raise ValueError('Response does not contain a PDF header')
        filename = f'twse_split_notice_{stock}.pdf'
        (RAW / filename).write_bytes(raw)
        result.update(status='SAVED', http_status=status, content_type=content_type,
                      raw_file='raw/' + filename, bytes=len(raw),
                      sha256=hashlib.sha256(raw).hexdigest())
    except Exception as exc:
        result['reason'] = f'{type(exc).__name__}: {exc}'
    return result

with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    results = list(pool.map(prices, checks))
    saved = list(pool.map(notice, notices))
report = {'checked_at_utc': datetime.now(timezone.utc).isoformat(),
          'status': 'PASS' if all(x['status'] == 'PASS' for x in results) else 'PARTIAL_OR_FAILED',
          'scope': 'Six stock-month samples; OHLC only, within the normalized backtest interval.',
          'total_compared_rows': sum(x['compared_rows'] for x in results),
          'total_compared_fields': sum(x['compared_fields'] for x in results),
          'total_mismatches': sum(len(x['mismatches']) for x in results),
          'checks': results, 'split_notices': saved}
(BASE / 'twse_spotcheck.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps(report, ensure_ascii=False, indent=2))
