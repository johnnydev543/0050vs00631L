"""Validate immutable provider snapshots and build the backtest input (stdlib only)."""
from __future__ import annotations
import argparse, csv, hashlib, json, math
from pathlib import Path

SOURCES = {
 '0050': {'date':'2025-06-18','ratio':4,'suspended':['2025-06-11','2025-06-12','2025-06-13','2025-06-16','2025-06-17'],
          'source':'https://www.twse.com.tw/staticFiles/news/news/tsecnews/8a8216d696b406fc0196ce27c2e90063.pdf'},
 '00631L': {'date':'2026-03-31','ratio':22,'suspended':['2026-03-25','2026-03-26','2026-03-27','2026-03-30'],
           'source':'https://wwwc.twse.com.tw/staticFiles/news/news/tsecnews/8a8216d69c37647f019c994d0e2700d1.pdf'},
}

def main() -> None:
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path(__file__).parent);a=p.parse_args();root=a.root
 queries={};manifest=[];errors=[]
 for path in sorted((root/'raw').glob('*.jsonl')):
  if path.name not in ['download.jsonl','parvalue.jsonl']: continue
  manifest.append({'path':str(path.relative_to(root)),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
  for line in path.read_text().splitlines():
   envelope=json.loads(line);params=envelope['params'];obj=json.loads(envelope.get('response_text','{}'))
   key=(params['dataset'],params.get('data_id',''))
   if envelope.get('http_status')==200 and obj.get('status')==200:
    if path.name=='parvalue.jsonl' and key in queries:continue
    queries[key]=obj['data']
   else:errors.append({'dataset':key[0],'stock_id':key[1],'http_status':envelope.get('http_status'),'message':obj.get('msg',envelope.get('error'))})
 official_path=root/'raw/yuanta_0050_dividends.json'
 official=json.loads(official_path.read_text())
 assert official['ResultCode']==0
 manifest.append({'path':str(official_path.relative_to(root)),'sha256':hashlib.sha256(official_path.read_bytes()).hexdigest(),
                  'source':'https://www.yuantafunds.com/myfund/dividend/history?fundid=1066'})
 official_dividends={r['SHARE_DATE'].replace('/','-'):r for r in official['Data']['Data']}
 start='2014-10-31';requested_end='2026-10-09'
 calendar=sorted(r['date'] for r in queries['TaiwanStockTradingDate',''] if start<=r['date']<=requested_end)
 assert len(calendar)==len(set(calendar)) and calendar[0]==start and calendar[-1]=='2026-10-08'
 out={'start':start,'end':calendar[-1],'calendar':calendar,'prices':{},'splits':{},'dividends':{},'metadata':{}}
 report={'status':'PASS_WITH_LIMITATIONS','requested_start':start,'requested_end':requested_end,'actual_end':calendar[-1],
         'market_sessions':len(calendar),'api_failures':errors,'manifest':manifest,'instruments':{},
         'limitations':['Calculations use raw prices and corporate-action accounting; provider-adjusted-price equivalence was not certified.',
          'Current data snapshot is retrospective; no historical vintage or event publication timestamps proven.',
          'Raw prices are preserved; not every historical price independently checked against TWSE.',
          'Only officially verified suspension dates receive stale valuation; they are never fabricated tradable prices.']}
 derived=[]
 for s in ['0050','00631L']:
  rows=[r for r in queries['TaiwanStockPrice',s] if start<=r['date']<=requested_end]
  prices={r['date']:r for r in rows};assert len(prices)==len(rows),f'duplicate prices {s}'
  missing=sorted(set(calendar)-prices.keys());extra=sorted(prices.keys()-set(calendar))
  assert missing==SOURCES[s]['suspended'] and not extra,(s,missing,extra)
  for d,r in prices.items():
   assert r['stock_id']==s and r['Trading_Volume']>0
   assert all(math.isfinite(float(r[k])) for k in ['open','max','min','close'])
   assert 0<r['min']<=r['open']<=r['max'] and r['min']<=r['close']<=r['max'],(s,d,'OHLC')
  splits=queries['TaiwanStockSplitPrice',s]
  assert len(splits)==1 and splits[0]['date']==SOURCES[s]['date'],(s,'split date')
  split=splits[0];ratio=SOURCES[s]['ratio']
  assert abs(split['before_price']/ratio-split['after_price'])<=0.011,(s,'rounded split reference')
  assert not queries['TaiwanStockCapitalReductionReferencePrice',s]
  assert not [r for r in queries['TaiwanStockParValueChange',''] if r.get('stock_id')==s]
  dividends=[];result_rows=[r for r in queries['TaiwanStockDividendResult',s] if start<=r['date']<=requested_end]
  result_by_date={r['date']:r for r in result_rows}
  assert len(result_by_date)==len(result_rows),'duplicate dividend-result records'
  checks=[]
  for r in queries['TaiwanStockDividend',s]:
   ex=r['CashExDividendTradingDate']
   if not start<=ex<=requested_end:continue
   assert not r['StockEarningsDistribution'] and not r['StockStatutorySurplus'],'unsupported stock dividend'
   amount=r['CashEarningsDistribution']+r['CashStatutorySurplus'];pay=r['CashDividendPaymentDate']
   assert amount>0 and pay>=ex and ex in prices,(s,'dividend date')
   result=result_by_date[ex]
   assert abs(result['stock_and_cache_dividend']-amount)<1e-8
   assert abs(result['before_price']-result['after_price']-amount)<1e-8
   prior=max(d for d in prices if d<ex)
   assert abs(prices[prior]['close']-result['before_price'])<1e-8,(s,ex,'reference close')
   dividends.append({'ex_date':ex,'pay_date':pay,'amount':amount,'provider_record_date':r['date'],'announcement_at':None})
   if s=='0050':
    official_row=official_dividends[ex]
    assert abs(official_row['DIVIDEN_PER_UNIT']-amount)<1e-8
    assert official_row['PAY_DATE'].replace('/','-')==pay
   checks.append({'ex_date':ex,'pay_date':pay,'amount':amount,'result_amount_match':True,'reference_price_match':True,'yuanta_official_match':s=='0050'})
  assert len(dividends)==len(result_by_date) and len({r['ex_date'] for r in dividends})==len(dividends)
  if s=='0050':assert {r['ex_date'] for r in dividends}=={d for d in official_dividends if start<=d<=requested_end}
  out['prices'][s]=prices;out['splits'][s]=[{'date':SOURCES[s]['date'],'ratio':ratio,'source':SOURCES[s]['source']}];out['dividends'][s]=dividends
  report['instruments'][s]={'observed_prices':len(prices),'missing_market_dates':missing,'verified_suspension_source':SOURCES[s]['source'],
   'unexplained_missing':[],'duplicates':0,'invalid_ohlc':0,'split_event':out['splits'][s][0],
   'dividends_checked':len(dividends),'dividend_checks':checks,'first_row':rows[0],'last_row':rows[-1]}
  # Self-built theoretical ex-date reinvested close index for diagnostics only.
  # Provider-adjusted prices are kept separately and never used as trade prices.
  prev=None;index=100.0;divmap={r['ex_date']:r['amount'] for r in dividends}
  daily_moves=[]
  for d in calendar:
   if d not in prices:
    derived.append({'stock_id':s,'date':d,'raw_close':'','split_adjusted_close':'','diagnostic_total_return_index':index,'stale':True});continue
   close=prices[d]['close'];mult=ratio if d==SOURCES[s]['date'] else 1
   ret=0 if prev is None else (close*mult+divmap.get(d,0))/prev-1
   if prev is not None:index*=1+ret;daily_moves.append({'date':d,'return':ret})
   adjclose=close/ratio if d<SOURCES[s]['date'] else close
   derived.append({'stock_id':s,'date':d,'raw_close':close,'split_adjusted_close':adjclose,'diagnostic_total_return_index':index,'stale':False})
   prev=close
  report['instruments'][s]['largest_absolute_diagnostic_returns']=sorted(daily_moves,key=lambda r:abs(r['return']),reverse=True)[:5]
 adjusted={}
 for s in SOURCES:
  items=[r for r in queries.get(('TaiwanStockPriceAdj',s),[]) if start<=r['date']<=requested_end]
  by_date={r['date']:r for r in items}
  assert len(by_date)==len(items), 'duplicate adjusted-price dates'
  assert all(math.isfinite(float(r['close'])) and float(r['close'])>0 for r in items), 'invalid adjusted close'
  checks=[]
  for event in out['splits'][s]+[dict(d,date=d['ex_date']) for d in out['dividends'][s]]:
   d=event['date'];prior=max(t for t in out['prices'][s] if t<d)
   if d in by_date and prior in by_date:
    checks.append({'date':d,'previous_actual_market_date':prior,
                   'adjusted_close_return':float(by_date[d]['close'])/float(by_date[prior]['close'])-1,
                   'raw_close_return':out['prices'][s][d]['close']/out['prices'][s][prior]['close']-1})
  adjusted[s]={'available':bool(items),'records':len(items),'duplicate_dates':len(items)-len(by_date),
                'date_set_matches_actual_prices':set(by_date)==set(out['prices'][s]) if items else None,
                'event_diagnostics':checks,
                'use':'Diagnostic only; portfolio signals, fills and dividends use raw prices and accounting.'}
 report['adjusted_price_diagnostics']=adjusted
 out['metadata']={'requested_end':requested_end,'verified_suspensions':{s:SOURCES[s]['suspended'] for s in SOURCES},
  'quality_status':report['status'],'finmind_adjusted_price_available':all(x['available'] for x in adjusted.values()),'data_manifest':manifest,
  'method':'Raw prices plus effective corporate-action accounting; no adjusted prices used for signal or fills.',
  'not_strict_historical_vintage':True}
 spotcheck=root/'twse_spotcheck.json'
 if spotcheck.exists():
  spot=json.loads(spotcheck.read_text())
  report['independent_twse_spotcheck']={k:spot[k] for k in ['status','scope','total_compared_rows','total_compared_fields','total_mismatches']}
 (root/'normalized.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
 (root/'data_quality.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
 with (root/'derived_price_diagnostics.csv').open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(derived[0]));w.writeheader();w.writerows(derived)
 print(json.dumps({s:{k:v for k,v in r.items() if k in ['observed_prices','dividends_checked','unexplained_missing','largest_absolute_diagnostic_returns']} for s,r in report['instruments'].items()},ensure_ascii=False))

if __name__=='__main__':main()
