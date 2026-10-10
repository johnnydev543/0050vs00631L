#!/usr/bin/env python3
"""Read public Yuanta 0050 dividend records; no account or token required."""
import argparse
import csv
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen
from uuid import uuid4

parser = argparse.ArgumentParser()
parser.add_argument('--output-dir', default='work')
args = parser.parse_args()
output = Path(args.output_dir)
output.mkdir(parents=True, exist_ok=True)
params = {
    'APIType': 'EC2API', 'AppName': 'FundWeb',
    'PageName': '/myfund/dividend/history',
    'Device': '4', 'DeviceId': str(uuid4()),
    'FuncId': 'FundDividend', 'FundId': '1066',
    'SDate': '20141001', 'EDate': '20261031', 'FundType': 'ETF',
}
url = 'https://api.yuantafunds.com/ectranslation/api/trans?' + urlencode(params)
with urlopen(url, timeout=30) as response:
    raw = response.read()
payload = json.loads(raw)
if payload.get('ResultCode') != 0:
    raise RuntimeError(payload.get('ResultMsg', 'Unknown Yuanta error'))
(output / 'yuanta_0050_dividends.json').write_bytes(raw)
rows = []
for record in payload['Data']['Data']:
    ex_date = record['SHARE_DATE'].replace('/', '-')
    if '2014-10-31' < ex_date <= '2026-10-09':
        rows.append({'stock_id': '0050', 'ex_date': ex_date,
                     'pay_date': record['PAY_DATE'].replace('/', '-'),
                     'dividend_per_unit': record['DIVIDEN_PER_UNIT']})
rows.sort(key=lambda row: row['ex_date'])
with (output / 'yuanta_0050_dividends.csv').open('w', newline='') as handle:
    writer = csv.DictWriter(handle, fieldnames=['stock_id', 'ex_date', 'pay_date', 'dividend_per_unit'])
    writer.writeheader()
    writer.writerows(rows)
print(json.dumps({'records_in_backtest': len(rows), 'source_url': url,
                  'source_page': 'https://www.yuantafunds.com/myfund/dividend/history?fundid=1066'}, ensure_ascii=False))
