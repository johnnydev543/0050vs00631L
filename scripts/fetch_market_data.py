"""Refresh successful market snapshots for the fixed study window."""
from __future__ import annotations
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[1]
BASE='https://api.finmindtrade.com/api/v4/data'


def main() -> None:
    token=os.environ.get('FINMIND_API_TOKEN')
    if not token:
        raise SystemExit('The repository Actions secret is not configured.')
    datasets=['TaiwanStockPrice','TaiwanStockPriceAdj','TaiwanStockDividend',
              'TaiwanStockDividendResult','TaiwanStockSplitPrice',
              'TaiwanStockCapitalReductionReferencePrice']
    queries=[(stock,dataset) for stock in ['0050','00631L'] for dataset in datasets]
    queries += [('', 'TaiwanStockTradingDate'),('', 'TaiwanStockParValueChange')]
    successful=[];checks=[]
    for stock,dataset in queries:
        params={'dataset':dataset,'start_date':'2014-01-01','end_date':'2026-10-09'}
        if stock:params['data_id']=stock
        request=Request(BASE+'?'+urlencode(params),headers={'Authorization':'Bearer '+token})
        http_status=None
        try:
            with urlopen(request,timeout=60) as response:
                http_status=response.status
                raw=response.read().decode().replace(token,'[REDACTED]')
            payload=json.loads(raw)
        except HTTPError as exc:
            http_status=exc.code;payload={}
        except Exception as exc:
            raise RuntimeError(f'Data download failed for {dataset} {stock}: {type(exc).__name__}') from None
        ok=http_status==200 and payload.get('status')==200
        checks.append({'dataset':dataset,'stock_id':stock,'http_status':http_status,
                       'available':ok,'records':len(payload.get('data',[])) if ok else None})
        if ok:
            successful.append({'source_url':BASE,'params':params,
                               'retrieved_at':datetime.now(timezone.utc).isoformat(),
                               'http_status':http_status,'response_text':raw})
        elif dataset!='TaiwanStockPriceAdj':
            raise RuntimeError(f'Required market dataset unavailable: {dataset} {stock} HTTP {http_status}')
        time.sleep(.2)
    raw_directory=ROOT/'raw';raw_directory.mkdir(exist_ok=True)
    snapshot='\n'.join(json.dumps(x,ensure_ascii=False) for x in successful)+'\n'
    temporary=raw_directory/'download.jsonl.tmp';temporary.write_text(snapshot)
    temporary.replace(raw_directory/'download.jsonl')
    (ROOT/'validation').mkdir(exist_ok=True)
    (ROOT/'validation/source_refresh.json').write_text(json.dumps({'checks':checks,'status':'PASS'},indent=2))
    print(json.dumps({'successful_datasets':len(successful),
                      'adjusted_prices_available':all(x['available'] for x in checks if x['dataset']=='TaiwanStockPriceAdj')},ensure_ascii=False))


if __name__=='__main__':main()
